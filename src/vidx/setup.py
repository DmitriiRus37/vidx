"""vidx setup [--dry-run] [--gpu auto|cuda|directml|cpu] [--no-models]

Per platform: checks the tools vidx runs (ffmpeg, ffprobe, deno, uv) and prints the command that installs a
missing one (it never runs a package manager: they need sudo or an elevated prompt, and differ by distro);
creates the environments with uv; compiles the Swift helpers (macOS); downloads the Russian Vosk model and
warms the model caches; reports the device each engine will use. Re-running is safe: existing venvs are kept
(delete one to rebuild it), packages are re-checked, and after a GPU change (or --gpu) the torch and onnxruntime
builds are replaced.

  darwin       uv tools mlx-whisper, parakeet-mlx, yt-dlp; .venv; .venv-giga (MPS); Swift ocr + apple_asr
  linux/win32  uv tool yt-dlp; .venv; .venv-giga; .venv-whisper (faster-whisper, RapidOCR); .venv-onnx (onnx-asr)

GPU: an NVIDIA GPU (nvidia-smi) selects the CUDA builds of torch (cu126) and onnxruntime-gpu (CUDA 12); on
Windows, DirectML for parakeet only with --gpu directml (a VM may have no DirectX 12 GPU).
"""
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from vidx import install, jobs, paths
from vidx.log import Fatal, log, warn

NEEDED = ("ffmpeg", "ffprobe", "deno", "uv")
GPU_CHOICES = ("auto", "cuda", "directml", "cpu")
PROBE_TIMEOUT_S = 120  # a device probe imports torch / onnxruntime / ctranslate2: slow the first time, not stuck


@dataclass
class Step:
    kind: str  # tool | venv | pip | swift | vosk | warm
    argv: list
    unless: str = ""  # skip when this path exists (venvs are kept)
    what: str = ""
    env: dict = field(default_factory=dict)


def nvidia_gpus():
    """Names of the NVIDIA GPUs the driver sees ([] without nvidia-smi)."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [x.strip() for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else []


class Plan:
    """Everything setup would do on a platform, as data (so --dry-run and the tests see it)."""

    def __init__(self, platform, home, which=shutil.which, gpus=None, gpu="auto", env=None, registry=None,
                 models=True):
        self.platform, self.home, self.which, self.env = platform, home, which, os.environ if env is None else env
        self.gpus = nvidia_gpus() if gpus is None else gpus
        self.missing = [t for t in NEEDED if not which(t)]
        cuda = gpu == "cuda" or (gpu == "auto" and bool(self.gpus))
        self.torch = "cuda" if cuda and platform != "darwin" else "cpu"
        self.onnx = "cuda" if cuda else "directml" if gpu == "directml" and platform == "win32" else "cpu"
        from vidx.engines import Registry

        self.registry = registry or Registry(platform, home)
        self.steps = self.make_steps(models)

    def venv(self, name):
        return os.path.join(self.home, name)

    def python(self, name):
        return paths.venv_python(self.venv(name), self.platform)

    def version(self, var, default):
        return self.env.get(var) or default

    def install_commands(self):
        """Commands that install the missing tools on this platform."""
        need = set(self.missing)
        if not need:
            return []
        ffmpeg = bool(need & {"ffmpeg", "ffprobe"})
        if self.platform == "darwin":
            return ["brew install " + " ".join([*(["ffmpeg"] if ffmpeg else []), *(t for t in ("deno", "uv")
                                                                                     if t in need)])]
        if self.platform == "win32":
            ids = {"ffmpeg": "Gyan.FFmpeg", "deno": "DenoLand.Deno", "uv": "astral-sh.uv"}
            return [f"winget install --id {ids[t]} -e" for t in ("ffmpeg", "deno", "uv")
                    if (t == "ffmpeg" and ffmpeg) or t in need]
        cmds = []
        if ffmpeg:
            if self.which("apt-get"):
                cmds.append("sudo apt install ffmpeg")
            elif self.which("dnf"):
                cmds.append("sudo dnf install ffmpeg-free  # full ffmpeg: RPM Fusion")
            else:
                cmds.append("install ffmpeg (with ffprobe) with your package manager")
        if "deno" in need:
            cmds.append("curl -fsSL https://deno.land/install.sh | sh")
        if "uv" in need:
            cmds.append("curl -LsSf https://astral.sh/uv/install.sh | sh")
        return cmds

    def make_steps(self, models):
        s, darwin = [], self.platform == "darwin"
        for pkg in install.TOOLS if darwin else install.PORTABLE_TOOLS:
            s.append(Step("tool", ["uv", "tool", "install", "-q", "--upgrade", pkg]))
        py = self.version("VIDX_PYTHON", install.PY_VERSION_DEFAULT)
        py_giga = self.version("VIDX_PYTHON_GIGA", install.PY_GIGA_VERSION_DEFAULT)
        py_eng = self.version("VIDX_PYTHON_ENGINES", install.PY_ENGINES_VERSION_DEFAULT)

        def env(name, version, *installs):
            s.append(Step("venv", ["uv", "venv", "-q", "--python", version, self.venv(name)], unless=self.python(name)))
            for pkgs, index in installs:
                index_args = ["--index-url", index] if index else []
                s.append(Step("pip", ["uv", "pip", "install", "-q", "--python", self.venv(name), *index_args, *pkgs]))

        env(".venv", py, (install.HELPER_PKGS, None))
        if darwin:
            env(".venv-giga", py_giga, (install.TORCH, None), (install.GIGA_PKGS, None))
        else:
            env(".venv-giga", py_giga, (install.torch_pins(self.torch), install.TORCH_INDEX[self.torch]),
                (install.GIGA_PKGS, None))
            env(".venv-whisper", py_eng, (install.WHISPER_PKGS, None))
            # another GPU, another onnxruntime: the other flavours go first, and this one is reinstalled, as
            # removing them also removed the files of the onnxruntime module they shared with it
            ort = install.ONNXRUNTIME_PACKAGES[self.onnx]
            others = [p for p in install.ONNXRUNTIME_PACKAGES.values() if p != ort]
            env(".venv-onnx", py_eng)
            s.append(Step("pip", ["uv", "pip", "uninstall", "-q", "--python", self.venv(".venv-onnx"), *others]))
            s.append(Step("pip", ["uv", "pip", "install", "-q", "--python", self.venv(".venv-onnx"),
                                  "--reinstall-package", ort, *install.ONNX_PKGS, install.ONNXRUNTIME[self.onnx]]))
        if darwin:
            s += [Step("swift", ["ocr"]), Step("swift", ["apple_asr"])]
        if models:
            s.append(Step("vosk", ["vosk-model-ru-0.42"]))
            s += self.warm_steps()
        return s

    def warm_steps(self):
        """Download every model once: run each engine on 1 s of silence (<tmp> is made at run time)."""
        reg, wav, t = self.registry, os.path.join("<tmp>", "s.wav"), "<tmp>"
        s = [Step("warm", ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1",
                           wav])]
        for name in ("whisper", "turbo"):
            e = reg.get(name)
            s.append(Step("warm", e.argv(wav, "--model", e.model, "-o", t, "--verbose", "False"), what=name))
        s.append(Step("warm", reg.lid_argv("lid"), what="language ID"))
        p = reg.get("parakeet")
        s.append(Step("warm", p.argv(wav, "--model", p.model, "--output-dir", t), what="parakeet"))
        giga = isolated(self.python(".venv-giga"), paths.LIB / "gigaam_warm.py", reg.get("gigaam").model)
        s.append(Step("warm", giga, what="GigaAM"))
        if self.platform != "darwin":
            o, png = reg.get("ocr"), os.path.join(t, "blank.png")
            s.append(Step("warm", ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=white:s=64x64",
                                   "-frames:v", "1", png]))
            s.append(Step("warm", o.argv("ru-RU,en-US;zh-Hans,en-US", png), what="RapidOCR", env=o.env))
        return s

    def device_probes(self):
        """(engine, argv printing its device) for the venvs of this platform: lib/device.py in each venv."""
        def probe(venv, engine):
            return isolated(self.python(venv), paths.LIB / "device.py", engine)

        if self.platform == "darwin":
            return [("GigaAM", probe(".venv-giga", "gigaam"))]
        return [("whisper, turbo, language ID", probe(".venv-whisper", "whisper")),
                ("parakeet", probe(".venv-onnx", "parakeet")),
                ("GigaAM", probe(".venv-giga", "gigaam"))]


def isolated(python, script, *args):
    """A lib script run by path in an engine venv, isolated: -I (no PYTHON* variables, no user site, neither the
    current directory nor the script's on sys.path: the script adds lib/ itself) and -X utf8, as -I also ignores the
    PYTHONUTF8=1 of vidx's children, and report_devices reads the output as UTF-8."""
    return [python, "-I", "-X", "utf8", str(script), *map(str, args)]


def show(step, platform=None):
    """A command as the user would type it in this platform's shell."""
    argv = [str(a) for a in step.argv]
    return subprocess.list2cmdline(argv) if (platform or paths.platform_of()) == "win32" else shlex.join(argv)


def parse(args):
    opts = {"dry_run": False, "gpu": "auto", "models": True}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry-run":
            opts["dry_run"] = True
        elif a == "--no-models":
            opts["models"] = False
        elif a == "--gpu":
            if i + 1 >= len(args) or args[i + 1] not in GPU_CHOICES:
                raise Fatal("--gpu must be auto, cuda, directml or cpu")
            opts["gpu"] = args[i + 1]
            i += 1
        else:
            raise Fatal(f"unknown setup option {a} (see: vidx help setup)")
        i += 1
    return opts


def run(args=(), rt=None):
    from vidx.pipeline import Runtime

    opts = parse(list(args))
    rt = rt or Runtime()
    plan = Plan(rt.platform, rt.home, gpu=opts["gpu"], registry=rt.registry, models=opts["models"])
    print(f"platform: {rt.platform}, home: {rt.home}")
    if rt.platform == "darwin":
        print("GPU: Apple (MLX engines; GigaAM on MPS)")
    else:
        print("NVIDIA GPU: " + (", ".join(plan.gpus) if plan.gpus else "none")
              + f"; torch: {plan.torch}; onnxruntime: {plan.onnx}")
    if plan.missing:
        print(f"missing: {', '.join(plan.missing)}; install with:")
        for c in plan.install_commands():
            print(f"  {c}")
        if not opts["dry_run"]:
            raise Fatal("install the missing tools, then run vidx setup again")
    if opts["dry_run"]:
        for step in plan.steps:
            if step.kind == "swift":
                print(f"+ swiftc -O -o {os.path.join(rt.registry.bin, step.argv[0])} {step.argv[0]}.swift")
            elif step.kind == "vosk":
                print(f"+ download Vosk model {step.argv[0]} into {rt.registry.models}")
            else:
                kept = f"   (only if {step.unless} is missing)" if step.unless else ""
                print(f"+ {show(step, rt.platform)}{kept}")
        print("devices: reported after the environments exist")
        return 0
    execute(plan, rt)
    report_devices(plan)
    log("setup done")
    return 0


def argv_of(step, tmp):
    """The command of a step as it runs now, or None to skip it: a working venv is kept, one whose interpreter
    is gone (e.g. its uv-managed Python was removed) is rebuilt with --clear."""
    argv = [str(a).replace("<tmp>", tmp) for a in step.argv]
    if step.kind == "venv":
        if os.path.exists(step.unless):
            return None
        if os.path.isdir(argv[-1]):
            warn(f"{argv[-1]}: its Python is missing, rebuilding it")
            return [*argv, "--clear"]
    return argv


def execute(plan, rt):
    ctx = jobs.Context()
    with tempfile.TemporaryDirectory() as tmp:
        for step in plan.steps:
            if step.kind == "swift":
                if step.argv[0] == "ocr":
                    log("compiling Swift helpers (Vision OCR, SpeechTranscriber)")
                rt.build_swift(step.argv[0])
                continue
            if step.kind == "vosk":
                rt.fetch_vosk(step.argv[0])
                continue
            argv = argv_of(step, tmp)
            if argv is None:
                continue
            if step.kind == "warm" and step.what:
                log(f"warming the model cache: {step.what}")
            log(show(Step(step.kind, argv)))
            if ctx.call(argv, env=step.env) != 0:
                raise Fatal(f"setup failed: {show(Step(step.kind, argv))}")


def report_devices(plan):
    for name, argv in plan.device_probes():
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=PROBE_TIMEOUT_S)
            why = (r.stderr.strip().splitlines() or ["?"])[-1]
            dev = r.stdout.strip() if r.returncode == 0 else f"unknown ({why})"
        except subprocess.TimeoutExpired:  # killed by run(); the report and the other probes go on
            dev = f"unknown (timed out after {PROBE_TIMEOUT_S} s)"
        except OSError as e:
            dev = f"unknown ({e})"
        print(f"devices: {name}: {dev}")
    if not plan.gpus and plan.platform != "darwin":
        warn("no NVIDIA GPU found: the engines run on the CPU (slower; see README for CUDA)")
