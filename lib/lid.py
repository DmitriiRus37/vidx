"""Spoken-language identification of one audio clip: the only place that knows which backend does it.

Backend today: whisper (large-v3-turbo) through mlx-whisper on Apple Silicon; runs in mlx-whisper's own
Python. A clip is padded to whisper's 30 s window, encoded once, and the decoder's first token gives a
probability for every language whisper knows (no transcription, so it is fast). Another platform swaps
in another backend here (e.g. faster-whisper's detect_language) without touching the callers.

    detect = load("mlx-community/whisper-large-v3-turbo")
    detect(clip)  # float32 16 kHz mono samples → {"en": 0.93, "de": 0.04, ...}
"""


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
