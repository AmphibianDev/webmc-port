package com.example;

import webmc.loader.WebMcApi;
import webmc.loader.WebMcMod;

/**
 * The smallest mod that does something visible, used to prove the loader's own mod door end to end.
 *
 * It touches no Minecraft class on purpose. That keeps it compilable anywhere (a JDK is the only
 * requirement, no game jar, no Fabric), which is what makes it a test fixture as well as an example:
 * if this mod's lines appear, then the loader found the jar, read webmc.mod.json, built a class
 * loader that can see the game, and called a class from that loader.
 */
public final class HelloMod implements WebMcMod {

    public void onLoad(final WebMcApi api) {
        api.log("Hello from " + api.gameVersion() + " (" + api.mode() + " mode)");
        api.set("hello-webmc.greeted", "yes");

        // A heartbeat, so a mod can do something repeated without hooking the game's tick loop. It
        // runs on the loader's clock thread - see the note on WebMcApi.every about which thread that
        // is, and why a callback that hopped threads silently would be worse than this.
        api.every(5000, new Runnable() {
            private int beats = 0;

            public void run() {
                beats++;
                api.log("heartbeat " + beats + " (loader clock " + api.ticks() + ")");
                if (beats >= 3) {
                    api.stop();
                }
            }
        });
    }
}
