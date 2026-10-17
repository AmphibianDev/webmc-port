package net.fabricmc.fabric.api.client.renderer.v1.mesh;

/**
 * A read-only view of a quad, as Fabric API's renderer API declares it.
 *
 * Only the members the merged mods call are declared: a shim is not a reimplementation, and adding
 * 30 methods nobody calls would be 30 more ways for the compile to fail on the next Minecraft
 * version. Everything a mod actually uses is here, with the real signature - and anything beyond it
 * is a named gap rather than a silent difference.
 */
public interface QuadView {
}
