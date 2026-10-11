"""Spoken-language identification of one audio clip: the only code that knows which backend does it.

Backend: whisper turbo, chosen by the interpreter that runs this file (vidx.engines: the language-ID entry):
  - mlx-whisper (macOS, mlx-whisper's own Python): the clip is padded to whisper's 30 s window, encoded once,
    and the decoder's first token gives a probability for every language whisper knows;
  - faster-whisper (Linux, Windows: .venv-whisper): WhisperModel.detect_language on the clip, one 30 s window.
No transcription, so it is fast.

    detect = load("mlx-community/whisper-large-v3-turbo")   # or "large-v3-turbo" with faster-whisper
    detect(clip)  # float32 16 kHz mono samples (numpy) → {"en": 0.93, "de": 0.04, ...}

usage: lid.py <model>   loads the model once (vidx setup: downloads and caches it)

Another backend means changing exactly: load() here (same signature; detect(clip) returns {whisper language
code: probability}, codes outside a run's list may be missing, values need not sum to 1, regions.restrict
renormalises) and the language-ID entry of vidx.engines (model id and interpreter). detect_lang.py and
regions.py stay as they are; the tests fake lid.load (tests/test_regions.py, tests/test_detect_lang.py) and the
real tests get the backend from the registry (tests/conftest.py, lid_backend).
"""
import importlib.util
import sys


def load(model):
    """→ detect(clip) function for the given whisper model, on the backend this interpreter has."""
    return load_mlx(model) if importlib.util.find_spec("mlx_whisper") else load_faster_whisper(model)


def load_faster_whisper(model):
    from faster_whisper import WhisperModel
    from whisper_fw import devices

    device, compute = devices()[0]
    try:
        m = WhisperModel(model, device=device, compute_type=compute)
    except RuntimeError:  # CUDA libraries missing: the CPU always works
        m = WhisperModel(model, device="cpu", compute_type="int8")

    def detect(clip):
        _, _, probs = m.detect_language(clip, language_detection_segments=1)
        return dict(probs)

    return detect


def load_mlx(model):
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.load_models import load_model

    m = load_model(model, dtype=mx.float16)

    def detect(clip):
        mel = log_mel_spectrogram(clip, n_mels=m.dims.n_mels, padding=N_SAMPLES)
        _, probs = m.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))
        return dict(probs[0] if isinstance(probs, list) else probs)

    return detect


if __name__ == "__main__":
    load(sys.argv[1])
