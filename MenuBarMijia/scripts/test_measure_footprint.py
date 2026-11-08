"""Unit tests for scripts/measure_footprint.py."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

import measure_footprint as fp


class CpuTimeParsingTests(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertAlmostEqual(fp.parse_cpu_time("00:01.50"), 1.5)
        self.assertAlmostEqual(fp.parse_cpu_time("0:03.45"), 3.45)

    def test_hours(self):
        self.assertAlmostEqual(fp.parse_cpu_time("1:02:03.50"), 3723.5)

    def test_long_minute_field(self):
        self.assertAlmostEqual(fp.parse_cpu_time("123:45.67"), 7425.67)

    def test_rejects_garbage(self):
        with self.assertRaises(fp.MeasureError):
            fp.parse_cpu_time("elapsed")


class FootprintParsingTests(unittest.TestCase):
    def test_footprint_tool_output(self):
        text = "MenuBarMijiaApp [1]: 64-bit    Footprint: 146 MB (16384 bytes per page)"
        self.assertAlmostEqual(fp.parse_footprint(text), 146.0)

    def test_footprint_tool_units(self):
        self.assertAlmostEqual(fp.parse_footprint("Footprint: 1.5 GB"), 1536.0)
        self.assertAlmostEqual(fp.parse_footprint("Footprint: 512 KB"), 0.5)

    def test_vmmap_summary(self):
        text = "Physical footprint:         145.7M\nPhysical footprint (peak):  152.6M"
        self.assertAlmostEqual(fp.parse_vmmap_summary(text), 145.7)

    def test_vmmap_uses_current_not_peak(self):
        text = "Physical footprint (peak):  152.6M\nPhysical footprint: 100.5M"
        self.assertAlmostEqual(fp.parse_vmmap_summary(text), 100.5)

    def test_rejects_unknown_output(self):
        with self.assertRaises(fp.MeasureError):
            fp.parse_footprint("nothing here")
        with self.assertRaises(fp.MeasureError):
            fp.parse_vmmap_summary("nothing here")


class CpuPercentTests(unittest.TestCase):
    def test_one_core_percent(self):
        self.assertAlmostEqual(fp.cpu_percent(1.5, 10.0), 15.0)

    def test_negative_delta_clamps_to_zero(self):
        self.assertAlmostEqual(fp.cpu_percent(-2.0, 10.0), 0.0)

    def test_zero_window_is_rejected(self):
        with self.assertRaises(fp.MeasureError):
            fp.cpu_percent(1.0, 0.0)


class SummarizeTests(unittest.TestCase):
    def test_median_of_samples(self):
        samples = [
            fp.Sample(rss_mb=100.0, cpu_percent=1.0, cpu_percent_lifetime=9.0),
            fp.Sample(rss_mb=140.0, cpu_percent=3.0, cpu_percent_lifetime=7.0),
            fp.Sample(rss_mb=120.0, cpu_percent=2.0, cpu_percent_lifetime=8.0),
        ]
        measured = fp.summarize(samples, "macOS", "idle", 130.0)
        self.assertEqual(measured.rss_mb, 120.0)
        self.assertEqual(measured.cpu_percent, 2.0)
        self.assertEqual(measured.cpu_percent_lifetime, 8.0)
        self.assertEqual(measured.footprint_mb, 130.0)
        self.assertEqual(measured.samples, 3)
        self.assertEqual(measured.source, "live")

    def test_even_sample_count_uses_the_midpoint(self):
        samples = [
            fp.Sample(rss_mb=100.0, cpu_percent=1.0, cpu_percent_lifetime=6.0),
            fp.Sample(rss_mb=300.0, cpu_percent=3.0, cpu_percent_lifetime=8.0),
        ]
        measured = fp.summarize(samples, "macOS", "idle", 150.0)
        self.assertEqual(measured.rss_mb, 200.0)
        self.assertEqual(measured.cpu_percent, 2.0)
        self.assertEqual(measured.cpu_percent_lifetime, 7.0)

    def test_missing_footprint_is_none(self):
        measured = fp.summarize([fp.Sample(90.0, 0.2)], "macOS", "panel", None)
        self.assertIsNone(measured.footprint_mb)

    def test_no_samples_is_rejected(self):
        with self.assertRaises(fp.MeasureError):
            fp.summarize([], "macOS", "idle", None)


class MacosSamplingTests(unittest.TestCase):
    def test_cpu_comes_from_cputime_deltas(self):
        responses = iter(
            [
                "1000 00:10.00 5.0",
                "3000 00:20.00 5.0",
                "2000 00:30.00 5.0",
                "4000 00:40.00 5.0",
            ]
        )
        walls = iter([0.0, 5.0, 10.0, 15.0])
        with mock.patch.object(fp, "_run", side_effect=lambda *a, **k: next(responses)), mock.patch.object(
            fp, "macos_footprint", return_value=120.0
        ):
            measured = fp.measure_macos(
                4242,
                "panel",
                samples=3,
                interval=5.0,
                clock=lambda: next(walls),
                sleep=lambda _seconds: None,
            )
        self.assertEqual(measured.samples, 3)
        self.assertEqual(measured.rss_mb, round(3000 / 1024, 1))
        self.assertEqual(measured.footprint_mb, 120.0)
        self.assertEqual(measured.cpu_percent, 200.0)

    def test_missing_process_is_reported(self):
        with mock.patch.object(fp, "_run", return_value=""):
            with self.assertRaises(fp.MeasureError):
                fp.measure_macos(1, "idle", samples=1, interval=1.0, sleep=lambda _s: None)


class WindowsProcessTreeTests(unittest.TestCase):
    def test_includes_app_and_webview_children_only(self):
        entries = [
            (100, 1, "MenuBarMijia.exe"),
            (200, 100, "msedgewebview2.exe"),
            (201, 200, "msedgewebview2.exe"),
            (300, 1, "msedgewebview2.exe"),
            (400, 300, "msedgewebview2.exe"),
        ]
        tree = fp.select_process_tree(entries, "MenuBarMijia.exe")
        self.assertEqual([pid for pid, _name in tree], [100, 200, 201])

    def test_missing_root_yields_nothing(self):
        self.assertEqual(fp.select_process_tree([(5, 1, "other.exe")], "MenuBarMijia.exe"), [])

    def test_matches_the_tauri_binary_name(self):
        entries = [
            (700, 1, "menubarmijia-windows.exe"),
            (701, 700, "msedgewebview2.exe"),
            (702, 1, "MenuBarMijia.exe"),
        ]
        tree = fp.select_process_tree(entries, fp.WINDOWS_APP_NAMES)
        self.assertEqual([pid for pid, _name in tree], [700, 701, 702])

    def test_name_matching_ignores_case(self):
        entries = [(800, 1, "MENUBARMIJIA-WINDOWS.EXE")]
        tree = fp.select_process_tree(entries, ("MenuBarMijia.exe", "menubarmijia-windows.exe"))
        self.assertEqual(tree, [(800, "MENUBARMIJIA-WINDOWS.EXE")])

    def test_sampler_sums_host_and_renderer_cpu(self):
        responses = iter(
            [
                (50.0, 10.0),
                (30.0, 5.0),
                (52.0, 11.0),
                (34.0, 6.0),
                (54.0, 12.0),
                (38.0, 7.0),
                (50.0, 13.0),
                (36.0, 8.0),
            ]
        )
        walls = iter([0.0, 5.0, 10.0, 15.0])
        with mock.patch.object(fp, "windows_process_tree", return_value=[(100, "MenuBarMijia.exe"), (200, "msedgewebview2.exe")]), mock.patch.object(
            fp, "_windows_reading", side_effect=lambda _pid: next(responses)
        ):
            measured = fp.measure_windows(
                "panel", samples=3, interval=5.0, clock=lambda: next(walls), sleep=lambda _s: None
            )
        self.assertEqual(measured.samples, 3)
        self.assertEqual(measured.rss_mb, 86.0)
        self.assertEqual(measured.cpu_percent, 40.0)


class ReportTests(unittest.TestCase):
    def test_baseline_has_every_macos_state(self):
        report = fp.build_report(live=False)
        self.assertEqual(report["mode"], "baseline")
        self.assertEqual({row["state"] for row in report["rows"]}, {"idle", "panel", "voice"})
        self.assertTrue(all(row["source"] == "recorded" for row in report["rows"]))
        self.assertIn("schema", report)

    def test_live_row_replaces_one_recorded_state(self):
        live = fp.Measurement("macOS (Apple Silicon)", "panel", 150.0, 140.0, 0.4, None, 3, "live")
        report = fp.build_report(live=True, sampler=lambda _state: live)
        self.assertEqual(report["mode"], "live")
        panel_rows = [row for row in report["rows"] if row["state"] == "panel"]
        self.assertEqual(len(panel_rows), 1)
        self.assertEqual(panel_rows[0]["source"], "live")
        self.assertEqual({row["state"] for row in report["rows"]}, {"idle", "panel", "voice"})

    def test_failed_live_sample_falls_back_with_a_note(self):
        def broken(_state):
            raise fp.MeasureError("MenuBarMijiaApp is not running")

        report = fp.build_report(live=True, sampler=broken)
        self.assertEqual(report["mode"], "baseline")
        self.assertTrue(any("live sample skipped" in note for note in report["notes"]))

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(fp.MeasureError):
            fp.build_report(state="screensaver", live=False)

    def test_windows_without_a_live_sample_is_an_error(self):
        with self.assertRaises(fp.MeasureError):
            fp.build_report(platform_name="windows", live=False)

    def test_synthesis_carries_twenty_four_device_statistics(self):
        report = fp.build_report(live=False)
        windows = report["synthesis"]["windows"]
        self.assertEqual(windows["devices"], 24)
        self.assertGreater(windows["runs"], 1)
        self.assertLessEqual(windows["min_ms"], windows["median_ms"])
        self.assertLessEqual(windows["median_ms"], windows["max_ms"])


class RenderingTests(unittest.TestCase):
    def report(self):
        return fp.build_report(live=False)

    def test_markdown_lists_states_and_sources(self):
        text = fp.render_markdown(self.report())
        self.assertIn("Idle (panel closed)", text)
        self.assertIn("Panel open", text)
        self.assertIn("Voice recognition", text)
        self.assertIn("recorded", text)
        self.assertIn("24", text)

    def test_markdown_marks_sub_tenth_cpu(self):
        self.assertEqual(fp._format_cpu(0.04), "<0.1%")
        self.assertEqual(fp._format_mb(None), "n/a")

    def test_json_round_trip(self):
        text = json.dumps(self.report())
        decoded = json.loads(text)
        self.assertEqual(decoded["schema"], fp.SCHEMA_VERSION)
        self.assertEqual(len(decoded["rows"]), 3)
        self.assertEqual(decoded["rows"][0]["platform"], "macOS (Apple Silicon)")


class CommandLineTests(unittest.TestCase):
    def run_main(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = fp.main(argv)
        return code, output.getvalue()

    def test_no_live_json_prints_a_report(self):
        code, output = self.run_main(["--no-live", "--json"])
        self.assertEqual(code, 0)
        decoded = json.loads(output)
        self.assertEqual(decoded["mode"], "baseline")

    def test_invalid_sample_count_exits_with_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                fp.main(["--no-live", "--samples", "0"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
