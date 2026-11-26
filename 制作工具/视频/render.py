#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

HF_VERSION = "0.9.94"


def hyperframes_command():
    """Prefer the installed official standalone skill launcher; pin the npm fallback."""
    custom = os.environ.get("HYPERFRAMES_RUNNER")
    if custom:
        return [str(Path(custom).expanduser().resolve(strict=True))]
    launcher = Path.home() / ".agents/skills/hyperframes/scripts/plugin-cli.mjs"
    if launcher.is_file():
        return ["node", str(launcher), "--cli-version", HF_VERSION]
    return ["npx", "--yes", "hyperframes@" + HF_VERSION]


class Composition(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = None
        self.page = {}
        self.scenes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.page = attrs
        if "data-composition-id" in attrs and "data-composition-src" not in attrs:
            if self.root is None or attrs.get("id") == "root":
                self.root = attrs
        if "data-composition-src" in attrs and attrs.get("data-track-kind") != "captions":
            self.scenes.append(attrs)

    def midway_samples(self, duration):
        spans = []
        for scene in self.scenes:
            try:
                start = float(scene["data-start"])
                end = min(duration, start + float(scene["data-duration"]))
            except (KeyError, ValueError):
                continue
            if 0 <= start < end:
                spans.append((start, end))
        spans.sort()
        if not spans:
            spans = [(duration * i / 6, duration * (i + 1) / 6) for i in range(6)]
        if len(spans) > 6:
            spans = [spans[round(i * (len(spans) - 1) / 5)] for i in range(6)]
        points = {0.0, max(0, duration - 0.4)}
        points.update((start + end) / 2 for start, end in spans)
        return ",".join(f"{point:.3f}" for point in sorted(points))


def run_stage(project, logs, name, command, as_json=False):
    stdout = logs / (name + (".json" if as_json else ".log"))
    stderr = logs / (name + ".stderr.log")
    print(f"[{project.name}] {name}  {stdout}", flush=True)
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        result = subprocess.run(command, cwd=project, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, check=False)
    if result.returncode:
        hint = (stdout.read_text(errors="replace")[-1400:] + "\n"
                + stderr.read_text(errors="replace")[-1200:])
        raise RuntimeError(f"{name} exited {result.returncode}. {stdout} / {stderr}\n{hint}")
    if as_json:
        payload = json.loads(stdout.read_text())
        if payload.get("ok") is False:
            raise RuntimeError(f"{name} returned ok=false. {stdout}")
        return payload


def validate_probe(probe, duration, rate, width, height):
    streams = probe.get("streams", [])
    videos = [s for s in streams if s["codec_type"] == "video"]
    audios = [s for s in streams if s["codec_type"] == "audio"]
    if len(videos) != 1:
        raise RuntimeError(f"Expected one video stream, got {len(videos)}")
    video = videos[0]
    if video["codec_name"] != "h264" or (video["width"], video["height"]) != (width, height):
        raise RuntimeError(f"Wrong codec or resolution: {video}")
    if Fraction(video["avg_frame_rate"]) != rate:
        raise RuntimeError(f'Wrong frame rate: {video["avg_frame_rate"]} instead of {rate}')
    if len(audios) != 1 or audios[0]["codec_name"] != "aac":
        raise RuntimeError("Delivery requires an AAC soundtrack.")
    video_end = float(video["duration"])
    container_end = float(probe["format"]["duration"])
    tolerance = 1 / float(rate) + 0.03
    if max(video_end, container_end) >= 180 or max(abs(t - duration) for t in (video_end, container_end)) > tolerance:
        raise RuntimeError(f"Rendered durations {video_end}/{container_end} differ from project {duration}, or exceed the limit")
    if abs(float(audios[0]["duration"]) - video_end) > 0.3:
        raise RuntimeError(f"Soundtrack ends at {audios[0].get('duration')}, video ends at {video_end}")


def main():
    parser = argparse.ArgumentParser(description="Check, preview, snapshot, render and probe a HyperFrames short.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output", type=Path,
                        help="Destination MP4. Defaults to a new file in the run logs.")
    parser.add_argument("--logs", type=Path,
                        help="Log directory (default: project's .hyperframes/artifacts/production).")
    parser.add_argument("--check-only", action="store_true",
                        help="Run HyperFrames checks, open local preview, and save representative frames.")
    parser.add_argument("--replace", action="store_true", help="Allow replacement of an existing output MP4.")
    parser.add_argument("--at", help="Additional frame times in seconds for review, comma-separated.")
    args = parser.parse_args()
    project = args.project.resolve(strict=True)
    composition = Composition()
    composition.feed((project / "index.html").read_text(encoding="utf-8"))
    if not composition.root:
        raise ValueError("Missing root data-composition-id")
    root = {**composition.page, **composition.root}
    duration = float(root["data-duration"])
    rate = Fraction(root["data-fps"])
    width, height = int(root["data-width"]), int(root["data-height"])
    if not math.isfinite(duration) or not 0 < duration < 180 or not 0 < rate <= 240:
        raise ValueError("Expected 0 < duration < 180 and a valid data-fps")
    if args.workers < 1:
        raise ValueError("Worker count must be positive")
    if args.check_only and args.output:
        parser.error("--check-only does not produce an MP4")
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("Choose a TCP port in the range 1–65535")
    output = args.output.expanduser().resolve() if args.output else None
    if output and (output.suffix.lower() != ".mp4" or (output.exists() and not args.replace)):
        parser.error("Choose a new .mp4 file or add --replace to replace that file.")
    if not shutil.which("ffprobe"):
        raise RuntimeError("Install FFmpeg and ffprobe, then retry.")
    hf = hyperframes_command()
    if not shutil.which(hf[0]):
        raise RuntimeError(f"Cannot find {hf[0]}. Install Node.js 22+ or set HYPERFRAMES_RUNNER.")
    os.environ.setdefault("HYPERFRAMES_NO_TELEMETRY", "1")
    if shutil.which("chromium"):
        os.environ.setdefault("HYPERFRAMES_BROWSER_PATH", shutil.which("chromium"))
    job = args.logs.resolve() / project.name if args.logs else project / ".hyperframes/artifacts/production"
    job.mkdir(parents=True, exist_ok=True)
    lock = (job / ".lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        raise RuntimeError(f"A render is already running for {project}") from e
    logs = job / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    logs.mkdir()
    stage = lambda name, command, as_json=False: run_stage(project, logs, name, command, as_json)
    times = set(map(float, composition.midway_samples(duration).split(",")))
    if args.at:
        extra = [float(v) for v in args.at.split(",")]
        if any(not math.isfinite(t) or not 0 <= t < duration for t in extra):
            parser.error("Each --at time must lie inside the composition")
        times.update(extra)
    timestamps = ",".join(f"{t:.3f}" for t in sorted(times))
    port = args.port or 18000 + int(hashlib.sha256(str(project).encode()).hexdigest()[:8], 16) % 18000
    stage("lint", [*hf, "lint", "--json"], True)
    report = stage("check", [*hf, "check", "--json", "--at", timestamps], True)
    if report.get("browserSkipped") or not report.get("layout", {}).get("samples"):
        raise RuntimeError(f"Browser verification skipped all samples. {logs}/check.json")
    for section, details in report.items():
        if isinstance(details, dict) and details.get("warningCount"):
            print(f"[{project.name}] {section}: {details['warningCount']} warning(s), {logs}/check.json", flush=True)
    preview = stage("preview", [*hf, "preview", "--background", "--no-open", "--port", str(port), "--json"], True)["result"]
    if Path(preview["projectDir"]).resolve() != project or not preview["ready"]:
        raise RuntimeError(f"Preview opened a different project or was not ready: {preview}")
    local = build_opener(ProxyHandler({}))
    with local.open(preview["serverUrl"], timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"Preview HTTP {response.status}")
    print(f"[{project.name}] {preview['studioUrl']}", flush=True)
    snapshots = logs / "snapshots"
    stage("snapshot", [*hf, "snapshot", "--at", timestamps, "--no-end", "--output", str(snapshots)])
    if sum(1 for _ in snapshots.glob("*.png")) != len(timestamps.split(",")):
        raise RuntimeError(f"Missing one or more expected snapshots: {snapshots}")
    if args.check_only:
        return
    output = output or (logs / "成片.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.parent / (".render-" + uuid.uuid4().hex + ".mp4")
    try:
        stage("render", [*hf, "render", "--quality", "delivery", "--fps", str(rate),
                         "--workers", str(args.workers), "--output", str(pending)])
        probe = stage("ffprobe", ["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                  "-of", "json", str(pending)], True)
        validate_probe(probe, duration, rate, width, height)
        if args.replace:
            os.replace(pending, output)
        else:
            os.link(pending, output)
            pending.unlink()
    except Exception:
        if pending.is_file():
            kept = logs / "unverified-render.mp4"
            shutil.move(str(pending), str(kept))
            print(f"[{project.name}] Unverified render retained at {kept}", file=sys.stderr)
        raise
    actual_duration = float(probe["format"]["duration"])
    print(f"[{project.name}] Verified {output}  {actual_duration:.3f}s  {width}x{height}  {rate}fps", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
