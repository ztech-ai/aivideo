#!/usr/bin/env python3
"""Build time-matched, levelled delivery takes from the original MiniMax speech."""
import argparse
import concurrent.futures
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

TARGET = -17.0
RATE = 48000


def probe(p):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=sample_rate,channels",
        "-of",
        "json",
        str(p),
    ]
    return json.loads(subprocess.check_output(cmd, text=True))


def measure(p):
    filters = "loudnorm=I=-17:LRA=10:TP=-3.5:print_format=json"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af", filters, "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    obj = json.loads(re.search(r'\{\s*"input_i"[^{}]+\}', result.stderr, re.S)[0])
    return float(obj["input_i"]), float(obj["input_tp"])


def mean_db(p):
    output = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(p), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return float(re.search(r"mean_volume: ([-\d.]+)", output)[1])


def level(take):
    dest = take.parents[1] / "mix" / take.with_suffix(".wav").name
    dest.parent.mkdir(exist_ok=True)
    seconds = float(probe(take)["format"]["duration"])
    total = round(seconds * RATE)
    origin_i, _ = measure(take)
    if math.isfinite(origin_i):
        gain = TARGET - origin_i
    else:
        gain = -20 - mean_db(take)
    ceiling = -3.6
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        # Evaluate each pass from the original take, so any feedback correction
        # avoids re-filtering an already limited signal.
        out = Path(tmp) / "take.wav"
        for _ in range(4):
            limit = 10 ** (ceiling / 20)
            filters = (
                f"aresample=192000,volume={gain:.4f}dB,alimiter=limit={limit:.7f}:attack=3:release=48:level=0:latency=1,"
                f"aresample={RATE},apad,atrim=end_sample={total}"
            )
            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(take),
                "-af",
                filters,
                "-ar",
                str(RATE),
                "-ac",
                "1",
                "-c:a",
                "pcm_s24le",
                str(out),
            ]
            subprocess.run(cmd, check=True)
            li, tp = measure(out)
            if not math.isfinite(li):
                li = mean_db(out) + 3.0
            if abs(li - TARGET) <= 0.24 and tp <= -3.0:
                break
            gain += min(3, max(-3, TARGET - li)) * 1.08
            if tp > -3.0:
                ceiling -= tp + 3.3
        if tp > -3.0 or abs(li - TARGET) > 1.0:
            raise ValueError((take.name, li, tp))
        meta = probe(out)
        assert meta["streams"][0]["channels"] == 1 and meta["streams"][0]["sample_rate"] == str(
            RATE
        )
        end = float(meta["format"]["duration"])
        if abs(end - seconds) > 0.00002:
            raise ValueError((take.name, end, seconds))
        # Publish only a verified, complete WAV on the project filesystem.
        out.replace(dest)
    return (take.parents[2].name, dest.name, li, tp, end - seconds)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", type=Path, nargs="+")
    ap.add_argument("--line", action="append", help="只处理指定片段，可重复")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    if args.jobs < 1:
        ap.error("--jobs 必须至少为 1")
    files = [
        p
        for d in args.projects
        for p in sorted((d / "assets/audio").glob("s[0-9][0-9]l[0-9][0-9].mp3"))
        if not args.line or p.stem in args.line
    ]
    if not files:
        ap.error("找不到待处理的 MP3 片段")
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(level, files))
    for project in sorted(set(x[0] for x in rows)):
        r = [x for x in rows if x[0] == project]
        print(
            project,
            len(r),
            f"{min(x[2] for x in r):.2f}..{max(x[2] for x in r):.2f} LUFS",
            f"{min(x[3] for x in r):.2f}..{max(x[3] for x in r):.2f} dBTP",
            f"max duration error {max(abs(x[4]) for x in r):.6f}",
            flush=True,
        )
