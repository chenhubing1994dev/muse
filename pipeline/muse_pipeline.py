# -*- coding: utf-8 -*-
# Muse 现象级视频拆解流水线
# 用法: python muse_pipeline.py [research|tts|animate|compose|all]
import argparse, asyncio, datetime, json, pathlib, shutil, subprocess, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / 'data'
BUILD = ROOT / 'build'
OUT = ROOT / 'output'
for d in (DATA, BUILD, OUT):
    d.mkdir(parents=True, exist_ok=True)

FFMPEG = 'ffmpeg'
FFPROBE = 'ffprobe'
VOICE = 'zh-CN-YunxiNeural'
W, H = 1080, 1920

FONT_CANDIDATES = [
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/msyh.ttf',
    'C:/Windows/Fonts/simhei.ttf',
    'C:/Windows/Fonts/simsun.ttc',
]
FONT = next((f for f in FONT_CANDIDATES if pathlib.Path(f).exists()), FONT_CANDIDATES[0])
FONT_LOCAL = 'msyh.ttf'

def today():
    return datetime.date.today().isoformat()

def run(cmd, cwd=None):
    print('+', ' '.join(str(c) for c in cmd)[:260])
    r = subprocess.run([str(c) for c 
in cmd], capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=cwd)
    if r.returncode != 0:
        print(r.stderr[-2500:])
        raise SystemExit('ffmpeg 执行失败')
    return r

def dur(p):
    r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

def fetch_bilibili():
    url = 'https://api.bilibili.com/x/web-interface/popular/series/one?number=1'
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode('utf-8'))
    items = []
    for it in data.get('data', {}).get('list', [])[:6]:
        stat = it.get('stat', {})
        items.append({
            'source': 'bilibili',
            'title': it.get('title', ''),
            'view': stat.get('view', 0),
            'coin': stat.get('coin', 0),
            'favorite': stat.get('favorite', 0),
            'url': 'https://www.bilibili.com/video/' + it.get('bvid', ''),
        })
    return items

def fallback_samples():
    return [
        {'source': 'fallback', 'title': '全网刷屏的『我不是人，我是你丢掉的那只猫』现象级视频解析', 'view': 1180000, 'coin': 54000, 'favorite': 22000, 'url': 'https://example.com/cat'},
        {'source': 'fallback', 'title': '一个视频讲透『为什么你越努力越穷』，千万人共鸣', 'view': 980000, 'coin': 41000, 'favorite': 19000, 'url': 'https://example.com/poor'},
        {'source': 'fallback', 'title': '现象级科普：如果地球停止自转，我们会怎样？', 'view': 760000, 'coin': 33000, 'favorite': 15000, 'url': 'https://example.com/earth'},
    ]

def make_script(topic):
    t = topic['title']
    return {
        'title': t,
        'hook': '你没看错——' + t + '。今天这条视频，就带你拆解这件全网都在讨论的事。',
        'body': [
            '事情是这样的：' + t + '。短短几天播放量就冲破百万，评论区直接炸锅。',
            '很多人问它凭什么能火？答案很简单——它精准戳中了大众情绪，再用最通俗的方式讲出来。',
            '现象级爆款的底层逻辑从来不变：强情绪、低门槛、高共鸣，三秒抓人，三十秒留住人。',
        ],
        'climax': '所以真正值得学的，不是这条视频说了什么，而是它为什么能让千万人愿意停下来看完。',
        'ending': '觉得有启发就点个赞收藏一下。我是 Muse，下期继续带你拆解全网最火的内容。',
        'source': topic,
    }

def cmd_research():
    try:
        items = fetch_bilibili()
        if not items:
            items = fallback_samples()
    except Exception as e:
        print('bilibili 抓取失败，使用内置样本:', e)
        items = fallback_samples()
    def score(x):
        return x.get('view', 0) * 0.6 + x.get('coin', 0) * 10 + x.get('favorite', 0) * 4
    ranked = sorted(items, key=score, reverse=True)
    top = ranked[0]
    script = make_script(top)
    (DATA / ('topic_' + today() + '.json')).write_text(json.dumps({'picked': top, 'ranked': ranked}, ensure_ascii=False, indent=2), encoding='utf-8')
    sp = DATA / ('script_' + today() + '.json')
    sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PICKED:', top['title'], '| view:', top['view'])
    print('SCRIPT:', sp)

def load_script():
    files = sorted(DATA.glob('script_*.json'))
    if not files:
        raise SystemExit('未找到脚本, 请先运行 research')
    return json.loads(files[-1].read_text(encoding='utf-8'))

def segments(script):
    segs = [('本期选题', script['title'], 1.8, None)]
    segs.append(('开场钩子', script['hook'], None, 'voice'))
    for i, b in enumerate(script['body'], 1):
        segs.append(('拆解 ' + str(i), b, None, 'voice'))
    segs.append(('高潮洞察', script['climax'], None, 'voice'))
    segs.append(('结尾互动', script['ending'], None, 'voice'))
    return segs

async def tts_edge(text, out_mp3):
    import edge_tts
    c = edge_tts.Communicate(text, VOICE, rate='+8%')
    await c.save(str(out_mp3))

def cmd_tts():
    script = load_script()
    segs = segments(script)
    meta = {'title': script['title'], 'source': script['source'], 'segs': []}
    for i, (tag, text, fixd, kind) in enumerate(segs):
        item = {'i': i, 'tag': tag, 'text': text}
        if kind == 'voice':
            mp3 = BUILD / ('seg_%02d.mp3' % i)
            ok = False
            for attempt in range(3):
                try:
                    asyncio.run(tts_edge(text, mp3))
                    ok = True
                    break
                except Exception as e:
                    print('TTS 重试', attempt + 1, e)
            if not ok:
                raise SystemExit('edge-tts 合成失败')
            item['mp3'] = str(mp3)
            item['dur'] = dur(mp3)
        else:
            item['dur'] = fixd
        meta['segs'].append(item)
        print('SEG', i, tag, item['dur'])
    (BUILD / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print('META:', BUILD / 'meta.json')

PALETTES = [
    ('0x0F2027', '0x2C5364', '0x203A43'),
    ('0x141E30', '0x243B55', '0x141E30'),
    ('0x232526', '0x414345', '0x232526'),
    ('0x0F0C29', '0x302B63', '0x24243E'),
    ('0x000428', '0x004E92', '0x000428'),
    ('0x1D2B64', '0x3A5BA9', '0x1D2B64'),
    ('0x136B62', '0x2A9D8F', '0x0B3D39'),
]

def wrap_cjk(text, width):
    lines, cur, w = [], '', 0
    for ch in text:
        cw = 2 if ord(ch) > 127 else 1
        if w + cw > width:
            lines.append(cur)
            cur, w = ch, cw
        else:
            cur += ch
            w += cw
    if cur:
        lines.append(cur)
    return chr(10).join(lines)

def cmd_animate():
    font_target = BUILD / FONT_LOCAL
    if not font_target.exists():
        shutil.copy2(FONT, font_target)
    meta = json.loads((BUILD / 'meta.json').read_text(encoding='utf-8'))
    total = len(meta['segs'])
    fa = "fontfile='" + FONT_LOCAL + "'"
    for seg in meta['segs']:
        i = seg['i']
        txt = BUILD / ('seg_%02d.txt' % i)
        hdr = BUILD / ('seg_%02d.hdr' % i)
        ftr = BUILD / ('seg_%02d.ftr' % i)
        txt.write_text(wrap_cjk(seg['text'], 28), encoding='utf-8')
        hdr.write_text('Muse 现象级拆解 | ' + seg['tag'] + ' (%d/%d)' % (i + 1, total), encoding='utf-8')
        ftr.write_text('Muse AI 自动拆解 · 二创解说仅供学习交流', encoding='utf-8')
        c0, c1, c2 = PALETTES[i % len(PALETTES)]
        out = BUILD / ('seg_%02d.mp4' % i)
        d = float(seg['dur']) + 0.25
        grad = 'gradients=s=%dx%d:c0=%s:c1=%s:c2=%s:x0=0:y0=0:x1=%d:y1=%d:speed=0.03,format=yuv420p' % (W, H, c0, c1, c2, W, H)
        vf = ('[0:v]drawtext=' + fa + ':textfile=' + hdr.name + ':fontcolor=white@0.85:fontsize=36:x=(w-text_w)/2:y=110,'
              + 'drawtext=' + fa + ':textfile=' + txt.name + ':fontcolor=white:fontsize=56:line_spacing=26:x=(w-text_w)/2:y=(h-text_h)/2+40:box=1:boxcolor=black@0.35:boxborderw=30,'
              + 'drawtext=' + fa + ':textfile=' + ftr.name + ':fontcolor=white@0.5:fontsize=28:x=(w-text_w)/2:y=h-150[v]')
        cmd = [FFMPEG, '-y', '-f', 'lavfi', '-t', '%.2f' % d, '-i', grad]
        if seg.get('mp3'):
            cmd += ['-i', seg['mp3']]
        else:
            cmd += ['-f', 'lavfi', '-t', '%.2f' % d, '-i', 'anullsrc=r=44100:cl=mono']
        cmd += ['-filter_complex', vf, '-map', '[v]', '-map', '1:a', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-r', '30', '-c:a', 'aac', '-b:a', '160k', '-ar', '44100', '-ac', '2', '-shortest', out.name]
        run(cmd, cwd=BUILD)
        print('SEG VIDEO:', out)

def cmd_compose():
    meta = json.loads((BUILD / 'meta.json').read_text(encoding='utf-8'))
    lst = BUILD / 'concat.txt'
    lst.write_text(chr(10).join('file ' + chr(39) + str(BUILD / ('seg_%02d.mp4' % s['i'])).replace(chr(92), '/') + chr(39) for s in meta['segs']), encoding='utf-8')
    joined = BUILD / 'joined.mp4'
    run([FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', str(lst), '-c', 'copy', str(joined)])
    total = dur(joined)
    final = OUT / ('muse_' + today() + '.mp4')
    bgm = 'aevalsrc=0.10*sin(2*PI*220*t)+0.07*sin(2*PI*277.18*t)+0.05*sin(2*PI*329.63*t):s=44100'
    run([FFMPEG, '-y', '-i', str(joined), '-f', 'lavfi', '-t', '%.2f' % total, '-i', bgm,
         '-filter_complex', '[1:a]volume=0.5[bg];[0:a][bg]amix=inputs=2:duration=first[a]',
         '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', str(final)])
    cover = OUT / ('muse_' + today() + '.jpg')
    run([FFMPEG, '-y', '-ss', '1.0', '-i', str(final), '-frames:v', '1', str(cover)])
    pub = {
        'title': '【动画拆解】' + meta['title'] + '，它凭什么刷屏全网？',
        'desc': 'Muse 用 1 分钟动画拆解现象级爆款：' + meta['title'] + chr(10) + '本视频为二次创作解说，素材来源: ' + meta['source'].get('url', ''),
        'tags': ['现象级', '动画解说', '爆款拆解', 'Muse', '短视频创作'],
    }
    (OUT / ('publish_' + today() + '.json')).write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding='utf-8')
    print('FINAL:', final, '%.1fs' % dur(final))
    print('COVER:', cover)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('step', choices=['research', 'tts', 'animate', 'compose', 'all'])
    a = ap.parse_args()
    if a.step in ('research', 'all'):
        cmd_research()
    if a.step in ('tts', 'all'):
        cmd_tts()
    if a.step in ('animate', 'all'):
        cmd_animate()
    if a.step in ('compose', 'all'):
        cmd_compose()

if __name__ == '__main__':
    main()