# Sourced by vidx when VIDX_FAKE_ENGINES=1 (tests only). Replaces every function that runs a model,
# downloads or compiles with tests/fakes/engine.py, which writes canned output. The rest of vidx
# (options, output dir, stamps, job queue, merge) runs for real. Environment:
#   VIDX_TEST_PYTHON   Python with numpy + pillow for frames.py / merge.py (default python3)
#   VIDX_FAKE_LOG      append one JSON line per fake call: engine, args, start, end
#   VIDX_FAKE_DELAY    seconds each fake engine takes (default 0)
#   VIDX_FAKE_FAIL     comma list of fake engines that fail (whisper,turbo,parakeet,gigaam,...)
#   VIDX_FAKE_LANG     what the fake language detection prints (default ru)
#   VIDX_FAKE_DATA     dir with canned <output stem>.json / regions.json to return instead of stubs
#   VIDX_FAKE_APPLE_LOCALES  locales the fake apple_asr --locales lists
PY=${VIDX_TEST_PYTHON:-python3}
FAKE_ENGINE="$VIDX_DIR/tests/fakes/engine.py"
fake() { "$PY" -I "$FAKE_ENGINE" "$@"; }
mlx_whisper() { fake whisper "$@"; }            # the mlx_whisper CLI, called by whisper()
parakeet-mlx() { fake parakeet "$@"; }          # the parakeet-mlx CLI, called by parakeet()
mlx_py() { local s; s=$(basename "$1" .py); shift; fake "$s" "$@"; }  # detect_lang.py, regions.py
gigaam() { fake gigaam "$@"; }
vosk() { fake vosk "$@"; }
apple_asr() { fake apple "$@"; }
ocr() { fake ocr "$@"; }
build_swift() { return 0; }
fetch_vosk() { [[ -n $(vosk_model "$1") ]]; }   # models "exist" for every language that has one
