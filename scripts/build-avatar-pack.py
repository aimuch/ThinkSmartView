#!/usr/bin/env python3
"""形象库（设计画布第 41 块）：把 Virtual-Assistant-Avatar 精选的形象做成设备用的小图包。

读 <源仓库>/avatars/catalog.json（只用精选的那些），每款三张透明 PNG（待机、唤醒、睡觉）按自带的取景范围
裁掉透明空边、最长边缩到 640 像素、转 WebP，再加一张 240 像素的缩略图；写出精简的 catalog.json。
原图、商品资料不进这个仓库，生成的图也不提交（默认写到 local/avatar-pack/，local/ 不进 git）。

    uv run --no-project --with pillow python scripts/build-avatar-pack.py --source ~/DevSpace/Virtual-Assistant-Avatar
    scripts/push-avatar-pack.sh 10.10.10.115      # 装到设备上（~/.local/share/thinksmart-client/avatar-pack）

已经生成过、源图没变的会跳过，重跑很快。
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
from pathlib import Path

from PIL import Image

BIG, THUMB = 640, 240
# 分组：泡泡玛特按 IP，另有大模型、节日、迪士尼。IP 名字太长的取短的
IP_LABEL = {'Twinkle Twinkle / 星星人': '星星人', 'THE MONSTERS / LABUBU': 'LABUBU'}
PACK_GROUP = {'models': '大模型', 'festivals': '节日', 'disney': '迪士尼'}
DISNEY = {'Mickey Mouse': '米老鼠', 'Donald Duck': '唐老鸭', 'Duffy': '达菲', 'LinaBell': '玲娜贝儿', 'StellaLou': '星黛露'}
STATES = (('idle', 'image', 'framing'), ('awake', 'awakeImage', 'awakeFraming'), ('sleep', 'sleepImage', 'sleepFraming'))


def clean(s):
    return ' '.join(str(s or '').replace('（', '(').replace('）', ')').split())


def describe(a):
    """-> (分组, 全名, 短名, 标签)。短名在分组里显示，全名在「全部」和语音里用。"""
    pack, ip = a.get('packId'), clean(a.get('ip'))
    if pack == 'disney':
        name = DISNEY.get(ip, clean(a.get('name')))
        return '迪士尼', name, name, ''
    if pack in ('models', 'festivals'):
        name = clean(a.get('name'))
        short = clean(a.get('variant')) if pack == 'models' else name
        return PACK_GROUP[pack], name, short, (a.get('holidays') or [''])[0]
    group = IP_LABEL.get(ip, ip)
    variant = clean(a.get('variant'))
    secret = '隐藏' in str(a.get('subtitle') or '') or 'secret' in variant.lower()
    variant = variant.replace('(Secret)', '').strip() or variant
    if a.get('series') == '助手基础形象':
        variant = '经典'
    return group, f'{group} · {variant}', variant, '隐藏' if secret else ''


def save(src, bounds, size, dst):
    im = Image.open(src).convert('RGBA')
    if bounds:
        im = im.crop(tuple(bounds))
    im.thumbnail((size, size), Image.LANCZOS)
    tmp = dst.with_suffix('.tmp')
    im.save(tmp, 'WEBP', quality=82 if size == BIG else 78, method=4)
    os.replace(tmp, dst)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--source', default=str(Path.home() / 'DevSpace' / 'Virtual-Assistant-Avatar'))
    ap.add_argument('--out', default=str(Path(__file__).resolve().parent.parent / 'local' / 'avatar-pack'))
    args = ap.parse_args()
    base = Path(args.source).expanduser() / 'avatars'
    catalog = json.loads((base / 'catalog.json').read_text(encoding='utf-8'))
    out = Path(args.out)
    (out / 'img').mkdir(parents=True, exist_ok=True)
    stamp_path = out / '.sources.json'
    stamps = json.loads(stamp_path.read_text()) if stamp_path.is_file() else {}
    items, jobs = [], []
    for a in catalog['avatars']:
        aid = a['id']
        if not all(a.get(k) for _, k, _ in STATES):
            print(f'跳过 {aid}：三张图不全', file=sys.stderr)
            continue
        group, name, short, tag = describe(a)
        files = {}
        for state, key, frame in STATES:
            src = base / a[key]
            bounds = (a.get(frame) or {}).get('bounds')
            st = src.stat()
            stamp = f'{st.st_size}:{int(st.st_mtime)}:{bounds}'
            for fname, size in [(f'{aid}-{state}.webp', BIG)] + ([(f'{aid}-thumb.webp', THUMB)] if state == 'idle' else []):
                dst = out / 'img' / fname
                fresh = stamps.get(fname) == stamp or (fname not in stamps and dst.is_file() and dst.stat().st_mtime >= st.st_mtime)
                if fresh and dst.is_file():
                    stamps[fname] = stamp
                else:
                    jobs.append((str(src), bounds, size, dst, fname, stamp))
            files[state] = f'img/{aid}-{state}.webp'
        files['thumb'] = f'img/{aid}-thumb.webp'
        items.append({'id': aid, 'name': name, 'short': short, 'group': group, 'tag': tag,
                      'series': clean(a.get('series')), 'files': files})
    with concurrent.futures.ProcessPoolExecutor() as pool:     # 一张要好几秒，分给几个核
        for job, fut in [(j, pool.submit(save, *j[:4])) for j in jobs]:
            fut.result()
            stamps[job[4]] = job[5]
    made = len(jobs)
    counts = {}
    for it in items:
        counts[it['group']] = counts.get(it['group'], 0) + 1
    tail = [g for g in PACK_GROUP.values() if g in counts]
    groups = sorted((g for g in counts if g not in tail), key=lambda g: (-counts[g], g)) + tail
    keep = {f for it in items for f in it['files'].values()}
    for f in (out / 'img').glob('*.webp'):           # 源仓库去掉的款，这里也删掉
        if f'img/{f.name}' not in keep:
            f.unlink()
            stamps.pop(f.name, None)
    body = {'groups': [{'name': g, 'count': counts[g]} for g in groups], 'avatars': items}
    version = hashlib.sha256(json.dumps([body, sorted(stamps.items())], ensure_ascii=False).encode()).hexdigest()[:12]
    (out / 'catalog.json').write_text(json.dumps({'version': version, **body}, ensure_ascii=False, indent=1), encoding='utf-8')
    stamp_path.write_text(json.dumps(stamps))
    size = sum(f.stat().st_size for f in (out / 'img').glob('*.webp'))
    print(f'{len(items)} 款，{len(groups)} 组，新生成 {made} 张，共 {size / 1e6:.1f} MB → {out}（版本 {version}）')


if __name__ == '__main__':
    main()
