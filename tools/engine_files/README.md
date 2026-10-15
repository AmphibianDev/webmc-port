# Cached engine files

These are copies of the few files in `TypeThe0ry/Gaius` (MIT - credited in `CREDITS.md`) that any entry in
`tools/aot_fixups.json` patches.

They are here for one reason: whether a fixup still matches is decidable without a build, but only if the file it
patches is on disk. `tests/test_fixup_anchors.py` runs the fixups against these copies in a second and says
whether the next build would have applied its patches or silently skipped them - which is the failure this project
keeps paying an hour for.

Nothing is built from them: they are the *targets* of a search-and-replace, kept small on purpose (five files,
about 460 KB).

Refresh them from a checkout of the engine:

```sh
python3 tools/refresh_engine_files.py --engine ~/devspace/WebMC/engine
```

`MANIFEST.json` records the repository, the commit and the time of the last refresh, and the test prints it: an
old cache is fine, an unnoticed one is not.
