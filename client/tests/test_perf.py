"""性能页（perf.py）：两次读之间的差算占用；温度分级；进程翻成中文名、同名合并。用临时目录假装 /proc 和 /sys。"""
from thinksmart_client import perf


def fake(tmp_path, ticks, ffmpeg_jiffies, temp_c=61.5, idle=900):
    proc = tmp_path / 'proc'
    proc.mkdir(exist_ok=True)
    (proc / 'stat').write_text(
        f"cpu  {ticks} 0 0 {idle} 0 0 0 0 0 0\n"
        f"cpu0 {ticks} 0 0 {idle} 0 0 0 0 0 0\nintr 1\n")
    (proc / 'meminfo').write_text("MemTotal: 2000000 kB\nMemAvailable: 500000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (proc / 'loadavg').write_text("1.20 0.80 0.50 1/300 999\n")
    (proc / 'uptime').write_text("3725.4 100.0\n")
    (proc / 'net').mkdir(exist_ok=True)
    (proc / 'net' / 'dev').write_text("Inter-|\n face |\n  wlan0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n")
    for pid, comm, j, cmd in ((101, 'ffmpeg', ffmpeg_jiffies, 'ffmpeg -i /dev/video10'), (102, 'python', 10, 'python -m thinksmart_client'),
                              (103, 'WebKitWebProces', 5, ''), (104, 'WebKitWebProces', 5, '')):
        d = proc / str(pid)
        d.mkdir(exist_ok=True)
        fields = ['S'] + ['0'] * 10 + [str(j), '0'] + ['0'] * 8 + ['1000']
        (d / 'stat').write_text(f"{pid} ({comm}) " + ' '.join(fields))
        (d / 'cmdline').write_bytes(cmd.replace(' ', '\0').encode())
    thermal = tmp_path / 'thermal'
    for i, (t, v) in enumerate((('cpu0-thermal', temp_c), ('gpu-thermal', 48.0), ('pm8953-thermal', 44.0))):
        z = thermal / f'thermal_zone{i}'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'type').write_text(t + '\n')
        (z / 'temp').write_text(str(int(v * 1000)))
    return proc, thermal


def test_two_samples(tmp_path, monkeypatch):
    proc, thermal = fake(tmp_path, 100, 0)
    monkeypatch.setattr(perf, 'PROC', proc)
    monkeypatch.setattr(perf, 'THERMAL', thermal)
    monkeypatch.setattr(perf, 'SYS_CPU', tmp_path / 'nocpu')
    monkeypatch.setattr(perf, 'GPU', tmp_path / 'nogpu')
    monkeypatch.setattr(perf.os, 'cpu_count', lambda: 1)
    clock = iter([10.0, 12.0])
    monkeypatch.setattr(perf.time, 'monotonic', lambda: next(clock))
    p = perf.Perf()
    first = p.sample()
    assert first['cpu']['usage'] == 0 and first['top'] == [] and first['uptime'] == 3725
    fake(tmp_path, 400, 680, idle=1600)          # 忙 300 / 共 1000 → 30%；2 秒里 ffmpeg 用了 680 个 jiffy（100/s）→ 340%
    s = p.sample()
    assert s['cpu']['usage'] == 30.0 and s['cpu']['cores'][0]['usage'] == 30.0
    assert s['cpu']['temp'] == 61.5 and s['cpu']['level'] == 'warn' and s['temps']['gpu'] == 48.0
    assert s['top'][0] == {'name': '摄像头画面', 'cpu': 340.0, 'mem': 1000 * perf.PAGE}
    names = [t['name'] for t in s['top']]
    assert '小麦客户端' in names and names.count('屏幕浏览器') == 1          # 同名合并
    assert s['mem']['used'] == 1500000 * 1024 and s['net']['rx'] == 0 and s['load'][0] == 1.2


def test_levels():
    assert [perf.level(x) for x in (None, 40, 55, 69.9, 70)] == [None, 'ok', 'warn', 'warn', 'hot']
