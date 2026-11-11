"""Fix lists as rows / CSV."""
import csv


def timecode(seconds):
    ms = int(round(seconds * 1000, 6) // 1)  # floor to whole milliseconds; a marker never shows a time after its sample
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"


FIELDS = ["module", "kind", "channel", "start_sample", "end_sample", "length", "seconds", "timecode"]


def fixes_rows(stages, sr):
    rows = []
    for st in stages:
        for f in st.fixes:
            rows.append(dict(module=st.module, kind=f.kind, channel=f.channel, start_sample=f.start,
                             end_sample=f.end, length=f.end - f.start, seconds=round(f.start / sr, 4),
                             timecode=timecode(f.start / sr)))
    return rows


def write_fixes_csv(path, stages, sr):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(fixes_rows(stages, sr))
