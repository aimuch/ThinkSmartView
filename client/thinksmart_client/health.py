"""设备自己报健康状况（设计画布第 83 块）：每分钟一份交给网关，后台「设备」页每台一张卡片。

报的是：开机多久、客户端跑了多久、整机 CPU 和最占的几个进程、内存、温度、Wi‑Fi 信号、到网关多快、
屏幕上是哪一页、常驻循环出错重启了几次（bg.forever）、最近 20 条警告和错误。
不报对话内容：警告和错误里没有识别出的话（那些是 INFO），页面脚本错误只有出错的位置。
"""
import collections
import logging
import time
from pathlib import Path

from . import bg, perf

RECENT = collections.deque(maxlen=20)   # 最近的警告和错误
MSG_MAX = 240


class _Recent(logging.Handler):
    def emit(self, record):
        try:
            RECENT.append({'t': round(record.created, 1), 'level': record.levelname.lower(), 'name': record.name.rsplit('.', 1)[-1],
                           'msg': record.getMessage()[:MSG_MAX]})
        except Exception:  # noqa: BLE001  a log handler never raises
            pass


def install():
    """把警告和错误也记一份到 RECENT（客户端启动时调一次）。"""
    root = logging.getLogger()
    if not any(isinstance(h, _Recent) for h in root.handlers):
        h = _Recent(level=logging.WARNING)
        root.addHandler(h)


def wifi(iface='wlan0'):
    """{'signal': dBm} from /proc/net/wireless，没有 Wi‑Fi 时 None。"""
    try:
        for line in Path('/proc/net/wireless').read_text().splitlines()[2:]:
            name, _, rest = line.partition(':')
            if name.strip() == iface:
                return {'signal': round(float(rest.split()[2].rstrip('.')))}
    except (OSError, ValueError, IndexError):
        pass
    return None


class Health:
    def __init__(self):
        self.perf = perf.Perf()          # 自己的一份：不打扰设置页「性能」的曲线
        self.started = time.monotonic()
        self.perf.sample()               # 第一次只是起点：下一次的占用是这一分钟的平均

    def report(self, **extra):
        s = self.perf.sample()
        mem = s['mem']
        return {
            'uptime': s['uptime'], 'client_uptime': round(time.monotonic() - self.started),
            'cpu': s['cpu']['usage'], 'top': [{'name': p['name'], 'cpu': p['cpu']} for p in s['top'][:4]],
            'mem': round(100 * mem['used'] / mem['total']) if mem['total'] else None,
            'temp': s['temps']['cpu'], 'gpu_temp': s['temps']['gpu'], 'disk': s['disk']['root'],
            'wifi': wifi(), 'restarts': dict(bg.restarts), 'errors': list(RECENT), **extra,
        }
