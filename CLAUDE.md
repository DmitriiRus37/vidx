# vidx — notes for Claude

Local CLI that extracts pictures and text from a video file or a yt-dlp URL: several speech engines,
OCR, and a merge into one transcript. README.md describes the algorithm step by step; read it before
changing behaviour. README_RUS.md is a full Russian translation.

## Commands

```bash
uv run --isolated --group test pytest                            # unit tests (~1 min, no models, no network)
uv run --isolated --group test --python 3.11 pytest              # oldest supported Python
HF_HUB_OFFLINE=1 uv run --isolated --group test pytest -m real   # real models (macOS, after `vidx setup`)
uvx ruff check src/ tests/
/bin/bash -n vidx                                                # must parse under macOS bash 3.2
VIDX_UPDATE_GOLDENS=1 uv run --isolated --group test pytest      # rewrite goldens after a deliberate change
```

Always `--isolated`: without it uv installs into `.venv`, which is vidx's own helper environment.

## Layout

- `vidx` — bash entry point: options, download, audio, language, job queue, setup. Bash 3.2 compatible.
- `lib/languages.py` — the one table of per-language settings (script, Apple locale, OCR languages,
  Vosk model, parakeet support, joiner, fillers, thresholds). New language → one row here + tests.
- `lib/lid.py` — spoken-language ID of a clip; the only backend-specific module for language ID
  (swap point for a faster-whisper port, together with `LID_MODEL` / `lid_py` in `vidx`).
- `lib/regions.py`, `lib/detect_lang.py` — mixed-speech regions, `--lang auto` voting.
- `lib/merge.py` — aligned.md / transcript.md / transcript.txt / ocr.md; `md()` escapes outside text.
- `lib/*.swift` — Apple-only helpers (`ocr`, `apple_asr`), compiled by `vidx setup`.
- `tests/fakes/` — fake engines (`VIDX_FAKE_ENGINES=1`); `tests/fixtures/`, `tests/golden/`.
- `docs/plans/` — implementation plans (AS IS / TO BE steps). The Linux/Windows port is planned there.

## Rules

- **TDD**: failing test first, then the smallest change, then refactor. Unit tests must not need models
  or network; use the fake engines and fixtures. Real-model tests get the `real` marker.
- **One plan step = one commit**, message `<area>: <what>` plus a short why. Don't batch unrelated work.
- **Public repo**: never commit employer/workplace content (internal system names, meeting text,
  people's names, emails, local paths) — not in code, fixtures, goldens, test strings or commit
  messages. Fixtures are generated with macOS `say` and neutral topics (weather, recipes). Check the
  whole branch history, not only the tip, before it is pushed.
- **Never push or open PRs without asking** the owner. Never change the repo-local git identity.
- **No big downloads in tests** (Vosk models are 0.3–4.4 GB; Apple speech models download per locale).
- Keep **both READMEs in sync** (same sections, same tables) and `vidx help` texts in sync with behaviour.
- Platforms: macOS today; Linux and Windows are planned as equal platforms — avoid new macOS-only code
  outside the existing backend boundaries.
- Python 3.11–3.14 for `lib/` and tests (GigaAM venv is 3.13: onnxruntime 1.23 has no 3.14 build).
- Keep the security properties: `python -I` for `-c` snippets, the `.vidx/` marker before writing into
  `-o`, option stamps per step, `--ignore-config --no-playlist` for yt-dlp, escaped Markdown for outside
  text, Ctrl-C kills child process trees.
