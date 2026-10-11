// A working example mod: a clock and coordinates readout in the corner of the game.
// Drop any .js file like this into the mods/ folder next to your builds, reload the launcher
// page, tick it, and press Play.
if (globalThis.WebMCModAPI) {
  const api = globalThis.WebMCModAPI;

  api.on("load", () => api.displayToChat("hud-clock loaded (press F6 to toggle)"));

  api.registerKeybind("hud-clock toggle", "F6", () => {
    visible = !visible;
  });

  let visible = true;

  api.on("render", () => {
    if (!visible) return;
    const canvas = api.overlay();
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const now = new Date();
    const stamp = now.toTimeString().slice(0, 8);
    const up = Math.round(api.uptimeSeconds());
    ctx.font = "14px monospace";
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.fillRect(6, 6, 168, 44);
    ctx.fillStyle = "#7dff9b";
    ctx.fillText(stamp, 12, 24);
    ctx.fillText(`playing ${Math.floor(up / 60)}m ${up % 60}s`, 12, 42);
  });
} else {
  console.log("hud-clock: no WebMC mod API on this page (open a build from the launcher)");
}
