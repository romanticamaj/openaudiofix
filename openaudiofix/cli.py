"""afix: command-line audio repair.

    afix scan INPUT
    afix repair INPUT [-m glitch,declip] [-o OUT.wav] [--float] [--no-diff] [-q]
    afix speech INPUT [INPUT ...] [--out-dir DIR] [--sensitivity low|mid|high] [--pad-before S] [--pad-after S]
    afix gpu status | install | test | remove      (background repair's GPU stack, in this environment)

Exit codes: 0 success, 2 usage error, 3 input error, 4 processing error.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import asdict

from . import __version__, chain, io as aio, modules, report
from .errors import AfixError, InputError
from .speech import Settings

EXIT_USAGE, EXIT_INPUT, EXIT_PROCESSING = 2, 3, 4


def _say(quiet, *a):
    if not quiet:
        print(*a, flush=True)


def _err(message, hint=""):
    print(f"afix: {message}" + (f"\n  hint: {hint}" if hint else ""), file=sys.stderr)


def _scan(a):
    src = aio.decode(a.input, a.stream)
    res = {m.id: asdict(m.scan(src.audio, src.sr)) for m in modules.all_modules()}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, default=float))
    else:
        for mid, r in res.items():
            print(f"{mid:8s} {'detected' if r['detected'] else 'clean':9s} {r['count']:>7d}  {r['summary']}")
    return 0


def _outputs(a):
    out = os.path.abspath(a.output) if a.output else os.path.splitext(os.path.abspath(a.input))[0] + "_afix.wav"
    stem = os.path.splitext(out)[0]
    return out, stem + "_diff.wav", stem + "_fixes.csv"


def _repair(a, ap):
    ids = [s.strip() for s in a.modules.split(",") if s.strip()]
    known = {m.id for m in modules.all_modules()}
    unknown = [i for i in ids if i not in known]
    if unknown or not ids:
        ap.error(f"unknown module(s): {', '.join(unknown) or '(none given)'}; available: {', '.join(sorted(known))}")
    out, diff_path, csv_path = _outputs(a)
    same = os.path.normcase(os.path.abspath(a.input))
    for path in (out, diff_path, csv_path):
        if os.path.normcase(path) == same:
            ap.error(f"the output {path} would overwrite the input")
    t0 = time.time()
    src = aio.decode(a.input, a.stream)
    if src.lossy:
        print(f"afix: warning: the input is lossy ({src.codec}); repair quality will be lower than from a WAV/FLAC.",
              file=sys.stderr)
    last = [0.0]

    def prog(module, index, total, pct=None, message=None):
        now = time.time()
        if a.quiet or now - last[0] < 1.0:
            return
        last[0] = now
        p = f"{pct:5.1f}%" if pct is not None else "     "
        print(f"[{index + 1}/{total}] {module:8s} {p} {message or ''}", file=sys.stderr, flush=True)

    stages = chain.run_chain(src.audio, src.sr, ids, progress=prog)
    final = stages[-1].audio
    info = aio.export(final, src.sr, out, src, float_out=a.float_out, reduce_gain=chain.reduce_gain_for(ids))
    _say(a.quiet, f"wrote {out}  ({info['subtype']}, gain {info['gain_db']:+.2f} dB"
                  f"{', byte-exact outside fixes' if info['byte_exact'] else ''})")
    if not a.no_diff:
        g = aio.write_diff(diff_path, src.audio, final, src.sr)
        if g is not None:
            _say(a.quiet, f"wrote {diff_path}  (diff, +{g:.1f} dB to peak -1 dBFS)")
    report.write_fixes_csv(csv_path, stages, src.sr)
    _say(a.quiet, f"wrote {csv_path}")
    for s in stages:
        _say(a.quiet, f"  {s.module:8s} {len(s.fixes):>7d} fixes  {s.notes}")
    if info["gain_db"] < 0:
        _say(a.quiet, f"  the output was lowered by {-info['gain_db']:.2f} dB so rebuilt peaks fit; "
                      f"raise it back in your DAW or use --float")
    _say(a.quiet, f"done in {time.time() - t0:.1f} s")
    return 0


def _speech(a, ap):
    import soundfile as sf

    from . import speech as SP
    s = SP.Settings.for_sensitivity(
        a.sensitivity, threshold=a.threshold, release=min(a.release, a.threshold), min_speech=a.min_speech,
        min_gap=a.min_gap, sure=a.sure, pad_before=a.pad_before, pad_after=a.pad_after, fade=a.fade, hp_hz=a.hp,
        music=not a.no_music)
    if a.open_db is not None:
        s.open_db = a.open_db
    if a.close_db is not None:
        s.close_db = a.close_db
    s.close_db = min(s.close_db, s.open_db)
    t0 = time.time()
    tracks = []
    for path in a.inputs:
        src = aio.decode(path)
        tracks.append(SP.Track(path, src.audio, src.sr, src.subtype))
    SP.check_session(tracks)
    sr = tracks[0].sr
    dur = len(tracks[0].audio) / sr
    outs = []
    for t in tracks:
        d = os.path.abspath(a.out_dir) if a.out_dir else os.path.dirname(os.path.abspath(t.path))
        stem = os.path.join(d, os.path.splitext(os.path.basename(t.path))[0])
        outs.append((stem + "_speech.wav", stem + "_removed.wav", stem + "_removed.txt"))
    last = [0.0]

    def prog(pct, msg):
        now = time.time()
        if not a.quiet and now - last[0] >= 1.0:
            last[0] = now
            print(f"{msg} {pct:5.1f}%", file=sys.stderr, flush=True)

    spans, analyses = SP.detect(tracks, s, model=a.model, download=not a.no_download,
                                say=lambda m: _say(a.quiet, m), progress=prog)
    if not spans:
        _err("no speech was found; nothing written", "Try --sensitivity high if the file does have speech.")
        return EXIT_PROCESSING
    removed = SP.removed_spans(spans, dur)
    islands = [i for an in analyses for i in an.islands]
    for t, (speech_path, removed_path, labels_path) in zip(tracks, outs):
        os.makedirs(os.path.dirname(speech_path), exist_ok=True)
        speech, rest = SP.split(t.audio, sr, spans, s)
        subtype = t.subtype if t.subtype in ("PCM_16", "PCM_24", "PCM_32", "FLOAT") else "PCM_24"
        sf.write(speech_path, speech, sr, subtype=subtype)
        sf.write(removed_path, rest, sr, subtype=subtype)
        # Audacity labels of what went to the removed track; the label is what was loudest in the gap
        # (thud, music, noise, click, short) or "removed" when it held only the room
        with open(labels_path, "w", encoding="utf-8") as fh:
            for a0, b0 in removed:
                fh.write(f"{a0:.6f}\t{b0:.6f}\t{SP.label_of((a0, b0), islands)}\n")
        for path in (speech_path, removed_path, labels_path):
            _say(a.quiet, f"wrote {path}")
    gone = sum(b - a for a, b in removed)
    _say(a.quiet, f"  {len(spans)} speech spans, {len(removed)} removed spans: "
                  f"{gone:.1f} s of {report.timecode(dur)} ({100 * gone / dur:.1f}%) moved to the removed track")
    for t, an in zip(tracks, analyses):
        other = ", ".join(f"{k} {v}" for k, v in an.counts.items() if v and k not in ("speech", "voiced"))
        _say(a.quiet, f"  {os.path.basename(t.path)}: floor {an.floor_db:.1f} dBFS, gate +{an.open_db:.0f}/"
                      f"+{an.close_db:.0f} dB{' (lowered: quiet speech)' if an.calibrated else ''}, "
                      f"speech {an.speech_s:.1f} s in {an.counts['speech'] + an.counts['voiced']} islands "
                      f"({an.counts['voiced']} voiced the VAD missed)" + (f"; removed {other}" if other else ""))
    _say(a.quiet, f"done in {time.time() - t0:.1f} s")
    return 0


def _parser():
    ap = argparse.ArgumentParser(prog="afix", description="OpenAudioFix command-line repair")
    ap.add_argument("--version", action="version", version=f"afix {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="detect problems without repairing")
    s.add_argument("input")
    s.add_argument("--stream", type=int, default=0, help="audio track index (default 0)")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("repair", help="repair a file")
    r.add_argument("input")
    r.add_argument("-m", "--modules", default="glitch,declip", help="comma-separated: glitch,declip,denoise")
    r.add_argument("-o", "--output")
    r.add_argument("--float", dest="float_out", action="store_true", help="32-bit float, no gain reduction")
    r.add_argument("--no-diff", action="store_true")
    r.add_argument("-q", "--quiet", action="store_true")
    r.add_argument("--stream", type=int, default=0)
    d = sub.add_parser("speech", help="split into the speech and the removed rest (two files, original length)",
                       description="Writes NAME_speech.wav (speech, the rest silent), NAME_removed.wav (the rest, "
                                   "speech silent; the two add up to the input) and NAME_removed.txt (Audacity "
                                   "labels naming what was loudest in each gap: thud, music, noise, click, short, or "
                                   "removed). Several inputs are tracks of one session and are split together.")
    d.add_argument("inputs", nargs="+")
    d.add_argument("--out-dir", help="default: next to each input")
    df = Settings()
    d.add_argument("--sensitivity", choices=["low", "mid", "high"], default="mid",
                   help="level gate over the noise floor: high +14/+6 dB, mid +18/+10, low +22/+14")
    d.add_argument("--open-db", type=float, help="gate opens this far over the noise floor (overrides --sensitivity)")
    d.add_argument("--close-db", type=float, help="gate closes below this over the noise floor")
    d.add_argument("--pad-before", type=float, default=df.pad_before, help="seconds kept before speech")
    d.add_argument("--pad-after", type=float, default=df.pad_after, help="seconds kept after speech")
    d.add_argument("--min-gap", type=float, default=df.min_gap, help="shorter pauses are kept")
    d.add_argument("--fade", type=float, default=df.fade, help="fade at each speech boundary, seconds")
    d.add_argument("--hp", type=float, default=df.hp_hz, help="high-pass (Hz) before measuring the level; 0 = off")
    d.add_argument("--no-music", action="store_true", help="do not remove long unbroken sounds the VAD never hears")
    d.add_argument("--threshold", type=float, default=df.threshold,
                   help="VAD probability that makes a sound speech (only classifies the gate's islands)")
    d.add_argument("--release", type=float, default=df.release, help="VAD release (reporting only)")
    d.add_argument("--min-speech", type=float, default=df.min_speech, help="VAD minimum run (reporting only)")
    d.add_argument("--sure", type=float, default=df.sure, help="islands shorter than 0.1 s stay if the VAD is this sure")
    d.add_argument("--model", help="path to silero_vad.onnx (skips the download)")
    d.add_argument("--no-download", action="store_true")
    d.add_argument("-q", "--quiet", action="store_true")
    g = sub.add_parser("gpu", help="install or test background repair's GPU stack in this environment")
    g.add_argument("action", choices=["status", "install", "test", "remove"])
    return ap


def _gpu(a):
    from . import gpu as G
    if a.action == "status":
        try:
            name, mem, drv = G.nvidia()
            print(f"GPU: {name} · {mem} · driver {drv}")
        except G.GpuUnavailable as e:
            print(f"GPU: none ({e})")
        have = G.installed_versions()
        print("packages: " + (", ".join(f"{k} {v}" for k, v in have.items()) or "not installed"))
        print(f"weights: {G.model_dir() / 'clearvoice'}")
        return 0 if G.packages_present() else EXIT_PROCESSING
    if a.action == "remove":
        G.uninstall()
        print("removed the GPU packages and the MossFormer2 weights")
        return 0
    if a.action == "install":
        if G.install_disabled():
            _err("installing is disabled in this environment (AFIX_GPU_INSTALL=0)",
                 "In Docker, use the GPU image: docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d")
            return EXIT_PROCESSING
        G.nvidia()  # no NVIDIA GPU: stop before downloading gigabytes
        if not G.packages_present():
            last = [-10.0]

            def report(pct=0.0, done_bytes=0, phase=""):
                if pct - last[0] >= 5 or pct >= 100:
                    last[0] = pct
                    print(f"  {phase} {pct:5.1f}%  {done_bytes / 1e9:.2f} GB", flush=True)
            print(f"installing the GPU packages (about {G.ESTIMATED_BYTES / 1e9:.1f} GB) into {sys.prefix}")
            G.install(report)
        print("downloading the MossFormer2 weights")
        G.download_weights()
    out, code = G.selftest()
    if code == 0 and out.get("ok"):
        print(f"self-test passed: {out.get('gpu')}, {out.get('speed_x')}x real time")
        return 0
    _err(f"self-test failed at {out.get('step')}: {out.get('error')}")
    return EXIT_PROCESSING


def main(argv=None):
    ap = _parser()
    try:
        a = ap.parse_args(argv)
        if a.cmd == "speech":
            return _speech(a, ap)
        if a.cmd == "gpu":
            return _gpu(a)
        return _scan(a) if a.cmd == "scan" else _repair(a, ap)
    except SystemExit as e:  # argparse usage errors (2) and --version (0)
        return e.code if isinstance(e.code, int) else 0
    except AfixError as e:
        _err(e.message, e.hint)
        return EXIT_INPUT if isinstance(e, InputError) else EXIT_PROCESSING
    except Exception as e:
        from .gpu import GpuUnavailable
        if not isinstance(e, (GpuUnavailable, RuntimeError)) or a.cmd != "gpu":
            raise
        _err(str(e))
        return EXIT_PROCESSING


if __name__ == "__main__":
    sys.exit(main())
