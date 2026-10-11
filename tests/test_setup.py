"""vidx setup per OS (vidx.setup): tool checks with install commands, uv environments, models, devices.
The plan is computed for a simulated platform; --dry-run prints it."""
from pathlib import PureWindowsPath

import pytest
from helpers import run_vidx

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
