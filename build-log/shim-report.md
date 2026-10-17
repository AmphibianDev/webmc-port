# what the boot test reported
```
{
 "code": 1,
 "javaLog": [
  "  glfwInit",
  "  glfwCreateWindow",
  "WebGL context: WebGL 2.0 (OpenGL ES 3.0 Chromium) / WebKit WebGL lost=false canvas=320x240 maxVertexAttribs=16",
  "  context current, version 3.3 WebMC",
  "  creating the vertex shader",
  "createShader(type=35633, typeof=number)",
  "createShader -> handle 1",
  "  vertex shader is 1",
  "  setting its source",
  "shaderSource: 64 characters",
  "  compiling it",
  "compileShader: compiling",
  "compileShader: done",
  "  compiled; asking for the status",
  "getShaderi: asking for 35713",
  "getShaderi(35713) -> 1",
  "  status is 1",
  "  creating the fragment shader",
  "createShader(type=35632, typeof=number)",
  "createShader -> handle 2",
  "shaderSource: 81 characters",
  "  compiling the fragment shader",
  "compileShader: compiling",
  "compileShader: done",
  "getShaderi: asking for 35713",
  "getShaderi(35713) -> 1",
  "  fragment status is 1",
  "  creating the program",
  "  program is 1",
  "  linking",
  "  shaders compiled and linked",
  "  geometry uploaded",
  "  triangle drawn",
  "opengl.GL11.glReadPixels threw: offset is out of bounds\nRangeError: offset is out of bounds\n    at Uint8Array.set (<anonymous>)\n    at Java_org_lwjgl_opengl_GL11_glReadPixels_inner (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:811:6)\n    at http://127.0.0.1:8777/natives/webmc-lwjgl3.js:1488:146\n    at guarded (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:155:12)\n    at Java_org_lwjgl_opengl_GL11_glReadPixels (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:1488:104)\n    at Java_org_lwjgl_opengl_GL11_glReadPixels (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:132:12)\n    at eval (eval at Pd (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:62446), <anonymous>:3:84)\n    at FT (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:504815)\n    at pp (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:76280)\n    at Object.kX [as a0] (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:8797)\n    at Og (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:74611)\n    at MessagePort.OL (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:16020)",
  "  pixel at the centre: r=0 g=0 b=0",
  "BOOT FAILED: java.lang.IllegalStateException: the readback was empty: nothing was rendered",
  "    at webmc.BootTest.main(Unknown Source)"
 ],
 "frames": 1,
 "seconds": 2.8,
 "gl": {
  "version": "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
  "renderer": "WebKit WebGL"
 },
 "arena": 16,
 "callbackError": null,
 "log": [
  "registered 237 natives",
  "app.jar over HTTP: 200",
  "  glfwInit\n",
  "[java]   glfwInit",
  "  glfwCreateWindow\n",
  "[java]   glfwCreateWindow",
  "  context current, version 3.3 WebMC\n",
  "[java]   context current, version 3.3 WebMC",
  "  creating the vertex shader\n",
  "[java]   creating the vertex shader",
  "  vertex shader is 1\n",
  "[java]   vertex shader is 1",
  "  setting its source\n",
  "[java]   setting its source",
  "  compiling it\n",
  "[java]   compiling it",
  "  compiled; asking for the status\n",
  "[java]   compiled; asking for the status",
  "  status is 1\n",
  "[java]   status is 1",
  "  creating the fragment shader\n",
  "[java]   creating the fragment shader",
  "  compiling the fragment shader\n",
  "[java]   compiling the fragment shader",
  "  fragment status is 1\n",
  "[java]   fragment status is 1",
  "  creating the program\n",
  "[java]   creating the program",
  "  program is 1\n",
  "[java]   program is 1",
  "  linking\n",
  "[java]   linking",
  "  shaders compiled and linked\n",
  "[java]   shaders compiled and linked",
  "  geometry uploaded\n",
  "[java]   geometry uploaded",
  "  triangle drawn\n",
  "[java]   triangle drawn",
  "  pixel at the centre: r=0 g=0 b=0\n",
  "[java]   pixel at the centre: r=0 g=0 b=0",
  "BOOT FAILED: java.lang.IllegalStateException: the readback was empty: nothing was rendered\n",
  "java.lang.IllegalStateException: the readback was empty: nothing was rendered\n",
  "\tat webmc.BootTest.main(Unknown Source)\n",
  "[java] BOOT FAILED: java.lang.IllegalStateException: the readback was empty: nothing was rendered",
  "[java]     at webmc.BootTest.main(Unknown Source)"
 ]
}
```

## the browser's own log, last 200 lines
```
[2248:2275:1017/154918.585692:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2275:1017/154920.253491:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2275:1017/154920.253521:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2275:1017/154920.554305:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2275:1017/154920.981432:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154923.733422:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2275:1017/154923.733495:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154923.733580:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2275:1017/154923.733620:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154923.733932:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2248:1017/154923.781088:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2275:1017/154923.814250:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154924.153442:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2248:1017/154924.172629:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2275:1017/154924.172930:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154924.178686:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2248:1017/154924.284980:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2248:1017/154924.285032:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.Properties.GetAll: object_path= /org/freedesktop/UPower/devices/DisplayDevice: org.freedesktop.DBus.Error.ServiceUnknown: The name org.freedesktop.UPower was not provided by any .service files
[2248:2275:1017/154924.285052:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154924.380138:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2275:1017/154924.380209:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2248:2248:1017/154924.431582:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2248:2277:1017/154927.813284:ERROR:google_apis/gcm/engine/registration_request.cc:291] Registration response error message: PHONE_REGISTRATION_ERROR
[2248:2277:1017/154927.825064:ERROR:google_apis/gcm/engine/registration_request.cc:291] Registration response error message: PHONE_REGISTRATION_ERROR
[2248:2277:1017/154927.829097:ERROR:google_apis/gcm/engine/registration_request.cc:291] Registration response error message: PHONE_REGISTRATION_ERROR
```
