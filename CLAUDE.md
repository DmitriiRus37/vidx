# vidx — notes for Claude

Local CLI that extracts pictures and text from a video file or a yt-dlp URL: several speech engines,
OCR, and a merge into one transcript, on macOS, Linux and Windows. README.md describes the algorithm step
by step; read it before changing behaviour. README_RUS.md is a full Russian translation.

## Commands

```bash
uv run --isolated --group test pytest                            # unit + CLI tests (~1.5 min, no models, no network)
uv run --isolated --group test --python 3.11 pytest              # oldest supported Python
HF_HUB_OFFLINE=1 uv run --isolated --group test pytest -m real   # real models (after `vidx setup`)
uvx ruff check src/ tests/
sh -n vidx                                                       # the POSIX launcher must parse
uvx --from actionlint-py actionlint .github/workflows/*.yml      # after changing CI
VIDX_UPDATE_GOLDENS=1 uv run --isolated --group test pytest      # rewrite goldens after a deliberate change
```

Always `--isolated`: without it uv installs into `.venv`, which is vidx's own helper environment.
`uv run` installs the package from `src/` into the test environment, so tests run `python -m vidx`.

## Layout

- `vidx` — POSIX `sh` launcher for a checkout (macOS/Linux): `python -P -m vidx` with `src/` on the path.
  Installed copies and Windows use the `vidx` console script (`uv tool install`).
- `src/vidx/` — the orchestrator (standard library + psutil): `cli.py` (commands, exit status),
  `options.py` (run options, the bash loop's messages), `pipeline.py` (the run), `engines.py` (registry:
  one program per engine and platform, same arguments everywhere), `jobs.py` (queue, process groups,
  Ctrl-C), `state.py` (output dir, stamps), `setup.py` / `install.py` (environments, pins), `paths.py`
  (platform, vidx home, venv interpreters), `texts.py` (help), `log.py`.
- `src/vidx/lib/` — standalone scripts run by the engines' interpreters (they import siblings as
  top-level modules): `languages.py` (the one table of per-language settings; new language → one row +
  tests), `lid.py` (language ID: mlx-whisper or faster-whisper by interpreter), `regions.py`,
  `detect_lang.py`, `merge.py` (`md()` escapes outside text), `frames.py`, `find.py`, engine runners
  (`gigaam_run.py`, `vosk_run.py`, `whisper_fw.py`, `parakeet_onnx.py`, `ocr_rapid.py`) and the Apple-only
  Swift helpers (`ocr.swift`, `apple_asr.swift`, compiled by `vidx setup` into `<home>/bin`).
- `tests/fakes/` — fake engines (`VIDX_FAKE_ENGINES=1` loads `hook.py`; `VIDX_FAKE_PLATFORM` simulates
  darwin/linux/win32); `tests/fixtures/`, `tests/golden/`.
- `.github/workflows/` — `ci.yml` (3 OS × Python 3.11/3.14, fake engines), `smoke.yml` (real engines, small
  models, Linux/Windows).
- `docs/plans/` — plans (AS IS / TO BE steps) and `2026-10-11-porting-analysis.md` (platform decisions,
  the Windows validation checklist).

## Rules

- **TDD**: failing test first, then the smallest change, then refactor. Unit tests must not need models
  or network; use the fake engines and fixtures. Real-model tests get the `real` marker.
- **One plan step = one commit**, message `<area>: <what>` plus a short why. Don't batch unrelated work.
- **Public repo**: never commit employer/workplace content (internal system names, meeting text,
  people's names, emails, local paths) — not in code, fixtures, goldens, test strings or commit
  messages. Fixtures are generated with macOS `say` and neutral topics (weather, recipes). Check the
  whole branch history, not only the tip, before it is pushed.
- **Never push or open PRs without asking** the owner. Never change the repo-local git identity.
- **No big downloads in tests** (Vosk models are 0.3–4.4 GB; Apple speech models download per locale;
  whisper large-v3 is 3 GB: real tests on Linux/Windows use `tiny`).
- Keep **both READMEs in sync** (same sections, same tables) and `vidx help` texts in sync with behaviour.
- **Three equal platforms**: platform differences live only in `engines.py` (which program), `setup.py`
  (how to install), `paths.py` (where) and the engine runners; the pipeline and the output files are the
  same everywhere. An engine runner takes the macOS tool's command line and writes its JSON keys.
- **Windows**: every text `open()` names `encoding="utf-8"` and writes `newline="\n"` (a test checks);
  no symlinks required, no shell; process groups via `jobs.spawn`; paths via `os.path`/`paths`.
- Python 3.11–3.14 for `src/` and tests (GigaAM venv is 3.13: onnxruntime 1.23 has no 3.14 build; the
  Linux/Windows engine venvs are 3.13).
- Keep the security properties: no `-c` snippets in the orchestrator (JSON is read in-process; the fake
  engine runs with `-I`), lib scripts run by path (never the cwd on `sys.path`; the launcher uses `-P`),
  the `.vidx/` marker before writing into `-o`, option stamps per step, `--ignore-config --no-playlist`
  for yt-dlp, video-id sanitising, escaped Markdown for outside text, Ctrl-C kills child process trees on
  every OS, argv lists only (no `shell=True`), zip-slip checks on downloads.
