"""vidx setup: tools, environments, models (macOS for now; Linux and Windows come with the step that
adds their backends)."""
import os
import shutil
import tempfile

from vidx import install, jobs, paths
from vidx.log import Fatal, log
from vidx.pipeline import Runtime


def run(rt=None):
    rt = rt or Runtime()
    reg, ctx = rt.registry, jobs.Context()

    def sh(*argv):
        if ctx.call(list(argv)) != 0:
            raise Fatal(f"setup failed: {' '.join(map(str, argv))}")

    if not shutil.which("brew"):
        raise Fatal("Homebrew is required")
    for tool in ("ffmpeg", "deno", "uv"):  # deno: yt-dlp needs a JS runtime for YouTube
        if not shutil.which(tool):
            sh("brew", "install", tool)
    for pkg in install.TOOLS:
        sh("uv", "tool", "install", "-q", "--upgrade", pkg)
    py_version = os.environ.get("VIDX_PYTHON") or install.PY_VERSION_DEFAULT
    giga_version = os.environ.get("VIDX_PYTHON_GIGA") or install.PY_GIGA_VERSION_DEFAULT
    for venv, version, pkgs in ((".venv", py_version, install.HELPER_PKGS),
                                (".venv-giga", giga_version, install.GIGA_PKGS)):
        path = os.path.join(rt.home, venv)
        if not os.path.exists(paths.venv_python(path, rt.platform)):
            sh("uv", "venv", "-q", "--python", version, path)
        sh(*install.uv_pip(path), *pkgs)
    log("compiling Swift helpers (Vision OCR, SpeechTranscriber)")
    rt.build_swift("ocr")
    rt.build_swift("apple_asr")
    from vidx.pipeline import languages_module

    rt.fetch_vosk(languages_module().get("ru").vosk_model)
    log("warming model caches (whisper large-v3, turbo, parakeet, GigaAM)")
    with tempfile.TemporaryDirectory() as t:
        wav = os.path.join(t, "s.wav")
        sh("ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1", wav)
        for name in ("whisper", "turbo"):
            e = reg.get(name)
            sh(*e.argv(wav, "--model", e.model, "-o", t, "--verbose", "False"))
        sh(*reg.lid_argv("lid"))  # the language-ID model (whisper turbo today, cached above)
        p = reg.get("parakeet")
        sh(*p.argv(wav, "--model", p.model, "--output-dir", t))
        sh(paths.venv_python(os.path.join(rt.home, ".venv-giga"), rt.platform), "-I", "-c",
           'import gigaam; gigaam.load_model("v3_e2e_rnnt", device="cpu")')
    log("setup done")
    return 0
