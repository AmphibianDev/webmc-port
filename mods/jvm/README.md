# Mods the real-JVM route carries

These are other people's binaries, and unlike everything else here they are not built by this
repository. They are the reason this folder exists: the JVM route loads mods at play time, so what it
needs is the *mod*, backported to Java 17 (a mod for 1.21.11 is Java 21 bytecode, and the browser JVM
stops at 17).

    meteor-client-1.21.11-86.jar    the 1.21.11-86 build from meteorclient.com, lowered by
                                   tools/backport_mods.py: classes 65 -> 61, the 15 jars nested
                                   inside it lowered too, 6 mixin configs lowered from JAVA_21 to
                                   JAVA_17, and `java: ">=21"` relaxed to ">=17" in its metadata.

To refresh or add one:

    python3 tools/backport_mods.py --input <the-downloaded.jar> --output mods/jvm/<name>.jar

The workflow hands everything in this folder to tools/jvm_prepare.py, which backports it again for the
bundle it deploys - the copy here is the input, not the artefact.
