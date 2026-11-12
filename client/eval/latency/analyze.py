import sys, re, statistics as st
d = sys.argv[1]
runs = [l.split('\t') for l in open(f'{d}/runs.log').read().split('\n') if l]
cl = []
for l in (x for x in open(f'{d}/client.log') if 'thinksmart_client.app: ' in x):
    t = float(l.split()[0]); cl.append((t, l.split('thinksmart_client.app: ')[1].strip()))
sse = [(float(a), b, c) for a, b, c in (l.rstrip('\n').split('\t') for l in open(f'{d}/sse.log'))]
hub = [l for l in open(f'{d}/hub.log')]
vad = [int(re.search(r'有声 (\d+) ms', l).group(1)) for l in hub if '判定说完' in l]
asr = [int(re.search(r'识别 (\d+) ms', l).group(1)) for l in hub if '] 识别 ' in l]
post = [int(re.search(r'发出文字 (\d+) ms', l).group(1)) for l in hub if '发出文字' in l]
rows = []
for i, (f, _t0, t1, dur) in enumerate(runs):
    t1 = float(t1)
    nxt = float(runs[i + 1][1]) if i + 1 < len(runs) else 1e12
    rec = next(t for t, m in cl if t1 < t < nxt and m.startswith('recognised'))
    msg = next(t for t, e, v in sse if t1 < t < nxt and e == 'message' and v.startswith("('user'"))
    spk = next((t for t, e, v in sse if t1 < t < nxt and e == 'state' and v == 'speaking'), None)
    vad_end = rec - post[i] / 1000
    r = dict(file=f, speech=float(dur), vad=round((vad_end - t1) * 1000), asr=asr[i], stt=round((rec - t1) * 1000),
             client=round((msg - rec) * 1000), shown=round((msg - t1) * 1000),
             reply=round((spk - msg) * 1000) if spk else None, vadsil=vad[i])
    rows.append(r)
    print('\t'.join(f'{k}={v}' for k, v in r.items()))
for kind in 'sl':
    g = [r for r in rows if r['file'].startswith(kind)]
    print(('短句' if kind == 's' else '长句'), len(g), '次平均:', {k: round(st.mean(r[k] for r in g if r[k] is not None)) for k in ('speech', 'vad', 'asr', 'stt', 'client', 'shown', 'reply')})
