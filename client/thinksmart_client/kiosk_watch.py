"""屏幕页面看门狗：屏幕上的浏览器长时间没连上来，就让它重新打开页面。

设备屏幕是 kiosk 里的 WebKitGTK，页面靠 /events 收设备推来的状态。实测客户端重启（更新、崩溃）以后，
WebKitGTK 有时整页的网络加载都卡住：旧连接停在 CLOSE-WAIT，新的 EventSource 和 fetch 都发不出去，
页面里自己重连也没用，屏幕就停在空白首页（没有时钟、模型、对话），直到浏览器重启。
结束网页进程后 kiosk 两秒内重新打开页面（thinksmart-kiosk.py 的 web-process-terminated），马上就能连上。

所以这里每 15 秒看一次：客户端启动 90 秒以后，如果连续 60 秒没有任何页面连着 /events，而本机又有
kiosk 的网页进程（同一个用户的 WebKitWebProcess），就结束它。5 分钟内最多做一次，免得反复重启。
没有 kiosk（开发机、在别的浏览器里看）时什么都不做。
"""

import asyncio
import logging
import os
import signal
import time

log = logging.getLogger(__name__)

EVERY = 15      # 秒：多久看一次
GRACE = 90      # 客户端刚启动：页面还在加载
STALE = 60      # 连续这么久没有页面连着才动手
COOLDOWN = 300  # 两次之间至少隔这么久
WEB_PROCESS = 'WebKitWebProces'   # /proc/<pid>/comm 只保留前 15 个字符


def web_processes():
    """本用户的 WebKit 网页进程（kiosk 的页面就在里面）。"""
    pids, uid = [], os.getuid()
    for d in os.listdir('/proc') if os.path.isdir('/proc') else []:
        if not d.isdigit():
            continue
        try:
            with open(f'/proc/{d}/comm') as f:
                if f.read().strip() != WEB_PROCESS:
                    continue
            if os.stat(f'/proc/{d}').st_uid == uid:
                pids.append(int(d))
        except OSError:
            pass
    return pids


class Watch:
    def __init__(self, events, now=time.monotonic, find=web_processes, kill=os.kill):
        self.events, self.now, self.find, self.kill = events, now, find, kill
        self.started = now()
        self.empty_since = None
        self.last_kick = None

    def tick(self):
        """看一次；动了手返回结束的进程号列表。"""
        t = self.now()
        if self.events.queues:
            self.empty_since = None
            return []
        if self.empty_since is None:
            self.empty_since = t
        if (t - self.started < GRACE or t - self.empty_since < STALE
                or (self.last_kick is not None and t - self.last_kick < COOLDOWN)):
            return []
        pids = self.find()
        if not pids:
            return []
        log.warning('screen page not connected for %d s: restarting the kiosk page (WebKit pid %s)',
                    t - self.empty_since, ', '.join(map(str, pids)))
        for pid in pids:
            try:
                self.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.last_kick, self.empty_since = t, None
        return pids


async def watch(events):
    w = Watch(events)
    while True:
        await asyncio.sleep(EVERY)
        try:
            w.tick()
        except Exception:  # noqa: BLE001  看门狗自己不能把客户端带崩
            log.exception('kiosk watch')
