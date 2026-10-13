# Where the loader stands

A Java runtime for the web with our own LWJGL 3 under it, so modern Minecraft can run in a browser. No
Eaglercraft code, no Gaius code: the mechanism came from reading how CheerpJ implements Java native
methods, and the work list came from measuring real clients.

## What is proven, from the CI job that runs it

`lwjgl3-webgl/test/BootTest.java` is a Java program that does what a game does on its first frame. The
last run got this far, and every line of it came through our shim:

```
  glfwInit
  glfwCreateWindow
  context current, version 3.3 WebMC
```

That means, end to end in a real browser:

* CheerpJ starts in Java 17 mode and loads our class from a jar.
* Our 214 JavaScript implementations are registered as natives and are actually being called.
* `GLFW.glfwInit` returns true, `glfwCreateWindow` creates a canvas and returns a handle,
  `glfwMakeContextCurrent` gets a WebGL2 context from it, and `GL11.glGetString(GL_VERTEX...)` answers
  from that context through our JavaScript.

Everything after that line - shader creation, geometry, the draw, the pixel readback - is where the next
run stops, and it is a small, specific thing rather than an open question.

## The pieces

| piece | what it is |
|---|---|
| `tools/lwjgl3_surface.py` | measures what a client demands: reads every `org.lwjgl` member out of a real client jar's constant pools. 458 members for 1.13 + 1.16.5 + 1.20.4 - so one shim covers 1.13 through 1.20.4, because Java 17 is the browser JVM's ceiling and that is where the range ends. |
| `tools/lwjgl3_scaffold.py` | generates the Java classes from that measurement, constants included (their real values, read out of the LWJGL jars - javac folds them into a client, so no measured surface mentions them and our own code cannot name them otherwise). |
| `tools/lwjgl3_natives.py` | reads the JavaScript and turns implemented members into `native` declarations, adds the few the measurement never saw, and generates the export block `cheerpjInit` consumes. It runs after the scaffold, in the workflow, because the scaffold rewrites those files. |
| `lwjgl3-webgl/natives/webmc-lwjgl3.js` | the implementation: GLFW as DOM events, GL as WebGL2, and the arena. 214 natives. |
| `lwjgl3-webgl/test/BootTest.java` | the question, asked in Java. |
| `.github/workflows/lwjgl3.yml` | regenerates, compiles for Java 17, serves the page, runs headless Chrome with SwiftShader, and asserts a frame. |

## What the failures cost, and what each one taught

1. `Could not find or load main class` - that is CheerpJ's message for a class file it will not load,
   and we had compiled with JDK 25. It runs Java 8, 11 and 17.
2. The same message again, with Java 17 bytecode: CheerpJ starts in **Java 8 mode** and the fix is
   `cheerpjInit({ version: 17 })`.
3. `UnsatisfiedLinkError: Java_org_lwjgl_glfw_GLFW_glfwInit` - the implementation existed and CheerpJ had
   never been told. `java.library.path` is the CheerpJ 3 mechanism; 4.x takes `natives: { ... }` as an
   option to `cheerpjInit`, with JNI names as keys.
4. A DOM dump that caught the script's own source, and a static import that failed before any of my code
   could report why: the page posts its result and its stage over HTTP now, with a watchdog, so a page
   that stops reporting says where it stopped.
5. A three-minute wait for a cold JVM on a shared runner: the stage posts showed it was simply still
   starting, so the wait is ten minutes and it ends as soon as there is a real result.

## The next step

`GL20.glCreateShader` is where it stops. The likely causes, in order: a JavaScript error inside a native
being reported as a JVM abort rather than a Java exception (their runner log will say which), the
`CharSequence` argument of `glShaderSource` arriving as a Java object rather than a JavaScript string, or
`glGetShaderi` misreading WebGL's boolean for `COMPILE_STATUS`.

The loop is: the shim workflow runs on every push that touches it, so a fix and a push is the whole
cycle, and the report names the last stage it reached.

## After the first frame

1. Wire the loader into the launcher for 1.13 to 1.20.4: the launch page asks for the version's jar from
   Mojang, as it does now, and hands CheerpJ the natives instead of the LWJGL 2 library path.
2. Then the mod loader, which is the easy half once the loader works: `mods/injector/` already patches a
   built client with a page-tier API, and with a real JVM the same layer can load Fabric mods the way
   CheerpJ loads anything else - by being on the classpath before the game asks for it.
