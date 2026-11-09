"""屏幕页面看门狗（kiosk_watch.py）：页面长时间没连上来才重启 kiosk 的网页进程，有冷却，没有 kiosk 时不动。"""
import signal
from types import SimpleNamespace

from thinksmart_client import kiosk_watch as kw


def make(pids=(4242,)):
    clock = {'t': 0.0}
    killed = []
    events = SimpleNamespace(queues=set())
    w = kw.Watch(events, now=lambda: clock['t'], find=lambda: list(pids), kill=lambda p, s: killed.append((p, s)))
    return w, clock, events, killed


def run_until(w, clock, until, step=kw.EVERY):
    out = []
    while clock['t'] < until:
        clock['t'] += step
        out += w.tick()
    return out


def test_restarts_page_after_it_stays_disconnected():
    w, clock, events, killed = make()
    run_until(w, clock, kw.GRACE - kw.EVERY)            # 刚启动：不动
    assert killed == []
    while not killed and clock['t'] < 600:
        run_until(w, clock, clock['t'] + kw.EVERY)
    assert killed == [(4242, signal.SIGTERM)] and kw.GRACE <= clock['t'] <= kw.GRACE + kw.STALE + kw.EVERY
    run_until(w, clock, clock['t'] + kw.COOLDOWN - kw.EVERY)       # 冷却期内不再动
    assert len(killed) == 1
    run_until(w, clock, clock['t'] + kw.STALE + 3 * kw.EVERY)
    assert len(killed) == 2


def test_connected_page_is_left_alone():
    w, clock, events, killed = make()
    events.queues.add(object())
    run_until(w, clock, 1000)
    assert killed == []
    events.queues.clear()                               # 断开不到 60 秒又连上：不动
    run_until(w, clock, 1000 + kw.STALE - kw.EVERY)
    events.queues.add(object())
    run_until(w, clock, 1200)
    assert killed == []


def test_no_kiosk_no_action():
    w, clock, events, killed = make(pids=())
    run_until(w, clock, 1000)
    assert killed == []
