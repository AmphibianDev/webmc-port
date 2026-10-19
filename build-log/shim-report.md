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
  "opengl.GL11.glReadPixels threw: offset is out of bounds\nRangeError: offset is out of bounds\n    at Uint8Array.set (<anonymous>)\n    at Java_org_lwjgl_opengl_GL11_glReadPixels_inner (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:847:6)\n    at http://127.0.0.1:8777/natives/webmc-lwjgl3.js:1531:146\n    at guarded (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:191:12)\n    at Java_org_lwjgl_opengl_GL11_glReadPixels (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:1531:104)\n    at Java_org_lwjgl_opengl_GL11_glReadPixels (http://127.0.0.1:8777/natives/webmc-lwjgl3.js:158:12)\n    at eval (eval at Pd (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:62446), <anonymous>:3:84)\n    at FT (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:504815)\n    at pp (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:76280)\n    at Object.kX [as a0] (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:8797)\n    at Og (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:74611)\n    at MessagePort.OL (https://cjrtnc.leaningtech.com/4.3/cj3.js:1:16020)",
  "  pixel at the centre: r=0 g=0 b=0",
  "BOOT FAILED: java.lang.IllegalStateException: the readback was empty: nothing was rendered",
  "    at webmc.BootTest.main(Unknown Source)"
 ],
 "frames": 1,
 "seconds": 2.9,
 "gl": {
  "version": "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
  "renderer": "WebKit WebGL"
 },
 "arena": 16,
 "callbackError": null,
 "log": [
  "[trace 1] Java_org_lwjgl_opengl_GL20_glCreateShader",
  "  vertex shader is 1\n",
  "[trace 5] Java_org_lwjgl_system_WebMC_log",
  "[java]   vertex shader is 1",
  "  setting its source\n",
  "[trace 6] Java_org_lwjgl_system_WebMC_log",
  "[java]   setting its source",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glShaderSource",
  "  compiling it\n",
  "[trace 7] Java_org_lwjgl_system_WebMC_log",
  "[java]   compiling it",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glCompileShader",
  "  compiled; asking for the status\n",
  "[trace 8] Java_org_lwjgl_system_WebMC_log",
  "[java]   compiled; asking for the status",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glGetShaderi",
  "  status is 1\n",
  "[trace 9] Java_org_lwjgl_system_WebMC_log",
  "[java]   status is 1",
  "  creating the fragment shader\n",
  "[trace 10] Java_org_lwjgl_system_WebMC_log",
  "[java]   creating the fragment shader",
  "[trace 2] Java_org_lwjgl_opengl_GL20_glCreateShader",
  "[trace 2] Java_org_lwjgl_opengl_GL20_glShaderSource",
  "  compiling the fragment shader\n",
  "[trace 11] Java_org_lwjgl_system_WebMC_log",
  "[java]   compiling the fragment shader",
  "[trace 2] Java_org_lwjgl_opengl_GL20_glCompileShader",
  "[trace 2] Java_org_lwjgl_opengl_GL20_glGetShaderi",
  "  fragment status is 1\n",
  "[trace 12] Java_org_lwjgl_system_WebMC_log",
  "[java]   fragment status is 1",
  "  creating the program\n",
  "[trace 13] Java_org_lwjgl_system_WebMC_log",
  "[java]   creating the program",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glCreateProgram",
  "  program is 1\n",
  "[trace 14] Java_org_lwjgl_system_WebMC_log",
  "[java]   program is 1",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glAttachShader",
  "[trace 2] Java_org_lwjgl_opengl_GL20_glAttachShader",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glBindAttribLocation",
  "  linking\n",
  "[trace 15] Java_org_lwjgl_system_WebMC_log",
  "[java]   linking",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glLinkProgram",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glGetProgrami",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glUseProgram",
  "  shaders compiled and linked\n",
  "[trace 16] Java_org_lwjgl_system_WebMC_log",
  "[java]   shaders compiled and linked",
  "[trace 1] Java_org_lwjgl_opengl_GL30_nglGenVertexArrays",
  "[trace 1] Java_org_lwjgl_opengl_GL30_glBindVertexArray",
  "[trace 1] Java_org_lwjgl_opengl_GL15_nglGenBuffers",
  "[trace 1] Java_org_lwjgl_opengl_GL15_glBindBuffer",
  "[trace 1] Java_org_lwjgl_opengl_GL15_nglBufferData",
  "[trace 1] Java_org_lwjgl_opengl_GL20_nglVertexAttribPointer",
  "[trace 1] Java_org_lwjgl_opengl_GL20_glEnableVertexAttribArray",
  "  geometry uploaded\n",
  "[trace 17] Java_org_lwjgl_system_WebMC_log",
  "[java]   geometry uploaded",
  "[trace 1] Java_org_lwjgl_opengl_GL11_glClearColor",
  "[trace 1] Java_org_lwjgl_opengl_GL11_glClear",
  "[trace 1] Java_org_lwjgl_opengl_GL11_nglDrawArrays",
  "  triangle drawn\n",
  "[trace 18] Java_org_lwjgl_system_WebMC_log",
  "[java]   triangle drawn",
  "[trace 1] Java_org_lwjgl_glfw_GLFW_glfwSwapBuffers",
  "[trace 1] Java_org_lwjgl_glfw_GLFW_glfwPollEvents",
  "[trace 1] Java_org_lwjgl_opengl_GL11_glReadPixels",
  "  pixel at the centre: r=0 g=0 b=0\n",
  "[trace 19] Java_org_lwjgl_system_WebMC_log",
  "[java]   pixel at the centre: r=0 g=0 b=0",
  "BOOT FAILED: java.lang.IllegalStateException: the readback was empty: nothing was rendered\n",
  "java.lang.IllegalStateException: the readback was empty: nothing was rendered\n",
  "\tat webmc.BootTest.main(Unknown Source)\n",
  "[trace 20] Java_org_lwjgl_system_WebMC_log",
  "[java] BOOT FAILED: java.lang.IllegalStateException: the readback was empty: nothing was rendered",
  "[trace 21] Java_org_lwjgl_system_WebMC_log",
  "[java]     at webmc.BootTest.main(Unknown Source)"
 ]
}
```

## the browser's own log, last 200 lines
```
[2472:2498:1019/135355.216681:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2498:1019/135355.416258:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2498:1019/135355.416291:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2498:1019/135355.463639:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2498:1019/135355.527902:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135355.933885:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2498:1019/135355.933967:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135355.934056:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2498:1019/135355.934097:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135355.934548:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2472:1019/135355.944708:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2498:1019/135355.945769:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135356.036395:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2472:1019/135356.042677:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2498:1019/135356.043780:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135356.048093:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2472:1019/135356.105714:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2472:1019/135356.105761:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.Properties.GetAll: object_path= /org/freedesktop/UPower/devices/DisplayDevice: org.freedesktop.DBus.Error.ServiceUnknown: The name org.freedesktop.UPower was not provided by any .service files
[2472:2498:1019/135356.105968:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135356.148275:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2498:1019/135356.148352:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address: Unknown address type (examples of valid types are "tcp" and on UNIX "unix")
[2472:2472:1019/135356.216727:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: 
[2472:2500:1019/135358.174502:ERROR:google_apis/gcm/engine/registration_request.cc:291] Registration response error message: PHONE_REGISTRATION_ERROR
[2472:2500:1019/135358.186097:ERROR:google_apis/gcm/engine/registration_request.cc:291] Registration response error message: PHONE_REGISTRATION_ERROR
[2472:2500:1019/135358.207555:ERROR:google_apis/gcm/engine/registration_request.cc:291] Registration response error message: PHONE_REGISTRATION_ERROR
```
