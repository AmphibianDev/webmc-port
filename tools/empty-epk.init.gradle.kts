// Writes the audio archives the single-file packager insists on, without shipping any audio.
//
// The engine's own skeleton manifest is explicit about this input:
//
//   "target_teavm/build/web/sounds.epk from a separately authorized, hash-pinned source"
//
// i.e. sounds.epk is the official Minecraft audio, which this project has no permission to
// redistribute and cannot rebuild from source. The packager reads it unconditionally (missing
// file = hard failure), and the client has an in-game "Get game audio" button that fetches the
// real audio at play time, so what a build of ours needs is a *valid but empty* archive.
//
// Rather than hand-rolling those bytes - the format is a header, a compressed object stream, CRCs
// and a trailer - this reuses the engine's own EPKPackagerMain, which writes the archive with its
// own EPKCompiler and then self-verifies it with EPKDecompiler. An empty input directory produces
// an archive with zero entries, which is exactly what we want and is checked by their code, not
// ours.
//
// Usage (from inside the engine checkout):
//   ./gradlew -I <this file> :target_lwjgl_desktop:packEmptySounds :target_lwjgl_desktop:packEmptyMusic
gradle.projectsEvaluated {
    val engine = gradle.rootProject.project(":target_lwjgl_desktop")
    val template = engine.tasks.named("buildAssetsEPK", org.gradle.api.tasks.JavaExec::class.java).get()
    val emptyInput = File(System.getProperty("webmc.epk.emptyDir") ?: "/tmp/webmc-empty-epk")
    val webDir = File(System.getProperty("webmc.epk.webDir")
        ?: File(engine.projectDir.parentFile, "target_teavm/build/web").absolutePath)

    fun pack(taskName: String, archive: String) {
        val output = File(webDir, archive)
        engine.tasks.register(taskName, org.gradle.api.tasks.JavaExec::class.java) {
            group = "webmc"
            description = "Packs an empty $archive (audio is fetched at play time, never redistributed)"
            dependsOn(engine.tasks.named("classes"))
            classpath = template.classpath
            mainClass.set(template.mainClass.get())
            emptyInput.mkdirs()
            output.parentFile.mkdirs()
            // --include-web-unreachable is what lets this work at all: EPKPackagerMain otherwise
            // asserts that the input tree is the *assets* tree (it insists on finding all the
            // web-unreachable realm/icon PNGs so it can exclude them) and throws "Expected
            // web-unreachable assets are missing from source tree" for any other directory. The
            // flag says "this is not the assets tree, skip that expectation", which is true here.
            args(emptyInput.absolutePath, output.absolutePath, archive, "--include-web-unreachable")
        }
    }

    pack("packEmptySounds", "sounds.epk")
    pack("packEmptyMusic", "music.epk")
}
