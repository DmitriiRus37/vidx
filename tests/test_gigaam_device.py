"""lib/gigaam_run.py picks its device: CUDA (Linux/Windows with NVIDIA), MPS (Apple), else CPU."""
import types

import gigaam_run
import pytest


def torch_stub(cuda, mps):
    return types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: cuda),
                                 backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)))


@pytest.mark.parametrize("cuda, mps, want", [(True, False, "cuda"), (True, True, "cuda"), (False, True, "mps"),
                                             (False, False, "cpu")])
def test_device_order(cuda, mps, want, monkeypatch):
    monkeypatch.delenv("VIDX_GIGAAM_DEVICE", raising=False)
    assert gigaam_run.device(torch_stub(cuda, mps)) == want


def test_device_override(monkeypatch):
    monkeypatch.setenv("VIDX_GIGAAM_DEVICE", "cpu")
    assert gigaam_run.device(torch_stub(True, True)) == "cpu"


def test_old_torch_without_mps(monkeypatch):
    monkeypatch.delenv("VIDX_GIGAAM_DEVICE", raising=False)
    no_cuda = types.SimpleNamespace(is_available=lambda: False)
    old = types.SimpleNamespace(cuda=no_cuda, backends=types.SimpleNamespace())
    assert gigaam_run.device(old) == "cpu"
