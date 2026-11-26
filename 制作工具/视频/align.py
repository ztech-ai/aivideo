#!/usr/bin/env python3
"""将四个短片的逐句原录音和文案对齐，在新目录生成 captions.json 和 字幕.srt。

默认离线使用 ASR/ 中的逐字时间，按拼接后的 16 kHz 单声道 PCM 的 SHA256
匹配。--transcribe 会把拼接后的对白提交至中国区 MiniMax 并保存可重用的逐字时间。
需 Python 3.9+、FFmpeg。在线模式另需 Node.js、npm 版 mmx-cli、mmx_api_key。
"""
import argparse
import difflib
import functools
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import wave
from html.parser import HTMLParser
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
# 16 位单声道每秒 32000 字节。两句之间以及最后一句之后均留 0.5 秒。
SILENCE = bytes(SAMPLE_RATE)
STRONG = '。？！……'
WEAK = '，、；：'


def spoken(text):
    return ''.join(c for c in text if c.isalnum()).lower()


def groups(text):
    """用制作时的分句评分在语气停顿处分卡，并保留原稿字词与标点。"""
    @functools.lru_cache(None)
    def optimize(start):
        if start == len(text):
            return 0, ()
        best_cost, best_cards = float('inf'), ()
        for end in range(start + 1, min(len(text), start + 24) + 1):
            if end < len(text) and text[end] in STRONG + WEAK + '”’':
                continue
            card = text[start:end]
            length = len(spoken(card))
            if not length:
                continue
            cost = 1 + ((length - 16) / 8) ** 2 + max(0, 5 - length) ** 2
            tail = card.rstrip('”’')
            if end < len(text):
                if not tail or tail[-1] not in STRONG + WEAK:
                    cost += 35
                elif tail[-1] in WEAK:
                    cost += 1
            cost += 1.4 * sum(c in STRONG for c in tail[:-1])
            next_cost, next_cards = optimize(end)
            if cost + next_cost < best_cost:
                best_cost, best_cards = cost + next_cost, (card,) + next_cards
        return best_cost, best_cards
    return optimize(0)[1]


def align_line(text, segments, offset, duration):
    observed = []
    for word in segments:
        start, end = word['start'] - offset, word['end'] - offset
        chars = spoken(word['text'])
        if not chars or not (0 <= (start + end) / 2 <= duration):
            continue
        left, right = max(0, start), min(end, duration)
        for i, char in enumerate(chars):
            a = left + (right - left) * i / len(chars)
            b = left + (right - left) * (i + 1) / len(chars)
            observed.append((char, a, b))
    recognized = ''.join(c for c, _, _ in observed)
    matcher = difflib.SequenceMatcher(None, spoken(text), recognized, autojunk=False)
    ratio = matcher.ratio()
    if not observed or ratio < .45:
        raise ValueError(f'逐字时间与文稿匹配率仅 {ratio:.1%}：{text}')

    intervals = []
    for mode, a, b, x, y in matcher.get_opcodes():
        if a == b:
            continue
        if mode == 'equal':
            intervals.extend((u[1], u[2]) for u in observed[x:y])
            continue
        left = observed[x][1] if x < len(observed) else observed[-1][2]
        right = observed[y - 1][2] if y > x else left
        for k in range(b - a):
            intervals.append((left + (right - left) * k / (b - a),
                              left + (right - left) * (k + 1) / (b - a)))

    cues = []
    pos = 0
    for card in groups(text):
        start = round(intervals[pos][0], 3)
        pos += len(spoken(card))
        end = round(intervals[pos - 1][1], 3)
        if end <= start:
            raise ValueError(f'这张字幕无法分配正时长：{card}')
        cues.append({'start': start, 'end': end, 'text': card})
    if ''.join(c['text'] for c in cues) != text:
        raise ValueError(f'字幕分句未覆盖整段原稿：{text}')
    return cues, ratio


def collect_audio(project):
    script = json.loads((project / 'script.json').read_text(encoding='utf-8'))
    takes = []
    pcm = bytearray()
    for i, scene in enumerate(script['scenes'], 1):
        for j, line in enumerate(scene['lines'], 1):
            key = f's{i:02d}l{j:02d}'
            audio = project / 'assets' / 'audio' / f'{key}.mp3'
            chunk = subprocess.check_output([
                'ffmpeg', '-loglevel', 'error', '-nostdin', '-i', str(audio),
                '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 's16le', '-',
            ])
            takes.append((key, line['text'], len(pcm) / (2 * SAMPLE_RATE), len(chunk) / (2 * SAMPLE_RATE)))
            pcm.extend(chunk)
            pcm.extend(SILENCE)
    if not takes:
        raise ValueError('script.json 未提供对白')
    return takes, pcm


def transcribe(folder, pcm):
    key = os.environ.get('mmx_api_key')
    mmx = shutil.which('mmx')
    if not key or not mmx:
        raise ValueError('在线对齐需要 npm 版 mmx-cli 和环境变量 mmx_api_key')
    wavpath, output = folder / 'speech.wav', folder / 'response.json'
    with wave.open(str(wavpath), 'wb') as w:
        w.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        w.writeframes(pcm)
    # 实际执行的 mmx 进程通过自身环境传入密钥，系统命令参数与生成日志不含密钥。
    launcher = """import {pathToFileURL} from 'node:url';
const [,, exe, ...args] = process.argv;
process.argv = [process.execPath, exe, ...args, '--api-key', process.env.mmx_api_key];
await import(pathToFileURL(exe));"""
    command = ['node', '--input-type=module', '-e', launcher, str(Path(mmx).resolve()),
               'speech', 'transcribe', '--file', str(wavpath), '--language', 'zh',
               '--timestamp-level', 'word', '--response-format', 'verbose_json',
               '--region', 'cn', '--base-url', 'https://api.minimax.cn', '--out', str(output),
               '--quiet', '--non-interactive', '--timeout', '180']
    run = subprocess.run(command, text=True, capture_output=True)
    if run.returncode:
        message = (run.stderr or run.stdout).replace(key, '[redacted]').strip()
        raise ValueError(f'MiniMax 转录失败：{message[:600]}')
    return json.loads(output.read_text(encoding='utf-8'))['segments']


def clean_segments(segments):
    words = []
    for raw in segments:
        start, end, text = raw['start'], raw['end'], raw['text']
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end and isinstance(text, str)):
            raise ValueError('ASR 逐字时间必须由非负起止秒数和文字组成')
        words.append({'start': start, 'end': end, 'text': text})
    return words


class Timeline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.clips = {}
        self.fps = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-fps' in attrs and 'data-composition-id' in attrs:
            self.fps = float(attrs['data-fps'])
        if tag == 'audio' and 'id' in attrs and 'data-start' in attrs:
            self.clips[attrs['id']] = (float(attrs['data-start']), float(attrs['data-duration']))


def split_line(text, breaks):
    if text in breaks:
        lines = breaks[text]
        if ''.join(lines) != text:
            raise ValueError(f'断行表内容与原字幕不符：{text}')
        return lines
    if len(text) <= 13:
        return [text]
    def cost(k):
        return (abs(k + .5 - len(text) / 2) + (0 if text[k - 1] in '，。？！、；：…' else 2.5)
                + (20 if text[k] in '，。？！；：、…」”' else 0))
    cut = min(range(max(1, len(text) - 15), min(len(text), 16)), key=cost)
    return [text[:cut], text[cut:]]


def timestamp(t):
    ms = round(t * 1000)
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def srt_text(project, result):
    timeline = Timeline()
    timeline.feed((project / 'index.html').read_text(encoding='utf-8'))
    breaks = json.loads((TOOL_DIR / '字幕断行.json').read_text(encoding='utf-8'))
    entries = []
    for key, cues in result.items():
        start, duration = timeline.clips[key]
        # 成片 HTML 按三位小数保存，剪辑点落在帧边界，以精确帧时刻生成 SRT。
        if timeline.fps:
            frame_start = round(start * timeline.fps) / timeline.fps
            if abs(start - frame_start) < .00051:
                start = frame_start
        for cue in cues:
            a, b = start + cue['start'], start + min(cue['end'], duration)
            if b <= a:
                raise ValueError(f'{key} 的字幕超出工程音轨范围，请核对 index.html 剪辑时长')
            entries.append((a, b, split_line(cue['text'], breaks)))
    entries.sort(key=lambda cue: cue[0])
    return '\n\n'.join(f'{i}\n{timestamp(a)} --> {timestamp(b)}\n' + '\n'.join(lines)
                        for i, (a, b, lines) in enumerate(entries, 1)) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project', type=Path, help='含 script.json、index.html 和 assets/audio 的工程目录')
    parser.add_argument('--out', type=Path, required=True, help='新建的暂存目录')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--asr-json', type=Path, help='已有的逐字 JSON，需带有同名 .sha256 音频校验文件')
    source.add_argument('--transcribe', action='store_true', help='调用 mmx 重新转录，随后保存去除请求标识的逐字时间')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        parser.error('--out 请指向一个尚不存在的暂存目录')
    project = args.project.resolve()
    if project == output or project in output.parents:
        parser.error('--out 请放到输入工程以外')
    takes, pcm = collect_audio(project)
    digest = hashlib.sha256(pcm).hexdigest()
    stem = project.name[:8]
    if args.transcribe:
        with tempfile.TemporaryDirectory() as scratch:
            segments = clean_segments(transcribe(Path(scratch), pcm))
    else:
        cache = args.asr_json or TOOL_DIR / 'ASR' / f'{stem}-asr.json'
        if not cache.is_file() or not cache.with_suffix('.sha256').is_file():
            parser.error('无匹配的离线 ASR 缓存，可指定 --asr-json，或选择 --transcribe 在线转录')
        if cache.with_suffix('.sha256').read_text(encoding='utf-8').strip() != digest:
            parser.error('音轨校验不符。请选择与本次原录音相符的逐字时间或使用 --transcribe')
        segments = clean_segments(json.loads(cache.read_text(encoding='utf-8'))['segments'])
    result, ratios = {}, []
    for key, text, offset, duration in takes:
        result[key], ratio = align_line(text, segments, offset, duration)
        ratios.append(ratio)
    srt = srt_text(project, result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        staged = Path(tmp) / 'staged'
        staged.mkdir()
        (staged / 'captions.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        (staged / '字幕.srt').write_text(srt, encoding='utf-8')
        if args.transcribe:
            name = staged / f'{stem}-asr.json'
            name.write_text(json.dumps({'segments': segments}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            name.with_suffix('.sha256').write_text(digest + '\n', encoding='utf-8')
        staged.rename(output)
    print(f'{len(takes)} 段对白，{sum(map(len, result.values()))} 条字幕。'
          f'逐句最低文字匹配率 {min(ratios):.1%}。文件位于 {output}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
