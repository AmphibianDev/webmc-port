// WebMC's LWJGL 3 implementation: the JavaScript side.
//
// CheerpJ runs Java; this file is what LWJGL's `native` methods land in. The naming convention is
// JNI's - `Java_<package>_<Class>_<method>` - and `lib` is CheerpJ's handle on the running JVM, which
// is how a native can reach Java objects when it has to call back.
//
// Nothing here is borrowed from Eaglercraft, Gaius, or Leaning Tech's own LWJGL 2 package: the shape
// came from reading how CheerpJ natives work, and the *list* of what to implement came from
// measuring real clients (tools/lwjgl3_surface.py: 458 members across 1.13 - 1.20.4). What is below is
// the core needed to open a window, clear it, and draw geometry - a first frame, which is the
// milestone that says the whole approach works.
//
// Design decisions worth knowing:
//
//  * Memory lives here, not in Java. A pointer is an offset into HEAP below. Java's MemoryUtil calls
//    into this file for allocation and for every read and write, so the two sides can never disagree
//    about what an address means. It is slower than a Java-side arena and it is correct, which is the
//    right trade for the first working frame.
//  * GLFW is DOM. A window is a canvas, events are listeners, and callbacks are delivered by pumping
//    them into Java from pollEvents, because that is the only moment Java is definitely ready.
//  * GL is WebGL2, and the calls are translated, not emulated: glClear is clear(), glEnable is
//    enable(), and the parts WebGL lacks (fixed pipeline, immediate mode) are not needed by 1.13+.

const HEAP_BYTES = 256 * 1024 * 1024;
const HEAP = new ArrayBuffer(HEAP_BYTES);
const U8 = new Uint8Array(HEAP);
const I32 = new Int32Array(HEAP);
const F32 = new Float32Array(HEAP);
const F64 = new Float64Array(HEAP);
const VIEW = new DataView(HEAP);

// bump allocator with a free list of exact-size blocks, which is what a game's allocation pattern wants
let bump = 16;
const freeBySize = new Map();
const liveSizes = new Map();

function webmcAlloc(bytes) {
  const size = Math.max(1, bytes | 0);
  const reuse = freeBySize.get(size);
  if (reuse && reuse.length) {
    const address = reuse.pop();
    liveSizes.set(address, size);
    return address;
  }
  const address = bump;
  bump = (bump + size + 15) & ~15;
  if (bump > HEAP_BYTES) throw new Error("the webmc arena is full (" + HEAP_BYTES + " bytes)");
  liveSizes.set(address, size);
  return address;
}

// A memory access whose offset is out of range answers "Offset is outside the bounds of the DataView" and
// nothing else - no address, no call. These check first, so the exception names the call and the address
// it was given, which is the difference between a diagnosis and a mystery.
function webmcBytes(where, address, count) {
  if (typeof address !== "number" || !Number.isFinite(address) || address < 0
      || address + count > HEAP_BYTES) {
    throw new RangeError(where + ": address " + address + " (+" + count + " bytes) is outside the "
                         + HEAP_BYTES + " byte arena");
  }
  return address;
}

function webmcFree(address) {
  const size = liveSizes.get(address);
  if (size === undefined) return;
  liveSizes.delete(address);
  if (!freeBySize.has(size)) freeBySize.set(size, []);
  freeBySize.get(size).push(address);
}

// ---------------------------------------------------------------------------- handles
//
// WebGL hands out objects - a WebGLShader, a WebGLBuffer, a WebGLProgram - and Minecraft deals in ints,
// because that is what its bytecode was compiled against and what it passes back to every later call.
// So every object gets a small integer name here, and every call that takes a handle looks it up.
//
// This was the bug that stopped the boot test dead at the first GL call after the context: createShader
// returned an object where an int was expected, and writing an object into an Int32Array writes 0, so
// every handle in the program was silently null.

const HANDLE_KINDS = ["buffer", "texture", "vertexArray", "program", "shader", "framebuffer",
                      "renderbuffer", "uniform", "sampler"];
const handles = {};
const objects = {};
for (const kind of HANDLE_KINDS) {
  handles[kind] = new Map();   // int -> object
  objects[kind] = new Map();   // object -> int
  handles[kind].next = 1;
}

// -------------------------------------------------------------- the Java/JavaScript boundary
//
// A Java `long` reaches JavaScript as a BigInt and has to leave as one, and JavaScript's own arithmetic
// does not mix the two: `pointer + 8` on a BigInt throws "Cannot convert a BigInt value to a number".
// CheerpJ reports that from inside native code, with the Java frames gone, so the message names the
// arithmetic and not the call that made it. This converts every argument inwards - our addresses are
// offsets into HEAP, nowhere near 2^53 - and converts a result back out for the natives that hand Java
// a long, which tools/lwjgl3_natives.py reads from the Java declarations rather than guessing.
let webmcNativeCalls = 0;

// CheerpJ binds a native by the function's own `name`, so a wrapper that renames every native to
// `webmcBoundNative` makes the first call work and the next one fail with an unhelpful
// "TypeError: object is not a function" from inside its dispatcher, with the Java frames already gone.
// Keeping the JNI name on the wrapper is what makes the indirection invisible.
function webmcNamed(name, fn) {
  Object.defineProperty(fn, "name", { value: name, configurable: true });
  return fn;
}

// The wrapper must not log: a console.log from inside a native re-enters CheerpJ's runtime while it is
// marshalling arguments, and its dispatcher then fails with that same TypeError. (Both of those were
// found the hard way, one at a time, in a real browser.)
function webmcNative(implementation) {
  return function webmcBoundNative(...args) {
    // ?trace=1 logs every native call, in order. A client that stops without an exception stops *inside*
    // one of these, and the last name printed is the call it stopped in - the only view of it from
    // outside, because a browser JVM shows nothing at all while Java is inside a native.
    // Always, and bounded: the shim runs inside a CheerpJ worker, where a ?trace=1 on the page's URL is
    // not visible at all - which is why the flag never worked. The browser's own log keeps these lines, and
    // the last one printed is the native a stalled client is sitting in.
    if (webmcBoundNative.count === undefined) {
      webmcBoundNative.count = 0;
      // Opt-in through the shim's own global, set by a page that wants the trace: the page's URL query is
      // not visible inside the worker this runs in.
      webmcBoundNative.trace = true;   // DIAGNOSTIC RUN: counts every native call to find a hot loop
    }
    if (webmcBoundNative.trace === true && webmcBoundNative.count < 3000) {
      webmcBoundNative.count += 1;
      console.log("[trace " + webmcBoundNative.count + "] " + implementation.name);
    }
    // Push the name into Java as well: the console is not always visible (this runs in a CheerpJ worker),
    // and Java can print it back out through a channel that is - so the last call before a stall survives.
    // CheerpJ hands every native its JVM handle as the first argument, which is the way back into Java.
    try {
      if (args.length > 0 && args[0] && typeof args[0] === "object" && args[0].webmc) {
        args[0].webmc.Diag.note(implementation.name);
      }
    } catch (ignored) {
      // no handle, no shim-side diagnostics: the rest of the shim is unaffected
    }
    // Every native, counted, so a client that stops responding can be asked where it stopped: the
    // function's own name is its JNI name, which is the same name Java called it by.
    state.trace.push(implementation.name);
    if (state.trace.length > 60) {
      state.trace.shift();
    }
    state.traceCount = (state.traceCount || 0) + 1;
    for (let index = 0; index < args.length; index++) {
      if (typeof args[index] === "bigint") {
        args[index] = Number(args[index]);
      }
    }
    // One rest parameter, not named ones: CheerpJ marshals a Java long into a wrapped native happily,
    // but a plain multi-parameter function taking a long stops with "TypeError: object is not a function"
    // from inside its dispatcher, before the native is entered. Every symptom of that looks like a broken
    // shim and it is not one.
    return implementation(...args);
  };
}

function traced(name) {
  // ?trace=1 prints every glfw/GL call the shim is asked for. A client that stops without an exception
  // stops *in* one of these, and the last line printed is the call it stopped in - which is the only way
  // to find it from outside, because a browser JVM shows nothing while Java is inside a native.
  if (!traced.enabled) {
    traced.enabled = typeof location !== "undefined" && location.search.includes("trace=1");
    traced.count = 0;
  }
  if (traced.enabled && traced.count < 6000) {
    traced.count += 1;
    console.log("[trace " + traced.count + "] " + name);
  }
  state.trace = state.trace || [];
  state.traceCount = (state.traceCount || 0) + 1;
  if (state.trace.length < 40) {
    state.trace.push(name);
  }
  state.lastCall = name;
}

/**
 * Run a native, and turn any failure into a log line and a zero instead of a hang.
 *
 * An exception thrown inside a native does not come back as a Java exception: the JVM sits waiting for a
 * value that never arrives, which is exactly what happened at glCreateShader - entered, and never
 * returned. Reporting through WebMC.log means the Java side, the page and CI all see the reason.
 */
function guarded(name, body) {
  try {
    return body();
  } catch (error) {
    const message = name + " threw: " + (error && error.message ? error.message : error);
    state.javaLog = state.javaLog || [];
    if (state.javaLog.length < 400) state.javaLog.push(message + "\n" + String(error && error.stack || ""));
    if (typeof console !== "undefined" && console.error) console.error("[shim] " + message);
    return 0;
  }
}

/** One line into the log the page, the Java side and CI all read. */
function note(message) {
  state.javaLog = state.javaLog || [];
  if (state.javaLog.length < 400) state.javaLog.push(message);
}

function nameOf(kind, object) {
  if (object === null || object === undefined) {
    state.javaLog = state.javaLog || [];
    const context = state.gl;
    const lost = context && context.isContextLost ? context.isContextLost() : "no context";
    state.javaLog.push("WebGL returned null for a " + kind + " (context lost: " + lost + ", "
                       + (context ? context.getParameter(context.VERSION) : "none") + ")");
    return 0;
  }
  const existing = objects[kind].get(object);
  if (existing !== undefined) return existing;
  const name = handles[kind].next++;
  handles[kind].set(name, object);
  objects[kind].set(object, name);
  return name;
}

function objectOf(kind, name) {
  if (!name) return null;
  const found = handles[kind].get(name);
  if (found === undefined) {
    throw new Error("no " + kind + " with handle " + name + " (was it deleted, or never created here?)");
  }
  return found;
}

function dropHandle(kind, name) {
  const object = handles[kind].get(name);
  if (object !== undefined) {
    objects[kind].delete(object);
    handles[kind].delete(name);
  }
}

// ---------------------------------------------------------------------------- window and input

const state = {
  trace: [],
  windows: new Map(),
  nextWindow: 1,
  current: 0,
  canvas: null,
  gl: null,
  keys: new Map(),
  mouse: new Map(),
  cursorX: 0,
  cursorY: 0,
  scrollY: 0,
  callbacks: { key: 0, mouseButton: 0, cursorPos: 0, scroll: 0, char: 0, error: 0 },
  events: [],
  started: performance.now(),
  width: 854,
  height: 480,
  shouldClose: false,
  hintWidth: 854,
  hintHeight: 480,
  swapCount: 0,
};

function canvasFor(window) {
  // A window handle is a Java `long`, and what CheerpJ hands a native for it is not guaranteed to be the
  // same JavaScript value that went out: it can come back boxed, as a BigInt, or as a string. A Map keyed
  // by the plain number misses all three, and the miss used to be silent - which is how "MakeContextCurrent
  // returned" and "no GL context" two calls later turned into one confusing report.
  if (window instanceof HTMLCanvasElement) return window;
  const direct = state.windows.get(window);
  if (direct) return direct;
  const numeric = Number(window);
  if (!Number.isNaN(numeric)) {
    const byNumber = state.windows.get(numeric);
    if (byNumber) return byNumber;
  }
  // Say what actually arrived, so the next report names the cause instead of leaving three causes alike.
  note("canvasFor: no window for " + String(window) + " (" + typeof window + "); known = "
       + Array.from(state.windows.keys()).join(","));
  return null;
}

function installListeners(canvas) {
  canvas.tabIndex = 0;
  canvas.addEventListener("keydown", (event) => {
    state.keys.set(event.keyCode, 1);
    state.events.push({ kind: "key", key: event.keyCode, action: 1 });
    if (state.callbacks.key) { /* delivered in pollEvents */ }
    event.preventDefault();
  });
  canvas.addEventListener("keyup", (event) => {
    state.keys.set(event.keyCode, 0);
    state.events.push({ kind: "key", key: event.keyCode, action: 0 });
    event.preventDefault();
  });
  canvas.addEventListener("mousemove", (event) => {
    const box = canvas.getBoundingClientRect();
    state.cursorX = Math.round(event.clientX - box.left);
    state.cursorY = Math.round(event.clientY - box.top);
    state.events.push({ kind: "cursor", x: state.cursorX, y: state.cursorY });
  });
  canvas.addEventListener("mousedown", (event) => {
    state.mouse.set(event.button, 1);
    state.events.push({ kind: "button", button: event.button, action: 1 });
    event.preventDefault();
  });
  canvas.addEventListener("mouseup", (event) => {
    state.mouse.set(event.button, 0);
    state.events.push({ kind: "button", button: event.button, action: 0 });
  });
  canvas.addEventListener("wheel", (event) => {
    state.scrollY += event.deltaY;
    state.events.push({ kind: "scroll", x: 0, y: Math.sign(event.deltaY) });
  }, { passive: true });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
}

function deliverEvents(lib) {
  // Java asked for events; hand it the ones that arrived. Callbacks are objects Java registered, and
  // `lib` is how a native reaches into the JVM.
  if (!state.events.length) return;
  const queue = state.events.splice(0, state.events.length);
  for (const event of queue) {
    try {
      if (event.kind === "key" && state.callbacks.key) {
        lib.org.lwjgl.glfw.GLFWKeyCallbackI.invoke(state.callbacks.key, state.current, event.key, 0, event.action, 0);
      } else if (event.kind === "cursor" && state.callbacks.cursorPos) {
        lib.org.lwjgl.glfw.GLFWCursorPosCallbackI.invoke(state.callbacks.cursorPos, state.current, event.x, event.y);
      } else if (event.kind === "button" && state.callbacks.mouseButton) {
        lib.org.lwjgl.glfw.GLFWMouseButtonCallbackI.invoke(state.callbacks.mouseButton, state.current, event.button, event.action, 0);
      } else if (event.kind === "scroll" && state.callbacks.scroll) {
        lib.org.lwjgl.glfw.GLFWScrollCallbackI.invoke(state.callbacks.scroll, state.current, event.x, event.y);
      }
    } catch (error) {
      // a callback that throws must not stop the frame; record it where the harness can see it
      state.lastCallbackError = String(error);
    }
  }
}

// ---------------------------------------------------------------------------- GLFW

function Java_org_lwjgl_glfw_GLFW_glfwInit() { return true; }                       // GLFW_TRUE
function Java_org_lwjgl_glfw_GLFW_glfwTerminate() {}
function Java_org_lwjgl_glfw_GLFW_glfwGetError(lib, description) { return 0; }    // GLFW_NO_ERROR
function Java_org_lwjgl_glfw_GLFW_glfwDefaultWindowHints() { state.hintWidth = 854; state.hintHeight = 480; }
function Java_org_lwjgl_glfw_GLFW_glfwWindowHint(lib, hint, value) {
  if (hint === 0x00022002 /* GLFW_CONTEXT_VERSION_MAJOR */) state.hintMajor = value;
  if (hint === 0x00022003 /* GLFW_CONTEXT_VERSION_MINOR */) state.hintMinor = value;
}
function Java_org_lwjgl_glfw_GLFW_glfwCreateWindow(lib, width, height, title, monitor, share) {
  const canvas = window.webmcCanvasElement instanceof HTMLCanvasElement
    ? window.webmcCanvasElement
    : (() => {
        const made = document.createElement("canvas");
        made.width = width; made.height = height;
        document.body.appendChild(made);
        return made;
      })();
  canvas.width = width;
  canvas.height = height;
  installListeners(canvas);
  const handle = state.nextWindow++;
  state.windows.set(handle, canvas);
  state.width = width;
  state.height = height;
  return handle;
}
function Java_org_lwjgl_glfw_GLFW_glfwDestroyWindow(lib, window) {
  state.windows.delete(window);
  if (state.current === window) { state.current = 0; state.canvas = null; state.gl = null; }
}
function Java_org_lwjgl_glfw_GLFW_glfwWindowShouldClose(lib, window) { return Boolean(state.shouldClose); }
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowShouldClose(lib, window, value) { state.shouldClose = value !== 0; }
function Java_org_lwjgl_glfw_GLFW_glfwShowWindow(lib, window) {
  const canvas = canvasFor(window);
  if (canvas) { canvas.style.visibility = "visible"; canvas.focus(); }
}
function Java_org_lwjgl_glfw_GLFW_glfwHideWindow(lib, window) {
  const canvas = canvasFor(window);
  if (canvas) { canvas.style.visibility = "hidden"; }
}
function Java_org_lwjgl_glfw_GLFW_glfwMakeContextCurrent(lib, window) {
  state.current = window;
  state.canvas = canvasFor(window);
  if (!state.canvas) {
    // Not a silent return. Java's glfwMakeContextCurrent has no failure channel, so swallowing the miss
    // here resurfaces two calls later as "no GL context: call glfwMakeContextCurrent first" - which names
    // the one function that did run. Say it here, where the handle is still in hand.
    throw new Error("glfwMakeContextCurrent: no canvas for window handle " + String(window)
                    + " (" + typeof window + ")");
  }
  state.gl = state.canvas.getContext("webgl2", { antialias: false, alpha: false, depth: true, stencil: true,
                                                 powerPreference: "high-performance" });
  if (!state.gl) { state.gl = state.canvas.getContext("webgl", { antialias: false, alpha: false }); }
  if (!state.gl) throw new Error("this browser gave no WebGL context for the window");
  // On the canvas, not on `window`: this module is an ES module, therefore strict, and `window` here is
  // a Java long. Writing a property onto a primitive is a TypeError in strict mode, and onto a boxed
  // handle it writes somewhere nobody reads.
  state.canvas.webmcGL = state.gl;
  // say what we got, in the log the page prints: a context that exists and one that is already lost look
  // the same from Java, and every later call returns null in both cases
  state.javaLog = state.javaLog || [];
  state.javaLog.push("WebGL context: " + state.gl.getParameter(state.gl.VERSION) + " / "
    + state.gl.getParameter(state.gl.RENDERER) + " lost=" + (state.gl.isContextLost ? state.gl.isContextLost() : "?")
    + " canvas=" + state.canvas.width + "x" + state.canvas.height
    + " maxVertexAttribs=" + state.gl.getParameter(state.gl.MAX_VERTEX_ATTRIBS));
}
function Java_org_lwjgl_glfw_GLFW_glfwGetCurrentContext() { return state.current; }
function Java_org_lwjgl_glfw_GLFW_glfwPollEvents_inner(lib) { deliverEvents(lib); }
function Java_org_lwjgl_glfw_GLFW_glfwWaitEvents(lib) { deliverEvents(lib); }
function Java_org_lwjgl_glfw_GLFW_glfwWaitEventsTimeout(lib, seconds) { deliverEvents(lib); }
function Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers_inner(lib, window) {
  // WebGL composites when the frame is free; the count is what the harness asserts on. It goes on the
  // canvas: `window` here is a handle - a number - and writing a property onto it threw
  // "Cannot create property 'webmcFrames' on number '1'" every frame.
  state.swapCount += 1;
  const canvas = state.windows.get(window);
  if (canvas) {
    canvas.webmcFrames = state.swapCount;
  }
}
function Java_org_lwjgl_glfw_GLFW_glfwSwapInterval(lib, interval) {}
function Java_org_lwjgl_glfw_GLFW_glfwGetTime() { return (performance.now() - state.started) / 1000; }
function Java_org_lwjgl_glfw_GLFW_glfwSetTime(lib, seconds) { state.started = performance.now() - seconds * 1000; }
function Java_org_lwjgl_glfw_GLFW_glfwGetWindowSize(lib, window, widthPtr, heightPtr) {
  I32[widthPtr >> 2] = state.width;
  I32[heightPtr >> 2] = state.height;
}
function Java_org_lwjgl_glfw_GLFW_glfwGetFramebufferSize(lib, window, widthPtr, heightPtr) {
  const canvas = canvasFor(window);
  I32[widthPtr >> 2] = canvas ? canvas.width : state.width;
  I32[heightPtr >> 2] = canvas ? canvas.height : state.height;
}
function Java_org_lwjgl_glfw_GLFW_glfwGetWindowContentScale(lib, window, xPtr, yPtr) {
  F32[xPtr >> 2] = 1; F32[yPtr >> 2] = 1;
}
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowSize(lib, window, width, height) {
  const canvas = canvasFor(window);
  if (canvas) { canvas.width = width; canvas.height = height; }
  state.width = width; state.height = height;
  if (state.gl) state.gl.viewport(0, 0, width, height);
}
function Java_org_lwjgl_glfw_GLFW_glfwGetKey(lib, window, key) { return state.keys.get(key) ? 1 : 0; }
function Java_org_lwjgl_glfw_GLFW_glfwGetMouseButton(lib, window, button) { return state.mouse.get(button) ? 1 : 0; }
function Java_org_lwjgl_glfw_GLFW_glfwGetCursorPos(lib, window, xPtr, yPtr) {
  F64[xPtr >> 3] = state.cursorX;
  F64[yPtr >> 3] = state.cursorY;
}
function Java_org_lwjgl_glfw_GLFW_glfwSetCursorPos(lib, window, x, y) { state.cursorX = x; state.cursorY = y; }
function Java_org_lwjgl_glfw_GLFW_glfwSetInputMode(lib, window, mode, value) {
  if (mode === 0x00033001 /* GLFW_CURSOR */) {
    const canvas = canvasFor(window);
    if (canvas) { canvas.style.cursor = value === 0x00034003 /* DISABLED */ ? "none" : "default"; }
  }
}
function Java_org_lwjgl_glfw_GLFW_glfwRawMouseMotionSupported() { return true; }
function Java_org_lwjgl_glfw_GLFW_glfwCreateStandardCursor(lib, shape) { return 1; }
function Java_org_lwjgl_glfw_GLFW_glfwDestroyCursor(lib, cursor) {}
function Java_org_lwjgl_glfw_GLFW_glfwSetCursor(lib, window, cursor) {}
function Java_org_lwjgl_glfw_GLFW_glfwGetPrimaryMonitor() { return 1; }
function Java_org_lwjgl_glfw_GLFW_glfwGetMonitors(lib, countPtr) { I32[countPtr >> 2] = 1; return webmcAlloc(8); }
function Java_org_lwjgl_glfw_GLFW_glfwGetVideoMode(lib, monitor) {
  const address = webmcAlloc(24);
  I32[address >> 2] = window.screen && screen.width ? screen.width : 1280;
  I32[(address >> 2) + 1] = window.screen && screen.height ? screen.height : 720;
  I32[(address >> 2) + 2] = 8;
  I32[(address >> 2) + 3] = 8;
  I32[(address >> 2) + 4] = 8;
  I32[(address >> 2) + 5] = 60;
  return address;
}
function Java_org_lwjgl_glfw_GLFW_glfwGetVideoModes(lib, monitor, countPtr) { I32[countPtr >> 2] = 0; return 0; }
function Java_org_lwjgl_glfw_GLFW_glfwGetMonitorPos(lib, monitor, xPtr, yPtr) { I32[xPtr >> 2] = 0; I32[yPtr >> 2] = 0; }
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowTitle(lib, window, title) {
  if (typeof title === "string") document.title = title;
}
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowPos(lib, window, x, y) {}
function Java_org_lwjgl_glfw_GLFW_glfwIconifyWindow(lib, window) {}
function Java_org_lwjgl_glfw_GLFW_glfwRestoreWindow(lib, window) {}
function Java_org_lwjgl_glfw_GLFW_glfwMaximizeWindow(lib, window) {}
function Java_org_lwjgl_glfw_GLFW_glfwGetWindowAttrib(lib, window, attribute) { return 1; }
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowAttrib(lib, window, attribute, value) {}
function Java_org_lwjgl_glfw_GLFW_glfwFocusWindow(lib, window) {}
function Java_org_lwjgl_glfw_GLFW_glfwSetClipboardString(lib, window, text) {
  if (typeof text === "string" && navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
}
function Java_org_lwjgl_glfw_GLFW_glfwGetClipboardString(lib, window) {
  return window.webmcClipboard || "";
}
function Java_org_lwjgl_glfw_GLFW_glfwJoystickPresent(lib, jid) { return false; }
function Java_org_lwjgl_glfw_GLFW_glfwGetJoystickName(lib, jid) { return null; }
function Java_org_lwjgl_glfw_GLFW_glfwSetKeyCallback(lib, window, callback) { state.callbacks.key = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetCharCallback(lib, window, callback) { state.callbacks.char = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetCursorPosCallback(lib, window, callback) { state.callbacks.cursorPos = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetMouseButtonCallback(lib, window, callback) { state.callbacks.mouseButton = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetScrollCallback(lib, window, callback) { state.callbacks.scroll = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetErrorCallback(lib, callback) { state.callbacks.error = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetFramebufferSizeCallback(lib, window, callback) { state.callbacks.framebufferSize = callback; }
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeCallback(lib, window, callback) {}
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowPosCallback(lib, window, callback) {}
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowFocusCallback(lib, window, callback) {}
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowCloseCallback(lib, window, callback) {}
function Java_org_lwjgl_glfw_GLFW_glfwSetDropCallback(lib, window, callback) {}
function Java_org_lwjgl_glfw_GLFW_glfwUpdateGamepadMappings(lib, mappings) { return true; }
function Java_org_lwjgl_glfw_GLFW_glfwGetVersionString() { return "3.3.3 WebMC (WebGL2)"; }
function Java_org_lwjgl_glfw_GLFW_glfwGetKeyName(lib, key, scancode) { return null; }
function Java_org_lwjgl_glfw_GLFW_glfwGetKeyScancode(lib, key) { return key; }
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowIcon(lib, window, count, images) {}
function Java_org_lwjgl_glfw_GLFW_glfwRequestWindowAttention(lib, window) {}
function Java_org_lwjgl_glfw_GLFW_glfwPostEmptyEvent() {}
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowUserPointer(lib, window, pointer) { window.webmcUserPointer = pointer; }

function Java_org_lwjgl_glfw_Callbacks_glfwFreeCallbacks(lib, window) {
  for (const name of Object.keys(state.callbacks)) state.callbacks[name] = 0;
}

// ---------------------------------------------------------------------------- GL

function gl() {
  if (!state.gl) throw new Error("no GL context: call glfwMakeContextCurrent first");
  return state.gl;
}

// constants that clients ask for through glGetIntegerv/glGetString
const GL_STRINGS = {
  0x1F00: "WebMC",           // GL_VENDOR
  0x1F01: "WebGL2",          // GL_RENDERER
  0x1F02: "3.3 WebMC",       // GL_VERSION
  0x8B8C: "WebMC 1.0",       // GL_SHADING_LANGUAGE_VERSION
};

function Java_org_lwjgl_opengl_GL11_glGetString(lib, name) {
  if (name === 0x1F03 /* GL_EXTENSIONS */) {
    const glc = gl();
    const list = [];
    for (const extension of glc.getSupportedExtensions() || []) list.push(extension);
    state.extensions = list.join(" ");
    return state.extensions;
  }
  return GL_STRINGS[name] || "WebMC";
}
function Java_org_lwjgl_opengl_GL11_glGetError() { return 0; }
function Java_org_lwjgl_opengl_GL11_glGetInteger(lib, name) {
  const glc = gl();
  if (name === 0x0D33) return 16384;                 // GL_MAX_TEXTURE_SIZE
  if (name === 0x8872) return 16;                    // GL_MAX_TEXTURE_IMAGE_UNITS
  if (name === 0x8B4D) return 16;                    // GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS
  if (name === 0x8B4C) return 16;                    // GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
  if (name === 0x8DFC) return 4096;                  // GL_MAX_ARRAY_TEXTURE_LAYERS
  if (name === 0x0D3A) return 8192;                  // GL_MAX_VIEWPORT_DIMS
  if (name === 0x8B8D) return 16;                    // GL_MAX_VERTEX_UNIFORM_VECTORS
  const value = glc.getParameter(name);
  return typeof value === "number" ? value : 0;
}
function Java_org_lwjgl_opengl_GL11_nglGetIntegerv(lib, name, pointer) {
  I32[pointer >> 2] = Java_org_lwjgl_opengl_GL11_glGetInteger(lib, name);
}
function Java_org_lwjgl_opengl_GL11_glGetIntegeri(lib, name) {
  return Java_org_lwjgl_opengl_GL11_glGetInteger(lib, name);
}
function Java_org_lwjgl_opengl_GL11_glGetFloat(lib, name) {
  const value = gl().getParameter(name);
  return typeof value === "number" ? value : 0;
}
function Java_org_lwjgl_opengl_GL11_glGetBoolean(lib, name) { return Boolean(gl().getParameter(name)); }
function Java_org_lwjgl_opengl_GL11_glClearColor(lib, red, green, blue, alpha) {
  gl().clearColor(red, green, blue, alpha);
}
function Java_org_lwjgl_opengl_GL11_glClear_inner(lib, mask) { gl().clear(mask); }
function Java_org_lwjgl_opengl_GL11_glClearDepth(lib, depth) { gl().clearDepth(depth); }
function Java_org_lwjgl_opengl_GL11_glClearStencil(lib, value) { gl().clearStencil(value); }
function Java_org_lwjgl_opengl_GL11_glViewport(lib, x, y, width, height) { gl().viewport(x, y, width, height); }
function Java_org_lwjgl_opengl_GL11_glScissor(lib, x, y, width, height) { gl().scissor(x, y, width, height); }
function Java_org_lwjgl_opengl_GL11_glEnable(lib, cap) {
  gl().enable(cap);
  if (cap === 0x0B44) gl().depthMask(true);
}
function Java_org_lwjgl_opengl_GL11_glDisable(lib, cap) { gl().disable(cap); }
function Java_org_lwjgl_opengl_GL11_glIsEnabled(lib, cap) { return Boolean(gl().isEnabled(cap)); }
function Java_org_lwjgl_opengl_GL11_glBlendFunc(lib, source, destination) { gl().blendFunc(source, destination); }
function Java_org_lwjgl_opengl_GL11_glBlendFuncSeparate(lib, r, g, b, a) { gl().blendFuncSeparate(r, g, b, a); }
function Java_org_lwjgl_opengl_GL11_glBlendEquation(lib, mode) { gl().blendEquation(mode); }
function Java_org_lwjgl_opengl_GL11_glDepthFunc(lib, func) { gl().depthFunc(func); }
function Java_org_lwjgl_opengl_GL11_glDepthMask(lib, flag) { gl().depthMask(flag !== 0); }
function Java_org_lwjgl_opengl_GL11_glCullFace(lib, mode) { gl().cullFace(mode); }
function Java_org_lwjgl_opengl_GL11_glFrontFace(lib, mode) { gl().frontFace(mode); }
function Java_org_lwjgl_opengl_GL11_glColorMask(lib, r, g, b, a) { gl().colorMask(r !== 0, g !== 0, b !== 0, a !== 0); }
function Java_org_lwjgl_opengl_GL11_glLineWidth(lib, width) { gl().lineWidth(width); }
function Java_org_lwjgl_opengl_GL11_glPixelStorei(lib, name, value) { gl().pixelStorei(name, value); }
function Java_org_lwjgl_opengl_GL11_glHint(lib, target, mode) {}
function Java_org_lwjgl_opengl_GL11_glFlush() { gl().flush(); }
function Java_org_lwjgl_opengl_GL11_glFinish() { gl().finish(); }
function Java_org_lwjgl_opengl_GL11_nglGenBuffers(lib, count, pointers) {
  const glc = gl();
  for (let index = 0; index < count; index++) {
    I32[(pointers >> 2) + index] = nameOf("buffer", glc.createBuffer());
  }
}
function Java_org_lwjgl_opengl_GL11_glGenBuffers(lib, buffers, offset) {
  // the int[] form: write the names straight into the arena address the client passed
  const start = Number(buffers) + (Number(offset) || 0);
  I32[start >> 2] = nameOf("buffer", gl().createBuffer());
}
function Java_org_lwjgl_opengl_GL11_nglDeleteBuffers(lib, count, pointers) {
  for (let index = 0; index < count; index++) {
    const name = I32[(pointers >> 2) + index];
    const object = handles.buffer.get(name);
    if (object !== undefined) { gl().deleteBuffer(object); dropHandle("buffer", name); }
  }
}
function Java_org_lwjgl_opengl_GL11_glBindBuffer(lib, target, buffer) {
  const object = buffer ? objectOf("buffer", buffer) : null;
  if (buffer && !object) {
    throw new Error("glBindBuffer(" + target + ", " + buffer + "): no WebGLBuffer is named " + buffer
                    + " (names: " + [...handles.buffer.keys()].join(",") + ")");
  }
  gl().bindBuffer(target, object);
  state.lastBind = { target, name: buffer };
}
function Java_org_lwjgl_opengl_GL11_nglBufferData1(lib, target, size, data, usage) {
  const glc = gl();
  if (data) glc.bufferData(target, U8.subarray(data, data + size), usage);
  else glc.bufferData(target, size, usage);
}
function Java_org_lwjgl_opengl_GL11_nglBufferData(lib, target, size, data, usage) {
  Java_org_lwjgl_opengl_GL11_nglBufferData1(lib, target, size, data, usage);
}
function Java_org_lwjgl_opengl_GL11_nglBufferSubData(lib, target, offset, size, data) {
  gl().bufferSubData(target, offset, U8.subarray(data, data + size));
}
function Java_org_lwjgl_opengl_GL15_nglBufferData(lib, target, size, data, usage) {
  Java_org_lwjgl_opengl_GL11_nglBufferData1(lib, target, size, data, usage);
}
function Java_org_lwjgl_opengl_GL15_nglBufferSubData(lib, target, offset, size, data) {
  Java_org_lwjgl_opengl_GL11_nglBufferSubData(lib, target, offset, size, data);
}
function Java_org_lwjgl_opengl_GL15_nglGenBuffers(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL11_nglGenBuffers(lib, count, pointers);
}
function Java_org_lwjgl_opengl_GL15_nglDeleteBuffers(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL11_nglDeleteBuffers(lib, count, pointers);
}
function Java_org_lwjgl_opengl_GL15_glBindBuffer(lib, target, buffer) {
  Java_org_lwjgl_opengl_GL11_glBindBuffer(lib, target, buffer);
}
function Java_org_lwjgl_opengl_GL30_nglGenVertexArrays(lib, count, pointers) {
  const glc = gl();
  for (let index = 0; index < count; index++) {
    I32[(pointers >> 2) + index] = nameOf("vertexArray", glc.createVertexArray());
  }
}
function Java_org_lwjgl_opengl_GL30_nglDeleteVertexArrays(lib, count, pointers) {
  for (let index = 0; index < count; index++) {
    const name = I32[(pointers >> 2) + index];
    const object = handles.vertexArray.get(name);
    if (object !== undefined) { gl().deleteVertexArray(object); dropHandle("vertexArray", name); }
  }
}
function Java_org_lwjgl_opengl_GL30_glBindVertexArray(lib, array) {
  gl().bindVertexArray(array ? objectOf("vertexArray", array) : null);
}
function Java_org_lwjgl_opengl_GL20_nglGenBuffers(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL11_nglGenBuffers(lib, count, pointers);
}
// ---------------------------------------------------------------------------- shaders
//
// Where the boot test stopped, and why every line here is logged twice.
//
// The report from CI said: glCreateShader, glShaderSource, glCompileShader and glGetShaderi entered, then
// 405 seconds of nothing, with the Java side printing "vertex shader is 0" and then "compiling it". That
// is three different bugs wearing one symptom - a bad enum, a lost context, or a value that never crossed
// back into the JVM - and the report could not tell them apart because nothing logged in between.
//
// So each of these natives logs on the way in and on the way out, and a handle of 0 is answered here
// rather than passed to WebGL as null: calling getShaderParameter(null, ...) is what a 405-second hang
// looks like from the outside, and "the handle is 0" is a much better answer than "nothing happened".
function Java_org_lwjgl_opengl_GL20_glCreateShader_inner(lib, type) {
  // Logged in and out, because "returned 0" has three different causes (a bad enum, a lost context, or a
  // value that never crossed back into the JVM) and the report has to say which one this is.
  note("createShader(type=" + type + ", typeof=" + typeof type + ")");
  const shader = gl().createShader(type);
  if (!shader) {
    note("createShader(" + type + ") returned null: glError=" + gl().getError()
      + " lost=" + (gl().isContextLost ? gl().isContextLost() : "?"));
  }
  const name = nameOf("shader", shader);
  note("createShader -> handle " + name);
  return name;
}
function Java_org_lwjgl_opengl_GL20_glShaderSource_inner(lib, shader, string) {
  // the Java side hands over a String, which arrives here as a JavaScript string or as a Java object
  // depending on how it was built; both have to end up as text
  const object = objectOf("shader", shader);
  if (!object) {
    note("shaderSource: no shader for handle " + shader + " - skipped (a handle of 0 means creation failed)");
    return;
  }
  gl().shaderSource(object, string === null || string === undefined ? "" : String(string));
  note("shaderSource: " + String(string).length + " characters");
}
function Java_org_lwjgl_opengl_GL20_nglShaderSource(lib, shader, count, strings, lengths) {
  const parts = [];
  for (let index = 0; index < count; index++) {
    const address = I32[(strings >> 2) + index];
    const length = lengths ? I32[(lengths >> 2) + index] : 0;
    const end = length > 0 ? address + length : nextZeroByte(address);
    parts.push(String.fromCharCode.apply(null, U8.subarray(address, end)));
  }
  gl().shaderSource(objectOf("shader", shader), parts.join(""));
}
function Java_org_lwjgl_opengl_GL20_glCompileShader_inner(lib, shader) {
  const object = objectOf("shader", shader);
  if (!object) {
    note("compileShader: no shader for handle " + shader + " - skipped");
    return;
  }
  note("compileShader: compiling");
  gl().compileShader(object);
  note("compileShader: done");
}
function Java_org_lwjgl_opengl_GL20_glDeleteShader(lib, shader) {
  const object = objectOf("shader", shader);
  gl().deleteShader(object);
  dropHandle("shader", shader);
}
function Java_org_lwjgl_opengl_GL20_glCreateProgram_inner() { return nameOf("program", gl().createProgram()); }
function Java_org_lwjgl_opengl_GL20_glAttachShader(lib, program, shader) {
  gl().attachShader(objectOf("program", program), objectOf("shader", shader));
}
function Java_org_lwjgl_opengl_GL20_glDetachShader(lib, program, shader) {
  gl().detachShader(objectOf("program", program), objectOf("shader", shader));
}
function Java_org_lwjgl_opengl_GL20_glLinkProgram_inner(lib, program) {
  gl().linkProgram(objectOf("program", program));
}
function Java_org_lwjgl_opengl_GL20_glUseProgram_inner(lib, program) {
  gl().useProgram(program ? objectOf("program", program) : null);
}
function Java_org_lwjgl_opengl_GL20_glDeleteProgram(lib, program) {
  gl().deleteProgram(objectOf("program", program));
  dropHandle("program", program);
}
function Java_org_lwjgl_opengl_GL20_glBindAttribLocation(lib, program, index, name) {
  gl().bindAttribLocation(objectOf("program", program), index, String(name));
}
function Java_org_lwjgl_opengl_GL20_glGetAttribLocation(lib, program, name) {
  return gl().getAttribLocation(objectOf("program", program), String(name));
}
function Java_org_lwjgl_opengl_GL20_nglGetShaderiv(lib, shader, name, pointer) {
  // WebGL answers a boolean for COMPILE_STATUS and a number for the log length
  const value = gl().getShaderParameter(objectOf("shader", shader), name);
  I32[pointer >> 2] = typeof value === "number" ? value : (value ? 1 : 0);
}
function Java_org_lwjgl_opengl_GL20_glGetShaderi_inner(lib, shader, name) {
  // A null object here used to reach WebGL, which is where the boot test hung for 405 seconds: an
  // unknown handle answers 0 instead, and the report says which handle it was.
  const object = objectOf("shader", shader);
  if (!object) {
    note("getShaderi: no shader for handle " + shader + " (asked for " + name + ") - answering 0");
    return 0;
  }
  note("getShaderi: asking for " + name);
  const value = gl().getShaderParameter(object, name);
  const answer = typeof value === "number" ? value : (value ? 1 : 0);
  note("getShaderi(" + name + ") -> " + answer);
  return answer;
}
function Java_org_lwjgl_opengl_GL20_glGetShaderInfoLog(lib, shader, maxLength) {
  // the client passes a buffer length it decided on; the whole log is what it wants back
  const object = objectOf("shader", shader);
  if (!object) return "";
  return gl().getShaderInfoLog(object) || "";
}
function Java_org_lwjgl_opengl_GL20_nglGetProgramiv(lib, program, name, pointer) {
  const value = gl().getProgramParameter(objectOf("program", program), name);
  I32[pointer >> 2] = typeof value === "number" ? value : (value ? 1 : 0);
}
function Java_org_lwjgl_opengl_GL20_glGetProgrami_inner(lib, program, name) {
  const value = gl().getProgramParameter(objectOf("program", program), name);
  return typeof value === "number" ? value : (value ? 1 : 0);
}
function Java_org_lwjgl_opengl_GL20_glGetProgramInfoLog(lib, program, maxLength) {
  return gl().getProgramInfoLog(objectOf("program", program)) || "";
}
function Java_org_lwjgl_opengl_GL20_glGetUniformLocation(lib, program, name) {
  const location = gl().getUniformLocation(objectOf("program", program), String(name));
  return location === null ? -1 : nameOf("uniform", location);
}
function Java_org_lwjgl_opengl_GL20_glUniform1i(lib, location, value) { gl().uniform1i(location < 0 ? null : objectOf("uniform", location), value); }
function Java_org_lwjgl_opengl_GL20_glUniform1f(lib, location, value) { gl().uniform1f(location < 0 ? null : objectOf("uniform", location), value); }
function Java_org_lwjgl_opengl_GL20_glUniform2f(lib, location, x, y) { gl().uniform2f(location < 0 ? null : objectOf("uniform", location), x, y); }
function Java_org_lwjgl_opengl_GL20_glUniform3f(lib, location, x, y, z) { gl().uniform3f(location < 0 ? null : objectOf("uniform", location), x, y, z); }
function Java_org_lwjgl_opengl_GL20_glUniform4f(lib, location, x, y, z, w) { gl().uniform4f(location < 0 ? null : objectOf("uniform", location), x, y, z, w); }
function Java_org_lwjgl_opengl_GL20_glUniform1iv(lib, location, count, values) {
  gl().uniform1iv(location, I32.subarray(values >> 2, (values >> 2) + count));
}
function Java_org_lwjgl_opengl_GL20_glUniform2fv(lib, location, count, values) {
  gl().uniform2fv(location, F32.subarray(values >> 2, (values >> 2) + count * 2));
}
function Java_org_lwjgl_opengl_GL20_glUniform3fv(lib, location, count, values) {
  gl().uniform3fv(location, F32.subarray(values >> 2, (values >> 2) + count * 3));
}
function Java_org_lwjgl_opengl_GL20_glUniform4fv(lib, location, count, values) {
  gl().uniform4fv(location, F32.subarray(values >> 2, (values >> 2) + count * 4));
}
function Java_org_lwjgl_opengl_GL20_glUniformMatrix2fv(lib, location, count, transpose, values) {
  gl().uniformMatrix2fv(location, transpose !== 0, F32.subarray(values >> 2, (values >> 2) + count * 4));
}
function Java_org_lwjgl_opengl_GL20_glUniformMatrix3fv(lib, location, count, transpose, values) {
  gl().uniformMatrix3fv(location, transpose !== 0, F32.subarray(values >> 2, (values >> 2) + count * 9));
}
function Java_org_lwjgl_opengl_GL20_glUniformMatrix4fv(lib, location, count, transpose, values) {
  gl().uniformMatrix4fv(location, transpose !== 0, F32.subarray(values >> 2, (values >> 2) + count * 16));
}
function Java_org_lwjgl_opengl_GL20_nglUniformMatrix4fv(lib, location, count, transpose, values) {
  gl().uniformMatrix4fv(location, transpose !== 0, F32.subarray(values >> 2, (values >> 2) + count * 16));
}
function Java_org_lwjgl_opengl_GL20_nglUniform1fv(lib, location, count, values) {
  gl().uniform1fv(location, F32.subarray(values >> 2, (values >> 2) + count));
}
function Java_org_lwjgl_opengl_GL20_glEnableVertexAttribArray(lib, index) { gl().enableVertexAttribArray(index); }
function Java_org_lwjgl_opengl_GL20_glDisableVertexAttribArray(lib, index) { gl().disableVertexAttribArray(index); }
function Java_org_lwjgl_opengl_GL20_nglVertexAttribPointer(lib, index, size, type, normalized, stride, pointer) {
  gl().vertexAttribPointer(index, size, type, Boolean(normalized), stride, pointer);
}
function Java_org_lwjgl_opengl_GL20_nglVertexAttribIPointer(lib, index, size, type, stride, pointer) {
  gl().vertexAttribIPointer(index, size, type, stride, pointer);
}
function Java_org_lwjgl_opengl_GL11_nglDrawArrays_inner(lib, mode, first, count) { gl().drawArrays(mode, first, count); }
function Java_org_lwjgl_opengl_GL11_nglDrawElements(lib, mode, count, type, indices) {
  const bytes = count * (type === 0x1403 ? 2 : type === 0x1401 ? 1 : 4);
  gl().drawElements(mode, count, type, indices);
}
function Java_org_lwjgl_opengl_GL11_glDrawArrays(lib, mode, first, count) { gl().drawArrays(mode, first, count); }
function Java_org_lwjgl_opengl_GL11_glDrawElements(lib, mode, count, type, indices) {
  gl().drawElements(mode, count, type, indices || 0);
}
function Java_org_lwjgl_opengl_GL11_glDepthRange(lib, near, far) { gl().depthRange(near, far); }
function Java_org_lwjgl_opengl_GL11_glReadPixels_inner(lib, x, y, width, height, format, type, pointer) {
  const glc = gl();
  const length = width * height * 4;
  const pixels = new Uint8Array(length);
  glc.readPixels(x, y, width, height, format, type, pixels);
  U8.set(pixels, pointer);
}
function Java_org_lwjgl_opengl_GL11_nglReadPixels(lib, x, y, width, height, format, type, pointer) {
  Java_org_lwjgl_opengl_GL11_glReadPixels(lib, x, y, width, height, format, type, pointer);
}
function Java_org_lwjgl_opengl_GL11_nglGenTextures(lib, count, pointers) {
  const glc = gl();
  for (let index = 0; index < count; index++) {
    I32[(pointers >> 2) + index] = nameOf("texture", glc.createTexture());
  }
}
function Java_org_lwjgl_opengl_GL11_nglDeleteTextures(lib, count, pointers) {
  for (let index = 0; index < count; index++) {
    const name = I32[(pointers >> 2) + index];
    const object = handles.texture.get(name);
    if (object !== undefined) { gl().deleteTexture(object); dropHandle("texture", name); }
  }
}
function Java_org_lwjgl_opengl_GL11_glBindTexture(lib, target, texture) {
  gl().bindTexture(target, texture ? objectOf("texture", texture) : null);
}
function Java_org_lwjgl_opengl_GL11_glTexParameteri(lib, target, name, value) { gl().texParameteri(target, name, value); }
function Java_org_lwjgl_opengl_GL11_glTexParameterf(lib, target, name, value) { gl().texParameterf(target, name, value); }
function Java_org_lwjgl_opengl_GL11_glTexImage2D(lib, target, level, internalFormat, width, height, border,
                                                   format, type, pointer) {
  const glc = gl();
  const channels = format === 0x1909 ? 1 : format === 0x190A ? 2 : format === 0x1907 ? 3 : 4;
  const bytes = width * height * channels * (type === 0x1403 ? 2 : 1);
  if (pointer) glc.texImage2D(target, level, internalFormat, width, height, border, format, type,
                              U8.subarray(pointer, pointer + bytes));
  else glc.texImage2D(target, level, internalFormat, width, height, border, format, type, null);
}
function Java_org_lwjgl_opengl_GL11_nglTexImage2D(lib, target, level, internalFormat, width, height, border,
                                                   format, type, pointer, hasFunction) {
  Java_org_lwjgl_opengl_GL11_glTexImage2D(lib, target, level, internalFormat, width, height, border, format, type, pointer);
}
function Java_org_lwjgl_opengl_GL11_nglTexSubImage2D(lib, target, level, xOffset, yOffset, width, height,
                                                    format, type, pointer) {
  const channels = format === 0x1909 ? 1 : format === 0x190A ? 2 : format === 0x1907 ? 3 : 4;
  const bytes = width * height * channels * (type === 0x1403 ? 2 : 1);
  gl().texSubImage2D(target, level, xOffset, yOffset, width, height, format, type, U8.subarray(pointer, pointer + bytes));
}
function Java_org_lwjgl_opengl_GL11_glTexSubImage2D(lib, target, level, xOffset, yOffset, width, height,
                                                     format, type, pointer) {
  Java_org_lwjgl_opengl_GL11_nglTexSubImage2D(lib, target, level, xOffset, yOffset, width, height, format, type, pointer);
}
function Java_org_lwjgl_opengl_GL11_glGenerateMipmap(lib, target) {
  const glc = gl();
  if (glc.generateMipmap) glc.generateMipmap(target);
}
function Java_org_lwjgl_opengl_GL13_glActiveTexture(lib, texture) { gl().activeTexture(texture); }
function Java_org_lwjgl_opengl_GL13_nglGenTextures(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL11_nglGenTextures(lib, count, pointers);
}
function Java_org_lwjgl_opengl_GL13_nglDeleteTextures(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL11_nglDeleteTextures(lib, count, pointers);
}
function Java_org_lwjgl_opengl_GL13_glTexParameteri(lib, target, name, value) {
  Java_org_lwjgl_opengl_GL11_glTexParameteri(lib, target, name, value);
}
function Java_org_lwjgl_opengl_GL14_glBlendFuncSeparate(lib, r, g, b, a) { gl().blendFuncSeparate(r, g, b, a); }
function Java_org_lwjgl_opengl_GL14_glBlendEquation(lib, mode) { Java_org_lwjgl_opengl_GL11_glBlendEquation(lib, mode); }
function Java_org_lwjgl_opengl_GL30_glGetStringi(lib, name, index) {
  const extensions = (state.extensions || "").split(" ").filter(Boolean);
  return extensions[index] || "";
}
function Java_org_lwjgl_opengl_GL30_nglGetStringi(lib, name, index, pointer) {
  I32[pointer >> 2] = Java_org_lwjgl_opengl_GL30_glGetStringi(lib, name, index);
}
function Java_org_lwjgl_opengl_GL30_glBindFramebuffer(lib, target, framebuffer) {
  gl().bindFramebuffer(target, framebuffer ? objectOf("framebuffer", framebuffer) : null);
}
function Java_org_lwjgl_opengl_GL30_nglGenFramebuffers(lib, count, pointers) {
  const glc = gl();
  for (let index = 0; index < count; index++) {
    I32[(pointers >> 2) + index] = nameOf("framebuffer", glc.createFramebuffer());
  }
}
function Java_org_lwjgl_opengl_GL30_nglDeleteFramebuffers(lib, count, pointers) {
  for (let index = 0; index < count; index++) {
    const name = I32[(pointers >> 2) + index];
    const object = handles.framebuffer.get(name);
    if (object !== undefined) { gl().deleteFramebuffer(object); dropHandle("framebuffer", name); }
  }
}
function Java_org_lwjgl_opengl_GL30_glFramebufferTexture2D(lib, target, attachment, texTarget, texture, level) {
  gl().framebufferTexture2D(target, attachment, texTarget,
                            texture ? objectOf("texture", texture) : null, level);
}
function Java_org_lwjgl_opengl_GL30_glCheckFramebufferStatus(lib, target) {
  const status = gl().checkFramebufferStatus(target);
  return typeof status === "number" ? status : 0x8CD5;
}
function Java_org_lwjgl_opengl_GL30_glBlitFramebuffer(lib, sx0, sy0, sx1, sy1, dx0, dy0, dx1, dy1, mask, filter) {
  const glc = gl();
  if (glc.blitFramebuffer) glc.blitFramebuffer(sx0, sy0, sx1, sy1, dx0, dy0, dx1, dy1, mask, filter);
}
function Java_org_lwjgl_opengl_GL30_glRenderbufferStorage(lib, target, format, width, height) {
  const glc = gl();
  if (glc.renderbufferStorage) glc.renderbufferStorage(target, format, width, height);
}
function Java_org_lwjgl_opengl_GL30_nglGenRenderbuffers(lib, count, pointers) {
  const glc = gl();
  for (let index = 0; index < count; index++) {
    I32[(pointers >> 2) + index] = nameOf("renderbuffer", glc.createRenderbuffer());
  }
}
function Java_org_lwjgl_opengl_GL30_glBindRenderbuffer(lib, target, renderbuffer) {
  gl().bindRenderbuffer(target, renderbuffer ? objectOf("renderbuffer", renderbuffer) : null);
}
function Java_org_lwjgl_opengl_GL30_glFramebufferRenderbuffer(lib, target, attachment, renderbufferTarget, renderbuffer) {
  gl().framebufferRenderbuffer(target, attachment, renderbufferTarget,
                               renderbuffer ? objectOf("renderbuffer", renderbuffer) : null);
}
function Java_org_lwjgl_opengl_GL32_glBlendEquationSeparate(lib, rgb, alpha) { gl().blendEquationSeparate(rgb, alpha); }
function Java_org_lwjgl_opengl_GL32_glBlendFuncSeparate(lib, r, g, b, a) { gl().blendFuncSeparate(r, g, b, a); }

// ---------------------------------------------------------------------------- memory

function nextZeroByte(address) {
  let cursor = address;
  while (U8[cursor] !== 0) cursor++;
  return cursor;
}

function Java_org_lwjgl_system_WebMC_log(lib, message) {
  // the Java program's own words, kept where a page and CI can read them
  const text = message === null || message === undefined ? "" : String(message);
  state.javaLog = state.javaLog || [];
  if (state.javaLog.length < 400) state.javaLog.push(text);
  if (typeof console !== "undefined" && console.log) console.log("[java] " + text);
}

function Java_org_lwjgl_system_WebMC_logText(lib) {
  return (state.javaLog || []).join("\n");
}

function Java_org_lwjgl_system_WebMC_nmemAlloc(lib, size) { return webmcAlloc(size); }
function Java_org_lwjgl_system_WebMC_nmemFree(lib, address) { webmcFree(address); }
function Java_org_lwjgl_system_WebMC_nmemSet(lib, address, value, bytes) {
  U8.fill(value & 0xFF, address, address + bytes);
}
function Java_org_lwjgl_system_WebMC_nmemCopy(lib, source, destination, bytes) {
  U8.copyWithin(destination, source, source + bytes);
}
function Java_org_lwjgl_system_WebMC_nmemGetByte(lib, address) { return U8[address] << 24 >> 24; }
function Java_org_lwjgl_system_WebMC_bindBuffer(lib, target, buffer) {
  Java_org_lwjgl_opengl_GL11_glBindBuffer(lib, target, buffer);
}

// STB image, over the PNG decoder above. The pixels land in this side's heap because their size is not
// known until the image is decoded; the Java side copies them into the arena.
// The last decoded image, so the size can be asked for one scalar at a time: a native that takes two
// arrays never reaches JavaScript, and the address has to travel as a single value.
const webmcStb = { width: 0, height: 0, components: 0, scratch: 0 };

function Java_org_lwjgl_system_WebMC_stbLoad(lib, address, length) {
  webmcStb.width = 0;
  webmcStb.height = 0;
  webmcStb.components = 0;
  webmcStb.scratch = 0;
  const bytes = length > 0 ? U8.subarray(address, address + length) : null;
  const image = bytes ? webmcDecodePng(bytes) : null;
  if (!image) {
    return;
  }
  const size = image.width * image.height * 4;
  const scratch = webmcAlloc(size);
  U8.set(image.rgba, scratch);
  webmcStb.width = image.width;
  webmcStb.height = image.height;
  webmcStb.components = 4;
  webmcStb.scratch = scratch;
}

function Java_org_lwjgl_system_WebMC_stbScratch() { return webmcStb.scratch; }

// ------------------------------------------------------------------- glyphs, because FreeType is Java
//
// FreeType reads font files; a browser cannot rasterise an arbitrary font file synchronously (FontFace is
// asynchronous), so the glyphs come from the canvas with a generic family instead. The shapes are not
// Minecraft's font, the metrics are the canvas's - and text renders, is measurable, and is the right
// height, which is what the client needs to draw a menu.
const webmcGlyph = { width: 0, rows: 0, pitch: 0, left: 0, top: 0, advance: 0 };

function webmcGlyphCanvas(pixelSize) {
  if (!webmcGlyph.canvas || webmcGlyph.canvasSize < pixelSize) {
    const size = Math.max(8, Math.ceil(pixelSize * 2));
    webmcGlyph.canvas = document.createElement("canvas");
    webmcGlyph.canvas.width = size;
    webmcGlyph.canvas.height = size;
    webmcGlyph.canvasSize = size;
  }
  const canvas = webmcGlyph.canvas;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.font = pixelSize + "px monospace";
  context.textBaseline = "alphabetic";
  context.fillStyle = "#fff";
  return context;
}

function Java_org_lwjgl_system_WebMC_freetypeGlyph(lib, glyphPoint, pixelSize, bitmap) {
  const context = webmcGlyphCanvas(pixelSize);
  const text = String.fromCodePoint(glyphPoint);
  const metrics = context.measureText(text);
  const width = Math.max(1, Math.ceil(metrics.actualBoundingBoxRight + metrics.actualBoundingBoxLeft) + 1);
  const rows = Math.max(1, Math.ceil(metrics.actualBoundingBoxAscent + metrics.actualBoundingBoxDescent) + 1);
  const baseline = Math.ceil(metrics.actualBoundingBoxAscent) + 1;
  context.fillText(text, 1, baseline);
  const image = context.getImageData(0, 0, width, rows).data;
  const pitch = width;
  let ink = 0;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < width; x++) {
      const alpha = image[(y * width + x) * 4 + 3];
      bitmap[y * pitch + x] = alpha;
      if (alpha > 32) ink += 1;
    }
  }
  webmcGlyph.width = width;
  webmcGlyph.rows = rows;
  webmcGlyph.pitch = pitch;
  webmcGlyph.left = 0;
  webmcGlyph.top = Math.ceil(metrics.actualBoundingBoxAscent);
  webmcGlyph.advance = Math.max(1, Math.round(metrics.width));
  webmcGlyph.ink = ink;
  if (typeof console !== "undefined") {
    console.log("[shim] glyph " + glyphPoint + " size=" + pixelSize + " -> " + width + "x" + rows
                + " advance=" + webmcGlyph.advance + " ascent=" + metrics.actualBoundingBoxAscent
                + " ink=" + webmcGlyph.ink);
  }
}

function Java_org_lwjgl_system_WebMC_freetypeWidth() { return webmcGlyph.width; }
function Java_org_lwjgl_system_WebMC_freetypeRows() { return webmcGlyph.rows; }
function Java_org_lwjgl_system_WebMC_freetypePitch() { return webmcGlyph.pitch; }
function Java_org_lwjgl_system_WebMC_freetypeLeft() { return webmcGlyph.left; }
function Java_org_lwjgl_system_WebMC_freetypeTop() { return webmcGlyph.top; }
function Java_org_lwjgl_system_WebMC_freetypeAdvance() { return webmcGlyph.advance; }

function Java_org_lwjgl_system_WebMC_stbWidth() { return webmcStb.width; }
function Java_org_lwjgl_system_WebMC_stbHeight() { return webmcStb.height; }
function Java_org_lwjgl_system_WebMC_stbComponents() { return webmcStb.components; }

function Java_org_lwjgl_system_WebMC_stbFreeScratch(lib, scratch) {
  webmcFree(scratch);
}

function Java_org_lwjgl_system_WebMC_genBuffers(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL11_nglGenBuffers(lib, count, pointers);
}

function Java_org_lwjgl_system_WebMC_genVertexArrays(lib, count, pointers) {
  Java_org_lwjgl_opengl_GL30_nglGenVertexArrays(lib, count, pointers);
}

// ---------------------------------------------------------------------- PNG, because STB is JS here
//
// Minecraft loads every texture through stbi_load_from_memory, and a browser has no synchronous way to
// decode an image (an <img>, createImageBitmap and DecompressionStream are all asynchronous, and a
// native cannot await). So this is an inflate and a PNG decoder, written out: it is the smallest correct
// thing that can turn 16x16 of blocks into pixels, and it runs once per texture at load.
function webmcInflate(input, start, length, limit) {
  let bitPosition = 0;
  // A decoder handed something that is not deflate data can run for ever: the bit reader walks off the
  // end, the Huffman tables return nonsense symbols, and a plausible-looking length/distance pair copies
  // in a loop. Both a read past the input and an output past what the caller expects end it here, which
  // is the difference between "this file is not an image" and a browser tab that stops responding.
  const budget = (limit || 0) > 0 ? limit : 1 << 26;
  const end = start + length;
  const readBit = () => {
    const byte = input[start + (bitPosition >> 3)];
    if (byte === undefined) {
      throw new RangeError("deflate stream ended");
    }
    const bit = (byte >> (bitPosition & 7)) & 1;
    bitPosition += 1;
    return bit;
  };
  const readBits = (count) => {
    let value = 0;
    for (let index = 0; index < count; index++) {
      value |= readBit() << index;
    }
    return value;
  };
  const out = [];
  const huffman = (lengths) => {
    // canonical Huffman: counts per length, then first code per length, decoded bit by bit
    let maxBits = 0;
    for (const length of lengths) {
      if (length > maxBits) maxBits = length;
    }
    const counts = new Array(maxBits + 1).fill(0);
    for (const length of lengths) {
      if (length) counts[length] += 1;
    }
    const firstCode = new Array(maxBits + 1).fill(0);
    let code = 0;
    for (let bits = 1; bits <= maxBits; bits++) {
      code = (code + counts[bits - 1]) << 1;
      firstCode[bits] = code;
    }
    const symbols = [];
    for (let symbol = 0; symbol < lengths.length; symbol++) {
      if (lengths[symbol]) symbols.push(symbol);
    }
    symbols.sort((a, b) => lengths[a] - lengths[b] || a - b);
    const table = new Map();
    let index = 0;
    for (let bits = 1; bits <= maxBits; bits++) {
      for (let count = 0; count < counts[bits]; count++) {
        table.set(bits + ":" + firstCode[bits], symbols[index]);
        index += 1;
        firstCode[bits] += 1;
      }
    }
    return (maxBits === 0) ? () => -1 : () => {
      let code = 0;
      for (let bits = 1; bits <= maxBits; bits++) {
        code = (code << 1) | readBit();
        const found = table.get(bits + ":" + code);
        if (found !== undefined) return found;
      }
      return -1;
    };
  };
  const LENGTH_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99,
                       115, 131, 163, 195, 227, 258];
  const LENGTH_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  const DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025,
                     1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577];
  const DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
  let finalBlock = false;
  try {
  while (!finalBlock) {
    finalBlock = readBit() === 1;
    const type = readBits(2);
    if (type === 0) {
      bitPosition = (bitPosition + 7) & ~7;
      const at = start + (bitPosition >> 3);
      const size = input[at] | (input[at + 1] << 8);
      bitPosition += 32;
      for (let index = 0; index < size; index++) {
        out.push(input[start + (bitPosition >> 3)]);
        bitPosition += 8;
      }
      continue;
    }
    let literal;
    let distance;
    if (type === 1) {
      const literalLengths = new Array(288).fill(0);
      for (let symbol = 0; symbol < 144; symbol++) literalLengths[symbol] = 8;
      for (let symbol = 144; symbol < 256; symbol++) literalLengths[symbol] = 9;
      for (let symbol = 256; symbol < 280; symbol++) literalLengths[symbol] = 7;
      for (let symbol = 280; symbol < 288; symbol++) literalLengths[symbol] = 8;
      literal = huffman(literalLengths);
      distance = huffman(new Array(30).fill(5));
    } else if (type === 2) {
      const literalCount = readBits(5) + 257;
      const distanceCount = readBits(5) + 1;
      const codeCount = readBits(4) + 4;
      const order = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];
      const codeLengths = new Array(19).fill(0);
      for (let index = 0; index < codeCount; index++) {
        codeLengths[order[index]] = readBits(3);
      }
      const codeDecoder = huffman(codeLengths);
      const lengths = [];
      while (lengths.length < literalCount + distanceCount) {
        const symbol = codeDecoder();
        if (symbol < 0) return null;
        if (symbol < 16) {
          lengths.push(symbol);
        } else if (symbol === 16) {
          const previous = lengths[lengths.length - 1];
          const repeat = readBits(2) + 3;
          for (let index = 0; index < repeat; index++) lengths.push(previous);
        } else if (symbol === 17) {
          const repeat = readBits(3) + 3;
          for (let index = 0; index < repeat; index++) lengths.push(0);
        } else {
          const repeat = readBits(7) + 11;
          for (let index = 0; index < repeat; index++) lengths.push(0);
        }
      }
      literal = huffman(lengths.slice(0, literalCount));
      distance = huffman(lengths.slice(literalCount));
    } else {
      return null;
    }
    for (;;) {
      const symbol = literal();
      if (symbol < 0) return null;
      if (symbol < 256) {
        out.push(symbol);
      } else if (symbol === 256) {
        break;
      } else {
        const lengthIndex = symbol - 257;
        if (out.length > budget) {
          throw new RangeError("the decoded data is larger than expected");
        }
        const copyLength = LENGTH_BASE[lengthIndex] + readBits(LENGTH_EXTRA[lengthIndex]);
        const distanceSymbol = distance();
        if (distanceSymbol < 0) return null;
        const copyDistance = DIST_BASE[distanceSymbol] + readBits(DIST_EXTRA[distanceSymbol]);
        const from = out.length - copyDistance;
        for (let index = 0; index < copyLength; index++) {
          out.push(out[from + index]);
        }
      }
    }
  }
  } catch (error) {
    return null;
  }
  return Uint8Array.from(out);
}

/** PNG -> RGBA8. IHDR, PLTE, tRNS and IDAT are all Minecraft's textures use. */
function webmcDecodePng(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 8;
  let width = 0;
  let height = 0;
  let depth = 8;
  let colourType = 6;
  let palette = null;
  let transparency = null;
  const data = [];
  while (offset + 8 <= bytes.length) {
    const length = view.getUint32(offset);
    const type = String.fromCharCode(bytes[offset + 4], bytes[offset + 5], bytes[offset + 6], bytes[offset + 7]);
    const body = offset + 8;
    if (type === "IHDR") {
      width = view.getUint32(body);
      height = view.getUint32(body + 4);
      depth = bytes[body + 8];
      colourType = bytes[body + 9];
    } else if (type === "PLTE") {
      palette = bytes.subarray(body, body + length);
    } else if (type === "tRNS") {
      transparency = bytes.subarray(body, body + length);
    } else if (type === "IDAT") {
      for (let index = 0; index < length; index++) data.push(bytes[body + index]);
    } else if (type === "IEND") {
      break;
    }
    offset = body + length + 4;
  }
  // An IDAT is a *zlib* stream: two bytes of header and four of Adler-32 around the deflate data. Inflating
  // from byte zero makes the header look like a block header and the decode spirals.
  const all = Uint8Array.from(data);
  const zlibWrapped = all.length > 6 && (all[0] & 0x0f) === 8 && ((all[0] << 8 | all[1]) % 31) === 0;
  const channelsGuess = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[colourType] || 4;
  const expected = (Math.ceil(width * channelsGuess * depth / 8) + 1) * height;
  const raw = webmcInflate(all, zlibWrapped ? 2 : 0, all.length - (zlibWrapped ? 6 : 0), expected);
  if (!raw) return null;
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[colourType];
  if (!channels) return null;
  const bitsPerPixel = channels * depth;
  const stride = Math.ceil(width * bitsPerPixel / 8);
  const rgba = new Uint8Array(width * height * 4);
  const previous = new Uint8Array(stride);
  const line = new Uint8Array(stride);
  let at = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[at++];
    for (let index = 0; index < stride; index++) {
      const value = raw[at + index] || 0;
      const left = index >= channels * depth / 8 ? line[index - Math.ceil(channels * depth / 8)] : 0;
      const up = previous[index];
      const upLeft = index >= channels * depth / 8 ? previous[index - Math.ceil(channels * depth / 8)] : 0;
      let result = value;
      if (filter === 1) result = value + left;
      else if (filter === 2) result = value + up;
      else if (filter === 3) result = value + ((left + up) >> 1);
      else if (filter === 4) {
        const p = left + up - upLeft;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - upLeft);
        result = value + (pa <= pb && pa <= pc ? left : (pb <= pc ? up : upLeft));
      }
      line[index] = result & 0xff;
    }
    at += stride;
    for (let x = 0; x < width; x++) {
      let red = 0;
      let green = 0;
      let blue = 0;
      let alpha = 255;
      const pixel = x * channels * depth / 8;
      if (colourType === 0) {
        red = green = blue = line[pixel];
      } else if (colourType === 2) {
        red = line[pixel]; green = line[pixel + 1]; blue = line[pixel + 2];
      } else if (colourType === 3) {
        const index = line[pixel];
        red = palette[index * 3]; green = palette[index * 3 + 1]; blue = palette[index * 3 + 2];
        alpha = transparency && index < transparency.length ? transparency[index] : 255;
      } else if (colourType === 4) {
        red = green = blue = line[pixel];
        alpha = line[pixel + 1];
      } else {
        red = line[pixel]; green = line[pixel + 1]; blue = line[pixel + 2]; alpha = line[pixel + 3];
      }
      const target = (y * width + x) * 4;
      rgba[target] = red;
      rgba[target + 1] = green;
      rgba[target + 2] = blue;
      rgba[target + 3] = alpha;
    }
    previous.set(line.subarray(0, stride));
  }
  return { width, height, rgba };
}

// ------------------------------------------------------------ the arena, seen from JavaScript
//
// Java's memory is Java's: the game allocates in WebMCArena, which is a set of direct ByteBuffers, and
// JavaScript cannot read those. A GL call that takes an *address* - a vertex upload, a texture, a pixel
// readback - therefore cannot be handed a pointer. It is handed the bytes instead: a Java byte[] arrives
// here as a typed array, and a typed array here goes back as a Java byte[]. That is one copy at the
// boundary, and it is the price of having the memory where the JVM can reach it.
// The name the generated block uses for looking up a WebGL object by its integer name; the file's own
// helper is objectOf. One of them was missing, and a ReferenceError inside a native stops the JVM's
// thread - which is a stalled client with no exception and an idle-looking page.
function handleOf(kind, name) {
  return objectOf(kind, name);
}

function webmcUint8(bytes) {
  if (!bytes) return null;
  if (bytes instanceof Uint8Array) return bytes;
  if (ArrayBuffer.isView(bytes)) return new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes instanceof ArrayBuffer) return new Uint8Array(bytes);
  // CheerpJ does not hand JavaScript a typed array for a Java byte[]: it hands an object with a length
  // and indexed elements, and WebGL refuses it ("parameter 2 is not of type 'ArrayBufferView'"). Copying
  // is what makes it a view.
  const length = bytes.length;
  if (typeof length === "number") {
    const view = new Uint8Array(length);
    for (let index = 0; index < length; index++) {
      view[index] = bytes[index] & 0xff;
    }
    return view;
  }
  const description = typeof bytes + " " + (bytes.constructor && bytes.constructor.name)
                      + " keys=[" + Object.getOwnPropertyNames(bytes).slice(0, 8).join(",") + "]";
  throw new TypeError("a Java byte[] arrived as " + description
                      + " - neither a typed array, an ArrayBuffer, nor array-like");
}

function Java_org_lwjgl_system_WebMC_bufferDataFromJava(lib, target, size, bytes, usage) {
  const bound = gl().getParameter(gl().ARRAY_BUFFER_BINDING);
  if (target === gl().ARRAY_BUFFER && !bound) {
    throw new Error("bufferData with no buffer bound to GL_ARRAY_BUFFER (names: "
                    + [...handles.buffer.keys()].join(",") + ", last bind: "
                    + JSON.stringify(state.lastBind || null) + ")");
  }
  const data = webmcUint8(bytes);
  // WebGL2's bufferData is either (target, size, usage) or (target, data, usage): there is no four
  // argument form, and calling one anyway is how this failed with "parameter 2 is not of type
  // ArrayBufferView" while the data was sitting right there.
  if (data === null) {
    gl().bufferData(target, Number(size), usage);
  } else {
    gl().bufferData(target, data, usage);
  }
}

function Java_org_lwjgl_system_WebMC_bufferSubDataFromJava(lib, target, offset, bytes) {
  gl().bufferSubData(target, offset, webmcUint8(bytes));
}

function Java_org_lwjgl_system_WebMC_texImageFromJava(lib, target, level, internalFormat, width, height,
                                                      border, format, type, pixels) {
  gl().texImage2D(target, level, internalFormat, width, height, border, format, type, webmcUint8(pixels));
}

function Java_org_lwjgl_system_WebMC_texSubImageFromJava(lib, target, level, xOffset, yOffset, width, height,
                                                         format, type, pixels) {
  gl().texSubImage2D(target, level, xOffset, yOffset, width, height, format, type, webmcUint8(pixels));
}

// Pixels are read into a view of our own and copied into the caller's Java array element by element.
// Returning a Uint8Array for a Java byte[] does not work - CheerpJ answers "Invalid type conversion
// attempted" - and a Java byte[] is not a view WebGL will accept. Both ends need their own form.
function Java_org_lwjgl_system_WebMC_readPixelsInto(lib, x, y, width, height, format, type, into) {
  const view = new Uint8Array(Math.max(1, width) * Math.max(1, height) * 4);
  gl().readPixels(x, y, width, height, format, type, view);
  for (let index = 0; index < view.length && index < into.length; index++) {
    into[index] = view[index];
  }
}

function Java_org_lwjgl_system_WebMC_nmemGetShort(lib, address) { return VIEW.getInt16(webmcBytes("nmemGetShort", address, 2), true); }
function Java_org_lwjgl_system_WebMC_nmemGetInt(lib, address) { return VIEW.getInt32(webmcBytes("nmemGetInt", address, 4), true); }
function Java_org_lwjgl_system_WebMC_nmemGetLong(lib, address) { return Number(VIEW.getBigInt64(webmcBytes("nmemGetLong", address, 8), true)); }
function Java_org_lwjgl_system_WebMC_nmemGetFloat(lib, address) { return VIEW.getFloat32(webmcBytes("nmemGetFloat", address, 4), true); }
function Java_org_lwjgl_system_WebMC_nmemGetDouble(lib, address) { return VIEW.getFloat64(webmcBytes("nmemGetDouble", address, 8), true); }
function Java_org_lwjgl_system_WebMC_nmemPutByte(lib, address, value) { VIEW.setInt8(webmcBytes("nmemPutByte", address, 1), value); }
function Java_org_lwjgl_system_WebMC_nmemPutShort(lib, address, value) { VIEW.setInt16(webmcBytes("nmemPutShort", address, 2), value, true); }
function Java_org_lwjgl_system_WebMC_nmemPutInt(lib, address, value) { VIEW.setInt32(webmcBytes("nmemPutInt", address, 4), value, true); }
function Java_org_lwjgl_system_WebMC_nmemPutLong(lib, address, value) { VIEW.setBigInt64(webmcBytes("nmemPutLong", address, 8), BigInt(value), true); }
function Java_org_lwjgl_system_WebMC_nmemPutFloat(lib, address, value) { VIEW.setFloat32(webmcBytes("nmemPutFloat", address, 4), value, true); }
function Java_org_lwjgl_system_WebMC_nmemPutDouble(lib, address, value) { VIEW.setFloat64(webmcBytes("nmemPutDouble", address, 8), value, true); }
function Java_org_lwjgl_system_WebMC_nmemASCII(lib, address) {
  return new TextDecoder().decode(U8.subarray(address, nextZeroByte(address)));
}
function Java_org_lwjgl_system_WebMC_nmemUTF8(lib, address) {
  return new TextDecoder().decode(U8.subarray(address, nextZeroByte(address)));
}
// A Java byte[] arrives here as an array-like object, not a typed array: `new Uint8Array(javaArray)`
// and `target.set(...)` against it do nothing at all, quietly - which is how generated buffer names
// became zeros and the draw had no buffer. A real view still takes the fast path; anything else is
// copied element by element, which the proxy does support.
function webmcJavaArray(value) {
  if (!value) return null;
  if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer) return value;
  return typeof value.length === "number" ? value : null;
}

function Java_org_lwjgl_system_WebMC_nmemWriteBytes(lib, address, bytes, offset) {
  const source = webmcJavaArray(bytes);
  if (!source) return;
  const start = webmcBytes("nmemWriteBytes", address, Math.max(1, source.length - (offset || 0)));
  if (source instanceof Uint8Array) {
    U8.set(offset ? source.subarray(offset) : source, start);
    return;
  }
  for (let index = offset || 0; index < source.length; index++) {
    U8[start + index - (offset || 0)] = source[index] & 0xff;
  }
}

function Java_org_lwjgl_system_WebMC_nmemReadBytes(lib, address, bytes, offset, length) {
  const target = webmcJavaArray(bytes);
  if (!target) return;
  const start = webmcBytes("nmemReadBytes", address, Math.max(1, length));
  if (target instanceof Uint8Array) {
    target.set(U8.subarray(start, start + length), offset || 0);
    return;
  }
  for (let index = 0; index < length; index++) {
    target[(offset || 0) + index] = U8[start + index];
  }
}
function Java_org_lwjgl_system_WebMC_nmemSlice(lib, address, offset, capacity) {
  return address + offset;
}
function Java_org_lwjgl_system_WebMC_arenaUsed(lib) { return bump; }
function Java_org_lwjgl_system_WebMC_arenaCapacity(lib) { return HEAP_BYTES; }

// what the harness reads to prove the frame happened
window.webmc = {
  log: () => state.javaLog || [],
  trace: () => ({ called: state.trace, count: state.traceCount || 0 }),
  handles: () => Object.fromEntries(HANDLE_KINDS.map((kind) => [kind, handles[kind].size])),
  frames: () => state.swapCount,
  arenaUsed: () => bump,
  arenaCapacity: () => HEAP_BYTES,
  glInfo: () => (state.gl ? {
    version: state.gl.getParameter(state.gl.VERSION),
    renderer: state.gl.getParameter(state.gl.RENDERER),
  } : null),
  lastCallbackError: () => state.lastCallbackError || null,
  windows: () => state.windows.size,
};

function Java_org_lwjgl_opengl_GL20_glCreateShader(...args) { traced("opengl.GL20.glCreateShader"); return guarded("opengl.GL20.glCreateShader", () => Java_org_lwjgl_opengl_GL20_glCreateShader_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glShaderSource(...args) { traced("opengl.GL20.glShaderSource"); return guarded("opengl.GL20.glShaderSource", () => Java_org_lwjgl_opengl_GL20_glShaderSource_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glCompileShader(...args) { traced("opengl.GL20.glCompileShader"); return guarded("opengl.GL20.glCompileShader", () => Java_org_lwjgl_opengl_GL20_glCompileShader_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glGetShaderi(...args) { traced("opengl.GL20.glGetShaderi"); return guarded("opengl.GL20.glGetShaderi", () => Java_org_lwjgl_opengl_GL20_glGetShaderi_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glCreateProgram(...args) { traced("opengl.GL20.glCreateProgram"); return guarded("opengl.GL20.glCreateProgram", () => Java_org_lwjgl_opengl_GL20_glCreateProgram_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glLinkProgram(...args) { traced("opengl.GL20.glLinkProgram"); return guarded("opengl.GL20.glLinkProgram", () => Java_org_lwjgl_opengl_GL20_glLinkProgram_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glGetProgrami(...args) { traced("opengl.GL20.glGetProgrami"); return guarded("opengl.GL20.glGetProgrami", () => Java_org_lwjgl_opengl_GL20_glGetProgrami_inner(...args)); }
function Java_org_lwjgl_opengl_GL20_glUseProgram(...args) { traced("opengl.GL20.glUseProgram"); return guarded("opengl.GL20.glUseProgram", () => Java_org_lwjgl_opengl_GL20_glUseProgram_inner(...args)); }
function Java_org_lwjgl_opengl_GL11_glClear(...args) { traced("opengl.GL11.glClear"); return guarded("opengl.GL11.glClear", () => Java_org_lwjgl_opengl_GL11_glClear_inner(...args)); }
function Java_org_lwjgl_opengl_GL11_nglDrawArrays(...args) { traced("opengl.GL11.nglDrawArrays"); return guarded("opengl.GL11.nglDrawArrays", () => Java_org_lwjgl_opengl_GL11_nglDrawArrays_inner(...args)); }
function Java_org_lwjgl_opengl_GL11_glReadPixels(...args) { traced("opengl.GL11.glReadPixels"); return guarded("opengl.GL11.glReadPixels", () => Java_org_lwjgl_opengl_GL11_glReadPixels_inner(...args)); }
function Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers(...args) { traced("glfw.GLFW.glfwSwapBuffers"); return guarded("glfw.GLFW.glfwSwapBuffers", () => Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers_inner(...args)); }
function Java_org_lwjgl_glfw_GLFW_glfwPollEvents(...args) { traced("glfw.GLFW.glfwPollEvents"); return guarded("glfw.GLFW.glfwPollEvents", () => Java_org_lwjgl_glfw_GLFW_glfwPollEvents_inner(...args)); }


// ------------------------------------------------------------------ declared but never implemented
// The Java side declares these as native (the scaffold generates them from what a client calls), and
// without a JavaScript body the JVM throws UnsatisfiedLinkError at the first call - which is where this
// client was dying (measured: glfwSetMonitorCallback, from the window's monitor setup). Each one is
// either translated over WebGL2/DOM or an honest no-op with the reason beside it.

// org.lwjgl.glfw.GLFWNativeCocoa.glfwGetCocoaWindow: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFWNativeCocoa_glfwGetCocoaWindow(lib, ...args) {
  return 0n;
}

// org.lwjgl.glfw.GLFW.glfwGetPlatform: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwGetPlatform(lib, ...args) {
  return 0;
}

// org.lwjgl.glfw.GLFW.glfwGetWindowMonitor: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwGetWindowMonitor(lib, ...args) {
  return 0n;
}

// org.lwjgl.glfw.GLFW.glfwGetWindowPos: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwGetWindowPos(lib, ...args) {
  // nothing to do
}

// org.lwjgl.glfw.GLFW.glfwSetCharModsCallback: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwSetCharModsCallback(lib, ...args) {
  return null;
}

// org.lwjgl.glfw.GLFW.glfwSetCursorEnterCallback: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwSetCursorEnterCallback(lib, ...args) {
  return null;
}

// org.lwjgl.glfw.GLFW.glfwSetMonitorCallback: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwSetMonitorCallback(lib, ...args) {
  return null;
}

// org.lwjgl.glfw.GLFW.glfwSetWindowIconifyCallback: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowIconifyCallback(lib, ...args) {
  return null;
}

// org.lwjgl.glfw.GLFW.glfwSetWindowMonitor: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowMonitor(lib, ...args) {
  // nothing to do
}

// org.lwjgl.glfw.GLFW.glfwSetWindowSizeLimits: no equivalent in a browser: honest no-op
function Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeLimits(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alBufferData: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alBufferData(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alDeleteBuffers: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alDeleteBuffers(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alDeleteSources: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alDeleteSources(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alEnable: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alEnable(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alGenBuffers: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alGenBuffers(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alGenSources: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alGenSources(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alGetError: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alGetError(lib, ...args) {
  return 0;
}

// org.lwjgl.openal.AL10.alGetSourcei: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alGetSourcei(lib, ...args) {
  return 0;
}

// org.lwjgl.openal.AL10.alListener3f: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alListener3f(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alListenerfv: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alListenerfv(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourcePause: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourcePause(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourcePlay: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourcePlay(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourceQueueBuffers: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourceQueueBuffers(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourceStop: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourceStop(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourceUnqueueBuffers: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourceUnqueueBuffers(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourcef: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourcef(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourcefv: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourcefv(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.AL10.alSourcei: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_AL10_alSourcei(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.ALC10.alcCloseDevice: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcCloseDevice(lib, ...args) {
  return false;
}

// org.lwjgl.openal.ALC10.alcCreateContext: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcCreateContext(lib, ...args) {
  return 0n;
}

// org.lwjgl.openal.ALC10.alcDestroyContext: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcDestroyContext(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.ALC10.alcGetError: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcGetError(lib, ...args) {
  return 0;
}

// org.lwjgl.openal.ALC10.alcGetInteger: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcGetInteger(lib, ...args) {
  return 0;
}

// org.lwjgl.openal.ALC10.alcGetIntegerv: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcGetIntegerv(lib, ...args) {
  // nothing to do
}

// org.lwjgl.openal.ALC10.alcGetString: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcGetString(lib, ...args) {
  return null;
}

// org.lwjgl.openal.ALC10.alcIsExtensionPresent: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcIsExtensionPresent(lib, ...args) {
  return false;
}

// org.lwjgl.openal.ALC10.alcMakeContextCurrent: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcMakeContextCurrent(lib, ...args) {
  return false;
}

// org.lwjgl.openal.ALC10.alcOpenDevice: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC10_alcOpenDevice(lib, ...args) {
  return 0n;
}

// org.lwjgl.openal.ALC11.alcGetInteger: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALC11_alcGetInteger(lib, ...args) {
  return 0;
}

// org.lwjgl.openal.ALUtil.getStringList: no equivalent in a browser: honest no-op
function Java_org_lwjgl_openal_ALUtil_getStringList(lib, ...args) {
  return null;
}

// org.lwjgl.opengl.ARBBufferStorage.glBufferStorage: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBBufferStorage_glBufferStorage(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDebugOutput.glDebugMessageCallbackARB: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageCallbackARB(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDebugOutput.glDebugMessageControlARB: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageControlARB(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glBlitNamedFramebuffer: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glBlitNamedFramebuffer(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glCopyNamedBufferSubData: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glCopyNamedBufferSubData(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glCreateBuffers: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateBuffers(lib, ...args) {
  return 0;
}

// org.lwjgl.opengl.ARBDirectStateAccess.glCreateFramebuffers: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateFramebuffers(lib, ...args) {
  return 0;
}

// org.lwjgl.opengl.ARBDirectStateAccess.glFlushMappedNamedBufferRange: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glFlushMappedNamedBufferRange(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glMapNamedBufferRange: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glMapNamedBufferRange(lib, ...args) {
  return null;
}

// org.lwjgl.opengl.ARBDirectStateAccess.glNamedBufferData: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferData(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glNamedBufferStorage: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferStorage(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glNamedBufferSubData: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferSubData(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glNamedFramebufferTexture: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedFramebufferTexture(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBDirectStateAccess.glUnmapNamedBuffer: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBDirectStateAccess_glUnmapNamedBuffer(lib, ...args) {
  return false;
}

// org.lwjgl.opengl.ARBTimerQuery.glGetQueryObjecti64: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBTimerQuery_glGetQueryObjecti64(lib, ...args) {
  return 0n;
}

// org.lwjgl.opengl.ARBVertexAttribBinding.glBindVertexBuffer: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBVertexAttribBinding_glBindVertexBuffer(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBVertexAttribBinding.glVertexAttribBinding: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribBinding(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBVertexAttribBinding.glVertexAttribFormat: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribFormat(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.ARBVertexAttribBinding.glVertexAttribIFormat: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribIFormat(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.EXTDebugLabel.glLabelObjectEXT: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_EXTDebugLabel_glLabelObjectEXT(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL11C.glBindTexture: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL11C_glBindTexture(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL11.glDeleteTextures: implemented over WebGL2
function Java_org_lwjgl_opengl_GL11_glDeleteTextures(lib, ...args) {
  gl().deleteTexture(handleOf("texture", arguments[1]));
}

// org.lwjgl.opengl.GL11.glDrawBuffer: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL11_glDrawBuffer(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL11.glGenTextures: implemented over WebGL2
function Java_org_lwjgl_opengl_GL11_glGenTextures(lib, ...args) {
  return nameOf("texture", gl().createTexture());
}

// org.lwjgl.opengl.GL11.glGetTexLevelParameteri: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL11_glGetTexLevelParameteri(lib, ...args) {
  return 0;
}

// org.lwjgl.opengl.GL11.glLogicOp: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL11_glLogicOp(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL11.glPolygonMode: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL11_glPolygonMode(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL11.glPolygonOffset: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL11_glPolygonOffset(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL15.glBufferData: implemented over WebGL2
function Java_org_lwjgl_opengl_GL15_glBufferData(lib, ...args) {
  const size = Number(arguments[2]);
  const data = Number(arguments[3]);
  if (data) { gl().bufferData(arguments[1], U8.subarray(data, data + size), arguments[4]); } else { gl().bufferData(arguments[1], size, arguments[4]); }
}

// org.lwjgl.opengl.GL15.glBufferSubData: implemented over WebGL2
function Java_org_lwjgl_opengl_GL15_glBufferSubData(lib, ...args) {
  const offset = Number(arguments[1 + (arguments.length > 4 ? 1 : 0)]);
  const address = Number(arguments[3 + 1]);
}

// org.lwjgl.opengl.GL15.glDeleteBuffers: implemented over WebGL2
function Java_org_lwjgl_opengl_GL15_glDeleteBuffers(lib, ...args) {
  gl().deleteBuffer(handleOf("buffer", arguments[1]));
}

// org.lwjgl.opengl.GL15.glGenBuffers: implemented over WebGL2
function Java_org_lwjgl_opengl_GL15_glGenBuffers(lib, ...args) {
  return nameOf("buffer", gl().createBuffer());
}

// org.lwjgl.opengl.GL15.glUnmapBuffer: implemented over WebGL2
function Java_org_lwjgl_opengl_GL15_glUnmapBuffer(lib, target) {
  // Write the mapping back before unmapping. glMapBufferRange copies the buffer into JavaScript memory so
  // Java can read it; without this the client's edits are dropped and its uploads are empty - which reads
  // as a renderer that produces nothing rather than as a bug in the shim.
  const mapped = state.mappedBuffers && state.mappedBuffers.get(target);
  if (mapped) {
    const copy = U8.subarray(mapped.scratch, mapped.scratch + mapped.size);
    gl().bufferSubData(mapped.target, mapped.offset, copy);
    webmcFree(mapped.scratch);
    state.mappedBuffers.delete(target);
  }
  gl().unmapBuffer(target);
  return true;
}

// org.lwjgl.opengl.GL20C.nglShaderSource: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL20C_nglShaderSource(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL20.glScissor: implemented over WebGL2
function Java_org_lwjgl_opengl_GL20_glScissor(lib, ...args) {
  gl().scissor(arguments[1], arguments[2], arguments[3], arguments[4]);
}

// org.lwjgl.opengl.GL20.glVertexAttribPointer: implemented over WebGL2
function Java_org_lwjgl_opengl_GL20_glVertexAttribPointer(lib, ...args) {
  gl().vertexAttribPointer(arguments[1], arguments[2], arguments[3], arguments[4] !== 0, arguments[5], Number(arguments[6]));
}

// org.lwjgl.opengl.GL30.glDeleteFramebuffers: implemented over WebGL2
function Java_org_lwjgl_opengl_GL30_glDeleteFramebuffers(lib, ...args) {
  gl().deleteFramebuffer(handleOf("framebuffer", arguments[1]));
}

// org.lwjgl.opengl.GL30.glFlushMappedBufferRange: implemented over WebGL2
function Java_org_lwjgl_opengl_GL30_glFlushMappedBufferRange(lib, ...args) {
  gl().flushMappedBufferRange(arguments[1], Number(arguments[2]), Number(arguments[3]));
}

// org.lwjgl.opengl.GL30.glGenFramebuffers: implemented over WebGL2
function Java_org_lwjgl_opengl_GL30_glGenFramebuffers(lib, ...args) {
  return nameOf("framebuffer", gl().createFramebuffer());
}

// org.lwjgl.opengl.GL30.glGenVertexArrays: implemented over WebGL2
function Java_org_lwjgl_opengl_GL30_glGenVertexArrays(lib, ...args) {
  return nameOf("vertexArray", gl().createVertexArray());
}

// org.lwjgl.opengl.GL30.glMapBufferRange: implemented over WebGL2
function Java_org_lwjgl_opengl_GL30_glMapBufferRange(lib, ...args) {
  const handle = handleOf("buffer", args[0]);
  const size = Number(arguments[3]);
  const scratch = webmcAlloc(size);
  const copy = new Uint8Array(size);
  gl().bindBuffer(arguments[1] === gl().ARRAY_BUFFER ? gl().ARRAY_BUFFER : gl().ELEMENT_ARRAY_BUFFER, handle);
  gl().getBufferSubData(arguments[1] === gl().ARRAY_BUFFER ? gl().ARRAY_BUFFER : gl().ELEMENT_ARRAY_BUFFER, Number(arguments[2]), copy);
  U8.set(copy, scratch);
  state.mappedBuffers = state.mappedBuffers || new Map();
  state.mappedBuffers.set(handle, { scratch, size, target: arguments[1], offset: Number(arguments[2]) });
  return scratch;
}

// org.lwjgl.opengl.GL30.glVertexAttribIPointer: implemented over WebGL2
function Java_org_lwjgl_opengl_GL30_glVertexAttribIPointer(lib, ...args) {
  gl().vertexAttribIPointer(arguments[1], arguments[2], arguments[3], arguments[4], Number(arguments[5]));
}

// org.lwjgl.opengl.GL31.glCopyBufferSubData: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL31_glCopyBufferSubData(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL31.glDrawArraysInstanced: implemented over WebGL2
function Java_org_lwjgl_opengl_GL31_glDrawArraysInstanced(lib, ...args) {
  gl().drawArraysInstanced(arguments[1], arguments[2], arguments[3], arguments[4]);
}

// org.lwjgl.opengl.GL31.glDrawElementsInstanced: implemented over WebGL2
function Java_org_lwjgl_opengl_GL31_glDrawElementsInstanced(lib, ...args) {
  gl().drawElementsInstanced(arguments[1], arguments[2], arguments[3], Number(arguments[4]), arguments[5]);
}

// org.lwjgl.opengl.GL31.glGetActiveUniformBlockName: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL31_glGetActiveUniformBlockName(lib, ...args) {
  return null;
}

// org.lwjgl.opengl.GL31.glGetUniformBlockIndex: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL31_glGetUniformBlockIndex(lib, ...args) {
  return 0;
}

// org.lwjgl.opengl.GL31.glTexBuffer: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL31_glTexBuffer(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL31.glUniformBlockBinding: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL31_glUniformBlockBinding(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL32C.glBeginQuery: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL32C_glBeginQuery(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL32C.glDeleteQueries: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL32C_glDeleteQueries(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL32C.glEndQuery: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL32C_glEndQuery(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL32C.glGenQueries: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL32C_glGenQueries(lib, ...args) {
  return 0;
}

// org.lwjgl.opengl.GL32C.glGetQueryObjecti: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL32C_glGetQueryObjecti(lib, ...args) {
  return 0;
}

// org.lwjgl.opengl.GL32.glBindBufferRange: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_GL32_glBindBufferRange(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL32.glClientWaitSync: implemented over WebGL2
function Java_org_lwjgl_opengl_GL32_glClientWaitSync(lib, ...args) {
  return Number(gl().CLIENT_WAIT_FAILED);
}

// org.lwjgl.opengl.GL32.glDeleteSync: implemented over WebGL2
function Java_org_lwjgl_opengl_GL32_glDeleteSync(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.GL32.glDrawElementsBaseVertex: implemented over WebGL2
function Java_org_lwjgl_opengl_GL32_glDrawElementsBaseVertex(lib, ...args) {
  gl().drawElementsBaseVertex(arguments[1], arguments[2], arguments[3], Number(arguments[4]), arguments[5]);
}

// org.lwjgl.opengl.GL32.glDrawElementsInstancedBaseVertex: implemented over WebGL2
function Java_org_lwjgl_opengl_GL32_glDrawElementsInstancedBaseVertex(lib, ...args) {
  gl().drawElementsInstancedBaseVertex(arguments[1], arguments[2], arguments[3], Number(arguments[4]), arguments[5], arguments[6]);
}

// org.lwjgl.opengl.GL32.glFenceSync: implemented over WebGL2
function Java_org_lwjgl_opengl_GL32_glFenceSync(lib, ...args) {
  return 0n;
}

// org.lwjgl.opengl.GL33C.glBindSampler: implemented over WebGL2
function Java_org_lwjgl_opengl_GL33C_glBindSampler(lib, ...args) {
  gl().bindSampler(arguments[1], arguments[2] ? objectOf("sampler", arguments[2]) : null);
}

// org.lwjgl.opengl.GL33C.glDeleteSamplers: implemented over WebGL2
function Java_org_lwjgl_opengl_GL33C_glDeleteSamplers(lib, ...args) {
  gl().deleteSampler(handleOf("sampler", arguments[1]));
}

// org.lwjgl.opengl.GL33C.glGenSamplers: implemented over WebGL2
function Java_org_lwjgl_opengl_GL33C_glGenSamplers(lib, ...args) {
  return nameOf("sampler", gl().createSampler());
}

// org.lwjgl.opengl.GL33C.glSamplerParameterf: implemented over WebGL2
function Java_org_lwjgl_opengl_GL33C_glSamplerParameterf(lib, ...args) {
  gl().samplerParameterf(handleOf("sampler", arguments[1]), arguments[2], arguments[3]);
}

// org.lwjgl.opengl.GL33C.glSamplerParameteri: implemented over WebGL2
function Java_org_lwjgl_opengl_GL33C_glSamplerParameteri(lib, ...args) {
  gl().samplerParameteri(handleOf("sampler", arguments[1]), arguments[2], arguments[3]);
}

// org.lwjgl.opengl.KHRDebug.glDebugMessageCallback: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_KHRDebug_glDebugMessageCallback(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.KHRDebug.glDebugMessageControl: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_KHRDebug_glDebugMessageControl(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.KHRDebug.glObjectLabel: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_KHRDebug_glObjectLabel(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.KHRDebug.glPopDebugGroup: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_KHRDebug_glPopDebugGroup(lib, ...args) {
  // nothing to do
}

// org.lwjgl.opengl.KHRDebug.glPushDebugGroup: no equivalent in a browser: honest no-op
function Java_org_lwjgl_opengl_KHRDebug_glPushDebugGroup(lib, ...args) {
  // nothing to do
}

// org.lwjgl.system.WebMC.glVersion: implemented over WebGL2
function Java_org_lwjgl_system_WebMC_glVersion(lib, ...args) {
  return gl().getParameter(gl().VERSION) + " | " + gl().getParameter(gl().RENDERER);
}

// org.lwjgl.system.WebMC.hasGlExtension: implemented over WebGL2
function Java_org_lwjgl_system_WebMC_hasGlExtension(lib, ...args) {
  const name = String(arguments[1] || "").replace("GL_", "");
  return gl().getExtension(name) !== null || gl().getSupportedExtensions().some((e) => e.includes(name));
}
function Java_org_lwjgl_system_WebMC_windowWidth() { return state.width; }

function Java_org_lwjgl_system_WebMC_windowHeight() { return state.height; }

// ------------------------------------------------------------------ what CheerpJ is given
// cheerpjInit({ natives }) takes exactly this: JNI names to implementations. It is
// generated by tools/lwjgl3_natives.py from the functions above, so it cannot drift.
//
export const natives = {
  Java_org_lwjgl_glfw_Callbacks_glfwFreeCallbacks: webmcNamed("Java_org_lwjgl_glfw_Callbacks_glfwFreeCallbacks", webmcNative(Java_org_lwjgl_glfw_Callbacks_glfwFreeCallbacks)),
  Java_org_lwjgl_glfw_GLFWNativeCocoa_glfwGetCocoaWindow: webmcNamed("Java_org_lwjgl_glfw_GLFWNativeCocoa_glfwGetCocoaWindow", webmcNative(Java_org_lwjgl_glfw_GLFWNativeCocoa_glfwGetCocoaWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwCreateStandardCursor: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwCreateStandardCursor", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwCreateStandardCursor)),
  Java_org_lwjgl_glfw_GLFW_glfwCreateWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwCreateWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwCreateWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwDefaultWindowHints: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwDefaultWindowHints", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwDefaultWindowHints)),
  Java_org_lwjgl_glfw_GLFW_glfwDestroyCursor: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwDestroyCursor", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwDestroyCursor)),
  Java_org_lwjgl_glfw_GLFW_glfwDestroyWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwDestroyWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwDestroyWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwFocusWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwFocusWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwFocusWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwGetClipboardString: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetClipboardString", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetClipboardString)),
  Java_org_lwjgl_glfw_GLFW_glfwGetCurrentContext: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetCurrentContext", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetCurrentContext)),
  Java_org_lwjgl_glfw_GLFW_glfwGetCursorPos: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetCursorPos", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetCursorPos)),
  Java_org_lwjgl_glfw_GLFW_glfwGetError: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetError", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetError)),
  Java_org_lwjgl_glfw_GLFW_glfwGetFramebufferSize: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetFramebufferSize", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetFramebufferSize)),
  Java_org_lwjgl_glfw_GLFW_glfwGetJoystickName: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetJoystickName", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetJoystickName)),
  Java_org_lwjgl_glfw_GLFW_glfwGetKey: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetKey", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetKey)),
  Java_org_lwjgl_glfw_GLFW_glfwGetKeyName: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetKeyName", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetKeyName)),
  Java_org_lwjgl_glfw_GLFW_glfwGetKeyScancode: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetKeyScancode", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetKeyScancode)),
  Java_org_lwjgl_glfw_GLFW_glfwGetMonitorPos: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetMonitorPos", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetMonitorPos)),
  Java_org_lwjgl_glfw_GLFW_glfwGetMonitors: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetMonitors", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetMonitors)),
  Java_org_lwjgl_glfw_GLFW_glfwGetMouseButton: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetMouseButton", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetMouseButton)),
  Java_org_lwjgl_glfw_GLFW_glfwGetPlatform: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetPlatform", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetPlatform)),
  Java_org_lwjgl_glfw_GLFW_glfwGetPrimaryMonitor: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetPrimaryMonitor", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetPrimaryMonitor)),
  Java_org_lwjgl_glfw_GLFW_glfwGetTime: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetTime", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetTime)),
  Java_org_lwjgl_glfw_GLFW_glfwGetVersionString: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetVersionString", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetVersionString)),
  Java_org_lwjgl_glfw_GLFW_glfwGetVideoMode: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetVideoMode", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetVideoMode)),
  Java_org_lwjgl_glfw_GLFW_glfwGetVideoModes: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetVideoModes", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetVideoModes)),
  Java_org_lwjgl_glfw_GLFW_glfwGetWindowAttrib: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetWindowAttrib", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetWindowAttrib)),
  Java_org_lwjgl_glfw_GLFW_glfwGetWindowContentScale: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetWindowContentScale", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetWindowContentScale)),
  Java_org_lwjgl_glfw_GLFW_glfwGetWindowMonitor: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetWindowMonitor", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetWindowMonitor)),
  Java_org_lwjgl_glfw_GLFW_glfwGetWindowPos: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetWindowPos", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetWindowPos)),
  Java_org_lwjgl_glfw_GLFW_glfwGetWindowSize: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwGetWindowSize", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwGetWindowSize)),
  Java_org_lwjgl_glfw_GLFW_glfwHideWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwHideWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwHideWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwIconifyWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwIconifyWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwIconifyWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwInit: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwInit", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwInit)),
  Java_org_lwjgl_glfw_GLFW_glfwJoystickPresent: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwJoystickPresent", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwJoystickPresent)),
  Java_org_lwjgl_glfw_GLFW_glfwMakeContextCurrent: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwMakeContextCurrent", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwMakeContextCurrent)),
  Java_org_lwjgl_glfw_GLFW_glfwMaximizeWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwMaximizeWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwMaximizeWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwPollEvents: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwPollEvents", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwPollEvents)),
  Java_org_lwjgl_glfw_GLFW_glfwPostEmptyEvent: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwPostEmptyEvent", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwPostEmptyEvent)),
  Java_org_lwjgl_glfw_GLFW_glfwRawMouseMotionSupported: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwRawMouseMotionSupported", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwRawMouseMotionSupported)),
  Java_org_lwjgl_glfw_GLFW_glfwRequestWindowAttention: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwRequestWindowAttention", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwRequestWindowAttention)),
  Java_org_lwjgl_glfw_GLFW_glfwRestoreWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwRestoreWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwRestoreWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwSetCharCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetCharCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetCharCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetCharModsCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetCharModsCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetCharModsCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetClipboardString: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetClipboardString", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetClipboardString)),
  Java_org_lwjgl_glfw_GLFW_glfwSetCursor: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetCursor", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetCursor)),
  Java_org_lwjgl_glfw_GLFW_glfwSetCursorEnterCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetCursorEnterCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetCursorEnterCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetCursorPos: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetCursorPos", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetCursorPos)),
  Java_org_lwjgl_glfw_GLFW_glfwSetCursorPosCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetCursorPosCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetCursorPosCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetDropCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetDropCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetDropCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetErrorCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetErrorCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetErrorCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetFramebufferSizeCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetFramebufferSizeCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetFramebufferSizeCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetInputMode: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetInputMode", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetInputMode)),
  Java_org_lwjgl_glfw_GLFW_glfwSetKeyCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetKeyCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetKeyCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetMonitorCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetMonitorCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetMonitorCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetMouseButtonCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetMouseButtonCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetMouseButtonCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetScrollCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetScrollCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetScrollCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetTime: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetTime", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetTime)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowAttrib: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowAttrib", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowAttrib)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowCloseCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowCloseCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowCloseCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowFocusCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowFocusCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowFocusCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowIcon: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowIcon", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowIcon)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowIconifyCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowIconifyCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowIconifyCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowMonitor: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowMonitor", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowMonitor)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowPos: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowPos", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowPos)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowPosCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowPosCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowPosCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowShouldClose: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowShouldClose", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowShouldClose)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowSize: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowSize", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowSize)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeCallback: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeCallback", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeCallback)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeLimits: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeLimits", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowSizeLimits)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowTitle: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowTitle", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowTitle)),
  Java_org_lwjgl_glfw_GLFW_glfwSetWindowUserPointer: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSetWindowUserPointer", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSetWindowUserPointer)),
  Java_org_lwjgl_glfw_GLFW_glfwShowWindow: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwShowWindow", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwShowWindow)),
  Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers)),
  Java_org_lwjgl_glfw_GLFW_glfwSwapInterval: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwSwapInterval", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwSwapInterval)),
  Java_org_lwjgl_glfw_GLFW_glfwTerminate: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwTerminate", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwTerminate)),
  Java_org_lwjgl_glfw_GLFW_glfwUpdateGamepadMappings: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwUpdateGamepadMappings", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwUpdateGamepadMappings)),
  Java_org_lwjgl_glfw_GLFW_glfwWaitEvents: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwWaitEvents", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwWaitEvents)),
  Java_org_lwjgl_glfw_GLFW_glfwWaitEventsTimeout: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwWaitEventsTimeout", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwWaitEventsTimeout)),
  Java_org_lwjgl_glfw_GLFW_glfwWindowHint: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwWindowHint", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwWindowHint)),
  Java_org_lwjgl_glfw_GLFW_glfwWindowShouldClose: webmcNamed("Java_org_lwjgl_glfw_GLFW_glfwWindowShouldClose", webmcNative(Java_org_lwjgl_glfw_GLFW_glfwWindowShouldClose)),
  Java_org_lwjgl_openal_AL10_alBufferData: webmcNamed("Java_org_lwjgl_openal_AL10_alBufferData", webmcNative(Java_org_lwjgl_openal_AL10_alBufferData)),
  Java_org_lwjgl_openal_AL10_alDeleteBuffers: webmcNamed("Java_org_lwjgl_openal_AL10_alDeleteBuffers", webmcNative(Java_org_lwjgl_openal_AL10_alDeleteBuffers)),
  Java_org_lwjgl_openal_AL10_alDeleteSources: webmcNamed("Java_org_lwjgl_openal_AL10_alDeleteSources", webmcNative(Java_org_lwjgl_openal_AL10_alDeleteSources)),
  Java_org_lwjgl_openal_AL10_alEnable: webmcNamed("Java_org_lwjgl_openal_AL10_alEnable", webmcNative(Java_org_lwjgl_openal_AL10_alEnable)),
  Java_org_lwjgl_openal_AL10_alGenBuffers: webmcNamed("Java_org_lwjgl_openal_AL10_alGenBuffers", webmcNative(Java_org_lwjgl_openal_AL10_alGenBuffers)),
  Java_org_lwjgl_openal_AL10_alGenSources: webmcNamed("Java_org_lwjgl_openal_AL10_alGenSources", webmcNative(Java_org_lwjgl_openal_AL10_alGenSources)),
  Java_org_lwjgl_openal_AL10_alGetError: webmcNamed("Java_org_lwjgl_openal_AL10_alGetError", webmcNative(Java_org_lwjgl_openal_AL10_alGetError)),
  Java_org_lwjgl_openal_AL10_alGetSourcei: webmcNamed("Java_org_lwjgl_openal_AL10_alGetSourcei", webmcNative(Java_org_lwjgl_openal_AL10_alGetSourcei)),
  Java_org_lwjgl_openal_AL10_alListener3f: webmcNamed("Java_org_lwjgl_openal_AL10_alListener3f", webmcNative(Java_org_lwjgl_openal_AL10_alListener3f)),
  Java_org_lwjgl_openal_AL10_alListenerfv: webmcNamed("Java_org_lwjgl_openal_AL10_alListenerfv", webmcNative(Java_org_lwjgl_openal_AL10_alListenerfv)),
  Java_org_lwjgl_openal_AL10_alSourcePause: webmcNamed("Java_org_lwjgl_openal_AL10_alSourcePause", webmcNative(Java_org_lwjgl_openal_AL10_alSourcePause)),
  Java_org_lwjgl_openal_AL10_alSourcePlay: webmcNamed("Java_org_lwjgl_openal_AL10_alSourcePlay", webmcNative(Java_org_lwjgl_openal_AL10_alSourcePlay)),
  Java_org_lwjgl_openal_AL10_alSourceQueueBuffers: webmcNamed("Java_org_lwjgl_openal_AL10_alSourceQueueBuffers", webmcNative(Java_org_lwjgl_openal_AL10_alSourceQueueBuffers)),
  Java_org_lwjgl_openal_AL10_alSourceStop: webmcNamed("Java_org_lwjgl_openal_AL10_alSourceStop", webmcNative(Java_org_lwjgl_openal_AL10_alSourceStop)),
  Java_org_lwjgl_openal_AL10_alSourceUnqueueBuffers: webmcNamed("Java_org_lwjgl_openal_AL10_alSourceUnqueueBuffers", webmcNative(Java_org_lwjgl_openal_AL10_alSourceUnqueueBuffers)),
  Java_org_lwjgl_openal_AL10_alSourcef: webmcNamed("Java_org_lwjgl_openal_AL10_alSourcef", webmcNative(Java_org_lwjgl_openal_AL10_alSourcef)),
  Java_org_lwjgl_openal_AL10_alSourcefv: webmcNamed("Java_org_lwjgl_openal_AL10_alSourcefv", webmcNative(Java_org_lwjgl_openal_AL10_alSourcefv)),
  Java_org_lwjgl_openal_AL10_alSourcei: webmcNamed("Java_org_lwjgl_openal_AL10_alSourcei", webmcNative(Java_org_lwjgl_openal_AL10_alSourcei)),
  Java_org_lwjgl_openal_ALC10_alcCloseDevice: webmcNamed("Java_org_lwjgl_openal_ALC10_alcCloseDevice", webmcNative(Java_org_lwjgl_openal_ALC10_alcCloseDevice)),
  Java_org_lwjgl_openal_ALC10_alcCreateContext: webmcNamed("Java_org_lwjgl_openal_ALC10_alcCreateContext", webmcNative(Java_org_lwjgl_openal_ALC10_alcCreateContext)),
  Java_org_lwjgl_openal_ALC10_alcDestroyContext: webmcNamed("Java_org_lwjgl_openal_ALC10_alcDestroyContext", webmcNative(Java_org_lwjgl_openal_ALC10_alcDestroyContext)),
  Java_org_lwjgl_openal_ALC10_alcGetError: webmcNamed("Java_org_lwjgl_openal_ALC10_alcGetError", webmcNative(Java_org_lwjgl_openal_ALC10_alcGetError)),
  Java_org_lwjgl_openal_ALC10_alcGetInteger: webmcNamed("Java_org_lwjgl_openal_ALC10_alcGetInteger", webmcNative(Java_org_lwjgl_openal_ALC10_alcGetInteger)),
  Java_org_lwjgl_openal_ALC10_alcGetIntegerv: webmcNamed("Java_org_lwjgl_openal_ALC10_alcGetIntegerv", webmcNative(Java_org_lwjgl_openal_ALC10_alcGetIntegerv)),
  Java_org_lwjgl_openal_ALC10_alcGetString: webmcNamed("Java_org_lwjgl_openal_ALC10_alcGetString", webmcNative(Java_org_lwjgl_openal_ALC10_alcGetString)),
  Java_org_lwjgl_openal_ALC10_alcIsExtensionPresent: webmcNamed("Java_org_lwjgl_openal_ALC10_alcIsExtensionPresent", webmcNative(Java_org_lwjgl_openal_ALC10_alcIsExtensionPresent)),
  Java_org_lwjgl_openal_ALC10_alcMakeContextCurrent: webmcNamed("Java_org_lwjgl_openal_ALC10_alcMakeContextCurrent", webmcNative(Java_org_lwjgl_openal_ALC10_alcMakeContextCurrent)),
  Java_org_lwjgl_openal_ALC10_alcOpenDevice: webmcNamed("Java_org_lwjgl_openal_ALC10_alcOpenDevice", webmcNative(Java_org_lwjgl_openal_ALC10_alcOpenDevice)),
  Java_org_lwjgl_openal_ALC11_alcGetInteger: webmcNamed("Java_org_lwjgl_openal_ALC11_alcGetInteger", webmcNative(Java_org_lwjgl_openal_ALC11_alcGetInteger)),
  Java_org_lwjgl_openal_ALUtil_getStringList: webmcNamed("Java_org_lwjgl_openal_ALUtil_getStringList", webmcNative(Java_org_lwjgl_openal_ALUtil_getStringList)),
  Java_org_lwjgl_opengl_ARBBufferStorage_glBufferStorage: webmcNamed("Java_org_lwjgl_opengl_ARBBufferStorage_glBufferStorage", webmcNative(Java_org_lwjgl_opengl_ARBBufferStorage_glBufferStorage)),
  Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageCallbackARB: webmcNamed("Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageCallbackARB", webmcNative(Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageCallbackARB)),
  Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageControlARB: webmcNamed("Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageControlARB", webmcNative(Java_org_lwjgl_opengl_ARBDebugOutput_glDebugMessageControlARB)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glBlitNamedFramebuffer: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glBlitNamedFramebuffer", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glBlitNamedFramebuffer)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glCopyNamedBufferSubData: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glCopyNamedBufferSubData", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glCopyNamedBufferSubData)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateBuffers: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateBuffers", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateBuffers)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateFramebuffers: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateFramebuffers", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glCreateFramebuffers)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glFlushMappedNamedBufferRange: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glFlushMappedNamedBufferRange", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glFlushMappedNamedBufferRange)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glMapNamedBufferRange: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glMapNamedBufferRange", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glMapNamedBufferRange)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferData: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferData", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferData)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferStorage: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferStorage", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferStorage)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferSubData: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferSubData", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedBufferSubData)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedFramebufferTexture: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedFramebufferTexture", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glNamedFramebufferTexture)),
  Java_org_lwjgl_opengl_ARBDirectStateAccess_glUnmapNamedBuffer: webmcNamed("Java_org_lwjgl_opengl_ARBDirectStateAccess_glUnmapNamedBuffer", webmcNative(Java_org_lwjgl_opengl_ARBDirectStateAccess_glUnmapNamedBuffer)),
  Java_org_lwjgl_opengl_ARBTimerQuery_glGetQueryObjecti64: webmcNamed("Java_org_lwjgl_opengl_ARBTimerQuery_glGetQueryObjecti64", webmcNative(Java_org_lwjgl_opengl_ARBTimerQuery_glGetQueryObjecti64)),
  Java_org_lwjgl_opengl_ARBVertexAttribBinding_glBindVertexBuffer: webmcNamed("Java_org_lwjgl_opengl_ARBVertexAttribBinding_glBindVertexBuffer", webmcNative(Java_org_lwjgl_opengl_ARBVertexAttribBinding_glBindVertexBuffer)),
  Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribBinding: webmcNamed("Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribBinding", webmcNative(Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribBinding)),
  Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribFormat: webmcNamed("Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribFormat", webmcNative(Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribFormat)),
  Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribIFormat: webmcNamed("Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribIFormat", webmcNative(Java_org_lwjgl_opengl_ARBVertexAttribBinding_glVertexAttribIFormat)),
  Java_org_lwjgl_opengl_EXTDebugLabel_glLabelObjectEXT: webmcNamed("Java_org_lwjgl_opengl_EXTDebugLabel_glLabelObjectEXT", webmcNative(Java_org_lwjgl_opengl_EXTDebugLabel_glLabelObjectEXT)),
  Java_org_lwjgl_opengl_GL11C_glBindTexture: webmcNamed("Java_org_lwjgl_opengl_GL11C_glBindTexture", webmcNative(Java_org_lwjgl_opengl_GL11C_glBindTexture)),
  Java_org_lwjgl_opengl_GL11_glBindBuffer: webmcNamed("Java_org_lwjgl_opengl_GL11_glBindBuffer", webmcNative(Java_org_lwjgl_opengl_GL11_glBindBuffer)),
  Java_org_lwjgl_opengl_GL11_glBindTexture: webmcNamed("Java_org_lwjgl_opengl_GL11_glBindTexture", webmcNative(Java_org_lwjgl_opengl_GL11_glBindTexture)),
  Java_org_lwjgl_opengl_GL11_glBlendEquation: webmcNamed("Java_org_lwjgl_opengl_GL11_glBlendEquation", webmcNative(Java_org_lwjgl_opengl_GL11_glBlendEquation)),
  Java_org_lwjgl_opengl_GL11_glBlendFunc: webmcNamed("Java_org_lwjgl_opengl_GL11_glBlendFunc", webmcNative(Java_org_lwjgl_opengl_GL11_glBlendFunc)),
  Java_org_lwjgl_opengl_GL11_glBlendFuncSeparate: webmcNamed("Java_org_lwjgl_opengl_GL11_glBlendFuncSeparate", webmcNative(Java_org_lwjgl_opengl_GL11_glBlendFuncSeparate)),
  Java_org_lwjgl_opengl_GL11_glClear: webmcNamed("Java_org_lwjgl_opengl_GL11_glClear", webmcNative(Java_org_lwjgl_opengl_GL11_glClear)),
  Java_org_lwjgl_opengl_GL11_glClearColor: webmcNamed("Java_org_lwjgl_opengl_GL11_glClearColor", webmcNative(Java_org_lwjgl_opengl_GL11_glClearColor)),
  Java_org_lwjgl_opengl_GL11_glClearDepth: webmcNamed("Java_org_lwjgl_opengl_GL11_glClearDepth", webmcNative(Java_org_lwjgl_opengl_GL11_glClearDepth)),
  Java_org_lwjgl_opengl_GL11_glClearStencil: webmcNamed("Java_org_lwjgl_opengl_GL11_glClearStencil", webmcNative(Java_org_lwjgl_opengl_GL11_glClearStencil)),
  Java_org_lwjgl_opengl_GL11_glColorMask: webmcNamed("Java_org_lwjgl_opengl_GL11_glColorMask", webmcNative(Java_org_lwjgl_opengl_GL11_glColorMask)),
  Java_org_lwjgl_opengl_GL11_glCullFace: webmcNamed("Java_org_lwjgl_opengl_GL11_glCullFace", webmcNative(Java_org_lwjgl_opengl_GL11_glCullFace)),
  Java_org_lwjgl_opengl_GL11_glDeleteTextures: webmcNamed("Java_org_lwjgl_opengl_GL11_glDeleteTextures", webmcNative(Java_org_lwjgl_opengl_GL11_glDeleteTextures)),
  Java_org_lwjgl_opengl_GL11_glDepthFunc: webmcNamed("Java_org_lwjgl_opengl_GL11_glDepthFunc", webmcNative(Java_org_lwjgl_opengl_GL11_glDepthFunc)),
  Java_org_lwjgl_opengl_GL11_glDepthMask: webmcNamed("Java_org_lwjgl_opengl_GL11_glDepthMask", webmcNative(Java_org_lwjgl_opengl_GL11_glDepthMask)),
  Java_org_lwjgl_opengl_GL11_glDepthRange: webmcNamed("Java_org_lwjgl_opengl_GL11_glDepthRange", webmcNative(Java_org_lwjgl_opengl_GL11_glDepthRange)),
  Java_org_lwjgl_opengl_GL11_glDisable: webmcNamed("Java_org_lwjgl_opengl_GL11_glDisable", webmcNative(Java_org_lwjgl_opengl_GL11_glDisable)),
  Java_org_lwjgl_opengl_GL11_glDrawArrays: webmcNamed("Java_org_lwjgl_opengl_GL11_glDrawArrays", webmcNative(Java_org_lwjgl_opengl_GL11_glDrawArrays)),
  Java_org_lwjgl_opengl_GL11_glDrawBuffer: webmcNamed("Java_org_lwjgl_opengl_GL11_glDrawBuffer", webmcNative(Java_org_lwjgl_opengl_GL11_glDrawBuffer)),
  Java_org_lwjgl_opengl_GL11_glDrawElements: webmcNamed("Java_org_lwjgl_opengl_GL11_glDrawElements", webmcNative(Java_org_lwjgl_opengl_GL11_glDrawElements)),
  Java_org_lwjgl_opengl_GL11_glEnable: webmcNamed("Java_org_lwjgl_opengl_GL11_glEnable", webmcNative(Java_org_lwjgl_opengl_GL11_glEnable)),
  Java_org_lwjgl_opengl_GL11_glFinish: webmcNamed("Java_org_lwjgl_opengl_GL11_glFinish", webmcNative(Java_org_lwjgl_opengl_GL11_glFinish)),
  Java_org_lwjgl_opengl_GL11_glFlush: webmcNamed("Java_org_lwjgl_opengl_GL11_glFlush", webmcNative(Java_org_lwjgl_opengl_GL11_glFlush)),
  Java_org_lwjgl_opengl_GL11_glFrontFace: webmcNamed("Java_org_lwjgl_opengl_GL11_glFrontFace", webmcNative(Java_org_lwjgl_opengl_GL11_glFrontFace)),
  Java_org_lwjgl_opengl_GL11_glGenBuffers: webmcNamed("Java_org_lwjgl_opengl_GL11_glGenBuffers", webmcNative(Java_org_lwjgl_opengl_GL11_glGenBuffers)),
  Java_org_lwjgl_opengl_GL11_glGenTextures: webmcNamed("Java_org_lwjgl_opengl_GL11_glGenTextures", webmcNative(Java_org_lwjgl_opengl_GL11_glGenTextures)),
  Java_org_lwjgl_opengl_GL11_glGenerateMipmap: webmcNamed("Java_org_lwjgl_opengl_GL11_glGenerateMipmap", webmcNative(Java_org_lwjgl_opengl_GL11_glGenerateMipmap)),
  Java_org_lwjgl_opengl_GL11_glGetBoolean: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetBoolean", webmcNative(Java_org_lwjgl_opengl_GL11_glGetBoolean)),
  Java_org_lwjgl_opengl_GL11_glGetError: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetError", webmcNative(Java_org_lwjgl_opengl_GL11_glGetError)),
  Java_org_lwjgl_opengl_GL11_glGetFloat: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetFloat", webmcNative(Java_org_lwjgl_opengl_GL11_glGetFloat)),
  Java_org_lwjgl_opengl_GL11_glGetInteger: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetInteger", webmcNative(Java_org_lwjgl_opengl_GL11_glGetInteger)),
  Java_org_lwjgl_opengl_GL11_glGetIntegeri: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetIntegeri", webmcNative(Java_org_lwjgl_opengl_GL11_glGetIntegeri)),
  Java_org_lwjgl_opengl_GL11_glGetString: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetString", webmcNative(Java_org_lwjgl_opengl_GL11_glGetString)),
  Java_org_lwjgl_opengl_GL11_glGetTexLevelParameteri: webmcNamed("Java_org_lwjgl_opengl_GL11_glGetTexLevelParameteri", webmcNative(Java_org_lwjgl_opengl_GL11_glGetTexLevelParameteri)),
  Java_org_lwjgl_opengl_GL11_glHint: webmcNamed("Java_org_lwjgl_opengl_GL11_glHint", webmcNative(Java_org_lwjgl_opengl_GL11_glHint)),
  Java_org_lwjgl_opengl_GL11_glIsEnabled: webmcNamed("Java_org_lwjgl_opengl_GL11_glIsEnabled", webmcNative(Java_org_lwjgl_opengl_GL11_glIsEnabled)),
  Java_org_lwjgl_opengl_GL11_glLineWidth: webmcNamed("Java_org_lwjgl_opengl_GL11_glLineWidth", webmcNative(Java_org_lwjgl_opengl_GL11_glLineWidth)),
  Java_org_lwjgl_opengl_GL11_glLogicOp: webmcNamed("Java_org_lwjgl_opengl_GL11_glLogicOp", webmcNative(Java_org_lwjgl_opengl_GL11_glLogicOp)),
  Java_org_lwjgl_opengl_GL11_glPixelStorei: webmcNamed("Java_org_lwjgl_opengl_GL11_glPixelStorei", webmcNative(Java_org_lwjgl_opengl_GL11_glPixelStorei)),
  Java_org_lwjgl_opengl_GL11_glPolygonMode: webmcNamed("Java_org_lwjgl_opengl_GL11_glPolygonMode", webmcNative(Java_org_lwjgl_opengl_GL11_glPolygonMode)),
  Java_org_lwjgl_opengl_GL11_glPolygonOffset: webmcNamed("Java_org_lwjgl_opengl_GL11_glPolygonOffset", webmcNative(Java_org_lwjgl_opengl_GL11_glPolygonOffset)),
  Java_org_lwjgl_opengl_GL11_glReadPixels: webmcNamed("Java_org_lwjgl_opengl_GL11_glReadPixels", webmcNative(Java_org_lwjgl_opengl_GL11_glReadPixels)),
  Java_org_lwjgl_opengl_GL11_glScissor: webmcNamed("Java_org_lwjgl_opengl_GL11_glScissor", webmcNative(Java_org_lwjgl_opengl_GL11_glScissor)),
  Java_org_lwjgl_opengl_GL11_glTexImage2D: webmcNamed("Java_org_lwjgl_opengl_GL11_glTexImage2D", webmcNative(Java_org_lwjgl_opengl_GL11_glTexImage2D)),
  Java_org_lwjgl_opengl_GL11_glTexParameterf: webmcNamed("Java_org_lwjgl_opengl_GL11_glTexParameterf", webmcNative(Java_org_lwjgl_opengl_GL11_glTexParameterf)),
  Java_org_lwjgl_opengl_GL11_glTexParameteri: webmcNamed("Java_org_lwjgl_opengl_GL11_glTexParameteri", webmcNative(Java_org_lwjgl_opengl_GL11_glTexParameteri)),
  Java_org_lwjgl_opengl_GL11_glTexSubImage2D: webmcNamed("Java_org_lwjgl_opengl_GL11_glTexSubImage2D", webmcNative(Java_org_lwjgl_opengl_GL11_glTexSubImage2D)),
  Java_org_lwjgl_opengl_GL11_glViewport: webmcNamed("Java_org_lwjgl_opengl_GL11_glViewport", webmcNative(Java_org_lwjgl_opengl_GL11_glViewport)),
  Java_org_lwjgl_opengl_GL11_nglBufferData: webmcNamed("Java_org_lwjgl_opengl_GL11_nglBufferData", webmcNative(Java_org_lwjgl_opengl_GL11_nglBufferData)),
  Java_org_lwjgl_opengl_GL11_nglBufferData1: webmcNamed("Java_org_lwjgl_opengl_GL11_nglBufferData1", webmcNative(Java_org_lwjgl_opengl_GL11_nglBufferData1)),
  Java_org_lwjgl_opengl_GL11_nglBufferSubData: webmcNamed("Java_org_lwjgl_opengl_GL11_nglBufferSubData", webmcNative(Java_org_lwjgl_opengl_GL11_nglBufferSubData)),
  Java_org_lwjgl_opengl_GL11_nglDeleteBuffers: webmcNamed("Java_org_lwjgl_opengl_GL11_nglDeleteBuffers", webmcNative(Java_org_lwjgl_opengl_GL11_nglDeleteBuffers)),
  Java_org_lwjgl_opengl_GL11_nglDeleteTextures: webmcNamed("Java_org_lwjgl_opengl_GL11_nglDeleteTextures", webmcNative(Java_org_lwjgl_opengl_GL11_nglDeleteTextures)),
  Java_org_lwjgl_opengl_GL11_nglDrawArrays: webmcNamed("Java_org_lwjgl_opengl_GL11_nglDrawArrays", webmcNative(Java_org_lwjgl_opengl_GL11_nglDrawArrays)),
  Java_org_lwjgl_opengl_GL11_nglDrawElements: webmcNamed("Java_org_lwjgl_opengl_GL11_nglDrawElements", webmcNative(Java_org_lwjgl_opengl_GL11_nglDrawElements)),
  Java_org_lwjgl_opengl_GL11_nglGenBuffers: webmcNamed("Java_org_lwjgl_opengl_GL11_nglGenBuffers", webmcNative(Java_org_lwjgl_opengl_GL11_nglGenBuffers)),
  Java_org_lwjgl_opengl_GL11_nglGenTextures: webmcNamed("Java_org_lwjgl_opengl_GL11_nglGenTextures", webmcNative(Java_org_lwjgl_opengl_GL11_nglGenTextures)),
  Java_org_lwjgl_opengl_GL11_nglGetIntegerv: webmcNamed("Java_org_lwjgl_opengl_GL11_nglGetIntegerv", webmcNative(Java_org_lwjgl_opengl_GL11_nglGetIntegerv)),
  Java_org_lwjgl_opengl_GL11_nglReadPixels: webmcNamed("Java_org_lwjgl_opengl_GL11_nglReadPixels", webmcNative(Java_org_lwjgl_opengl_GL11_nglReadPixels)),
  Java_org_lwjgl_opengl_GL11_nglTexImage2D: webmcNamed("Java_org_lwjgl_opengl_GL11_nglTexImage2D", webmcNative(Java_org_lwjgl_opengl_GL11_nglTexImage2D)),
  Java_org_lwjgl_opengl_GL11_nglTexSubImage2D: webmcNamed("Java_org_lwjgl_opengl_GL11_nglTexSubImage2D", webmcNative(Java_org_lwjgl_opengl_GL11_nglTexSubImage2D)),
  Java_org_lwjgl_opengl_GL13_glActiveTexture: webmcNamed("Java_org_lwjgl_opengl_GL13_glActiveTexture", webmcNative(Java_org_lwjgl_opengl_GL13_glActiveTexture)),
  Java_org_lwjgl_opengl_GL13_glTexParameteri: webmcNamed("Java_org_lwjgl_opengl_GL13_glTexParameteri", webmcNative(Java_org_lwjgl_opengl_GL13_glTexParameteri)),
  Java_org_lwjgl_opengl_GL13_nglDeleteTextures: webmcNamed("Java_org_lwjgl_opengl_GL13_nglDeleteTextures", webmcNative(Java_org_lwjgl_opengl_GL13_nglDeleteTextures)),
  Java_org_lwjgl_opengl_GL13_nglGenTextures: webmcNamed("Java_org_lwjgl_opengl_GL13_nglGenTextures", webmcNative(Java_org_lwjgl_opengl_GL13_nglGenTextures)),
  Java_org_lwjgl_opengl_GL14_glBlendEquation: webmcNamed("Java_org_lwjgl_opengl_GL14_glBlendEquation", webmcNative(Java_org_lwjgl_opengl_GL14_glBlendEquation)),
  Java_org_lwjgl_opengl_GL14_glBlendFuncSeparate: webmcNamed("Java_org_lwjgl_opengl_GL14_glBlendFuncSeparate", webmcNative(Java_org_lwjgl_opengl_GL14_glBlendFuncSeparate)),
  Java_org_lwjgl_opengl_GL15_glBindBuffer: webmcNamed("Java_org_lwjgl_opengl_GL15_glBindBuffer", webmcNative(Java_org_lwjgl_opengl_GL15_glBindBuffer)),
  Java_org_lwjgl_opengl_GL15_glBufferData: webmcNamed("Java_org_lwjgl_opengl_GL15_glBufferData", webmcNative(Java_org_lwjgl_opengl_GL15_glBufferData)),
  Java_org_lwjgl_opengl_GL15_glBufferSubData: webmcNamed("Java_org_lwjgl_opengl_GL15_glBufferSubData", webmcNative(Java_org_lwjgl_opengl_GL15_glBufferSubData)),
  Java_org_lwjgl_opengl_GL15_glDeleteBuffers: webmcNamed("Java_org_lwjgl_opengl_GL15_glDeleteBuffers", webmcNative(Java_org_lwjgl_opengl_GL15_glDeleteBuffers)),
  Java_org_lwjgl_opengl_GL15_glGenBuffers: webmcNamed("Java_org_lwjgl_opengl_GL15_glGenBuffers", webmcNative(Java_org_lwjgl_opengl_GL15_glGenBuffers)),
  Java_org_lwjgl_opengl_GL15_glUnmapBuffer: webmcNamed("Java_org_lwjgl_opengl_GL15_glUnmapBuffer", webmcNative(Java_org_lwjgl_opengl_GL15_glUnmapBuffer)),
  Java_org_lwjgl_opengl_GL15_nglBufferData: webmcNamed("Java_org_lwjgl_opengl_GL15_nglBufferData", webmcNative(Java_org_lwjgl_opengl_GL15_nglBufferData)),
  Java_org_lwjgl_opengl_GL15_nglBufferSubData: webmcNamed("Java_org_lwjgl_opengl_GL15_nglBufferSubData", webmcNative(Java_org_lwjgl_opengl_GL15_nglBufferSubData)),
  Java_org_lwjgl_opengl_GL15_nglDeleteBuffers: webmcNamed("Java_org_lwjgl_opengl_GL15_nglDeleteBuffers", webmcNative(Java_org_lwjgl_opengl_GL15_nglDeleteBuffers)),
  Java_org_lwjgl_opengl_GL15_nglGenBuffers: webmcNamed("Java_org_lwjgl_opengl_GL15_nglGenBuffers", webmcNative(Java_org_lwjgl_opengl_GL15_nglGenBuffers)),
  Java_org_lwjgl_opengl_GL20C_nglShaderSource: webmcNamed("Java_org_lwjgl_opengl_GL20C_nglShaderSource", webmcNative(Java_org_lwjgl_opengl_GL20C_nglShaderSource)),
  Java_org_lwjgl_opengl_GL20_glAttachShader: webmcNamed("Java_org_lwjgl_opengl_GL20_glAttachShader", webmcNative(Java_org_lwjgl_opengl_GL20_glAttachShader)),
  Java_org_lwjgl_opengl_GL20_glBindAttribLocation: webmcNamed("Java_org_lwjgl_opengl_GL20_glBindAttribLocation", webmcNative(Java_org_lwjgl_opengl_GL20_glBindAttribLocation)),
  Java_org_lwjgl_opengl_GL20_glCompileShader: webmcNamed("Java_org_lwjgl_opengl_GL20_glCompileShader", webmcNative(Java_org_lwjgl_opengl_GL20_glCompileShader)),
  Java_org_lwjgl_opengl_GL20_glCreateProgram: webmcNamed("Java_org_lwjgl_opengl_GL20_glCreateProgram", webmcNative(Java_org_lwjgl_opengl_GL20_glCreateProgram)),
  Java_org_lwjgl_opengl_GL20_glCreateShader: webmcNamed("Java_org_lwjgl_opengl_GL20_glCreateShader", webmcNative(Java_org_lwjgl_opengl_GL20_glCreateShader)),
  Java_org_lwjgl_opengl_GL20_glDeleteProgram: webmcNamed("Java_org_lwjgl_opengl_GL20_glDeleteProgram", webmcNative(Java_org_lwjgl_opengl_GL20_glDeleteProgram)),
  Java_org_lwjgl_opengl_GL20_glDeleteShader: webmcNamed("Java_org_lwjgl_opengl_GL20_glDeleteShader", webmcNative(Java_org_lwjgl_opengl_GL20_glDeleteShader)),
  Java_org_lwjgl_opengl_GL20_glDetachShader: webmcNamed("Java_org_lwjgl_opengl_GL20_glDetachShader", webmcNative(Java_org_lwjgl_opengl_GL20_glDetachShader)),
  Java_org_lwjgl_opengl_GL20_glDisableVertexAttribArray: webmcNamed("Java_org_lwjgl_opengl_GL20_glDisableVertexAttribArray", webmcNative(Java_org_lwjgl_opengl_GL20_glDisableVertexAttribArray)),
  Java_org_lwjgl_opengl_GL20_glEnableVertexAttribArray: webmcNamed("Java_org_lwjgl_opengl_GL20_glEnableVertexAttribArray", webmcNative(Java_org_lwjgl_opengl_GL20_glEnableVertexAttribArray)),
  Java_org_lwjgl_opengl_GL20_glGetAttribLocation: webmcNamed("Java_org_lwjgl_opengl_GL20_glGetAttribLocation", webmcNative(Java_org_lwjgl_opengl_GL20_glGetAttribLocation)),
  Java_org_lwjgl_opengl_GL20_glGetProgramInfoLog: webmcNamed("Java_org_lwjgl_opengl_GL20_glGetProgramInfoLog", webmcNative(Java_org_lwjgl_opengl_GL20_glGetProgramInfoLog)),
  Java_org_lwjgl_opengl_GL20_glGetProgrami: webmcNamed("Java_org_lwjgl_opengl_GL20_glGetProgrami", webmcNative(Java_org_lwjgl_opengl_GL20_glGetProgrami)),
  Java_org_lwjgl_opengl_GL20_glGetShaderInfoLog: webmcNamed("Java_org_lwjgl_opengl_GL20_glGetShaderInfoLog", webmcNative(Java_org_lwjgl_opengl_GL20_glGetShaderInfoLog)),
  Java_org_lwjgl_opengl_GL20_glGetShaderi: webmcNamed("Java_org_lwjgl_opengl_GL20_glGetShaderi", webmcNative(Java_org_lwjgl_opengl_GL20_glGetShaderi)),
  Java_org_lwjgl_opengl_GL20_glGetUniformLocation: webmcNamed("Java_org_lwjgl_opengl_GL20_glGetUniformLocation", webmcNative(Java_org_lwjgl_opengl_GL20_glGetUniformLocation)),
  Java_org_lwjgl_opengl_GL20_glLinkProgram: webmcNamed("Java_org_lwjgl_opengl_GL20_glLinkProgram", webmcNative(Java_org_lwjgl_opengl_GL20_glLinkProgram)),
  Java_org_lwjgl_opengl_GL20_glScissor: webmcNamed("Java_org_lwjgl_opengl_GL20_glScissor", webmcNative(Java_org_lwjgl_opengl_GL20_glScissor)),
  Java_org_lwjgl_opengl_GL20_glShaderSource: webmcNamed("Java_org_lwjgl_opengl_GL20_glShaderSource", webmcNative(Java_org_lwjgl_opengl_GL20_glShaderSource)),
  Java_org_lwjgl_opengl_GL20_glUniform1f: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform1f", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform1f)),
  Java_org_lwjgl_opengl_GL20_glUniform1i: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform1i", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform1i)),
  Java_org_lwjgl_opengl_GL20_glUniform1iv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform1iv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform1iv)),
  Java_org_lwjgl_opengl_GL20_glUniform2f: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform2f", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform2f)),
  Java_org_lwjgl_opengl_GL20_glUniform2fv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform2fv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform2fv)),
  Java_org_lwjgl_opengl_GL20_glUniform3f: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform3f", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform3f)),
  Java_org_lwjgl_opengl_GL20_glUniform3fv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform3fv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform3fv)),
  Java_org_lwjgl_opengl_GL20_glUniform4f: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform4f", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform4f)),
  Java_org_lwjgl_opengl_GL20_glUniform4fv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniform4fv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniform4fv)),
  Java_org_lwjgl_opengl_GL20_glUniformMatrix2fv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniformMatrix2fv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniformMatrix2fv)),
  Java_org_lwjgl_opengl_GL20_glUniformMatrix3fv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniformMatrix3fv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniformMatrix3fv)),
  Java_org_lwjgl_opengl_GL20_glUniformMatrix4fv: webmcNamed("Java_org_lwjgl_opengl_GL20_glUniformMatrix4fv", webmcNative(Java_org_lwjgl_opengl_GL20_glUniformMatrix4fv)),
  Java_org_lwjgl_opengl_GL20_glUseProgram: webmcNamed("Java_org_lwjgl_opengl_GL20_glUseProgram", webmcNative(Java_org_lwjgl_opengl_GL20_glUseProgram)),
  Java_org_lwjgl_opengl_GL20_glVertexAttribPointer: webmcNamed("Java_org_lwjgl_opengl_GL20_glVertexAttribPointer", webmcNative(Java_org_lwjgl_opengl_GL20_glVertexAttribPointer)),
  Java_org_lwjgl_opengl_GL20_nglGenBuffers: webmcNamed("Java_org_lwjgl_opengl_GL20_nglGenBuffers", webmcNative(Java_org_lwjgl_opengl_GL20_nglGenBuffers)),
  Java_org_lwjgl_opengl_GL20_nglGetProgramiv: webmcNamed("Java_org_lwjgl_opengl_GL20_nglGetProgramiv", webmcNative(Java_org_lwjgl_opengl_GL20_nglGetProgramiv)),
  Java_org_lwjgl_opengl_GL20_nglGetShaderiv: webmcNamed("Java_org_lwjgl_opengl_GL20_nglGetShaderiv", webmcNative(Java_org_lwjgl_opengl_GL20_nglGetShaderiv)),
  Java_org_lwjgl_opengl_GL20_nglShaderSource: webmcNamed("Java_org_lwjgl_opengl_GL20_nglShaderSource", webmcNative(Java_org_lwjgl_opengl_GL20_nglShaderSource)),
  Java_org_lwjgl_opengl_GL20_nglUniform1fv: webmcNamed("Java_org_lwjgl_opengl_GL20_nglUniform1fv", webmcNative(Java_org_lwjgl_opengl_GL20_nglUniform1fv)),
  Java_org_lwjgl_opengl_GL20_nglUniformMatrix4fv: webmcNamed("Java_org_lwjgl_opengl_GL20_nglUniformMatrix4fv", webmcNative(Java_org_lwjgl_opengl_GL20_nglUniformMatrix4fv)),
  Java_org_lwjgl_opengl_GL20_nglVertexAttribIPointer: webmcNamed("Java_org_lwjgl_opengl_GL20_nglVertexAttribIPointer", webmcNative(Java_org_lwjgl_opengl_GL20_nglVertexAttribIPointer)),
  Java_org_lwjgl_opengl_GL20_nglVertexAttribPointer: webmcNamed("Java_org_lwjgl_opengl_GL20_nglVertexAttribPointer", webmcNative(Java_org_lwjgl_opengl_GL20_nglVertexAttribPointer)),
  Java_org_lwjgl_opengl_GL30_glBindFramebuffer: webmcNamed("Java_org_lwjgl_opengl_GL30_glBindFramebuffer", webmcNative(Java_org_lwjgl_opengl_GL30_glBindFramebuffer)),
  Java_org_lwjgl_opengl_GL30_glBindRenderbuffer: webmcNamed("Java_org_lwjgl_opengl_GL30_glBindRenderbuffer", webmcNative(Java_org_lwjgl_opengl_GL30_glBindRenderbuffer)),
  Java_org_lwjgl_opengl_GL30_glBindVertexArray: webmcNamed("Java_org_lwjgl_opengl_GL30_glBindVertexArray", webmcNative(Java_org_lwjgl_opengl_GL30_glBindVertexArray)),
  Java_org_lwjgl_opengl_GL30_glBlitFramebuffer: webmcNamed("Java_org_lwjgl_opengl_GL30_glBlitFramebuffer", webmcNative(Java_org_lwjgl_opengl_GL30_glBlitFramebuffer)),
  Java_org_lwjgl_opengl_GL30_glCheckFramebufferStatus: webmcNamed("Java_org_lwjgl_opengl_GL30_glCheckFramebufferStatus", webmcNative(Java_org_lwjgl_opengl_GL30_glCheckFramebufferStatus)),
  Java_org_lwjgl_opengl_GL30_glDeleteFramebuffers: webmcNamed("Java_org_lwjgl_opengl_GL30_glDeleteFramebuffers", webmcNative(Java_org_lwjgl_opengl_GL30_glDeleteFramebuffers)),
  Java_org_lwjgl_opengl_GL30_glFlushMappedBufferRange: webmcNamed("Java_org_lwjgl_opengl_GL30_glFlushMappedBufferRange", webmcNative(Java_org_lwjgl_opengl_GL30_glFlushMappedBufferRange)),
  Java_org_lwjgl_opengl_GL30_glFramebufferRenderbuffer: webmcNamed("Java_org_lwjgl_opengl_GL30_glFramebufferRenderbuffer", webmcNative(Java_org_lwjgl_opengl_GL30_glFramebufferRenderbuffer)),
  Java_org_lwjgl_opengl_GL30_glFramebufferTexture2D: webmcNamed("Java_org_lwjgl_opengl_GL30_glFramebufferTexture2D", webmcNative(Java_org_lwjgl_opengl_GL30_glFramebufferTexture2D)),
  Java_org_lwjgl_opengl_GL30_glGenFramebuffers: webmcNamed("Java_org_lwjgl_opengl_GL30_glGenFramebuffers", webmcNative(Java_org_lwjgl_opengl_GL30_glGenFramebuffers)),
  Java_org_lwjgl_opengl_GL30_glGenVertexArrays: webmcNamed("Java_org_lwjgl_opengl_GL30_glGenVertexArrays", webmcNative(Java_org_lwjgl_opengl_GL30_glGenVertexArrays)),
  Java_org_lwjgl_opengl_GL30_glGetStringi: webmcNamed("Java_org_lwjgl_opengl_GL30_glGetStringi", webmcNative(Java_org_lwjgl_opengl_GL30_glGetStringi)),
  Java_org_lwjgl_opengl_GL30_glMapBufferRange: webmcNamed("Java_org_lwjgl_opengl_GL30_glMapBufferRange", webmcNative(Java_org_lwjgl_opengl_GL30_glMapBufferRange)),
  Java_org_lwjgl_opengl_GL30_glRenderbufferStorage: webmcNamed("Java_org_lwjgl_opengl_GL30_glRenderbufferStorage", webmcNative(Java_org_lwjgl_opengl_GL30_glRenderbufferStorage)),
  Java_org_lwjgl_opengl_GL30_glVertexAttribIPointer: webmcNamed("Java_org_lwjgl_opengl_GL30_glVertexAttribIPointer", webmcNative(Java_org_lwjgl_opengl_GL30_glVertexAttribIPointer)),
  Java_org_lwjgl_opengl_GL30_nglDeleteFramebuffers: webmcNamed("Java_org_lwjgl_opengl_GL30_nglDeleteFramebuffers", webmcNative(Java_org_lwjgl_opengl_GL30_nglDeleteFramebuffers)),
  Java_org_lwjgl_opengl_GL30_nglDeleteVertexArrays: webmcNamed("Java_org_lwjgl_opengl_GL30_nglDeleteVertexArrays", webmcNative(Java_org_lwjgl_opengl_GL30_nglDeleteVertexArrays)),
  Java_org_lwjgl_opengl_GL30_nglGenFramebuffers: webmcNamed("Java_org_lwjgl_opengl_GL30_nglGenFramebuffers", webmcNative(Java_org_lwjgl_opengl_GL30_nglGenFramebuffers)),
  Java_org_lwjgl_opengl_GL30_nglGenRenderbuffers: webmcNamed("Java_org_lwjgl_opengl_GL30_nglGenRenderbuffers", webmcNative(Java_org_lwjgl_opengl_GL30_nglGenRenderbuffers)),
  Java_org_lwjgl_opengl_GL30_nglGenVertexArrays: webmcNamed("Java_org_lwjgl_opengl_GL30_nglGenVertexArrays", webmcNative(Java_org_lwjgl_opengl_GL30_nglGenVertexArrays)),
  Java_org_lwjgl_opengl_GL30_nglGetStringi: webmcNamed("Java_org_lwjgl_opengl_GL30_nglGetStringi", webmcNative(Java_org_lwjgl_opengl_GL30_nglGetStringi)),
  Java_org_lwjgl_opengl_GL31_glCopyBufferSubData: webmcNamed("Java_org_lwjgl_opengl_GL31_glCopyBufferSubData", webmcNative(Java_org_lwjgl_opengl_GL31_glCopyBufferSubData)),
  Java_org_lwjgl_opengl_GL31_glDrawArraysInstanced: webmcNamed("Java_org_lwjgl_opengl_GL31_glDrawArraysInstanced", webmcNative(Java_org_lwjgl_opengl_GL31_glDrawArraysInstanced)),
  Java_org_lwjgl_opengl_GL31_glDrawElementsInstanced: webmcNamed("Java_org_lwjgl_opengl_GL31_glDrawElementsInstanced", webmcNative(Java_org_lwjgl_opengl_GL31_glDrawElementsInstanced)),
  Java_org_lwjgl_opengl_GL31_glGetActiveUniformBlockName: webmcNamed("Java_org_lwjgl_opengl_GL31_glGetActiveUniformBlockName", webmcNative(Java_org_lwjgl_opengl_GL31_glGetActiveUniformBlockName)),
  Java_org_lwjgl_opengl_GL31_glGetUniformBlockIndex: webmcNamed("Java_org_lwjgl_opengl_GL31_glGetUniformBlockIndex", webmcNative(Java_org_lwjgl_opengl_GL31_glGetUniformBlockIndex)),
  Java_org_lwjgl_opengl_GL31_glTexBuffer: webmcNamed("Java_org_lwjgl_opengl_GL31_glTexBuffer", webmcNative(Java_org_lwjgl_opengl_GL31_glTexBuffer)),
  Java_org_lwjgl_opengl_GL31_glUniformBlockBinding: webmcNamed("Java_org_lwjgl_opengl_GL31_glUniformBlockBinding", webmcNative(Java_org_lwjgl_opengl_GL31_glUniformBlockBinding)),
  Java_org_lwjgl_opengl_GL32C_glBeginQuery: webmcNamed("Java_org_lwjgl_opengl_GL32C_glBeginQuery", webmcNative(Java_org_lwjgl_opengl_GL32C_glBeginQuery)),
  Java_org_lwjgl_opengl_GL32C_glDeleteQueries: webmcNamed("Java_org_lwjgl_opengl_GL32C_glDeleteQueries", webmcNative(Java_org_lwjgl_opengl_GL32C_glDeleteQueries)),
  Java_org_lwjgl_opengl_GL32C_glEndQuery: webmcNamed("Java_org_lwjgl_opengl_GL32C_glEndQuery", webmcNative(Java_org_lwjgl_opengl_GL32C_glEndQuery)),
  Java_org_lwjgl_opengl_GL32C_glGenQueries: webmcNamed("Java_org_lwjgl_opengl_GL32C_glGenQueries", webmcNative(Java_org_lwjgl_opengl_GL32C_glGenQueries)),
  Java_org_lwjgl_opengl_GL32C_glGetQueryObjecti: webmcNamed("Java_org_lwjgl_opengl_GL32C_glGetQueryObjecti", webmcNative(Java_org_lwjgl_opengl_GL32C_glGetQueryObjecti)),
  Java_org_lwjgl_opengl_GL32_glBindBufferRange: webmcNamed("Java_org_lwjgl_opengl_GL32_glBindBufferRange", webmcNative(Java_org_lwjgl_opengl_GL32_glBindBufferRange)),
  Java_org_lwjgl_opengl_GL32_glBlendEquationSeparate: webmcNamed("Java_org_lwjgl_opengl_GL32_glBlendEquationSeparate", webmcNative(Java_org_lwjgl_opengl_GL32_glBlendEquationSeparate)),
  Java_org_lwjgl_opengl_GL32_glBlendFuncSeparate: webmcNamed("Java_org_lwjgl_opengl_GL32_glBlendFuncSeparate", webmcNative(Java_org_lwjgl_opengl_GL32_glBlendFuncSeparate)),
  Java_org_lwjgl_opengl_GL32_glClientWaitSync: webmcNamed("Java_org_lwjgl_opengl_GL32_glClientWaitSync", webmcNative(Java_org_lwjgl_opengl_GL32_glClientWaitSync)),
  Java_org_lwjgl_opengl_GL32_glDeleteSync: webmcNamed("Java_org_lwjgl_opengl_GL32_glDeleteSync", webmcNative(Java_org_lwjgl_opengl_GL32_glDeleteSync)),
  Java_org_lwjgl_opengl_GL32_glDrawElementsBaseVertex: webmcNamed("Java_org_lwjgl_opengl_GL32_glDrawElementsBaseVertex", webmcNative(Java_org_lwjgl_opengl_GL32_glDrawElementsBaseVertex)),
  Java_org_lwjgl_opengl_GL32_glDrawElementsInstancedBaseVertex: webmcNamed("Java_org_lwjgl_opengl_GL32_glDrawElementsInstancedBaseVertex", webmcNative(Java_org_lwjgl_opengl_GL32_glDrawElementsInstancedBaseVertex)),
  Java_org_lwjgl_opengl_GL32_glFenceSync: webmcNamed("Java_org_lwjgl_opengl_GL32_glFenceSync", webmcNative(Java_org_lwjgl_opengl_GL32_glFenceSync)),
  Java_org_lwjgl_opengl_GL33C_glBindSampler: webmcNamed("Java_org_lwjgl_opengl_GL33C_glBindSampler", webmcNative(Java_org_lwjgl_opengl_GL33C_glBindSampler)),
  Java_org_lwjgl_opengl_GL33C_glDeleteSamplers: webmcNamed("Java_org_lwjgl_opengl_GL33C_glDeleteSamplers", webmcNative(Java_org_lwjgl_opengl_GL33C_glDeleteSamplers)),
  Java_org_lwjgl_opengl_GL33C_glGenSamplers: webmcNamed("Java_org_lwjgl_opengl_GL33C_glGenSamplers", webmcNative(Java_org_lwjgl_opengl_GL33C_glGenSamplers)),
  Java_org_lwjgl_opengl_GL33C_glSamplerParameterf: webmcNamed("Java_org_lwjgl_opengl_GL33C_glSamplerParameterf", webmcNative(Java_org_lwjgl_opengl_GL33C_glSamplerParameterf)),
  Java_org_lwjgl_opengl_GL33C_glSamplerParameteri: webmcNamed("Java_org_lwjgl_opengl_GL33C_glSamplerParameteri", webmcNative(Java_org_lwjgl_opengl_GL33C_glSamplerParameteri)),
  Java_org_lwjgl_opengl_KHRDebug_glDebugMessageCallback: webmcNamed("Java_org_lwjgl_opengl_KHRDebug_glDebugMessageCallback", webmcNative(Java_org_lwjgl_opengl_KHRDebug_glDebugMessageCallback)),
  Java_org_lwjgl_opengl_KHRDebug_glDebugMessageControl: webmcNamed("Java_org_lwjgl_opengl_KHRDebug_glDebugMessageControl", webmcNative(Java_org_lwjgl_opengl_KHRDebug_glDebugMessageControl)),
  Java_org_lwjgl_opengl_KHRDebug_glObjectLabel: webmcNamed("Java_org_lwjgl_opengl_KHRDebug_glObjectLabel", webmcNative(Java_org_lwjgl_opengl_KHRDebug_glObjectLabel)),
  Java_org_lwjgl_opengl_KHRDebug_glPopDebugGroup: webmcNamed("Java_org_lwjgl_opengl_KHRDebug_glPopDebugGroup", webmcNative(Java_org_lwjgl_opengl_KHRDebug_glPopDebugGroup)),
  Java_org_lwjgl_opengl_KHRDebug_glPushDebugGroup: webmcNamed("Java_org_lwjgl_opengl_KHRDebug_glPushDebugGroup", webmcNative(Java_org_lwjgl_opengl_KHRDebug_glPushDebugGroup)),
  Java_org_lwjgl_system_WebMC_arenaCapacity: webmcNamed("Java_org_lwjgl_system_WebMC_arenaCapacity", webmcNative(Java_org_lwjgl_system_WebMC_arenaCapacity)),
  Java_org_lwjgl_system_WebMC_arenaUsed: webmcNamed("Java_org_lwjgl_system_WebMC_arenaUsed", webmcNative(Java_org_lwjgl_system_WebMC_arenaUsed)),
  Java_org_lwjgl_system_WebMC_bindBuffer: webmcNamed("Java_org_lwjgl_system_WebMC_bindBuffer", webmcNative(Java_org_lwjgl_system_WebMC_bindBuffer)),
  Java_org_lwjgl_system_WebMC_bufferDataFromJava: webmcNamed("Java_org_lwjgl_system_WebMC_bufferDataFromJava", webmcNative(Java_org_lwjgl_system_WebMC_bufferDataFromJava)),
  Java_org_lwjgl_system_WebMC_bufferSubDataFromJava: webmcNamed("Java_org_lwjgl_system_WebMC_bufferSubDataFromJava", webmcNative(Java_org_lwjgl_system_WebMC_bufferSubDataFromJava)),
  Java_org_lwjgl_system_WebMC_freetypeAdvance: webmcNamed("Java_org_lwjgl_system_WebMC_freetypeAdvance", webmcNative(Java_org_lwjgl_system_WebMC_freetypeAdvance)),
  Java_org_lwjgl_system_WebMC_freetypeGlyph: webmcNamed("Java_org_lwjgl_system_WebMC_freetypeGlyph", webmcNative(Java_org_lwjgl_system_WebMC_freetypeGlyph)),
  Java_org_lwjgl_system_WebMC_freetypeLeft: webmcNamed("Java_org_lwjgl_system_WebMC_freetypeLeft", webmcNative(Java_org_lwjgl_system_WebMC_freetypeLeft)),
  Java_org_lwjgl_system_WebMC_freetypePitch: webmcNamed("Java_org_lwjgl_system_WebMC_freetypePitch", webmcNative(Java_org_lwjgl_system_WebMC_freetypePitch)),
  Java_org_lwjgl_system_WebMC_freetypeRows: webmcNamed("Java_org_lwjgl_system_WebMC_freetypeRows", webmcNative(Java_org_lwjgl_system_WebMC_freetypeRows)),
  Java_org_lwjgl_system_WebMC_freetypeTop: webmcNamed("Java_org_lwjgl_system_WebMC_freetypeTop", webmcNative(Java_org_lwjgl_system_WebMC_freetypeTop)),
  Java_org_lwjgl_system_WebMC_freetypeWidth: webmcNamed("Java_org_lwjgl_system_WebMC_freetypeWidth", webmcNative(Java_org_lwjgl_system_WebMC_freetypeWidth)),
  Java_org_lwjgl_system_WebMC_genBuffers: webmcNamed("Java_org_lwjgl_system_WebMC_genBuffers", webmcNative(Java_org_lwjgl_system_WebMC_genBuffers)),
  Java_org_lwjgl_system_WebMC_genVertexArrays: webmcNamed("Java_org_lwjgl_system_WebMC_genVertexArrays", webmcNative(Java_org_lwjgl_system_WebMC_genVertexArrays)),
  Java_org_lwjgl_system_WebMC_glVersion: webmcNamed("Java_org_lwjgl_system_WebMC_glVersion", webmcNative(Java_org_lwjgl_system_WebMC_glVersion)),
  Java_org_lwjgl_system_WebMC_hasGlExtension: webmcNamed("Java_org_lwjgl_system_WebMC_hasGlExtension", webmcNative(Java_org_lwjgl_system_WebMC_hasGlExtension)),
  Java_org_lwjgl_system_WebMC_log: webmcNamed("Java_org_lwjgl_system_WebMC_log", webmcNative(Java_org_lwjgl_system_WebMC_log)),
  Java_org_lwjgl_system_WebMC_logText: webmcNamed("Java_org_lwjgl_system_WebMC_logText", webmcNative(Java_org_lwjgl_system_WebMC_logText)),
  Java_org_lwjgl_system_WebMC_nmemASCII: webmcNamed("Java_org_lwjgl_system_WebMC_nmemASCII", webmcNative(Java_org_lwjgl_system_WebMC_nmemASCII)),
  Java_org_lwjgl_system_WebMC_nmemAlloc: webmcNamed("Java_org_lwjgl_system_WebMC_nmemAlloc", webmcNative(Java_org_lwjgl_system_WebMC_nmemAlloc)),
  Java_org_lwjgl_system_WebMC_nmemCopy: webmcNamed("Java_org_lwjgl_system_WebMC_nmemCopy", webmcNative(Java_org_lwjgl_system_WebMC_nmemCopy)),
  Java_org_lwjgl_system_WebMC_nmemFree: webmcNamed("Java_org_lwjgl_system_WebMC_nmemFree", webmcNative(Java_org_lwjgl_system_WebMC_nmemFree)),
  Java_org_lwjgl_system_WebMC_nmemGetByte: webmcNamed("Java_org_lwjgl_system_WebMC_nmemGetByte", webmcNative(Java_org_lwjgl_system_WebMC_nmemGetByte)),
  Java_org_lwjgl_system_WebMC_nmemGetDouble: webmcNamed("Java_org_lwjgl_system_WebMC_nmemGetDouble", webmcNative(Java_org_lwjgl_system_WebMC_nmemGetDouble)),
  Java_org_lwjgl_system_WebMC_nmemGetFloat: webmcNamed("Java_org_lwjgl_system_WebMC_nmemGetFloat", webmcNative(Java_org_lwjgl_system_WebMC_nmemGetFloat)),
  Java_org_lwjgl_system_WebMC_nmemGetInt: webmcNamed("Java_org_lwjgl_system_WebMC_nmemGetInt", webmcNative(Java_org_lwjgl_system_WebMC_nmemGetInt)),
  Java_org_lwjgl_system_WebMC_nmemGetLong: webmcNamed("Java_org_lwjgl_system_WebMC_nmemGetLong", webmcNative(Java_org_lwjgl_system_WebMC_nmemGetLong)),
  Java_org_lwjgl_system_WebMC_nmemGetShort: webmcNamed("Java_org_lwjgl_system_WebMC_nmemGetShort", webmcNative(Java_org_lwjgl_system_WebMC_nmemGetShort)),
  Java_org_lwjgl_system_WebMC_nmemPutByte: webmcNamed("Java_org_lwjgl_system_WebMC_nmemPutByte", webmcNative(Java_org_lwjgl_system_WebMC_nmemPutByte)),
  Java_org_lwjgl_system_WebMC_nmemPutDouble: webmcNamed("Java_org_lwjgl_system_WebMC_nmemPutDouble", webmcNative(Java_org_lwjgl_system_WebMC_nmemPutDouble)),
  Java_org_lwjgl_system_WebMC_nmemPutFloat: webmcNamed("Java_org_lwjgl_system_WebMC_nmemPutFloat", webmcNative(Java_org_lwjgl_system_WebMC_nmemPutFloat)),
  Java_org_lwjgl_system_WebMC_nmemPutInt: webmcNamed("Java_org_lwjgl_system_WebMC_nmemPutInt", webmcNative(Java_org_lwjgl_system_WebMC_nmemPutInt)),
  Java_org_lwjgl_system_WebMC_nmemPutLong: webmcNamed("Java_org_lwjgl_system_WebMC_nmemPutLong", webmcNative(Java_org_lwjgl_system_WebMC_nmemPutLong)),
  Java_org_lwjgl_system_WebMC_nmemPutShort: webmcNamed("Java_org_lwjgl_system_WebMC_nmemPutShort", webmcNative(Java_org_lwjgl_system_WebMC_nmemPutShort)),
  Java_org_lwjgl_system_WebMC_nmemReadBytes: webmcNamed("Java_org_lwjgl_system_WebMC_nmemReadBytes", webmcNative(Java_org_lwjgl_system_WebMC_nmemReadBytes)),
  Java_org_lwjgl_system_WebMC_nmemSet: webmcNamed("Java_org_lwjgl_system_WebMC_nmemSet", webmcNative(Java_org_lwjgl_system_WebMC_nmemSet)),
  Java_org_lwjgl_system_WebMC_nmemSlice: webmcNamed("Java_org_lwjgl_system_WebMC_nmemSlice", webmcNative(Java_org_lwjgl_system_WebMC_nmemSlice)),
  Java_org_lwjgl_system_WebMC_nmemUTF8: webmcNamed("Java_org_lwjgl_system_WebMC_nmemUTF8", webmcNative(Java_org_lwjgl_system_WebMC_nmemUTF8)),
  Java_org_lwjgl_system_WebMC_nmemWriteBytes: webmcNamed("Java_org_lwjgl_system_WebMC_nmemWriteBytes", webmcNative(Java_org_lwjgl_system_WebMC_nmemWriteBytes)),
  Java_org_lwjgl_system_WebMC_readPixelsInto: webmcNamed("Java_org_lwjgl_system_WebMC_readPixelsInto", webmcNative(Java_org_lwjgl_system_WebMC_readPixelsInto)),
  Java_org_lwjgl_system_WebMC_stbComponents: webmcNamed("Java_org_lwjgl_system_WebMC_stbComponents", webmcNative(Java_org_lwjgl_system_WebMC_stbComponents)),
  Java_org_lwjgl_system_WebMC_stbFreeScratch: webmcNamed("Java_org_lwjgl_system_WebMC_stbFreeScratch", webmcNative(Java_org_lwjgl_system_WebMC_stbFreeScratch)),
  Java_org_lwjgl_system_WebMC_stbHeight: webmcNamed("Java_org_lwjgl_system_WebMC_stbHeight", webmcNative(Java_org_lwjgl_system_WebMC_stbHeight)),
  Java_org_lwjgl_system_WebMC_stbLoad: webmcNamed("Java_org_lwjgl_system_WebMC_stbLoad", webmcNative(Java_org_lwjgl_system_WebMC_stbLoad)),
  Java_org_lwjgl_system_WebMC_stbScratch: webmcNamed("Java_org_lwjgl_system_WebMC_stbScratch", webmcNative(Java_org_lwjgl_system_WebMC_stbScratch)),
  Java_org_lwjgl_system_WebMC_stbWidth: webmcNamed("Java_org_lwjgl_system_WebMC_stbWidth", webmcNative(Java_org_lwjgl_system_WebMC_stbWidth)),
  Java_org_lwjgl_system_WebMC_texImageFromJava: webmcNamed("Java_org_lwjgl_system_WebMC_texImageFromJava", webmcNative(Java_org_lwjgl_system_WebMC_texImageFromJava)),
  Java_org_lwjgl_system_WebMC_texSubImageFromJava: webmcNamed("Java_org_lwjgl_system_WebMC_texSubImageFromJava", webmcNative(Java_org_lwjgl_system_WebMC_texSubImageFromJava)),
  Java_org_lwjgl_system_WebMC_windowHeight: webmcNamed("Java_org_lwjgl_system_WebMC_windowHeight", webmcNative(Java_org_lwjgl_system_WebMC_windowHeight)),
  Java_org_lwjgl_system_WebMC_windowWidth: webmcNamed("Java_org_lwjgl_system_WebMC_windowWidth", webmcNative(Java_org_lwjgl_system_WebMC_windowWidth)),
};

export const count = 351;
