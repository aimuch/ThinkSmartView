"""屏幕页面：保证 kiosk 浏览器用的是这个版本的页面，长时间没连上来就重新打开。

2026-11-09 屏幕一直停在空白首页（没有时钟、模型、对话）：kiosk 的 WebKitGTK 用的是磁盘缓存里很旧的
index.html 和 app.js，旧 app.js 找不到新页面里没有的元素，一开头就报错，后面的代码（包括收推送）都没跑。
发布包里的文件时间戳是 1970 年，WebKit 对以前某次没带 Cache-Control 的回应按「最后修改越早、缓存越久」
算成了好几年都新鲜，之后不再向客户端要；现在的回应都带 no-store，但旧的缓存条目还在。

所以：
- 客户端换了版本后第一次启动时，清掉 kiosk 的网页缓存，再让 kiosk 重新打开页面（结束网页进程，
  thinksmart-kiosk.py 两秒后重新打开）。版本没变的重启不动，页面自己会重连。
- 兜底：每 15 秒看一次，启动 30 秒以后，如果连续 30 秒没有任何页面连着 /events，而本机又有 kiosk 的
  网页进程，也清缓存、重新打开。5 分钟内最多一次。
没有 kiosk（开发机、在别的浏览器里看）时什么都不做。
"""

import asyncio
import logging
import os
import shutil
import signal
import time
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

EVERY = 15      # 秒：多久看一次
GRACE = 30      # 客户端刚启动：正常的页面十几秒内就连上了
STALE = 30      # 连续这么久没有页面连着才动手
COOLDOWN = 300  # 两次之间至少隔这么久
WEB_PROCESS = 'WebKitWebProces'      # /proc/<pid>/comm 只保留前 15 个字符
NETWORK_PROCESS = 'WebKitNetworkPr'  # 磁盘缓存在网络进程手里：先结束它，再删缓存
CACHE_DIRS = [Path('~/.cache/thinksmart-kiosk/WebKitCache').expanduser(),
              Path('~/.cache/thinksmart-kiosk/CacheStorage').expanduser()]
SEEN_VERSION = Path('~/.local/share/thinksmart-client/kiosk-version').expanduser()


def processes(comm):
    """本用户叫这个名字的进程。"""
    pids, uid = [], os.getuid()
    for d in os.listdir('/proc') if os.path.isdir('/proc') else []:
        if not d.isdigit():
            continue
        try:
            with open(f'/proc/{d}/comm') as f:
                if f.read().strip() != comm:
                    continue
            if os.stat(f'/proc/{d}').st_uid == uid:
                pids.append(int(d))
        except OSError:
            pass
    return pids


def _kill(pids, kill):
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass


def reopen_page(why, find=processes, kill=os.kill, cache_dirs=None, wait=0.5):
    """清掉 kiosk 的网页缓存，让它重新打开页面；没有 kiosk 时什么都不做。返回结束的网页进程。"""
    pages = find(WEB_PROCESS)
    if not pages:
        return []
    _kill(find(NETWORK_PROCESS), kill)
    if wait:
        time.sleep(wait)
    for d in CACHE_DIRS if cache_dirs is None else cache_dirs:
        shutil.rmtree(d, ignore_errors=True)
    _kill(pages, kill)
    log.warning('%s: cleared the kiosk cache and reopened the page (WebKit pid %s)', why, ', '.join(map(str, pages)))
    return pages


def version_changed(path=SEEN_VERSION, version=__version__):
    """这个版本是不是第一次启动（记下来，下次就不算了）。"""
    try:
        if path.read_text().strip() == version:
            return False
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version)
    except OSError:
        pass
    return True


class Watch:
    def __init__(self, events, now=time.monotonic, reopen=None):
        self.events, self.now = events, now
        self.reopen = reopen or (lambda: reopen_page('screen page not connected'))
        self.started = now()
        self.empty_since = None
        self.last_kick = None

    def tick(self):
        """看一次；动了手返回 True。"""
        t = self.now()
        if self.events.queues:
            self.empty_since = None
            return False
        if self.empty_since is None:
            self.empty_since = t
        if (t - self.started < GRACE or t - self.empty_since < STALE
                or (self.last_kick is not None and t - self.last_kick < COOLDOWN)):
            return False
        if not self.reopen():
            return False
        self.last_kick, self.empty_since = t, None
        return True


async def watch(events):
    await asyncio.sleep(3)   # 等网页服务开始监听：kiosk 两秒后就会重新打开页面
    loop = asyncio.get_running_loop()
    if version_changed():
        await loop.run_in_executor(None, reopen_page, f'client {__version__} started for the first time')
    w = Watch(events)
    while True:
        await asyncio.sleep(EVERY)
        try:
            await loop.run_in_executor(None, w.tick)   # 动手时要等网络进程退出，不占事件循环
        except Exception:  # noqa: BLE001  看门狗自己不能把客户端带崩
            log.exception('kiosk watch')
