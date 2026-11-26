#!/usr/bin/env python3
"""Reconstruct the six-scene rough cut in a separate new directory.
The production HTML in each project includes subsequent manual revisions.
"""
import argparse
import json, math, re, shutil, subprocess, html
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("project", type=Path, help="One of the four finished source projects")
parser.add_argument("destination", type=Path, help="New directory for the reconstructed rough cut")
args = parser.parse_args()
original = args.project.resolve(strict=True)
proj = args.destination.resolve()
if proj.exists() or original in proj.parents or proj in original.parents:
    parser.error("Choose a new destination outside the source project.")
project_code = original.name[:8]
config={
'20260929': dict(cover='我熬了三个月，|他没翻一页 PPT', ground='#272823', ink='#fff3d9', accent='#f4a36d', light='#f0d8b2', dark='#28241f', quote='解决问题的第一性原理|是识别真需求，|不是自我感动。', source='03', serif=False),
'20261116': dict(cover='那条59秒的语音，|你会发给谁？', ground='#25293a', ink='#fff0db', accent='#ffab8d', light='#f3d7ba', dark='#282535', quote='把真诚，真心，真话，|留给那些|真正爱我们的人。', source='24', serif=True),
'20261118': dict(cover='我订好了海景房，|爸偏要等一朵花', ground='#193e3a', ink='#fff4d7', accent='#c4dcaa', light='#e6e8ce', dark='#183a35', quote='地图不一样，|不代表谁走错了路。', source='27', serif=True),
'20261122': dict(cover='饭桌突然安静，|你先怪自己吗？', ground='#26332d', ink='#fff1d9', accent='#efb37e', light='#e4cfac', dark='#283029', quote='情绪来了，|但我还有选择。', source='47', serif=False)}
if project_code not in config:
    parser.error("The archived templates cover the four projects in this collection.")
for item in ("script.json", "captions.json", "hyperframes.json", "assets"):
    if not (original / item).exists():
        parser.error(f"Missing production input: {original / item}")
proj.mkdir(parents=True)
shutil.copytree(original / "assets", proj / "assets")
for item in ("script.json", "captions.json", "hyperframes.json"):
    shutil.copy2(original / item, proj / item)
c=config[project_code]
s=json.loads((proj/'script.json').read_text())
caps=json.loads((proj/'captions.json').read_text()) if (proj/'captions.json').exists() else {}
if 'segments' in caps: caps=caps['segments']
if 'lines' in caps: caps=caps['lines']
def esc(v):return html.escape(str(v),quote=True)
def number(x):return f'{x:.3f}'
def run(*args):return subprocess.check_output([str(z) for z in args],text=True,stderr=subprocess.DEVNULL)
def duration(p):return float(run('ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',p).strip())
def tim(t):return f'{int(t)//60:02}:{t%60:04.1f}'
def srt_t(t):
 n=round(t*1000);return f'00:{n//60000:02}:{n//1000%60:02},{n%1000:03}'
manual=json.loads((Path(__file__).resolve().parents[1] / '字幕断行.json').read_text())
def splitline(t):
 if t in manual:return manual[t]
 # Balance two lines at a natural break whenever possible.
 if len(t)<=13:return [t]
 def score(k):
  return abs((k+.5)-len(t)/2) + (0 if t[k-1] in '，。？！、；：…' else 2.5) + (20 if t[k] in '，。？！；：、…」”' else 0)
 cut=min(range(max(1,len(t)-15),min(len(t),16)),key=score)
 return [t[:cut],t[cut:]]
fps=24; audio=proj/'assets/audio'; comps=proj/'compositions'; comps.mkdir(exist_ok=True)
# Use the project's licensed font subsets and GSAP runtime copied with its assets.
(proj/'package.json').write_text(json.dumps({'name':'editorial-short-'+project_code,'private':True,'scripts':{'dev':'npx --yes hyperframes@0.9.94 preview','render':'npx --yes hyperframes@0.9.94 render --quality delivery --fps 24 -o 成片.mp4'}},ensure_ascii=False,indent=2)+'\n')
fontcss="""@font-face{font-family:Title;src:url('assets/fonts/title.woff') format('woff');font-weight:700}@font-face{font-family:Body;src:url('assets/fonts/body.woff') format('woff');font-weight:400}"""
sequence=[]; subtitle=[]; track=[]; scenes=[]; frame=0
for i,sc in enumerate(s['scenes'],1):
 st=frame/fps
 frame+=4 if i==1 else 5
 for j,ln in enumerate(sc['lines'],1):
  key=f's{i:02}l{j:02}'
  f=audio/(key+'.mp3')
  d=duration(f);start=frame/fps;end=start+d
  if key in caps:
   group=caps[key]
   if isinstance(group,dict):group=group.get('cues',group.get('segments'))
  else:
   raise ValueError('Missing captions: '+key)
  isquote=(i==4 and ln['text'].strip('“”').strip('。')==c['quote'].replace('|','').strip('。'))
  for part in group:
   cs=max(start,start+float(part['start']));ce=min(end,start+float(part['end']))
   if ce<=cs: continue
   subtitle.append(dict(start=cs,end=ce,text=part['text'],name=ln['name'],speaker=ln['speaker'],quote=isquote,scene=i))
  sequence.append(dict(key=key,start=start,end=end,duration=d,**ln))
  track.append(f'<audio id="{key}" class="clip" src="assets/mix/{key}.wav" data-start="{number(start)}" data-duration="{number(d)}" data-track-index="3" data-volume="1.0"></audio>')
  frame+=math.ceil(d*fps)+4
 frame+= (25 if i==len(s['scenes']) else 5)
 scenes.append((i,sc,st,frame/fps-st))
D=frame/fps;assert D<180 and scenes[0][3]<=5.0
def title_html(title):return '<br>'.join(esc(v) for v in title.split('|'))
def host(k,f,st,d,lane,kind):
 return f'<div id="{k}" class="clip" data-composition-id="{k}" data-composition-src="compositions/{f}.html" data-start="{number(st)}" data-duration="{number(d)}" data-width="1080" data-height="1920" data-track-index="{lane}" data-track-kind="{kind}"></div>'
hosts=[]
for i,sc,st,d in scenes:
 k='scene'+str(i); h=c['cover'] if i==1 else c['quote'] if i==4 else sc['headline']
 if '|' not in h and len(h)>9:h=h[:6]+'|'+h[6:]
 label=(s['theme']+'　/　'+sc['kicker'] if i==1 else '原文摘句　/　第'+c['source']+'段' if i==4 else sc['kicker'])
 fg=c['ink'];bg=c['ground'];ac=c['accent']
 if i==4:fg=c['dark'];bg=c['light'];ac=c['dark']
 # Each picture begins with its focal subject in view. The text is stationary once set.
 edge=700 if i==4 and h.count('|')>1 else 605 if i in [1,4] or '|' in h else 520
 pixels=77 if i in (1,4) else 76
 if project_code=='20260929' and i==4:pixels=68
 css=f"""{fontcss} #root{{position:absolute;inset:0;background:{bg};overflow:hidden;color:{fg};font-family:Body,sans-serif}}
 .illustration{{position:absolute;inset:{edge}px 0 0;overflow:hidden}}
 .plate{{width:100%;height:100%;display:block;object-fit:cover;object-position:50% {['50','47','50','49','45','48'][i-1]}%}}
 .mast{{position:absolute;left:0;right:0;top:0;height:{edge}px;background:{bg};}}
 .peek{{position:absolute;top:0;left:0;right:0;height:132px;background-image:url('assets/art/{i:02}.jpg');background-size:1080px auto;background-position:50% {80 if i%2 else 16}%;opacity:0.70}}
 .story-label{{position:absolute;left:137px;top:298px;color:{ac};font:400 32px/1.25 Body,sans-serif;letter-spacing:3px;white-space:nowrap}}
 .headline{{position:absolute;left:137px;top:356px;margin:0;font:700 {pixels}px/1.22 Title,sans-serif;letter-spacing:-1.8px;white-space:nowrap;}}
 .rule{{position:absolute;left:0;bottom:0;width:100%;height:8px;background:{ac};transform-origin:left center}}
 """
 htmlscene=f'''<template>
<style>{css}</style>
<div id="root" data-composition-id="{k}" data-width="1080" data-height="1920">
 <div class="illustration" data-layout-allow-overflow><img id="{k}-picture" class="plate" src="assets/art/{i:02}.jpg" alt="{esc(sc['visual'])}"></div>
 <div class="mast"><div class="peek"></div><div id="{k}-tag" class="story-label">{esc(label)}</div><h1 id="{k}-heading" class="headline">{title_html(h)}</h1><div id="{k}-rule" class="rule"></div></div>
</div>
<script>
const tl = gsap.timeline({{paused:true}});
tl.fromTo("#{k}-picture", {{scale:{1.02 if i%2 else 1.105},xPercent:{-0.6 if i%2 else 0.5}}}, {{scale:{1.105 if i%2 else 1.02},xPercent:{0.55 if i%2 else -0.5},duration:{number(d)},ease:"sine.inOut"}},0);
tl.fromTo("#{k}-rule", {{scaleX:0.2}}, {{scaleX:1,duration:0.62,ease:"power2.out"}},0);
tl.fromTo("#{k}-heading", {{x:{0 if i==1 else -8}}}, {{x:0,duration:0.3,ease:"power2.out"}},0);
window.__timelines["{k}"]=tl;
</script></template>'''
 (comps/(k+'.html')).write_text(htmlscene)
 hosts.append(host(k,k,st,d,1,'graphics'))
# A shared subtitle lane keeps equal voices together and allows a direct edit in HyperFrames.
bits=[]
for nn,sub in enumerate(subtitle):
 if sub['quote']:continue
 t=sub['text']
 lines=splitline(t)
 cssclass='actor' if sub['speaker']!='narrator' else 'narration'
 bits.append(f'''<div id="cue-{nn+1}" class="clip cue" data-start="{number(sub['start'])}" data-duration="{number(sub['end']-sub['start'])}" data-track-index="0"><div class="caption {cssclass}" data-layout-allow-caption-zone><div class="voice">{esc(sub['name'])}</div><div class="words">{'<br>'.join(esc(v) for v in lines)}</div></div></div>''')
captions=f'''<template><style>{fontcss}
#root{{position:absolute;inset:0;pointer-events:none}}
.cue{{position:absolute;inset:0}}
.caption{{position:absolute;top:1042px;left:121px;width:max-content;max-width:846px;padding:17px 29px 22px 21px;border-left:6px solid {c['accent']};background:{c['ground']}f5;color:{c['ink']};}}
.voice{{font:400 28px/1.1 Body,sans-serif;letter-spacing:2px;color:{c['accent']};margin-bottom:9px}}
.words{{font:400 42px/1.34 Body,sans-serif;letter-spacing:0;white-space:nowrap}}
</style><div id="root" data-composition-id="captions" data-width="1080" data-height="1920">
{''.join(bits)}
</div><script>const tl=gsap.timeline({{paused:true}});window.__timelines["captions"]=tl;</script></template>'''
(comps/'captions.html').write_text(captions)
hosts.append(host('captions','captions',0,D,2,'captions'))
music=audio/'music.mp3'
if music.exists():
 # Measure the specific arrangement and keep the score well beneath speech.
 log=subprocess.run(['ffmpeg','-hide_banner','-nostats','-t',str(D),'-i',str(music),'-af','loudnorm=print_format=json','-f','null','-'],capture_output=True,text=True).stderr
 loud=float(re.findall(r'"input_i"\s*:\s*"([^"]+)"',log)[-1]);volume=min(1.,10**((-31-loud)/20))
 track.append(f'<audio id="music" class="clip" src="assets/audio/music.mp3" data-start="0" data-duration="{number(D)}" data-volume="{volume:.4f}" data-fade-in="0.25" data-fade-out="1.3" data-track-index="4"></audio>')
else:raise FileNotFoundError(music)
root=f'''<!DOCTYPE html><html lang="zh-CN" data-resolution="portrait"><head><meta charset="UTF-8"><meta name="viewport" content="width=1080, height=1920"><title>{esc(s['title'])}</title><script src="assets/gsap.min.js"></script><style>*{{box-sizing:border-box}}html,body{{margin:0;width:1080px;height:1920px;overflow:hidden;background:{c['ground']}}}#root{{position:relative;width:100%;height:100%}}</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{number(D)}" data-width="1080" data-height="1920" data-fps="{fps}">
{chr(10).join(hosts)}
{chr(10).join(track)}
</div><script>window.__timelines["main"]=gsap.timeline({{paused:true}});</script></body></html>'''
(proj/'index.html').write_text(root)
(proj/'字幕.srt').write_text('\n\n'.join(f'{idx}\n{srt_t(sub["start"])} --> {srt_t(sub["end"])}\n'+'\n'.join(splitline(sub['text'])) for idx,sub in enumerate(subtitle,1))+'\n')
md=[f'# {s["title"]}',f'1080 × 1920 竖屏，{fps} 帧／秒，剪辑时长 {tim(D)}。','| 时间 | 分场与画面 | 配音 |','| --- | --- | --- |']
for i,sc,st,d in scenes:
 cues=[ln for ln in sequence if ln['key'].startswith(f's{i:02}')]
 role='开场钩子' if i==1 else '原文金句' if i==4 else '收束' if i==len(scenes) else sc['kicker']
 md.append(f'| {tim(st)} – {tim(st+d)} | **{role} · {sc["headline"]}**<br>{sc["visual"]} | '+'<br>'.join(f'**{ln["name"]}**　{ln["text"]}' for ln in cues)+' |')
md+=['','## 声音','',s['music'],'','文中逐字引句与改编情节见同目录《创作提炼.md》。字幕另存为《字幕.srt》。']
(proj/'短视频脚本.md').write_text('\n'.join(md)+'\n')
spec=[f'# {s["title"]} · 画面与时间线','',f'竖屏 1080 × 1920，24 fps，{frame} 帧。六幅分场插画硬切。标题使用内嵌思源系简体字形，底色 {c["ground"]}，重点色 {c["accent"]}。', '','| 帧区间（含首帧） | 景别与动作 |','| --- | --- |']
for i,sc,st,d in scenes:spec+=[f'| {round(st*fps)}–{round((st+d)*fps)-1} | {sc["visual"]} |']
(proj/'SPEC.md').write_text('\n'.join(spec)+'\n')
print(proj.name,tim(D),round(D,3),len(subtitle),flush=True)
