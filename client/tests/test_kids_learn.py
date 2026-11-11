"""孩子和学习：最近 7 天、学一学（设计画布第 34 块）。"""
import pytest

from thinksmart_client import kids


def test_week_keeps_earlier_days(tmp_path, monkeypatch):
    monkeypatch.setattr(kids, 'USAGE', tmp_path / 'kids-usage.json')
    kids.save_usage('2026-11-08', 600)
    kids.save_usage('2026-11-09', 1200)
    kids.save_usage('2026-11-11', 300)
    assert kids.load_usage('2026-11-11') == 300
    assert kids.load_usage('2026-11-10') == 0
    w = kids.week('2026-11-11')
    assert [d['date'] for d in w][-1] == '2026-11-11' and len(w) == 7
    assert [d['minutes'] for d in w][-4:] == [10, 20, 0, 5]


def test_old_file_without_days(tmp_path, monkeypatch):
    f = tmp_path / 'kids-usage.json'
    f.write_text('{"date": "2026-11-10", "seconds": 120}')
    monkeypatch.setattr(kids, 'USAGE', f)
    kids.save_usage('2026-11-11', 60)
    assert [d['minutes'] for d in kids.week('2026-11-11')][-2:] == [2, 1]


def test_learn_prompt():
    t = kids.learn_prompt('poem', 5)
    assert t.startswith('【学习·背古诗】') and '5岁' in t
    assert 'self_screen_show_card' in kids.learn_prompt('chars', None)
    with pytest.raises(ValueError):
        kids.learn_prompt('nope', 5)
