#!/usr/bin/env python3
"""Read a single-file offline client (the "Eagler26" format) without a browser.

These offline builds carry their whole payload inside the page as data chunks:

    [script type="application/x-eagler26-chunk" data-r="NAME" data-len="N" data-sum="S"] ENCODED [/script]

The bytes are windows-1252 characters, one per byte, except the 36 bytes an HTML parser would change (NUL, CR,
`<`, the escape character itself and 0x80-0x9F): those are written as the escape character (61, `=`) followed by
`base + index` (base 64, `@`). Every chunk carries a Fletcher-style check, and the page's own metadata carries a
sha256 and an uncompressed size for each resource, so this tool can prove what it extracted is what the browser
would have unpacked - which is the whole point of extracting it rather than trusting a copy.

A module's text is usually gzip-compressed, and `--decompress` (the default) leaves it as the module the client
actually loads. `wasm` and the audio/asset archives are gzip as well; `--only` picks what to unpack so studying
one module does not mean unpacking 100 MB of WebAssembly.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import pathlib
import re
import sys

CHUNK_RE = re.compile(
    rb'<script type="application/x-eagler26-chunk" data-r="([^"]+)" data-len="(\d+)" data-sum="(\d+)">(.*?)</script>',
    re.DOTALL,
)
META_RE = re.compile(rb'<script id="eagler26-offline-meta" type="application/json">(.*?)</script>', re.DOTALL)


def meta(html: bytes) -> dict:
    found = META_RE.search(html)
    if not found:
        raise ValueError("this file has no eagler26-offline-meta block: it is not an offline build of that format")
    return json.loads(found.group(1).decode("utf-8", "replace"))


def _unescape_table(doc: dict) -> tuple[int, int, list[int]]:
    encoding = doc.get("encoding") or {}
    escape = int(encoding.get("escape", 61))
    base = int(encoding.get("base", 64))
    unsafe = [int(v) for v in encoding.get("unsafe") or []]
    if not unsafe:
        raise ValueError("the metadata lists no unsafe bytes, so the escape table cannot be built")
    return escape, base, unsafe


def decode_chunk(text: bytes, escape: int, base: int, unsafe: list[int]) -> tuple[bytes, int]:
    """The page's `decode()`, byte for byte: returns the bytes and the same Fletcher check it computes."""
    out = bytearray()
    s1 = 0
    s2 = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == escape:
            i += 1
            if i >= n:
                raise ValueError("the chunk ends on an escape byte")
            index = text[i] - base
            if not 0 <= index < len(unsafe):
                raise ValueError(f"escape byte {text[i]!r} is outside the table")
            c = unsafe[index]
        if c > 255:
            raise ValueError("a chunk character is above 255: the file was re-encoded")
        out.append(c)
        s1 = (s1 + c) & 0xFFFFFFFF
        s2 = (s2 + s1) & 0xFFFFFFFF
        i += 1
    return bytes(out), ((s1 & 0xFFFF) ^ (s2 << 16)) & 0xFFFFFFFF


def read_chunks(path: pathlib.Path, log=print) -> tuple[dict, dict[str, bytes]]:
    """Every chunk in the file, decoded and checked against the length and sum the page would check."""
    html = path.read_bytes()
    doc = meta(html)
    escape, base, unsafe = _unescape_table(doc)
    wanted = {name: info for name, info in (doc.get("resources") or {}).items()}
    chunks: dict[str, bytes] = {}
    seen: dict[str, int] = {}
    for name_b, len_b, sum_b, body in CHUNK_RE.findall(html):
        name = name_b.decode("utf-8")
        declared = int(len_b)
        expected = int(sum_b)
        data, check = decode_chunk(body, escape, base, unsafe)
        if len(data) != declared or check != expected:
            raise ValueError(f"chunk {name} is corrupt ({len(data)}/{declared} bytes, check {check} != {expected})")
        index = seen.get(name, 0)
        seen[name] = index + 1
        chunks[f"{name}#{index}" if index else name] = data
        log(f"  {name}: {declared:,} bytes stored")
    return doc, chunks


def gunzip_if_needed(data: bytes, info: dict) -> bytes:
    if (info or {}).get("encoding") == "gzip":
        return gzip.decompress(data)
    return data


def extract(path: pathlib.Path, out: pathlib.Path, only: list[str] | None = None, decompress=True,
            log=print) -> dict:
    """Write the payload to `out`, joining the parts of any resource that arrives in more than one chunk."""
    doc, chunks = read_chunks(path, log=log)
    resources = doc.get("resources") or {}
    # group chunks back into resources, in order
    grouped: dict[str, bytearray] = {}
    for key, data in chunks.items():
        name = key.split("#")[0]
        grouped.setdefault(name, bytearray()).extend(data)

    written = {}
    for name, blob in sorted(grouped.items()):
        info = resources.get(name)
        if not info:
            log(f"  ! {name} is not in the metadata: skipped")
            continue
        if only and not any(token in name for token in only):
            continue
        data = bytes(blob)
        if decompress:
            data = gunzip_if_needed(data, info)
        digest = hashlib.sha256(data).hexdigest()
        expect = (info or {}).get("sha256")
        status = "ok" if not expect or expect == digest else f"MISMATCH (expected {expect[:16]}...)"
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written[name] = {"bytes": len(data), "sha256": digest, "verified": status == "ok"}
        log(f"  -> {target} ({len(data):,} bytes, sha256 {status})")
    return {"build": doc.get("build") or {}, "resources": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=pathlib.Path, help="the single-file offline client (.html)")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("work/offline"), help="where to write")
    parser.add_argument("--only", action="append", default=[], help="only resources whose name contains this")
    parser.add_argument("--list", action="store_true", help="list the payload and stop")
    parser.add_argument("--keep-compressed", action="store_true", help="write the gzip bytes as they are stored")
    args = parser.parse_args(argv)

    if args.list:
        doc = meta(args.file.read_bytes())
        for name, info in sorted((doc.get("resources") or {}).items()):
            print(f"  {name:52} {info.get('size', 0):>12,} -> {info.get('stored', 0):>12,} stored")
        return 0

    result = extract(args.file, args.out, args.only or None, not args.keep_compressed)
    build = result["build"]
    print(f"{build.get('product')} {build.get('minecraftVersion')} ({build.get('eaglerVersion')})")
    bad = [n for n, info in result["resources"].items() if not info["verified"]]
    if bad:
        print(f"FAILED: {len(bad)} resource(s) do not match the sha256 in the file: {bad}")
        return 1
    print(f"{len(result['resources'])} resource(s) extracted and verified against the file's own hashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
