#!/usr/bin/env python3
"""Commission a project score through MiniMax's official mmx text chat CLI."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
from generate_audio import mmx_command

SCHEMA = """Write an original instrumental underscore for this Mandarin short film. You are a composer. Return only valid JSON with a compact performable score and no explanation. Strict schema:
{"title":"original Chinese score title","bpm":72,"programs":{"lead":0,"harmony":24,"bass":43},"bars":[{"chord":[48,55,60],"bass":36,"melody":[[0,1,67],[2,1.5,64]]}],"pattern":[[0,0],[1.5,1],[2.5,2]],"form":[{"bars":[0,1,2,3,4,5,6,7],"lead":0.6,"harmony":0.5,"bass":0.3}]}
Compose 8 DISTINCT bars in 4/4, zero-based indices 0..7. Each chord has 3 or 4 MIDI pitches, melody entries [start beat from 0, duration beats, MIDI pitch]. Rests are space, and some silent bars are encouraged. Pattern = [start beat,chord-note index] for subtle countermovement. Choose real General MIDI programs (zero based), with gentle acoustic instruments. Write 5 sections of 7 or 8 bars (total duration roughly 115–125 seconds), supply complete form with specific bar order and dynamics 0–1 for each of the 3 voices. Leave a spacious opening for a spoken hook, a slight lift at the turn, land gently with a restful last bar. Keep melody below the voice without big jumps, avoid a percussive click, a pop vocal hook or constant busy arpeggios. Last section must resolve to a resting chord. Do not output a program or any external links.
"""


def compose(project, model, force):
    musicdir = project / "assets/audio"
    target = musicdir / "score.json"
    if target.exists() and not force:
        return
    musicdir.mkdir(parents=True, exist_ok=True)
    script = json.loads((project / "script.json").read_text(encoding="utf-8"))
    prompt = (
        SCHEMA
        + "\nTitle: "
        + script["title"]
        + "\nMusic direction: "
        + script["music"]
        + "\nFilm ending: "
        + script["scenes"][-1]["lines"][-1]["text"]
    )
    cmd = mmx_command(
        "text",
        "chat",
        "--message",
        prompt,
        "--model",
        model,
        "--max-tokens",
        "5500",
        "--output",
        "json",
        "--timeout",
        "240",
    )
    job = subprocess.run(cmd, capture_output=True, text=True, timeout=265)
    if job.returncode:
        message = (job.stderr + job.stdout).replace(os.environ["mmx_api_key"], "[redacted]")[-800:]
        raise RuntimeError(f"{project.name}: {message}")
    result = json.loads(job.stdout)
    if isinstance(result, dict) and "bars" in result:
        score = result
    else:
        if isinstance(result, str):
            raw = result
        elif isinstance(result.get("content"), list):
            raw = "".join(b["text"] for b in result["content"] if b.get("type") == "text")
        else:
            raw = result["text"]
        score = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    if len(score["bars"]) < 5 or len(score["form"]) < 3:
        raise ValueError(f"{project.name}: 返回的乐谱缺少乐句或曲式段落")
    score["generation"] = {"provider": "MiniMax", "cli": "mmx text chat", "model": model}
    with tempfile.TemporaryDirectory(dir=musicdir) as temp:
        dest = Path(temp) / target.name
        dest.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        dest.replace(target)
    print(project.name, score["title"], model, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", type=Path, nargs="+")
    ap.add_argument("--model", default="MiniMax-M3")
    ap.add_argument("--force", action="store_true", help="重新创作并替换现有乐谱")
    args = ap.parse_args()
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(compose, p, args.model, args.force) for p in args.projects]
        for future in concurrent.futures.as_completed(futures):
            future.result()
