#!/usr/bin/env python3
"""从成片提取最终音轨，与 HyperFrames 工程中的每句原音和配乐对齐比对。"""
import argparse
from html.parser import HTMLParser
from pathlib import Path
import json
import hashlib
import os
import re
import subprocess
import tempfile
import numpy as np
from scipy.signal import correlate, butter, sosfilt

RATE = 12000
BAND = butter(3, [160, 4700], fs=RATE, btype="bandpass", output="sos")
ROOT = Path(__file__).resolve().parents[2]


class Clips(HTMLParser):
    def __init__(self):
        super().__init__()
        self.audio = []

    def handle_starttag(self, tag, attrs):
        if tag == "audio":
            self.audio.append(dict(attrs))


def audio(p):
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "1",
        "-i",
        str(p),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(RATE),
        "-f",
        "f32le",
        "pipe:1",
    ]
    return np.frombuffer(subprocess.check_output(cmd), dtype="<f4").astype(np.float64)


def fit(reference, track, expected, search_samples):
    ref = reference
    padded = np.pad(track, (search_samples, search_samples))
    start = max(0, expected)
    stop = min(len(padded), expected + 2 * search_samples + len(ref))
    target = padded[start:stop]
    if len(target) < len(ref):
        raise ValueError("Short alignment window")
    dots = correlate(target, ref, mode="valid", method="fft")
    energy = np.r_[0, np.cumsum(target * target)]
    refenergy = np.dot(ref, ref)
    norms = np.sqrt(np.maximum(1e-18, (energy[len(ref) :] - energy[: -len(ref)]) * refenergy))
    correlations = dots / norms
    pos = int(np.argmax(correlations))
    offset = start + pos - search_samples - expected
    return offset, float(correlations[pos]), float(dots[pos] / refenergy)


def overlay(dest, sound, position, gain):
    s = max(0, position)
    begin = max(-position, 0)
    n = min(len(sound) - begin, len(dest) - s)
    if n > 0:
        dest[s : s + n] += sound[begin : begin + n] * gain


def loudness(p):
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-threads",
        "1",
        "-i",
        str(p),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        "silencedetect=noise=-52dB:d=1.4,loudnorm=I=-16:LRA=10:TP=-1:print_format=json",
        "-f",
        "null",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    m = re.search(r'\{\s*"input_i"[^{}]+\}', r.stderr, re.S)
    stats = json.loads(m[0])
    silences = []
    for m in re.finditer(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", r.stderr):
        end, length = map(float, m.groups())
        silences.append({"start": round(end - length, 3), "seconds": round(length, 3)})
    return float(stats["input_i"]), float(stats["input_tp"]), silences


def verify(proj, root):
    path = proj.resolve() / "成片.mp4"
    meta = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,channels,sample_rate,duration,start_time:format=duration",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    st = next(s for s in meta["streams"] if s["codec_type"] == "audio")
    raw = audio(path)
    final = sosfilt(BAND, raw)
    parser = Clips()
    parser.feed((proj / "index.html").read_text())
    ref = np.zeros_like(final)
    findings = []
    music = None
    for clip in parser.audio:
        source = proj / clip["src"]
        if "/mix/" not in clip["src"]:
            music = clip
            continue
        samples = sosfilt(BAND, audio(source))
        start = round(float(clip["data-start"]) * RATE)
        trim = min(round(0.14 * RATE), len(samples) // 8)
        segment = samples[trim : len(samples) - trim]
        offset, corr, gain = fit(segment, final, start + trim, round(0.35 * RATE))
        overlay(ref, samples, start + offset, gain)
        findings.append(
            {
                "line": clip["id"],
                "reference_start_s": start / RATE,
                "lag_ms": round(offset * 1000 / RATE, 2),
                "correlation": round(corr, 5),
                "declared_gain": float(clip.get("data-volume", 1)),
                "fitted_gain": round(gain, 4),
            }
        )
    # Verify the bed independently after projecting out the matched speech takes.
    # The early-middle window has music throughout and excludes opening/ending fades.
    if music is None:
        raise ValueError("Missing source music declaration")
    bed = sosfilt(BAND, audio(proj / music["src"]))
    musicstart = round(float(music["data-start"]) * RATE)
    begin = int(3.5 * RATE)
    end = min(int(22 * RATE), len(final) - RATE, len(bed) - RATE)
    lag, music_corr, music_gain = fit(
        bed[begin:end], final - ref, musicstart + begin, round(0.2 * RATE)
    )
    integrated, peak, silences = loudness(path)
    duration = float(st.get("duration", len(final) / RATE))
    voiced_count = sum(
        len(s["lines"]) for s in json.loads((proj / "script.json").read_text())["scenes"]
    )
    worst_lag = max(abs(row["lag_ms"]) for row in findings)
    min_corr = min(row["correlation"] for row in findings)
    notes = []
    if len(findings) != voiced_count:
        notes.append("declared voice count mismatch")
    if min_corr < 0.80:
        notes.append("poor speech match")
    if worst_lag > 65:
        notes.append("speech offset exceeds 65 ms")
    if music_corr < 0.30 or music_gain <= 0.018:
        notes.append("insufficient music match")
    if not -22 <= integrated <= -12:
        notes.append("check loudness")
    if peak > 0.05:
        notes.append("true peak over 0 dBTP")
    if any(s["seconds"] > 2.0 for s in silences):
        notes.append("silence longer than 2 seconds")
    with path.open("rb") as media:
        digest = hashlib.file_digest(media, "sha256").hexdigest()
    return {
        "file": Path(os.path.relpath(path, root)).as_posix(),
        "sha256": digest,
        "duration_s": duration,
        "codec": st["codec_name"],
        "channels": st["channels"],
        "sample_rate": int(st["sample_rate"]),
        "integrated_lufs": integrated,
        "true_peak_dbtp": peak,
        "silence_threshold_dbfs": -52,
        "silences_1p4s_and_longer": silences,
        "voice_count": len(findings),
        "minimum_line_correlation": round(min_corr, 5),
        "max_absolute_speech_lag_ms": round(worst_lag, 2),
        "lines": findings,
        "music": {
            "source": music["src"],
            "lag_ms": round(lag * 1000 / RATE, 2),
            "residual_correlation": round(music_corr, 5),
            "declared_gain": float(music.get("data-volume", 1)),
            "fitted_gain": round(music_gain, 4),
        },
        "passed": not notes,
        "findings": notes,
    }


def report(rows, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        temp = Path(folder) / output.name
        temp.write_text(
            json.dumps(
                {
                    "method": "Mono 12 kHz speech-band normalized cross-correlations per independent source line, waveform start from HyperFrames data-start. Background score tested against final mix after voice subtraction. FFmpeg stereo EBU R128 and silencedetect.",
                    "completed_projects": len(rows),
                    "projects": rows,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        temp.replace(output)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "projects", type=Path, nargs="*", help="默认检查项目根目录下含 script.json 的全部目录"
    )
    ap.add_argument("--root", type=Path, default=ROOT, help="报告内文件路径的基准目录")
    ap.add_argument("--output", type=Path, help="报告位置，默认根目录下的 验收/音轨核验.json")
    opts = ap.parse_args()
    root = opts.root.resolve()
    projects = opts.projects or [s.parent for s in sorted(root.glob("*/script.json"))]
    if not projects:
        ap.error("未找到项目")
    results = {}
    for proj in projects:
        info = verify(proj, root)
        results[proj.resolve().name] = info
        print(
            proj.name,
            "PASS" if info["passed"] else "FAIL",
            info["integrated_lufs"],
            "LUFS",
            info["true_peak_dbtp"],
            "dBTP",
            info["findings"],
            flush=True,
        )
    report(results, opts.output or root / "验收/音轨核验.json")
    if not all(row["passed"] for row in results.values()):
        raise SystemExit(1)
