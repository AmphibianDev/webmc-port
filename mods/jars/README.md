# Drop-in jars live here

Put a mod jar in this folder and it goes to the launcher: `tools/launchplan.py` checks that
`mods/mods.json` and this folder agree, hashes the jar, and stages it into the deployed site as
`site/mods/<name>.jar`, where the launch page fetches it with the rest of the launch.

```
mods/
  mods.json          what each jar is and which versions it is for   (committed)
  jars/              the jars themselves                            (git-ignored)
  examples/          source-built mods, compiled by tools/build_mods.py
```

Two kinds of jar are welcome, and the loader tells them apart by what is inside:

| The jar contains | What happens | Which mode |
| --- | --- | --- |
| `fabric.mod.json` | Fabric Loader starts it. Mixins work, because Fabric transforms the game's classes at load time. | `fabric`, `legacyfabric` |
| `webmc.mod.json` | Our own loader starts it, on the classpath with the game but without class transformation. | `vanilla` |
| neither | It stays on the classpath (a library another mod needs) and is reported as not started. | any |

Nothing here may be a native library: a browser has no native libraries, so a mod that ships a `.so`
or `.dll` cannot work in any row of the launcher. That is a property of the platform, not a setting.

## Licensing

`*.jar` is git-ignored on purpose. A mod jar is somebody else's binary with somebody else's licence,
and it is deployed into the site you deploy - so the rule is: only add jars you are allowed to
redistribute, and expect the deploy to serve them to whoever opens the launcher.
