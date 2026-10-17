"""vidx setup per OS (vidx.setup): tool checks with install commands, uv environments, models, devices.
The plan is computed for a simulated platform; --dry-run prints it."""
import os
import subprocess
import sys
import types
from pathlib import Path, PureWindowsPath

import pytest
from helpers import LIB, run_vidx

from vidx import setup


def plan(platform, missing=(), gpus=(), gpu="auto", home="/h"):
    tools = {t: None if t in missing else f"/usr/bin/{t}" for t in ("ffmpeg", "ffprobe", "deno", "uv", "apt-get")}
    return setup.Plan(platform, home, which=tools.get, gpus=list(gpus), gpu=gpu, env={})


def commands(p):
    return [" ".join(map(str, s.argv)) for s in p.steps]


@pytest.mark.parametrize("platform, want", [
    ("darwin", ["brew install ffmpeg deno uv"]),
    ("linux", ["sudo apt install ffmpeg", "curl -fsSL https://deno.land/install.sh | sh",
               "curl -LsSf https://astral.sh/uv/install.sh | sh"]),
    ("win32", ["winget install --id Gyan.FFmpeg -e", "winget install --id DenoLand.Deno -e",
               "winget install --id astral-sh.uv -e"]),
])
def test_missing_tools_get_install_commands(platform, want):
    p = plan(platform, missing=("ffmpeg", "ffprobe", "deno", "uv"))
    assert p.missing == ["ffmpeg", "ffprobe", "deno", "uv"] and p.install_commands() == want


def test_fedora_gets_dnf():
    tools = {"dnf": "/usr/bin/dnf"}
    p = setup.Plan("linux", "/h", which=tools.get, gpus=[], gpu="auto", env={})
    assert p.install_commands()[0] == "sudo dnf install ffmpeg-free  # full ffmpeg: RPM Fusion"


def test_nothing_missing():
    assert plan("linux").missing == [] and plan("linux").install_commands() == []


def test_darwin_keeps_mlx_tools_and_swift():
    cmds = commands(plan("darwin"))
    assert "uv tool install -q --upgrade mlx-whisper==0.4.3" in cmds
    assert "uv tool install -q --upgrade parakeet-mlx==0.5.3" in cmds
    assert not any(".venv-whisper" in c or ".venv-onnx" in c for c in cmds)
    assert [s.kind for s in plan("darwin").steps].count("swift") == 2


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_portable_envs(platform):
    cmds = commands(plan(platform))
    assert any("yt-dlp[default]" in c for c in cmds) and not any("mlx" in c for c in cmds)
    whisper = next(c for c in cmds if "faster-whisper==" in c)
    assert ".venv-whisper" in whisper and "rapidocr[rtl]==3.10.0" in whisper and "onnxruntime==" in whisper
    onnx = next(c for c in cmds if "onnx-asr" in c)
    assert ".venv-onnx" in onnx and "onnxruntime==" in onnx  # no GPU: the CPU flavour
    assert not [s for s in plan(platform).steps if s.kind == "swift"]


def test_cuda_flavours():
    cmds = commands(plan("linux", gpus=["NVIDIA RTX 4090"]))
    assert any("download.pytorch.org/whl/cu126" in c and "torch==2.14.1" in c for c in cmds)
    assert any("onnxruntime-gpu>=1.20,<1.27" in c for c in cmds)  # CUDA 12, as CTranslate2
    cmds = commands(plan("linux"))
    assert any("download.pytorch.org/whl/cpu" in c and "torch==2.14.1" in c for c in cmds)


def test_windows_directml_only_when_asked():
    assert any("onnxruntime==" in c for c in commands(plan("win32")))  # a VM may have no DirectX 12 GPU
    assert any("onnxruntime-directml==" in c for c in commands(plan("win32", gpu="directml")))
    assert any("download.pytorch.org/whl/cu126" in c for c in commands(plan("win32", gpus=["NVIDIA T4"])))


def test_torch_installed_before_and_apart_from_gigaam():
    """torch comes from PyTorch's index alone; GigaAM and the rest from PyPI: no index mixing."""
    cmds = commands(plan("linux"))
    torch = next(i for i, c in enumerate(cmds) if "torch==2.14.1" in c)
    giga = next(i for i, c in enumerate(cmds) if "GigaAM/archive/" in c)
    assert torch < giga and "--index-url" in cmds[torch] and "--index-url" not in cmds[giga]
    assert "unsafe-best-match" not in " ".join(cmds)


def test_windows_paths():
    p = plan("win32", home=r"C:\vidx")
    venvs = [s.argv[-1] for s in p.steps if s.kind == "venv"]
    assert PureWindowsPath(venvs[0]) == PureWindowsPath(r"C:\vidx\.venv")
    assert PureWindowsPath(p.python(".venv-whisper")) == PureWindowsPath(r"C:\vidx\.venv-whisper\Scripts\python.exe")


def test_helper_venv_has_psutil():
    assert any("psutil==" in c and ".venv " in c + " " for c in commands(plan("linux")))


def test_dry_run_cli(tmp_path):
    r = run_vidx("setup", "--dry-run", tmp_path=tmp_path, env={"VIDX_FAKE_PLATFORM": "win32"})
    assert r.returncode == 0, r.stderr
    assert "platform: win32" in r.stdout and "+ uv venv" in r.stdout and ".venv-onnx" in r.stdout
    assert "devices:" in r.stdout


def test_setup_options_refused(tmp_path):
    r = run_vidx("setup", "--frobnicate", tmp_path=tmp_path)
    assert r.returncode == 1 and "unknown setup option --frobnicate" in r.stderr
    r = run_vidx("setup", "--gpu", "tpu", tmp_path=tmp_path)
    assert r.returncode == 1 and "--gpu must be auto, cuda, directml or cpu" in r.stderr


def test_existing_venv_kept_broken_one_rebuilt(tmp_path):
    """A venv whose interpreter is gone (its uv-managed Python was removed) is rebuilt with --clear; a working
    one is kept, as before."""
    p = plan("linux", home=str(tmp_path))
    step = next(s for s in p.steps if s.kind == "venv" and s.argv[-1].endswith(".venv"))
    assert setup.argv_of(step, "/t") == step.argv  # nothing there yet
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    assert setup.argv_of(step, "/t") == [*step.argv, "--clear"]  # broken: no interpreter
    (tmp_path / ".venv" / "bin" / "python").write_text("", encoding="utf-8", newline="\n")
    assert setup.argv_of(step, "/t") is None  # kept


def test_tmp_placeholder():
    step = setup.Step("warm", ["ffmpeg", "-i", "x", "<tmp>/s.wav"])
    assert setup.argv_of(step, "/t") == ["ffmpeg", "-i", "x", "/t/s.wav"]


def test_dry_run_on_macos_names_apple_gpu(tmp_path):
    r = run_vidx("setup", "--dry-run", tmp_path=tmp_path, env={"VIDX_FAKE_PLATFORM": "darwin"})
    assert r.returncode == 0, r.stderr
    assert "GPU: Apple (MLX engines; GigaAM on MPS)" in r.stdout and "NVIDIA" not in r.stdout


def test_device_report_reads_the_probes_as_utf8(tmp_path, capsys):
    """A probe's output (a GPU name, the last line of an error) is read as UTF-8, which the probe must then print
    whatever the locale: Windows' code page or a Latin-1 locale would give its own bytes or stop it."""

    ok, broken = tmp_path / "ok.py", tmp_path / "broken.py"
    ok.write_text("print('caf\\xe9 \\u2192 \\u0412\\u0438\\u0434\\u0435\\u043e')\n", encoding="utf-8", newline="\n")
    broken.write_text("import sys\nsys.exit('\\u043d\\u0435\\u0442 CUDA: caf\\xe9')\n", encoding="utf-8", newline="\n")
    probes = [("ok", setup.isolated(sys.executable, ok)), ("broken", setup.isolated(sys.executable, broken))]
    setup.report_devices(types.SimpleNamespace(device_probes=lambda: probes, gpus=["x"], platform="linux"))
    assert capsys.readouterr().out == "devices: ok: café → Видео\ndevices: broken: unknown (нет CUDA: café)\n"


def test_a_probe_that_times_out_is_reported_and_the_rest_still_run(tmp_path, monkeypatch, capsys):
    """A probe stuck past its timeout (a driver that hangs, a first import that compiles for minutes) must not stop
    vidx setup with a TimeoutExpired: its device is reported as unknown and the next probe runs."""

    slow = tmp_path / "slow_probe.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8", newline="\n")
    fast = tmp_path / "fast_probe.py"
    fast.write_text("print('cpu')\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(setup, "PROBE_TIMEOUT_S", 1)
    probes = [("slow", [sys.executable, "-I", str(slow)]), ("fast", [sys.executable, "-I", str(fast)])]
    setup.report_devices(types.SimpleNamespace(device_probes=lambda: probes, gpus=["x"], platform="linux"))
    assert capsys.readouterr().out == "devices: slow: unknown (timed out after 1 s)\ndevices: fast: cpu\n"


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_probes_and_warm_steps_run_lib_scripts_by_path(platform):
    """CLAUDE.md: no `python -c <code>` in the orchestrator. The device probes and the GigaAM warm-up are lib scripts
    run by path, isolated (-I) and in UTF-8 mode (-X utf8: -I ignores the PYTHONUTF8=1 of vidx's children)."""
    p = plan(platform)
    runs = [list(map(str, argv)) for _, argv in p.device_probes()] + [list(map(str, s.argv)) for s in p.steps]
    assert not [argv for argv in runs if "-c" in argv]
    lib_runs = [argv for argv in runs if "-I" in argv]
    for argv in lib_runs:
        assert argv[1:4] == ["-I", "-X", "utf8"] and Path(argv[4]).parent == LIB, argv
        assert Path(argv[4]).is_file(), argv
    scripts = sorted(Path(argv[4]).name for argv in lib_runs)
    assert scripts == ["device.py"] * len(p.device_probes()) + ["gigaam_warm.py"]


def test_device_script_names_a_device_from_any_directory(tmp_path):
    """lib/device.py run as setup runs it (-I: neither the cwd nor its own directory on sys.path) imports its
    siblings itself. VIDX_WHISPER_DEVICE stands in for CTranslate2, which the test environment does not have."""
    r = subprocess.run(setup.isolated(sys.executable, LIB / "device.py", "whisper"), cwd=tmp_path, capture_output=True,
                       text=True, encoding="utf-8", env={**os.environ, "VIDX_WHISPER_DEVICE": "cpu"}, timeout=60)
    assert (r.returncode, r.stdout) == (0, "cpu int8\n"), r.stderr


def fake_uv(steps, venvs):
    """What `uv pip` does to the packages of each venv ({venv: {name: version}}), as far as flavours go: a pin
    without a local version (`torch==2.14.1`) is satisfied by any installed local build (`2.14.1+cpu`); a missing
    package comes from the index (PyTorch's: a `+cpu` / `+cu126` build); --reinstall-package installs anew."""
    for s in steps:
        a = list(map(str, s.argv))
        if a[:3] == ["uv", "pip", "uninstall"]:
            venv = a[a.index("--python") + 1]
            for name in a[a.index("--python") + 2:]:
                venvs.setdefault(venv, {}).pop(name, None)
        elif a[:3] == ["uv", "pip", "install"]:
            venv = a[a.index("--python") + 1]
            index = a[a.index("--index-url") + 1] if "--index-url" in a else ""
            again = {a[i + 1] for i, x in enumerate(a) if x == "--reinstall-package"}
            for req in (x for x in a[3:] if "==" in x or ">=" in x):
                name, _, version = req.partition("==") if "==" in req else (req.split(">=")[0], "", "1.26.0")
                have = venvs.setdefault(venv, {}).get(name)
                if have and name not in again and ("+" in version and have == version
                                                   or "+" not in version and have.split("+")[0] == version):
                    continue
                local = index.rsplit("/", 1)[-1] if "download.pytorch.org" in index else ""
                venvs[venv][name] = version if "+" in version or not local else f"{version}+{local}"


def test_a_gpu_change_replaces_torch_and_onnxruntime(tmp_path):
    """vidx setup after an NVIDIA GPU appeared (or went): `torch==2.14.1` was satisfied by the 2.14.1+cpu already
    there, so torch stayed a CPU build; and onnxruntime-gpu was installed next to onnxruntime in one venv (both
    own the onnxruntime module). The builds are now pinned with their local version, and the other onnxruntime
    flavours are removed first."""
    venvs = {}
    fake_uv(plan("linux", home=str(tmp_path)).steps, venvs)
    onnx, giga = os.path.join(str(tmp_path), ".venv-onnx"), os.path.join(str(tmp_path), ".venv-giga")
    assert venvs[giga]["torch"] == "2.14.1+cpu" and venvs[giga]["torchaudio"] == "2.11.0+cpu"
    fake_uv(plan("linux", home=str(tmp_path), gpus=["NVIDIA T4"]).steps, venvs)
    assert venvs[giga]["torch"] == "2.14.1+cu126" and venvs[giga]["torchaudio"] == "2.11.0+cu126"
    assert [p for p in venvs[onnx] if p.startswith("onnxruntime")] == ["onnxruntime-gpu"]
    fake_uv(plan("win32", home=str(tmp_path), gpu="directml").steps, venvs)
    assert [p for p in venvs[onnx] if p.startswith("onnxruntime")] == ["onnxruntime-directml"]
    fake_uv(plan("linux", home=str(tmp_path)).steps, venvs)
    assert venvs[giga]["torch"] == "2.14.1+cpu"
    assert [p for p in venvs[onnx] if p.startswith("onnxruntime")] == ["onnxruntime"]


def test_macos_torch_has_no_local_version():
    cmds = commands(plan("darwin"))
    assert any("torch==2.14.1 " in c + " " for c in cmds) and not any("+cpu" in c for c in cmds)


class FakeRuntime:
    def __init__(self, swift=True, vosk=True):
        self.platform, self.home, self.swift, self.vosk = "darwin", "/h", swift, vosk
        self.registry = types.SimpleNamespace(bin="/h/bin", models="/h/models")

    def build_swift(self, name):
        return self.swift

    def fetch_vosk(self, name):
        return self.vosk


@pytest.mark.parametrize("rt, why", [(FakeRuntime(swift=False), "could not compile the Swift helper ocr"),
                                     (FakeRuntime(vosk=False), "could not download the Vosk model vosk-model-ru-0.42")])
def test_setup_fails_when_a_helper_or_model_is_missing(monkeypatch, capsys, rt, why):
    """A failed Swift build or Vosk download was ignored: vidx setup said "setup done" and exited 0, and the runs
    then lacked OCR or Vosk."""
    from vidx import cli, pipeline

    steps = [setup.Step("swift", ["ocr"]), setup.Step("vosk", ["vosk-model-ru-0.42"])]
    fake_plan = types.SimpleNamespace(steps=steps, missing=[], gpus=[], device_probes=lambda: [], platform="darwin")
    monkeypatch.setattr(setup, "Plan", lambda *a, **k: fake_plan)
    monkeypatch.setattr(pipeline, "Runtime", lambda: rt)
    monkeypatch.setattr(setup.jobs, "install_interrupt_handlers", lambda: None)
    assert cli.main(["setup"]) == 1
    out, err = capsys.readouterr()
    assert why in err and "setup done" not in err + out
