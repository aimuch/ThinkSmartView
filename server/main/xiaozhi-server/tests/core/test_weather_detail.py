"""[xiaozhi-hub] 设备天气详情页的数据（设计画布第 47 块）：从当前小时起 24 小时、7 天的标签、空气质量分档。"""
from core.utils import open_meteo as om


def forecast():
    hours = [f"2026-11-13T{h:02d}:00" for h in range(24)] + [f"2026-11-14T{h:02d}:00" for h in range(24)]
    return {
        "current": {"time": "2026-11-13T14:15", "temperature_2m": 15.2, "apparent_temperature": 13.1, "weather_code": 61,
                    "relative_humidity_2m": 88, "wind_speed_10m": 12, "wind_direction_10m": 45},
        "hourly": {"time": hours, "temperature_2m": list(range(48)), "precipitation_probability": [10] * 48,
                   "weather_code": [0] * 48},
        "daily": {"time": [f"2026-11-{13 + i}" for i in range(7)], "weather_code": [61, 2, 0, 0, 1, 80, 3],
                  "temperature_2m_min": [13] * 7, "temperature_2m_max": [20] * 7,
                  "precipitation_probability_max": [80, 20, 0, 0, 10, 60, 30], "uv_index_max": [2.1] + [3] * 6,
                  "sunrise": ["2026-11-13T06:21"] * 7, "sunset": ["2026-11-13T16:58"] * 7},
    }


def test_detail_hours_days_and_air():
    air = {"current": {"pm2_5": 36, "pm10": 40, "ozone": 70, "nitrogen_dioxide": 20, "sulphur_dioxide": 5,
                       "carbon_monoxide": 600}}
    d = om.detail({"name": "上海市青浦区"}, forecast(), air)
    assert d["place"] == "上海市青浦区" and d["temp"] == 15.2 and d["icon"] == "🌧" and d["wind"] == "东北风 2 级"
    assert d["sunrise"] == "06:21" and d["sunset"] == "16:58" and d["uv"] == 2.1
    assert len(d["hours"]) == 24 and d["hours"][0]["hour"] == 14 and d["hours"][-1]["hour"] == 13   # 从这个小时起
    assert [x["label"] for x in d["days"]][:3] == ["今天", "明天", "周日"] and len(d["days"]) == 7
    by = {a["key"]: a for a in d["air"]}
    assert by["pm2_5"]["level"] == "良" and by["ozone"]["level"] == "优"
    assert by["carbon_monoxide"]["value"] == 0.6 and by["carbon_monoxide"]["level"] == "优"   # µg/m³ 换成 mg/m³


def test_no_air_quality_is_fine():
    assert om.detail({"name": "x"}, forecast(), None)["air"] == []


def test_air_levels():
    assert om.air_level(10, (35, 75, 115, 150, 250)) == "优"
    assert om.air_level(120, (35, 75, 115, 150, 250)) == "中度污染"
    assert om.air_level(999, (35, 75, 115, 150, 250)) == "严重污染"
