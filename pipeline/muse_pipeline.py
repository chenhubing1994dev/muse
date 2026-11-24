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
    if not isinstance(data, dict) or data.get('code') != 0:
        raise RuntimeError('Bilibili returned an unsuccessful or malformed response')
    payload = data.get('data')
    records = payload.get('list') if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise RuntimeError('Bilibili response did not contain a current ranking list')
    retrieved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items = []
    for it in records[:6]:
        if not isinstance(it, dict):
            continue
        stat = it.get('stat')
        title = str(it.get('title') or '').strip()
        bvid = str(it.get('bvid') or '').strip()
        if not title or not bvid or not isinstance(stat, dict):
            continue
        metrics = {key: stat.get(key) for key in ('view', 'coin', 'favorite')}
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in metrics.values()):
            continue
        items.append({
            'source': 'bilibili',
            'title': title,
            **metrics,
            'url': 'https://www.bilibili.com/video/' + bvid,
            'retrieved_at': retrieved_at,
            'signal_type': 'platform_ranking_snapshot',
        })
    return items


def make_script(topic):
    if not isinstance(topic, dict) or topic.get('source') != 'bilibili':
        raise ValueError('script generation requires a live Bilibili source record')
    title = topic.get('title')
    url = topic.get('url')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('trend record is missing a title')
    if not isinstance(url, str) or not url.startswith('https://www.bilibili.com/video/'):
        raise ValueError('trend record is missing a canonical Bilibili video URL')
    metrics = {}
    for key in ('view', 'coin', 'favorite'):
        value = topic.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f'trend record has invalid {key} metric')
        metrics[key] = value
    retrieved_at = topic.get('retrieved_at')
    if not isinstance(retrieved_at, str) or not retrieved_at.strip():
        raise ValueError('trend record is missing its retrieval timestamp')
    metric_line = (
        f"截至本次抓取，平台记录播放量 {metrics['view']:,}、投币 {metrics['coin']:,}、"
        f"收藏 {metrics['favorite']:,}；数据时间：{retrieved_at}。"
    )
    return {
        'title': '热点观察：' + title.strip(),
        'hook': '呢條內容點解會出現在平台熱門榜單？我哋先睇數據，再拆解佢嘅表達方式。',
        'body': [
            metric_line,
            '呢啲只係平台熱度訊號，唔代表內容事實已核實，亦唔能夠證明觀眾願意付費。',
            '以下拆解只屬編輯假設：題材、開場、節奏同視覺呈現可能影響觀看；需要用獨立受眾測試驗證。',
        ],
        'climax': '真正值得複製嘅唔係照搬題材，而係用可核實資料，測試一個對觀眾有用嘅原創角度。',
        'ending': '想睇更多有來源、有反證嘅內容拆解，可以留言講你最想研究嘅題目。',
        'source': topic,
        'research_state': 'trend_signal_only',
        'fact_check_required': True,
        'publish_state': 'draft_only',
    }


def cmd_research():
    try:
        items = fetch_bilibili()
    except Exception as exc:
        raise RuntimeError(
            '热点研究已停止：未能取得实时 Bilibili 榜单；不会使用虚构样本或旧数据代替。'
        ) from exc
    if not items:
        raise RuntimeError(
            '热点研究已停止：没有可验证的实时榜单记录；不会生成伪热点文案。'
        )

    def score(x):
        return x.get('view', 0) * 0.6 + x.get('coin', 0) * 10 + x.get('favorite', 0) * 4

    ranked = sorted(items, key=score, reverse=True)
    top = ranked[0]
    script = make_script(top)
    (DATA / ('topic_' + today() + '.json')).write_text(json.dumps({
        'picked': top, 'ranked': ranked, 'research_state': 'trend_signal_only',
        'fact_check_required': True, 'publish_state': 'draft_only',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    sp = DATA / ('script_' + today() + '.json')
    sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PICKED:', top['title'], '| view:', top['view'], '| source:', top['url'])
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