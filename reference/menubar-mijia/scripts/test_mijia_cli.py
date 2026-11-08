#!/usr/bin/env python3
"""`scripts/mijia` 与 `scripts/mijia-mcp` 的回归测试：只读临时快照、注入假的 open 与假时钟，不打开真实应用。

运行：python3 scripts/test_mijia_cli.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Loading the extension-less script would otherwise drop a __pycache__ entry next to it.
sys.dont_write_bytecode = True

SCRIPT = Path(__file__).resolve().with_name("mijia")
MCP_SCRIPT = Path(__file__).resolve().with_name("mijia-mcp")
_loader = importlib.machinery.SourceFileLoader("mijia_cli", str(SCRIPT))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
assert _spec is not None and _spec.loader is not None
mijia = importlib.util.module_from_spec(_spec)
sys.modules[_loader.name] = mijia
_spec.loader.exec_module(mijia)

TOKEN = "t" * 40


def make_snapshot() -> dict:
    return {
        "schemaVersion": 1,
        "updatedAt": "2026-11-06T08:00:00.000Z",
        "bridgeToken": TOKEN,
        "connectionSummary": "Home Assistant · 米家云端直连",
        "isStale": False,
        "devices": [
            {
                "id": "dev_living_light",
                "name": "客厅吸顶灯",
                "roomId": "living",
                "roomName": "客厅",
                "roomSortOrder": 0,
                "category": "light",
                "categoryTitle": "灯",
                "model": "yeelink.light.ceiling",
                "stateText": "已开 · 80%",
                "isOnline": True,
                "isActiveOn": True,
                "isPrimaryToggleable": True,
                "isFavorite": True,
                "pinyinInitials": "ktxdd",
                "pinyinFull": "",
                "roomPinyinInitials": "kt",
                "capabilities": {
                    "canTogglePower": True,
                    "brightnessWritable": True,
                    "brightnessPercent": 80,
                    "colorWritable": True,
                    "colorTempKelvin": 4000,
                    "minKelvin": 2700,
                    "maxKelvin": 6500,
                },
            },
            {
                "id": "dev_study_lamp",
                "name": "书房台灯",
                "roomId": "study",
                "roomName": "书房",
                "roomSortOrder": 1,
                "category": "light",
                "categoryTitle": "灯",
                "model": "yeelink.light.lamp",
                "stateText": "已关",
                "isOnline": True,
                "isActiveOn": False,
                "isPrimaryToggleable": True,
                "isFavorite": False,
                "pinyinInitials": "sftd",
                "pinyinFull": "",
                "roomPinyinInitials": "sf",
                "capabilities": {
                    "canTogglePower": True,
                    "brightnessWritable": True,
                    "colorWritable": False,
                },
            },
            {
                "id": "dev_study_ac",
                "name": "书房空调",
                "roomId": "study",
                "roomName": "书房",
                "roomSortOrder": 1,
                "category": "climate",
                "categoryTitle": "空调",
                "model": "xiaomi.aircondition.mc5",
                "stateText": "制冷 · 25°C",
                "isOnline": True,
                "isActiveOn": True,
                "isPrimaryToggleable": True,
                "isFavorite": False,
                "pinyinInitials": "sfkt",
                "pinyinFull": "",
                "roomPinyinInitials": "sf",
                "capabilities": {
                    "canTogglePower": True,
                    "temperatureWritable": True,
                    "targetTemperature": 25.0,
                    "currentTemperature": 26.5,
                    "minTemp": 16.0,
                    "maxTemp": 30.0,
                    "step": 0.5,
                },
            },
            {
                "id": "dev_living_fan",
                "name": "客厅风扇",
                "roomId": "living",
                "roomName": "客厅",
                "roomSortOrder": 0,
                "category": "fan",
                "categoryTitle": "风扇",
                "model": "zhimi.fan.za5",
                "stateText": "已开 · 自然风",
                "isOnline": True,
                "isActiveOn": True,
                "isPrimaryToggleable": True,
                "isFavorite": False,
                "pinyinInitials": "ktfs",
                "pinyinFull": "",
                "roomPinyinInitials": "kt",
                "capabilities": {
                    "canTogglePower": True,
                    "fanSpeedWritable": True,
                    "fanPercentage": 50,
                    "availablePresetModes": ["直吹风", "自然风", "睡眠"],
                },
            },
            {
                "id": "dev_balcony_lamp",
                "name": "阳台灯",
                "roomId": "living",
                "roomName": "客厅",
                "roomSortOrder": 0,
                "category": "light",
                "categoryTitle": "灯",
                "model": "yeelink.light.color1",
                "stateText": "离线",
                "isOnline": False,
                "isActiveOn": False,
                "isPrimaryToggleable": False,
                "isFavorite": False,
                "pinyinInitials": "ytd",
                "pinyinFull": "",
                "roomPinyinInitials": "kt",
                "capabilities": {"canTogglePower": False},
            },
        ],
        "quickActions": [
            {"id": "lightsOff", "name": "全屋关灯", "subtitle": "", "icon": "lightbulb.slash",
             "isEnabled": True, "affectedCount": 2, "pinyinInitials": "qwgd"},
            {"id": "allOff", "name": "一键全关", "subtitle": "", "icon": "power",
             "isEnabled": True, "affectedCount": 3, "pinyinInitials": "yjqg"},
            {"id": "undo", "name": "撤销上一步", "subtitle": "", "icon": "arrow.uturn.backward",
             "isEnabled": False, "affectedCount": 0, "pinyinInitials": "cxydb"},
        ],
        "scenes": [
            {"id": "scene_movie", "name": "观影模式", "entityId": "scene.movie", "roomName": None,
             "icon": "film.fill", "visualCategory": "movie", "pinyinInitials": "gyms",
             "pinyinFull": ""}
        ],
        "climate": [],
        "environmentReadings": [
            {"id": "sensor_living", "label": "沙发温湿度计", "roomName": "客厅", "temperature": 24.5,
             "humidity": 52, "battery": 90, "updatedAt": "2026-11-06T08:00:00.000Z",
             "pinyinInitials": "sfsd"}
        ],
        "lastCommandFeedback": None,
    }


class FakeApp:
    """替身：记录打开的链接，并按其中的 requestId 写回一条结果。"""

    def __init__(self, path: Path, *, respond: bool = True, is_error: bool = False, message: str = "已执行"):
        self.path = path
        self.opened: list[str] = []
        self.respond = respond
        self.is_error = is_error
        self.message = message

    def opener(self, url: str) -> None:
        self.opened.append(url)
        if not self.respond:
            return
        request_id = parse_qs(urlparse(url).query).get("id", [""])[0]
        snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        snapshot["lastCommandFeedback"] = {
            "requestId": request_id,
            "command": urlparse(url).netloc,
            "message": self.message,
            "isError": self.is_error,
            "completedAt": "2026-11-06T08:00:01.000Z",
        }
        self.path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    def query(self, index: int = -1) -> dict:
        return {key: values[0] for key, values in parse_qs(urlparse(self.opened[index]).query).items()}


class MijiaCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "raycast-snapshot.json"
        self.write_snapshot(make_snapshot())
        self.clock = 0.0

    def write_snapshot(self, snapshot: dict) -> None:
        self.path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def run_cli(self, argv: list[str], app: FakeApp | None = None, env: dict | None = None):
        app = app or FakeApp(self.path)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mijia.main(
                argv + ["--snapshot", str(self.path)],
                opener=app.opener,
                now=self.now,
                sleep=self.sleep,
                env=env or {},
            )
        return code, out.getvalue(), err.getvalue(), app

    def test_list_groups_devices_by_room_and_marks_offline(self) -> None:
        code, out, _, app = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(app.opened, [])
        self.assertIn("客厅", out)
        self.assertIn("客厅吸顶灯", out)
        self.assertIn("已开 · 80%", out)
        self.assertIn("阳台灯", out)
        self.assertIn("离线", out)
        # 收藏的设备带星标，房间按 roomSortOrder 排：客厅在书房前面。
        self.assertIn("★", out)
        self.assertLess(out.index("客厅"), out.index("书房"))

    def test_list_filters_by_room_name_and_id(self) -> None:
        for room in ("书房", "study"):
            code, out, _, _ = self.run_cli(["list", "--room", room])
            self.assertEqual(code, 0, room)
            self.assertIn("书房台灯", out)
            self.assertNotIn("客厅吸顶灯", out)

    def test_list_rejects_unknown_room(self) -> None:
        code, _, err, _ = self.run_cli(["list", "--room", "车库"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("书房", err)

    def test_list_json_contains_full_device_rows(self) -> None:
        code, out, _, _ = self.run_cli(["list", "--json", "--room", "study"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual([device["id"] for device in payload["devices"]], ["dev_study_lamp", "dev_study_ac"])
        self.assertEqual(payload["devices"][0]["capabilities"]["canTogglePower"], True)

    def test_status_reports_connection_counts_and_readings(self) -> None:
        code, out, _, _ = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn("Home Assistant", out)
        self.assertIn("5 台", out)
        self.assertIn("沙发温湿度计", out)
        self.assertIn("24.5°C", out)
        self.assertIn("52%", out)

    def test_toggle_sends_device_id_token_and_request_id_then_reports_result(self) -> None:
        code, out, _, app = self.run_cli(["toggle", "书房台灯"])
        self.assertEqual(code, 0)
        self.assertEqual(len(app.opened), 1)
        url = app.opened[0]
        self.assertTrue(url.startswith("menumijia://toggle?"))
        query = app.query()
        self.assertEqual(query["device"], "dev_study_lamp")
        self.assertEqual(query["token"], TOKEN)
        self.assertEqual(len(query["id"]), 32)
        self.assertIn("已执行", out)

    def test_toggle_accepts_room_prefix_and_pinyin_initials(self) -> None:
        _, _, _, app = self.run_cli(["toggle", "书房书房台灯"])
        self.assertEqual(app.query()["device"], "dev_study_lamp")
        _, _, _, app = self.run_cli(["toggle", "sftd"])
        self.assertEqual(app.query()["device"], "dev_study_lamp")

    def test_toggle_refuses_a_device_without_a_power_switch(self) -> None:
        code, _, err, app = self.run_cli(["toggle", "阳台灯"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("没有可直接开关", err)
        self.assertEqual(app.opened, [])

    def test_toggle_errors_on_an_ambiguous_name(self) -> None:
        snapshot = make_snapshot()
        snapshot["devices"][1]["name"] = "台灯"
        snapshot["devices"][2]["name"] = "台灯"
        snapshot["devices"][2]["pinyinInitials"] = "td"
        self.write_snapshot(snapshot)
        code, _, err, app = self.run_cli(["toggle", "台灯"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("匹配到多个设备", err)
        self.assertEqual(app.opened, [])

    def test_on_and_off_send_explicit_power_state(self) -> None:
        code, _, _, app = self.run_cli(["on", "书房台灯"])
        self.assertEqual(code, 0)
        self.assertTrue(app.opened[0].startswith("menumijia://power?"))
        self.assertEqual(app.query()["on"], "true")

        code, _, _, app = self.run_cli(["off", "客厅吸顶灯"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["on"], "false")
        self.assertEqual(app.query()["device"], "dev_living_light")

    def test_brightness_color_temp_temperature_fan_and_focus_cli_subcommands(self) -> None:
        code, _, _, app = self.run_cli(["brightness", "客厅吸顶灯", "35"])
        self.assertEqual(code, 0)
        self.assertTrue(app.opened[-1].startswith("menumijia://brightness?"))
        self.assertEqual(app.query()["percent"], "35")

        code, _, _, app = self.run_cli(["color-temp", "客厅吸顶灯", "4500"])
        self.assertEqual(code, 0)
        self.assertTrue(app.opened[-1].startswith("menumijia://color-temp?"))
        self.assertEqual(app.query()["kelvin"], "4500")

        code, _, _, app = self.run_cli(["temperature", "书房空调", "24.5"])
        self.assertEqual(code, 0)
        self.assertTrue(app.opened[-1].startswith("menumijia://temperature?"))
        self.assertEqual(app.query()["celsius"], "24.5")

        code, _, _, app = self.run_cli(["fan", "客厅风扇", "--percent", "75"])
        self.assertEqual(code, 0)
        self.assertTrue(app.opened[-1].startswith("menumijia://fan?"))
        self.assertEqual(app.query()["percent"], "75")

        code, _, _, app = self.run_cli(["fan", "客厅风扇", "--preset", "自然"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["preset"], "自然风")

        code, _, _, app = self.run_cli(["focus", "书房空调"])
        self.assertEqual(code, 0)
        self.assertTrue(app.opened[-1].startswith("menumijia://focus?"))
        self.assertEqual(app.query()["device"], "dev_study_ac")

    def test_scene_uses_quick_prefix_for_builtin_and_id_for_remote_scenes(self) -> None:
        code, _, _, app = self.run_cli(["scene", "全屋关灯"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["name"], "quick:lightsOff")

        code, _, _, app = self.run_cli(["scene", "观影模式"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["name"], "scene_movie")

        code, _, _, app = self.run_cli(["scene", "undo"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["name"], "quick:undo")

    def test_exact_quick_action_name_beats_a_scene_that_only_contains_it(self) -> None:
        snapshot = make_snapshot()
        snapshot["scenes"].append(
            {
                "id": "scene_room_off",
                "name": "全屋关灯客厅",
                "entityId": "scene.room_off",
                "roomName": "客厅",
                "icon": "lightbulb",
                "visualCategory": "light",
                "pinyinInitials": "",
                "pinyinFull": "",
            }
        )
        self.write_snapshot(snapshot)
        code, _, _, app = self.run_cli(["scene", "全屋关灯"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["name"], "quick:lightsOff")

    def test_a_partial_scene_name_resolves_when_it_is_unique(self) -> None:
        code, _, _, app = self.run_cli(["scene", "观影"])
        self.assertEqual(code, 0)
        self.assertEqual(app.query()["name"], "scene_movie")

    def test_ask_and_command_percent_encode_the_sentence(self) -> None:
        for subcommand in ("ask", "command"):
            code, out, _, app = self.run_cli([subcommand, "把客厅灯调到 40%"])
            self.assertEqual(code, 0)
            self.assertTrue(app.opened[0].startswith("menumijia://ask?"))
            # 空格编码成 %20，应用才按原样收到文本（"+" 会被当成字面加号）。
            self.assertIn("text=%E6%8A%8A%E5%AE%A2%E5%8E%85%E7%81%AF%E8%B0%83%E5%88%B0%2040%25", app.opened[0])
            self.assertEqual(app.query()["text"], "把客厅灯调到 40%")
            self.assertIn("已执行", out)

    def test_a_failed_command_exits_with_one(self) -> None:
        app = FakeApp(self.path, is_error=True, message="设备当前离线")
        code, out, _, _ = self.run_cli(["toggle", "书房台灯"], app)
        self.assertEqual(code, mijia.EXIT_COMMAND_FAILED)
        self.assertIn("设备当前离线", out)

    def test_a_missing_feedback_times_out(self) -> None:
        app = FakeApp(self.path, respond=False)
        code, _, err, _ = self.run_cli(["toggle", "书房台灯", "--timeout", "1.5"], app)
        self.assertEqual(code, mijia.EXIT_TIMEOUT)
        self.assertIn("没收到执行结果", err)
        self.assertGreaterEqual(self.clock, 1.5)

    def test_stale_feedback_from_an_earlier_command_is_ignored(self) -> None:
        snapshot = make_snapshot()
        snapshot["lastCommandFeedback"] = {
            "requestId": "older-request",
            "command": "toggle",
            "message": "上一次的结果",
            "isError": False,
            "completedAt": "2026-11-06T07:59:00.000Z",
        }
        self.write_snapshot(snapshot)
        app = FakeApp(self.path, respond=False)
        code, out, _, _ = self.run_cli(["toggle", "书房台灯", "--timeout", "0.3"], app)
        self.assertEqual(code, mijia.EXIT_TIMEOUT)
        self.assertNotIn("上一次的结果", out)

    def test_dry_run_prints_the_link_with_a_masked_token(self) -> None:
        code, out, _, app = self.run_cli(["toggle", "书房台灯", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(app.opened, [])
        self.assertIn("menumijia://toggle?", out)
        self.assertIn("device=dev_study_lamp", out)
        self.assertNotIn(TOKEN, out)

    def test_no_wait_opens_the_link_without_polling(self) -> None:
        app = FakeApp(self.path, respond=False)
        code, _, _, _ = self.run_cli(["toggle", "书房台灯", "--no-wait"], app)
        self.assertEqual(code, 0)
        self.assertEqual(len(app.opened), 1)
        self.assertEqual(self.clock, 0)

    def test_missing_snapshot_points_at_the_settings_switch(self) -> None:
        self.path.unlink()
        code, _, err, app = self.run_cli(["list"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("Raycast 集成", err)
        self.assertEqual(app.opened, [])

    def test_unknown_schema_version_is_rejected(self) -> None:
        snapshot = make_snapshot()
        snapshot["schemaVersion"] = 99
        self.write_snapshot(snapshot)
        code, _, err, _ = self.run_cli(["list"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("99", err)

    def test_empty_bridge_token_is_reported(self) -> None:
        snapshot = make_snapshot()
        snapshot["bridgeToken"] = ""
        self.write_snapshot(snapshot)
        code, _, err, app = self.run_cli(["toggle", "书房台灯"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("本机令牌", err)
        self.assertEqual(app.opened, [])

    def test_snapshot_path_comes_from_the_environment_variable(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mijia.main(
                ["list"],
                opener=lambda url: None,
                now=self.now,
                sleep=self.sleep,
                env={mijia.SNAPSHOT_ENV: str(self.path)},
            )
        self.assertEqual(code, 0)
        self.assertIn("客厅吸顶灯", out.getvalue())

    def test_build_url_escapes_spaces_and_keeps_the_token_last(self) -> None:
        url = mijia.build_url("ask", [("text", "开 灯")], TOKEN, "req1")
        self.assertEqual(url, f"menumijia://ask?text=%E5%BC%80%20%E7%81%AF&id=req1&token={TOKEN}")

    def test_room_filter_merges_devices_sharing_a_room_name_across_room_ids(self) -> None:
        snapshot = make_snapshot()
        snapshot["devices"].append(
            {
                "id": "dev_ha_living_tv",
                "name": "客厅电视",
                "roomId": "ha_area_living",
                "roomName": "客厅",
                "isOnline": True,
                "isOn": False,
                "canToggle": True,
                "statusText": "已关",
                "icon": "tv",
                "visualCategory": "generic",
                "pinyinInitials": "ktds",
                "pinyinFull": "ketingdianshi",
            }
        )
        self.write_snapshot(snapshot)
        code, out, err, _ = self.run_cli(["list", "--room", "客厅", "--json"])
        self.assertEqual(code, 0, err)
        ids = [d["id"] for d in json.loads(out)["devices"]]
        self.assertEqual(
            ids,
            ["dev_living_light", "dev_living_fan", "dev_balcony_lamp", "dev_ha_living_tv"],
        )

    def test_device_and_scene_matching_supports_spaced_room_pinyin_full_entity_id_and_builtin_aliases(self) -> None:
        snapshot = make_snapshot()
        for dev in snapshot["devices"]:
            if dev["id"] == "dev_study_lamp":
                dev["pinyinFull"] = "shufangtaideng"
        self.write_snapshot(snapshot)
        for query in ("书房 台灯", "shufangtaideng"):
            code, _, err, app = self.run_cli(["toggle", query])
            self.assertEqual(code, 0, err)
            self.assertEqual(app.query()["device"], "dev_study_lamp")

        code, _, err, app = self.run_cli(["scene", "scene.movie"])
        self.assertEqual(code, 0, err)
        self.assertEqual(app.query()["name"], "scene_movie")

        for alias, expected in (
            ("lights_off", "quick:lightsOff"),
            ("quick:全屋关灯", "quick:lightsOff"),
            ("睡眠关空调", "quick:climateSleepOff"),
            ("撤销", "quick:undo"),
        ):
            code, _, err, app = self.run_cli(["scene", alias])
            self.assertEqual(code, 0, err)
            self.assertEqual(app.query()["name"], expected)

    def test_fan_preset_validation_rejects_empty_modes_and_matches_bilingual_or_level_aliases(self) -> None:
        # 仅支持百分比调速、无挡位列表的风扇不应放行 --preset
        snapshot = make_snapshot()
        for dev in snapshot["devices"]:
            if dev["id"] == "dev_living_fan":
                dev["capabilities"]["availablePresetModes"] = []
        self.write_snapshot(snapshot)
        code, _, err, app = self.run_cli(["fan", "客厅风扇", "--preset", "睡眠"])
        self.assertEqual(code, mijia.EXIT_LOCAL_ERROR)
        self.assertIn("不支持预设档位", err)
        self.assertEqual(app.opened, [])

        # 英文 HA 挡位名可通过中文别名或高/中/低挡匹配并归一到快照中的真实挡位名
        for dev in snapshot["devices"]:
            if dev["id"] == "dev_living_fan":
                dev["capabilities"]["availablePresetModes"] = ["Level1", "Level2", "Level3", "Sleep"]
        self.write_snapshot(snapshot)
        code, _, err, app = self.run_cli(["fan", "客厅风扇", "--preset", "睡眠"])
        self.assertEqual(code, 0, err)
        self.assertEqual(app.query()["preset"], "Sleep")

        code, _, err, app = self.run_cli(["fan", "客厅风扇", "--preset", "高档"])
        self.assertEqual(code, 0, err)
        self.assertEqual(app.query()["preset"], "Level3")

    def test_invalid_timeout_values_fail_fast(self) -> None:
        for bad_timeout in ("0", "-1", "nan", "inf", "500"):
            code, _, err, app = self.run_cli(["toggle", "书房台灯", "--timeout", bad_timeout])
            self.assertEqual(code, mijia.EXIT_LOCAL_ERROR, bad_timeout)
            self.assertIn("等待超时秒数", err)
            self.assertEqual(app.opened, [])


class MijiaMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "raycast-snapshot.json"
        self.path.write_text(json.dumps(make_snapshot(), ensure_ascii=False), encoding="utf-8")
        self.clock = 0.0

    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def make_ctx(self, app: FakeApp | None = None, timeout: float = 1.5) -> tuple[mijia.McpContext, FakeApp]:
        app = app or FakeApp(self.path)
        ctx = mijia.McpContext(
            path=self.path,
            opener=app.opener,
            now=self.now,
            sleep=self.sleep,
            timeout=timeout,
        )
        return ctx, app

    def call_tool(self, name: str, arguments: dict | None = None, app: FakeApp | None = None) -> tuple[dict, FakeApp]:
        ctx, app = self.make_ctx(app)
        resp = mijia.handle_mcp_message(
            ctx,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
        )
        self.assertIsNotNone(resp)
        assert resp is not None
        self.assertNotIn("error", resp, resp)
        return resp["result"], app

    def test_initialize_negotiates_protocol_version_and_notifications_produce_no_reply(self) -> None:
        ctx, _ = self.make_ctx()
        init = mijia.handle_mcp_message(
            ctx,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "cursor"}},
            },
        )
        assert init is not None
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(init["result"]["serverInfo"]["name"], "menubarmijia")
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIn("resources", init["result"]["capabilities"])

        fallback = mijia.handle_mcp_message(
            ctx,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            },
        )
        assert fallback is not None
        self.assertEqual(fallback["result"]["protocolVersion"], "2024-11-05")

        notif = mijia.handle_mcp_message(
            ctx,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self.assertIsNone(notif)

        ping = mijia.handle_mcp_message(ctx, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert ping is not None
        self.assertEqual(ping["result"], {})

    def test_resources_list_and_read_never_expose_bridge_token(self) -> None:
        ctx, _ = self.make_ctx()
        listed = mijia.handle_mcp_message(ctx, {"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        assert listed is not None
        uris = [item["uri"] for item in listed["result"]["resources"]]
        self.assertEqual(uris, ["mijia://status", "mijia://devices", "mijia://scenes"])

        for uri in uris:
            read = mijia.handle_mcp_message(
                ctx,
                {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}},
            )
            assert read is not None
            raw_text = read["result"]["contents"][0]["text"]
            self.assertNotIn(TOKEN, raw_text, uri)
            self.assertNotIn("bridgeToken", raw_text, uri)
            parsed = json.loads(raw_text)
            self.assertIsInstance(parsed, dict)

    def test_read_only_tools_filter_devices_and_strip_bridge_token(self) -> None:
        res, app = self.call_tool("list_devices", {"room": "书房", "online_only": True})
        self.assertFalse(res["isError"])
        self.assertEqual(app.opened, [])
        serialized = json.dumps(res, ensure_ascii=False)
        self.assertNotIn(TOKEN, serialized)
        self.assertEqual(
            [d["id"] for d in res["structuredContent"]["devices"]],
            ["dev_study_lamp", "dev_study_ac"],
        )

        status, _ = self.call_tool("get_home_status")
        self.assertFalse(status["isError"])
        self.assertEqual(status["structuredContent"]["deviceCount"], 5)
        self.assertNotIn(TOKEN, json.dumps(status, ensure_ascii=False))

    def test_control_tools_dispatch_exact_links_and_report_feedback(self) -> None:
        res, app = self.call_tool("set_device_power", {"device": "sftd", "on": True})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://power?"))
        self.assertEqual(app.query()["device"], "dev_study_lamp")
        self.assertEqual(app.query()["on"], "true")

        res, app = self.call_tool("set_light_brightness", {"device": "客厅吸顶灯", "percent": 60})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://brightness?"))
        self.assertEqual(app.query()["percent"], "60")

        res, app = self.call_tool("set_light_color_temp", {"device": "客厅吸顶灯", "kelvin": 4200})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://color-temp?"))
        self.assertEqual(app.query()["kelvin"], "4200")

        res, app = self.call_tool("set_temperature", {"device": "书房空调", "celsius": 26})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://temperature?"))
        self.assertEqual(app.query()["celsius"], "26")

        res, app = self.call_tool("set_fan", {"device": "客厅风扇", "preset": "睡眠"})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://fan?"))
        self.assertEqual(app.query()["preset"], "睡眠")

        res, app = self.call_tool("run_scene", {"name": "观影模式"})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://scene?"))
        self.assertEqual(app.query()["name"], "scene_movie")

        res, app = self.call_tool("ask_home", {"text": "把客厅灯调到 30%"})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://ask?"))
        self.assertEqual(app.query()["text"], "把客厅灯调到 30%")

        res, app = self.call_tool("focus_device", {"device": "客厅吸顶灯"})
        self.assertFalse(res["isError"])
        self.assertTrue(app.opened[-1].startswith("menumijia://focus?"))
        self.assertEqual(app.query()["device"], "dev_living_light")

    def test_validation_and_app_refusals_return_tool_is_error_without_opening_bad_links(self) -> None:
        # 未知参数（如把 room 写成 room_name）不静默忽略
        res, app = self.call_tool("list_devices", {"room_name": "书房"})
        self.assertTrue(res["isError"])
        self.assertIn("room_name", res["content"][0]["text"])
        self.assertEqual(app.opened, [])

        # 非字符串档位（如布尔值）报错
        res, app = self.call_tool("set_fan", {"device": "客厅风扇", "preset": True})
        self.assertTrue(res["isError"])
        self.assertEqual(app.opened, [])

        # 布尔值不能冒充整数亮度
        res, app = self.call_tool("set_light_brightness", {"device": "客厅吸顶灯", "percent": True})
        self.assertTrue(res["isError"])
        self.assertEqual(app.opened, [])

        # 书房台灯不支持色温
        res, app = self.call_tool("set_light_color_temp", {"device": "书房台灯", "kelvin": 4000})
        self.assertTrue(res["isError"])
        self.assertIn("不支持调节色温", res["content"][0]["text"])
        self.assertEqual(app.opened, [])

        # 客厅吸顶灯色温越界（2700–6500K）
        res, app = self.call_tool("set_light_color_temp", {"device": "客厅吸顶灯", "kelvin": 2000})
        self.assertTrue(res["isError"])
        self.assertIn("2700K–6500K", res["content"][0]["text"])
        self.assertEqual(app.opened, [])

        # 空调温度越界（16–30°C）
        res, app = self.call_tool("set_temperature", {"device": "书房空调", "celsius": 32})
        self.assertTrue(res["isError"])
        self.assertIn("16°C–30°C", res["content"][0]["text"])
        self.assertEqual(app.opened, [])

        # 风扇同时传 percent 与 preset 视为歧义
        res, app = self.call_tool("set_fan", {"device": "客厅风扇", "percent": 50, "preset": "自然风"})
        self.assertTrue(res["isError"])
        self.assertEqual(app.opened, [])

        # 风扇未知档位报错并列出可选档位
        res, app = self.call_tool("set_fan", {"device": "客厅风扇", "preset": "狂风"})
        self.assertTrue(res["isError"])
        self.assertIn("直吹风", res["content"][0]["text"])
        self.assertEqual(app.opened, [])

        # 应用侧拒绝（如锁屏或用户取消高风险确认）原样返回 isError=True
        refused_app = FakeApp(self.path, is_error=True, message="屏幕已锁定，已拒绝控制")
        res, _ = self.call_tool("toggle_device", {"device": "书房台灯"}, app=refused_app)
        self.assertTrue(res["isError"])
        self.assertIn("屏幕已锁定", res["content"][0]["text"])

    def test_jsonrpc_protocol_errors_and_content_length_framing(self) -> None:
        ctx, _ = self.make_ctx()
        unknown_method = mijia.handle_mcp_message(
            ctx, {"jsonrpc": "2.0", "id": 10, "method": "unknown/method"}
        )
        assert unknown_method is not None
        self.assertEqual(unknown_method["error"]["code"], -32601)

        unknown_tool = mijia.handle_mcp_message(
            ctx,
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "no_such_tool"}},
        )
        assert unknown_tool is not None
        self.assertEqual(unknown_tool["error"]["code"], -32602)

        unknown_resource = mijia.handle_mcp_message(
            ctx,
            {"jsonrpc": "2.0", "id": 12, "method": "resources/read", "params": {"uri": "mijia://secret"}},
        )
        assert unknown_resource is not None
        self.assertEqual(unknown_resource["error"]["code"], -32602)

        # 验证 Content-Type 在前、Content-Length 在后的多头帧，以及异常 Content-Length 恢复能力
        req_body = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}).encode("utf-8")
        stream_payload = (
            b"Content-Length: -1\r\nContent-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n"
            + b"Content-Length: abc\r\nContent-Type: application/vscode-jsonrpc\r\n\r\n"
            + b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
            + f"Content-Length: {len(req_body)}\r\n\r\n".encode("ascii")
            + req_body
        )
        framed_in = io.BytesIO(stream_payload)
        framed_out = io.BytesIO()
        code = mijia.serve_mcp_stdio(ctx, stdin=framed_in, stdout=framed_out)
        self.assertEqual(code, 0)
        reply_reader = io.BytesIO(framed_out.getvalue())
        bodies = []
        while True:
            raw_frame, is_framed = mijia._read_mcp_frame(reply_reader)
            if raw_frame is None:
                break
            self.assertTrue(is_framed)
            bodies.append(json.loads(raw_frame))
        self.assertEqual(len(bodies), 3)
        self.assertEqual(bodies[0]["error"]["code"], -32700)
        self.assertEqual(bodies[1]["error"]["code"], -32700)
        self.assertEqual(bodies[2], {"jsonrpc": "2.0", "id": 99, "result": {}})

        # 截断帧体也应返回 -32700 Parse error 而非静默吞掉
        truncated_in = io.BytesIO(b"Content-Length: 50\r\n\r\n{\"jsonrpc\":")
        truncated_out = io.BytesIO()
        mijia.serve_mcp_stdio(ctx, stdin=truncated_in, stdout=truncated_out)
        _, trunc_body = truncated_out.getvalue().split(b"\r\n\r\n", 1)
        self.assertEqual(json.loads(trunc_body.decode("utf-8"))["error"]["code"], -32700)

    def test_mijia_mcp_executable_runs_over_stdio_subprocess(self) -> None:
        lines = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get_home_status", "arguments": {}},
                }
            ),
        ]
        completed = subprocess.run(
            [sys.executable, str(MCP_SCRIPT), "--snapshot", str(self.path)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        replies = [json.loads(line) for line in completed.stdout.strip().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        tool_names = [t["name"] for t in replies[1]["result"]["tools"]]
        self.assertIn("list_devices", tool_names)
        self.assertIn("set_light_brightness", tool_names)
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertNotIn(TOKEN, completed.stdout)


if __name__ == "__main__":
    sys.exit(unittest.main())
