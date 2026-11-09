"""设备性能（设计画布第 30 块）：CPU（每个核的占用、频率、温度）、GPU（频率、温度）、内存、温度、存储、
网速、负载和最占资源的进程。只在页面打开时（每 2 秒一次 GET /api/perf）读，数据都来自 /proc 和 /sys，
不装别的程序。占用和网速是两次读之间的差，所以第一次读时为 0。

2026-11-10 起有这一页：拍照问的摄像头没关，ffmpeg 占了 3 个多核几个小时，设备 60℃，有这一页一眼能看到。
"""

import os
import time
from pathlib import Path

PROC = Path('/proc')
SYS_CPU = Path('/sys/devices/system/cpu')
THERMAL = Path('/sys/class/thermal')
GPU = Path('/sys/class/devfreq/1c00000.gpu')
NET_IFACE = 'wlan0'
TOP = 6
HISTORY = 30                 # 曲线上留几个点（每 2 秒一个：一分钟）
CLK = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
PAGE = os.sysconf('SC_PAGE_SIZE') if hasattr(os, 'sysconf') else 4096

# 进程名翻译成人看得懂的（设计画布第 30 块）
NAMES = [('thinksmart_client', '小麦客户端'), ('thinksmart-kiosk', '屏幕程序'), ('WebKitWebProces', '屏幕浏览器'),
         ('WebKitNetworkPr', '屏幕浏览器（网络）'), ('tscam-bridge', '摄像头'), ('ffmpeg', '摄像头画面'),
         ('weston', '显示（Weston）'), ('pipewire', '声音（PipeWire）'), ('wireplumber', '声音（WirePlumber）'),
         ('bt_helper', '蓝牙'), ('NetworkManager', '网络'), ('sshd', '远程登录'), ('idle-blanker', '熄屏'),
         ('screen-wake', '亮屏'), ('gpu-watchdog', 'GPU 看门狗')]


def _read(path, default=''):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def _int(path, default=None):
    try:
        return int(_read(path).strip())
    except ValueError:
        return default


def cpu_times():
    """{'cpu': (busy, total), 'cpu0': ..., ...} from /proc/stat (jiffies)."""
    out = {}
    for line in _read(PROC / 'stat').splitlines():
        if not line.startswith('cpu'):
            break
        name, *vals = line.split()
        v = [int(x) for x in vals[:8]]
        idle = v[3] + v[4]                  # idle + iowait
        out[name] = (sum(v) - idle, sum(v))
    return out


def temps():
    """{type: ℃} for every thermal zone (cpu0-thermal … gpu-thermal, pm8953-thermal)."""
    out = {}
    for z in sorted(THERMAL.glob('thermal_zone*')):
        t = _int(z / 'temp')
        if t is not None:
            out[_read(z / 'type').strip() or z.name] = round(t / 1000, 1)
    return out


def meminfo():
    m = {}
    for line in _read(PROC / 'meminfo').splitlines():
        k, _, rest = line.partition(':')
        try:
            m[k] = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    return m


def disk(path):
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    return {'total': total, 'used': total - st.f_bfree * st.f_frsize}


def net_bytes(iface=NET_IFACE):
    for line in _read(PROC / 'net' / 'dev').splitlines():
        name, _, rest = line.partition(':')
        if name.strip() == iface:
            v = rest.split()
            return int(v[0]), int(v[8])
    return None


def label(comm, cmdline):
    text = f'{comm} {cmdline}'
    # 没有命令行的是内核线程（sugov:0、kworker…），合成一项
    return next((n for key, n in NAMES if key in text), comm if cmdline.strip() else '系统内核')


def processes():
    """{pid: (cpu jiffies, rss bytes, comm, cmdline)}"""
    out = {}
    for d in PROC.iterdir() if PROC.is_dir() else []:
        if not d.name.isdigit():
            continue
        try:
            stat = (d / 'stat').read_text()
            comm = stat[stat.index('(') + 1:stat.rindex(')')]
            f = stat[stat.rindex(')') + 2:].split()
            jiffies = int(f[11]) + int(f[12])            # utime + stime
            rss = int(f[21]) * PAGE
            cmd = (d / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')[:200]
        except (OSError, ValueError, IndexError):
            continue
        out[int(d.name)] = (jiffies, rss, comm, cmd)
    return out


def level(c):
    """温度颜色：55℃ 以下 ok、55～70 warn、70 以上 hot。"""
    return None if c is None else 'ok' if c < 55 else 'warn' if c < 70 else 'hot'


class Perf:
    def __init__(self):
        self.prev = None          # (monotonic, cpu_times, processes, net)
        self.history = []         # CPU 总占用 %，最近 HISTORY 次

    def sample(self):
        now = time.monotonic()
        cpu, procs, net = cpu_times(), processes(), net_bytes()
        prev, self.prev = self.prev, (now, cpu, procs, net)
        dt = (now - prev[0]) if prev else 0

        def pct(name):
            if not prev or name not in prev[1] or name not in cpu:
                return 0.0
            busy = cpu[name][0] - prev[1][name][0]
            total = cpu[name][1] - prev[1][name][1]
            return round(100 * busy / total, 1) if total > 0 else 0.0

        t = temps()
        cores = []
        for i in range(os.cpu_count() or 0):
            freq = _int(SYS_CPU / f'cpu{i}' / 'cpufreq' / 'scaling_cur_freq')
            ct = t.get(f'cpu{i}-thermal')
            cores.append({'usage': pct(f'cpu{i}'), 'mhz': round(freq / 1000) if freq else None, 'temp': ct, 'level': level(ct)})
        total = pct('cpu')
        if prev:
            self.history = (self.history + [total])[-HISTORY:]
        max_mhz = _int(SYS_CPU / 'cpu0' / 'cpufreq' / 'cpuinfo_max_freq')

        gpu_hz, gpu_max = _int(GPU / 'cur_freq'), _int(GPU / 'max_freq')
        gpu_t = t.get('gpu-thermal')

        m = meminfo()
        mem_total, mem_avail = m.get('MemTotal', 0), m.get('MemAvailable', 0)
        swap_total, swap_free = m.get('SwapTotal', 0), m.get('SwapFree', 0)

        top = []
        if prev and dt > 0:
            for pid, (j, rss, comm, cmd) in procs.items():
                before = prev[2].get(pid)
                used = j - before[0] if before and before[2] == comm else 0
                top.append({'name': label(comm, cmd), 'pid': pid, 'cpu': round(100 * used / CLK / dt, 1), 'mem': rss})
            top.sort(key=lambda p: (-p['cpu'], -p['mem']))
            # 同名的（比如几个屏幕浏览器进程）合并显示
            merged = {}
            for p in top:
                cur = merged.setdefault(p['name'], {'name': p['name'], 'cpu': 0.0, 'mem': 0})
                cur['cpu'] = round(cur['cpu'] + p['cpu'], 1)
                cur['mem'] += p['mem']
            top = sorted(merged.values(), key=lambda p: (-p['cpu'], -p['mem']))[:TOP]

        rx = tx = None
        if prev and net and prev[3] and dt > 0:
            rx, tx = round((net[0] - prev[3][0]) / dt), round((net[1] - prev[3][1]) / dt)

        load = _read(PROC / 'loadavg').split()
        uptime = float((_read(PROC / 'uptime').split() or ['0'])[0])
        cpu_temps = [c['temp'] for c in cores if c['temp'] is not None]
        hottest = max(cpu_temps) if cpu_temps else None
        return {
            'cpu': {'usage': total, 'cores': cores, 'max_mhz': round(max_mhz / 1000) if max_mhz else None,
                    'history': self.history, 'temp': hottest, 'level': level(hottest)},
            'gpu': {'mhz': round(gpu_hz / 1e6) if gpu_hz else None, 'max_mhz': round(gpu_max / 1e6) if gpu_max else None,
                    'temp': gpu_t, 'level': level(gpu_t)},
            'temps': {'cpu': hottest, 'gpu': gpu_t, 'pmic': t.get('pm8953-thermal'),
                      'level': level(max([x for x in (hottest, gpu_t) if x is not None], default=None))},
            'mem': {'total': mem_total, 'used': mem_total - mem_avail, 'available': mem_avail,
                    'swap_total': swap_total, 'swap_used': swap_total - swap_free},
            'disk': {'root': disk('/'), 'boot': disk('/boot')},
            'net': {'iface': NET_IFACE, 'rx': rx, 'tx': tx},
            'load': [float(x) for x in load[:3]] if len(load) >= 3 else None,
            'uptime': round(uptime),
            'top': top,
        }

    def brief(self):
        """一句话（语音用）：CPU、最热温度、内存，都正常或哪里偏高。"""
        s = self.sample()
        if not s['cpu']['history']:          # 第一次读没有占用：隔一下再读一次
            time.sleep(0.5)
            s = self.sample()
        mem = s['mem']
        mem_pct = round(100 * mem['used'] / mem['total']) if mem['total'] else 0
        hot = max([x for x in (s['temps']['cpu'], s['temps']['gpu']) if x is not None], default=None)
        lvl = level(hot)
        parts = [f"CPU {round(s['cpu']['usage'])}%"]
        if hot is not None:
            parts.append(f'最热 {round(hot)}℃')
        parts.append(f'内存用了 {mem_pct}%')
        verdict = {'ok': '都正常。', 'warn': '温度有点高。', 'hot': '温度太高了，最好让它歇一会儿。', None: ''}[lvl]
        if s['top'] and s['top'][0]['cpu'] > 150:
            verdict += f"{s['top'][0]['name']}占得最多。"
        return '，'.join(parts) + '，' + verdict, s
