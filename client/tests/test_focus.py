"""专注计时（设计画布第 47 块）：开始、暂停继续、到点、提前结束也记分钟、声景最多两层、跨天清零。"""
from thinksmart_client import focus


def test_start_pause_resume_and_finish():
    f = focus.Focus()
    v = f.start(25, now=1000)
    assert v['phase'] == 'focus' and v['left'] == 1500 and f.minutes == 25
    f.pause(now=1600)
    assert f.view(now=9999)['left'] == 900 and f.view()['paused']     # 暂停时不走
    f.resume(now=2000)
    assert f.tick(now=2899) is None
    assert f.tick(now=2900) == 'focus'
    assert f.today['minutes'] == 25 and f.today['rounds'] == 1 and f.phase is None


def test_break_and_odd_minutes():
    f = focus.Focus({'minutes': 45})
    assert f.start(phase='break', now=0)['left'] == focus.BREAK * 60
    assert f.tick(now=300) == 'break' and f.today['rounds'] == 0       # 休息不算番茄
    assert f.start(None, now=0)['left'] == 45 * 60                     # 没说多久：上次的
    assert f.start(10, now=0)['left'] == 600 and f.minutes == 45       # 说了别的分钟数：这次用，不改默认
    assert f.start(500, now=0)['left'] == 45 * 60                      # 不合理的：默认


def test_stopping_early_still_counts_the_minutes():
    f = focus.Focus()
    f.start(25, now=0)
    f.stop(now=610)
    assert f.today['minutes'] == 10 and f.today['rounds'] == 0 and f.phase is None


def test_sounds_at_most_two_known_layers_and_new_day_resets():
    f = focus.Focus({'sounds': ['rain', 'birds', 'ocean', 'nope']})
    assert f.sounds == ['rain', 'birds']
    assert f.set_sounds(['x', 'white', 'white']) == ['white']
    g = focus.Focus({'today': {'date': '2000-01-01', 'minutes': 99, 'rounds': 4}})
    assert g.today['minutes'] == 0
    assert focus.Focus(g.saved()).sounds == ['rain']
