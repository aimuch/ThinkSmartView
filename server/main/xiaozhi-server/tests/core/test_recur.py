"""清单的重复规则（设计画布第 76 块）。"""
import datetime as dt

import pytest

from core.utils import recur

D = dt.date


def test_daily_workdays_weekly():
    assert recur.next_due({"freq": "daily", "interval": 3}, D(2026, 11, 16)) == D(2026, 11, 19)
    assert recur.next_due("workdays", D(2026, 11, 20)) == D(2026, 11, 23)          # 周五 → 周一
    every2 = {"freq": "weekly", "interval": 2, "days": [1, 4]}
    seq, d = [], D(2026, 11, 16)                                                    # 周一
    for _ in range(4):
        d = recur.next_due(every2, d)
        seq.append(d)
    assert seq == [D(2026, 11, 19), D(2026, 11, 30), D(2026, 12, 3), D(2026, 12, 14)]


def test_monthly_last_day_nth_weekday_and_yearly():
    assert recur.next_due({"freq": "monthly", "monthday": 31}, D(2027, 1, 31)) == D(2027, 2, 28)
    assert recur.next_due({"freq": "monthly", "monthday": -1}, D(2027, 2, 28)) == D(2027, 3, 31)
    assert recur.next_due({"freq": "monthly", "nth": [2, 2]}, D(2026, 11, 16)) == D(2026, 12, 8)   # 第二个周二
    assert recur.next_due({"freq": "monthly", "nth": [-1, 5]}, D(2026, 11, 16)) == D(2026, 11, 27)  # 最后一个周五
    assert recur.next_due({"freq": "monthly", "interval": 3, "monthday": 15}, D(2026, 11, 15)) == D(2027, 2, 15)
    assert recur.next_due({"freq": "yearly", "month": 2, "day": 29}, D(2027, 1, 1)) == D(2027, 2, 28)
    assert recur.next_due({"freq": "yearly", "month": 3, "day": 2}, D(2027, 3, 2)) == D(2028, 3, 2)


def test_lunar_yearly():
    pytest.importorskip("cnlunar")
    # 农历腊月初八：2027-01-15（2027 年春节 2 月 6 日）、2028-01-04（2028 年春节 1 月 26 日）
    assert recur.next_due({"freq": "lunar", "month": 12, "day": 8}, D(2026, 11, 16)) == D(2027, 1, 15)
    assert recur.next_due({"freq": "lunar", "month": 12, "day": 8}, D(2027, 1, 15)) == D(2028, 1, 4)
    assert recur.next_due({"freq": "lunar", "month": 8, "day": 15}, D(2026, 11, 16)) == D(2027, 9, 15)   # 中秋


def test_end_rules_and_text():
    r = {"freq": "daily", "until": "2026-11-18"}
    assert recur.next_due(r, D(2026, 11, 17)) == D(2026, 11, 18) and recur.next_due(r, D(2026, 11, 18)) is None
    assert recur.next_due({"freq": "daily", "count": 3}, D(2026, 11, 17), done_count=3) is None
    assert recur.describe({"freq": "weekly", "interval": 2, "days": [1, 4]}) == "每两周的周一、周四"
    assert recur.describe({"freq": "monthly", "monthday": 15}) == "每月 15 号"
    assert recur.describe({"freq": "monthly", "monthday": -1}) == "每月最后一天"
    assert recur.describe({"freq": "monthly", "nth": [2, 2]}) == "每月第二个周二"
    assert recur.describe({"freq": "lunar", "month": 12, "day": 8}) == "农历每年腊月初八"
    assert recur.describe({"freq": "yearly", "month": 3, "day": 2, "count": 5}) == "每年 3 月 2 日，共 5 次"
    assert recur.first_due({"freq": "weekly", "days": [1]}, D(2026, 11, 16)) == D(2026, 11, 16)
    assert recur.first_due({"freq": "monthly", "monthday": 15}, D(2026, 11, 16)) == D(2026, 12, 15)
    with pytest.raises(ValueError):
        recur.normalize({"freq": "weekly"})
