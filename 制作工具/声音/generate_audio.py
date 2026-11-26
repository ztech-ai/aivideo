#!/usr/bin/env python3
"""用 MiniMax mmx 生成 script.json 对应的逐句旁白和角色音频。"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

NARRATOR = "Chinese (Mandarin)_Gentleman"
DEFAULT_CAST = {
    "narrator": NARRATOR,
    "a": "Chinese (Mandarin)_Warm_Bestie",
    "b": "Chinese (Mandarin)_Reliable_Executive",
}
CAST = {
    "20260929": {
        "narrator": "Chinese (Mandarin)_News_Anchor",
        "a": "Chinese (Mandarin)_Reliable_Executive",
        "b": "Chinese (Mandarin)_Gentleman",
    },
    "20261116": {
        "narrator": NARRATOR,
        "a": "Chinese (Mandarin)_Warm_Bestie",
        "b": "Chinese (Mandarin)_Kind-hearted_Antie",
    },
    "20261118": {
        "narrator": "Chinese (Mandarin)_News_Anchor",
        "a": "Chinese (Mandarin)_Warm_Bestie",
        "b": "Chinese (Mandarin)_Reliable_Executive",
    },
    "20261122": DEFAULT_CAST,
}
SCENE_CAST = {
    ("20260929", 5, "a"): "Chinese (Mandarin)_Mature_Woman",
    ("20260929", 6, "a"): "Chinese (Mandarin)_Mature_Woman",
}
# The installed npm CLI accepts a credential argument. Inject it within Node so
# it never appears in shell history, the operating system's argv or a config file.
LAUNCHER = """import { pathToFileURL } from 'node:url';
const [, cli, ...args] = process.argv;
process.argv = [process.execPath, cli, ...args, '--api-key', process.env.mmx_api_key];
await import(pathToFileURL(cli));"""


def mmx_command(*args):
    if not os.environ.get("mmx_api_key"):
        raise RuntimeError("请在环境变量 mmx_api_key 中配置密钥。")
    exe = shutil.which("mmx")
    if not exe:
        raise RuntimeError("找不到官方 npm 命令 mmx（mmx-cli）。")
    return [
        "node",
        "--input-type=module",
        "-e",
        LAUNCHER,
        str(Path(exe).resolve()),
        *args,
        "--region",
        "cn",
        "--base-url",
        "https://api.minimax.cn",
        "--quiet",
        "--non-interactive",
    ]


def synthesize(path, text, voice, speaker):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        take = Path(tmp) / path.name
        cmd = mmx_command(
            "speech",
            "synthesize",
            "--text",
            text,
            "--voice",
            voice,
            "--model",
            "speech-2.8-hd",
            "--language",
            "Chinese",
            "--speed",
            "1.02" if speaker == "narrator" else "1.0",
            "--format",
            "mp3",
            "--sample-rate",
            "44100",
            "--bitrate",
            "128000",
            "--subtitles",
            "--out",
            str(take),
            "--timeout",
            "300",
        )
        for attempt in range(4):
            job = subprocess.run(cmd, capture_output=True, text=True, timeout=335)
            if job.returncode == 0 and take.is_file():
                duration = float(
                    subprocess.check_output(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "csv=p=0",
                            str(take),
                        ],
                        text=True,
                    )
                )
                if duration <= 0:
                    raise ValueError(f"{path}: 音频时长无效")
                break
            message = (job.stderr + job.stdout).replace(os.environ["mmx_api_key"], "[redacted]")[
                -1600:
            ]
            if attempt == 3 or not any(
                x in message.lower()
                for x in (
                    "rate limit",
                    "429",
                    "concurrency",
                    "overload",
                    "busy",
                    "http 503",
                    "http 502",
                    "频率",
                    "过于频繁",
                )
            ):
                raise RuntimeError(f"{path.name}: {message}")
            time.sleep(4 * (attempt + 1))
        take.replace(path)
        subtitle = take.with_suffix(".srt")
        if subtitle.is_file():
            subtitle.replace(path.with_suffix(".srt"))
        else:
            path.with_suffix(".srt").unlink(missing_ok=True)
        print(path, f"{duration:.2f} s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", type=Path, nargs="+")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--line", action="append", help="只处理指定片段，可重复，例如 s05l03")
    ap.add_argument("--force", action="store_true", help="重新生成已存在的片段")
    options = ap.parse_args()
    if options.jobs < 1:
        ap.error("--jobs 必须至少为 1")
    jobs = []
    selected = set(options.line or ())
    found = set()
    for project in options.projects:
        script = json.loads((project / "script.json").read_text(encoding="utf-8"))
        key = project.resolve().name[:8]
        cast = CAST.get(key, DEFAULT_CAST)
        for sn, scene in enumerate(script["scenes"], 1):
            for ln, line in enumerate(scene["lines"], 1):
                ident = f"s{sn:02d}l{ln:02d}"
                if selected and ident not in selected:
                    continue
                found.add(ident)
                path = project / "assets/audio" / f"{ident}.mp3"
                if path.is_file() and not options.force:
                    continue
                speaker = line["speaker"]
                voice = line.get("voice_id") or SCENE_CAST.get((key, sn, speaker)) or cast[speaker]
                jobs.append((path, line["text"], voice, speaker))
    if selected - found:
        ap.error("未找到片段 " + "、".join(sorted(selected - found)))
    with concurrent.futures.ThreadPoolExecutor(options.jobs) as pool:
        for task in concurrent.futures.as_completed([pool.submit(synthesize, *j) for j in jobs]):
            task.result()


if __name__ == "__main__":
    main()
