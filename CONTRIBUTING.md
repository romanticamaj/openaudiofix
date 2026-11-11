# Contributing

## Add a repair module

1. Create `openaudiofix/modules/<id>.py` with a subclass of `RepairModule`: set `id`, `name = "module.<id>"`,
   `device` (`"cpu"` or `"gpu"`), `default_order` (glitch is 10, declip 20, background 30) and `whole_file`
   (True if it changes every sample). Implement `scan(audio, sr) -> ScanResult` and
   `repair(audio, sr, params, progress) -> RepairResult`. `audio` is float64 with shape (samples, channels);
   return the same shape, no gain change, and a `Fix` for every changed region.
2. Register it at the bottom of `openaudiofix/modules/__init__.py`.
3. Add `module.<id>`, `module.<id>.desc` and `module.<id>.found` to every file in `web/src/i18n/`.
4. Add tests in `tests/test_<id>_module.py`: output shape, no NaN, and that samples outside your fixes are unchanged.

## Add a UI language

1. Copy `web/src/i18n/en.json` to `web/src/i18n/<lang>.json` and translate the values (keep the keys and `{placeholders}`).
2. Add it to `DICTS` in `web/src/i18n/index.ts` and to the language menu in `web/src/App.tsx`.
3. `npm test` checks that every language has exactly the same keys.

## Tests

**Every bug gets a test.** A bug is not fixed until a test fails without the fix and passes with it, in the layer
where the bug can show: real audio playback and anything else a stand-in cannot prove belongs in `web/e2e/`. Add a
line to [tests/REGRESSIONS.md](tests/REGRESSIONS.md). Before saying something works, run everything:

```bash
python tests/check_all.py          # types, web, build, browser, Python (add --full for the slow Python tests)
```

- Python: `python -m pytest -m "not private and not docker"`. The `docker` tests need a running Docker
  daemon; the `private` tests need maintainer-only regression audio and are skipped elsewhere; `slow`
  tests take more than 30 s (add `and not slow` for a quick run).
- Web: `cd web && npm test`.
- End-to-end: `cd web && npm run build && npx playwright test` (set `AFIX_PYTHON` to the Python that has
  `.[server]` installed if it is not `.venv`).

Regression references for the synthetic test signals are in `tests/regression/public_refs.json`. The
output hash is compared only on the platform the references were recorded on (OS + NumPy + SciPy version); other
platforms compare fix counts and gain.

## Release checklist

CI has no GPU, so before every release, on a machine with an NVIDIA GPU:

1. Native: in a fresh virtual environment with `.[server]`, install the GPU module from the web UI
   (Repair modules → Background repair) and run one background repair on a real recording; `afix gpu status`
   must list torch 2.7.1+cu128 and clearvoice 0.1.2, and numpy/scipy/soundfile must still be the pinned versions.
2. Docker: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml build`, then
   `docker run --rm --gpus all ghcr.io/romanticamaj/openaudiofix-gpu:<version> python -m server.gpu_selftest` must print `"ok": true`.
3. If the GPU stack changes, re-resolve `openaudiofix/gpu-requirements.txt` against the core pins (see its header).
4. CI is green on the release commit (Python, web and end-to-end jobs).
5. Bump `version` in `pyproject.toml` and the image tags in `docker-compose.yml` and `docker-compose.gpu.yml` together.
