"""接近传感器（设计画布第 59 块）：每次熄屏先量这台没人时的读数，比它高出一截才算有人走近；客户端可以关掉。"""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).with_name('vcnl4200-proximity-daemon.py')


def load():
    spec = importlib.util.spec_from_file_location('vcnl4200_proximity_daemon', SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_unit_that_reads_high_with_nobody_near_does_not_wake_itself():
    m = load()
    g = m.Gate()
    assert g.calibrate([0, 179, 179, 180, 182, 178, 179, 179]) == 182   # the stale 0 doesn't count
    assert g.threshold == 252
    assert not any(g.feed(v) for v in [179, 185, 160, 200, 230, 178] * 5)  # its usual level, and some wobble
    assert not g.feed(900)                  # one jump isn't enough
    assert g.feed(950)                      # a hand: twice in a row
    assert not g.feed(960)                  # counting starts again


def test_a_normal_unit_keeps_the_old_threshold():
    m = load()
    g = m.Gate()
    g.calibrate([3, 3, 2, 3, 4, 3])
    assert g.threshold == 74                # 4 + 70
    assert not g.feed(60) and not g.feed(70)
    assert not g.feed(90) and g.feed(120)


def test_level_follows_slow_drift_and_nothing_measured():
    m = load()
    g = m.Gate()
    g.calibrate([100] * 8)
    for _ in range(200):
        g.feed(40)                          # the room got darker: the level comes down slowly
    assert g.baseline < 45 and g.threshold == max(m.PROXIMITY_THRESHOLD, round(g.baseline + m.MARGIN))
    g2 = m.Gate()
    assert g2.calibrate([0, 0, 0]) is None and g2.threshold == m.PROXIMITY_THRESHOLD
    assert not g2.feed(30) and g2.baseline == 30                  # first good reading becomes the level


def test_switch_from_the_client(tmp_path):
    m = load()
    hint = tmp_path / 'hint.json'
    assert m.proximity_wanted(str(hint))                         # no hint yet: on
    hint.write_text(json.dumps({'hold': False, 'proximity': False}))
    assert not m.proximity_wanted(str(hint))
    hint.write_text(json.dumps({'hold': False}))
    assert m.proximity_wanted(str(hint))                         # an older client: on
    hint.write_text('not json')
    assert m.proximity_wanted(str(hint))
    out = tmp_path / 'status.json'
    m.write_status({'baseline': 182, 'threshold': 252}, str(out))
    assert json.loads(out.read_text())['threshold'] == 252
