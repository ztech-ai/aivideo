#!/usr/bin/env python3
"""Perform an mmx-authored JSON score using FluidSynth and a General MIDI SoundFont."""
import argparse
import ctypes as C
import ctypes.util
import json
import math
from pathlib import Path
import subprocess
import tempfile
import wave

RATE = 44100
SF = Path("/usr/share/sounds/sf2/TimGM6mb.sf2")
fluid_library = ctypes.util.find_library("fluidsynth")
if not fluid_library:
    raise SystemExit("需要 FluidSynth 动态库。")
lib = C.CDLL(fluid_library)
P, I, D, S = C.c_void_p, C.c_int, C.c_double, C.c_char_p


def bind(name, result, *args):
    fn = getattr(lib, name)
    fn.argtypes = args
    fn.restype = result
    return fn


settings = bind("new_fluid_settings", P)
setnum = bind("fluid_settings_setnum", I, P, S, D)
setint = bind("fluid_settings_setint", I, P, S, I)
synth_new = bind("new_fluid_synth", P, P)
sfload = bind("fluid_synth_sfload", I, P, S, I)
program = bind("fluid_synth_program_change", I, P, I, I)
control = bind("fluid_synth_cc", I, P, I, I, I)
noteon = bind("fluid_synth_noteon", I, P, I, I, I)
noteoff = bind("fluid_synth_noteoff", I, P, I, I)
write = bind("fluid_synth_write_s16", I, P, I, P, I, I, P, I, I)
free_synth = bind("delete_fluid_synth", None, P)
free_settings = bind("delete_fluid_settings", None, P)
MAX_BLOCK = 8192


def arrange(score):
    count = sum(len(section["bars"]) for section in score["form"])
    # A short notated cycle is repeated within each section for the long film bed.
    repeat = max(1, math.ceil(36 / count))
    form = [(i, section) for section in score["form"] for i in section["bars"] * repeat]
    beat = min(60 / score["bpm"], 126 / (4 * len(form)))
    notes = []

    def add(channel, start, length, pitch, dynamics):
        velocity = max(1, min(108, round(29 + 74 * dynamics)))
        key = max(0, min(127, round(pitch)))
        pos = round(RATE * max(0, start))
        notes.append((pos, 1, channel, key, velocity))
        notes.append((pos + max(1, round(RATE * length)), 0, channel, key, 0))

    for n, (bar_i, section) in enumerate(form):
        bar = score["bars"][bar_i % len(score["bars"])]
        start = n * 4 * beat + 0.09
        phrasing = (0.94, 0.98, 1, 0.93)[n % 4]
        for offset, length, key in bar["melody"]:
            if section["lead"] > 0:
                add(
                    0,
                    start + offset * beat + 0.011,
                    float(length) * beat * 0.91,
                    key,
                    section["lead"] * phrasing,
                )
        for m, (offset, idx) in enumerate(score["pattern"]):
            if section["harmony"] > 0:
                chord = bar["chord"]
                add(
                    1,
                    start + offset * beat + 0.012 * (m % 2),
                    beat * (1.45 if m == 0 else 0.95),
                    chord[round(idx) % len(chord)],
                    section["harmony"] * (0.91 if m else 1),
                )
        if section["bass"] > 0:
            add(
                2,
                start,
                beat * (2.45 if n + 1 < len(form) else 3.2),
                bar["bass"],
                section["bass"] * phrasing,
            )
    return sorted(notes), 4 * len(form) * beat + 1.8


def perform(project, soundfont):
    ad = project / "assets/audio"
    score = json.loads((ad / "score.json").read_text())
    notes, seconds = arrange(score)
    opts = settings()
    setnum(opts, b"synth.sample-rate", RATE)
    setnum(opts, b"synth.gain", 0.62)
    setint(opts, b"synth.chorus.active", 0)
    setnum(opts, b"synth.reverb.room-size", 0.28)
    setnum(opts, b"synth.reverb.level", 0.33)
    synth = synth_new(opts)
    if sfload(synth, str(soundfont).encode(), 1) < 0:
        raise RuntimeError("SoundFont could not be loaded")
    for i, name in enumerate(("lead", "harmony", "bass")):
        program(synth, i, int(score["programs"][name]))
        control(synth, i, 10, [55, 76, 63][i])
        control(synth, i, 7, [95, 84, 73][i])
    with tempfile.TemporaryDirectory(dir=ad) as temp:
        wav = Path(temp) / "performed.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            pos = 0
            buf = (C.c_int16 * (2 * MAX_BLOCK))()

            def write_to(target):
                nonlocal pos
                while pos < target:
                    n = min(target - pos, MAX_BLOCK)
                    if write(synth, n, buf, 0, 2, buf, 1, 2) < 0:
                        raise RuntimeError("Audio synthesizer render failed")
                    w.writeframesraw(C.string_at(C.addressof(buf), n * 4))
                    pos += n

            for when, on, ch, key, vel in notes:
                write_to(when)
                if on:
                    noteon(synth, ch, key, vel)
                else:
                    noteoff(synth, ch, key)
            write_to(round(seconds * RATE))
        free_synth(synth)
        free_settings(opts)
        mp3 = Path(temp) / "music.mp3"
        af = f"highpass=f=36,loudnorm=I=-19:TP=-2:LRA=9,afade=t=in:d=0.15,afade=t=out:st={seconds-1.8:.3f}:d=1.8"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(wav),
                "-af",
                af,
                "-ar",
                "44100",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-metadata",
                "title=" + score["title"],
                "-metadata",
                f"comment=Score composed by {score['generation']['model']} via mmx. Performed by FluidSynth with {soundfont.stem}.",
                str(mp3),
            ],
            check=True,
        )
        mp3.replace(ad / "music.mp3")
    print(project.name, score["title"], f"{seconds:.1f} sec", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", type=Path, nargs="+")
    ap.add_argument(
        "--soundfont", type=Path, default=SF, help="General MIDI SoundFont（原片使用 TimGM6mb.sf2）"
    )
    args = ap.parse_args()
    if not args.soundfont.is_file():
        ap.error("找不到音色库，请用 --soundfont 指定 .sf2 文件")
    for project in args.projects:
        perform(project, args.soundfont)
