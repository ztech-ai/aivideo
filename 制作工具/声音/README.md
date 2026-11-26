# 声音制作与成片核验

四篇工程都附有逐句语音、归一化 WAV、原始乐谱和配乐。以下命令从仓库根目录执行，示例处理《赚钱的第一性原理》。

## 依赖

- Python 3.11 或更新版本，系统路径中可调用 FFmpeg、FFprobe。
- 重新生成语音或创作新谱时需要 Node.js、官方 `mmx-cli`（制作用 1.0.29）以及环境变量 `mmx_api_key`。现有脚本使用 MiniMax 中国区。
- 演奏需要 FluidSynth 动态库和 General MIDI 音色库。原片使用 TimGM6mb.sf2。可用 `--soundfont` 指定其他位置。
- 成片比对需要 NumPy 和 SciPy，可用 `python -m pip install numpy scipy` 安装。

## 配音与响度

```bash
project=20260929赚钱的第一性原理
python 制作工具/声音/generate_audio.py "$project"
python 制作工具/声音/level_voice.py "$project"
```

`generate_audio.py` 根据 `script.json` 中的场次、说话人和台词写出 `assets/audio/sXXlXX.mp3`，默认跳过已有文件。四篇的选角保存在脚本的 `CAST` 和 `SCENE_CAST` 中。需要替换指定角色音色时，也可为台词增加 `voice_id`。例如只重配财经片中第二位客户的两句：

```bash
python 制作工具/声音/generate_audio.py "$project" --force --line s05l03 --line s06l02
python 制作工具/声音/level_voice.py "$project" --line s05l03 --line s06l02
```

`level_voice.py` 输出 `assets/mix/sXXlXX.wav`（48 kHz、24 位、单声道）。处理目标约 −17 LUFS，真峰值上限 −3 dBTP。文件长度与对应 MP3 容器时长对齐至采样点。更换台词或音色后，应按新音频时长重新对齐视频时间轴和字幕。

## 作曲与演奏

```bash
python 制作工具/声音/compose_score.py "$project"
python 制作工具/声音/perform_score.py "$project"
```

成片配乐由 `mmx text chat` 调用 MiniMax-M3 创作，谱面保存在 `assets/audio/score.json`，其中记录速度、旋律、和弦、低音和各段落的演奏力度。`compose_score.py` 仅在缺谱时调用模型，可加 `--force` 重新作曲。`perform_score.py` 读取现有谱面，经 FluidSynth 演奏并写出 `assets/audio/music.mp3`。视频时间轴控制配乐的使用区间、音量和淡出。

## 验证导出音轨

```bash
python 制作工具/声音/verify_final_audio.py
```

脚本逐行比对 `index.html` 指定的原音与最终 MP4 的波形，测量每句入点偏移，扣除人声后单独检查配乐，再测整片响度、真峰值和静音。默认把四篇的结果写入 `验收/音轨核验.json`。只检查单篇时，可单独指定报告路径以保留四片总表。

```bash
python 制作工具/声音/verify_final_audio.py "$project" --output "$project/音轨核验.json"
```
