"""屏幕页面（kiosk_watch.py）：换版本后清 kiosk 缓存并重开页面；页面长时间没连上也这样，有冷却；没有 kiosk 时不动。"""
import signal
from types import SimpleNamespace

from thinksmart_client import kiosk_watch as kw


def make():
    clock = {'t': 0.0}
    calls = []
    events = SimpleNamespace(queues=set())
    w = kw.Watch(events, now=lambda: clock['t'], reopen=lambda: calls.append(clock['t']) or True)
    return w, clock, events, calls


def run_until(w, clock, until, step=kw.EVERY):
    while clock['t'] < until:
        clock['t'] += step
        w.tick()


def test_reopens_after_page_stays_disconnected_with_cooldown():
    w, clock, events, calls = make()
    run_until(w, clock, kw.GRACE - kw.EVERY)              # 刚启动：不动
    assert calls == []
    while not calls and clock['t'] < 600:
        run_until(w, clock, clock['t'] + kw.EVERY)
    assert len(calls) == 1 and kw.GRACE <= calls[0] <= kw.GRACE + kw.STALE + kw.EVERY
    run_until(w, clock, calls[0] + kw.COOLDOWN - kw.EVERY)   # 冷却期内不再动
    assert len(calls) == 1
    run_until(w, clock, clock['t'] + kw.STALE + 3 * kw.EVERY)
    assert len(calls) == 2


def test_connected_page_is_left_alone():
    w, clock, events, calls = make()
    events.queues.add(object())
    run_until(w, clock, 1000)
    events.queues.clear()                                 # 断开不到 30 秒又连上：不动
    run_until(w, clock, 1000 + kw.STALE - kw.EVERY)
    events.queues.add(object())
    run_until(w, clock, 1200)
    assert calls == []


def test_reopen_page_clears_cache_then_restarts_page(tmp_path):
    cache = tmp_path / 'WebKitCache'
    (cache / 'Version 17').mkdir(parents=True)
    procs = {kw.WEB_PROCESS: [7], kw.NETWORK_PROCESS: [5]}
    killed = []
    pages = kw.reopen_page('test', find=lambda c: procs.get(c, []), kill=lambda p, s: killed.append((p, s)),
                           cache_dirs=[cache], wait=0)
    assert pages == [7] and killed == [(5, signal.SIGTERM), (7, signal.SIGTERM)] and not cache.exists()


def test_no_kiosk_no_action(tmp_path):
    cache = tmp_path / 'WebKitCache'
    cache.mkdir()
    killed = []
    assert kw.reopen_page('test', find=lambda c: [], kill=lambda p, s: killed.append(p), cache_dirs=[cache], wait=0) == []
    assert killed == [] and cache.exists()


def test_version_changed_only_once(tmp_path):
    f = tmp_path / 'kiosk-version'
    assert kw.version_changed(f, '0.1.635') is True
    assert kw.version_changed(f, '0.1.635') is False
    assert kw.version_changed(f, '0.1.636') is True
