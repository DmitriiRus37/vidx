"""Spoken-language identification of one audio clip: the only code that knows which backend does it.

Backend today: whisper (large-v3-turbo) through mlx-whisper on Apple Silicon; runs in mlx-whisper's own
Python. A clip is padded to whisper's 30 s window, encoded once, and the decoder's first token gives a
probability for every language whisper knows (no transcription, so it is fast).

    detect = load("mlx-community/whisper-large-v3-turbo")
    detect(clip)  # float32 16 kHz mono samples (numpy) → {"en": 0.93, "de": 0.04, ...}

usage: lid.py <model>   loads the model once (vidx setup: downloads and caches it)

Porting to another backend (e.g. faster-whisper's detect_language on Linux/Windows) means changing exactly:
  1. load() here: same signature, detect(clip) returns {whisper language code: probability}; the codes
     outside a run's list may be missing, the values need not sum to 1 (regions.restrict renormalises);
  2. vidx: LID_MODEL (the model id) and lid_py (the interpreter that runs lib/<script>.py <model> ...).
detect_lang.py and regions.py stay as they are; the tests fake lid.load (tests/test_regions.py,
tests/test_detect_lang.py) and the real tests get both from vidx (tests/conftest.py, lid_backend).
"""
import sys


def load(model):
    """→ detect(clip) function for the given whisper model."""
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
