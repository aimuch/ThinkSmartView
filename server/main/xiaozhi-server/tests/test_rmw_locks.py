"""[xiaozhi-hub] 读改写要串行（代码评审 2026-11-19）：后台的保存在线程池里跑，两个请求同时改同一份数据时，
以前没加锁的地方后写的会盖掉先写的。"""
import threading

from core.agent import wake_arbiter


def test_wake_settings_saved_together_keep_both(tmp_path, monkeypatch):
    monkeypatch.setattr(wake_arbiter, "_path", lambda: str(tmp_path / "wake.json"))
    real_load = wake_arbiter.load
    gate = threading.Barrier(2, timeout=1)

    def slow_load():
        d = real_load()
        try:
            gate.wait()          # both readers read before either writes: the old race
        except threading.BrokenBarrierError:
            pass                 # with the lock only one gets here at a time
        return d
    monkeypatch.setattr(wake_arbiter, "load", slow_load)
    window = next(w for w in wake_arbiter.WINDOWS if w != wake_arbiter.DEFAULTS["window"])
    a = threading.Thread(target=wake_arbiter.save, kwargs={"enabled": not wake_arbiter.DEFAULTS["enabled"]})
    b = threading.Thread(target=wake_arbiter.save, kwargs={"window": window})
    a.start(); b.start(); a.join(); b.join()
    monkeypatch.setattr(wake_arbiter, "load", real_load)
    d = wake_arbiter.load()
    assert d["enabled"] == (not wake_arbiter.DEFAULTS["enabled"]) and d["window"] == window
