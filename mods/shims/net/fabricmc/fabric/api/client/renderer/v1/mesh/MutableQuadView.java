package net.fabricmc.fabric.api.client.renderer.v1.mesh;

import net.minecraft.client.renderer.chunk.ChunkSectionLayer;

/**
 * A quad a mod may change, as Fabric API's renderer API declares it.
 *
 * The three members here are the ones a merged mod calls (measured with `apisurface.py` against
 * Meteor Client: `chunkLayer`, `color(int)` and `color(int, int)`). The renderer integration behind
 * them - so that a call actually changes what is drawn - is a named gap: this exists so the client
 * compiles and boots, and the moment a mod leans on it, the gap shows up as behaviour, not as a
 * mystery.
 */
public interface MutableQuadView extends QuadView {
    MutableQuadView chunkLayer(ChunkSectionLayer layer);

    MutableQuadView color(int color);

    MutableQuadView color(int index, int color);
}
