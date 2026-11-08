#!/usr/bin/env python3
"""Measure MenuBarMijia's memory and CPU, and print the README FAQ comparison.

The live path samples a process that is already running; it never launches the
app. Put the app in the state you want to measure first:

    python3 scripts/measure_footprint.py --state idle
    # press Option-M to open the panel, then
    python3 scripts/measure_footprint.py --state panel
    # hold the global hotkey to start push-to-talk and run this while it listens
    python3 scripts/measure_footprint.py --state voice

CPU comes from the CPU-time delta across each sample window. `ps -o %cpu=` is a
lifetime average, so it keeps reporting a high number for hours after a busy
period; the script records it only as `cpu_percent_lifetime`.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as _datetime
import json
import platform as _platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

SCHEMA_VERSION = 1
STATES = ("idle", "panel", "voice")
STATE_LABELS = {
    "idle": "Idle (panel closed)",
    "panel": "Panel open",
    "voice": "Voice recognition",
}
DEFAULT_SAMPLES = 3
DEFAULT_INTERVAL_SECONDS = 5.0
MACOS_PROCESS_NAME = "MenuBarMijiaApp"
# Tauri's package name (windows/src-tauri/Cargo.toml) names the built binary
# menubarmijia-windows.exe; installers and shortcuts ship it as MenuBarMijia.exe.
WINDOWS_APP_NAMES = ("MenuBarMijia.exe", "menubarmijia-windows.exe")
SYNTHESIS_DEVICES = 24

# Recorded rows back the table when no app is running and fill the states the
# caller did not sample. Every row names its source so a reader can tell a live
# sample from a remembered one.
RECORDED_MACOS = {
    "idle": (159.0, 146.0, 0.1),
    "panel": (147.0, None, 0.1),
    "voice": (152.0, None, 25.7),
}

RECORDED_SYNTHESIS = {
    "macos": {"devices": SYNTHESIS_DEVICES, "median_ms": 2.103, "min_ms": 2.025, "max_ms": 2.688, "runs": 31},
    "windows": {"devices": SYNTHESIS_DEVICES, "median_ms": 0.099, "min_ms": 0.074, "max_ms": 0.127, "runs": 31},
}

PROVENANCE = {
    "app_version": "1.1.7 (426)",
    "measured_on": "2026-11-07",
    "hardware": "Apple M-series, 10 cores",
    "os": "macOS 27.0.1",
    "synthesis": "24 devices (18 lights, 6 switches) from one snapshot; macOS built with swiftc -O, Windows through node",
}

CPU_TIME_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")
FOOTPRINT_RE = re.compile(r"Footprint:\s*([\d.]+)\s*(B|KB|MB|GB)\b", re.IGNORECASE)
VMMAP_RE = re.compile(r"Physical footprint:\s*([\d.]+)\s*([KMG])B?\b", re.IGNORECASE)


class MeasureError(RuntimeError):
    """The host tools could not produce a reading."""


@dataclass(frozen=True)
class Sample:
    rss_mb: float
    cpu_percent: float
    cpu_percent_lifetime: float | None = None


@dataclass(frozen=True)
class Measurement:
    platform: str
    state: str
    rss_mb: float
    footprint_mb: float | None
    cpu_percent: float
    cpu_percent_lifetime: float | None
    samples: int
    source: str


def parse_cpu_time(text: str) -> float:
    """Accept `MM:SS.ss`, `HH:MM:SS.ss`, and `MMM:SS.ss` from `ps -o cputime=`."""
    match = CPU_TIME_RE.match(text.strip())
    if not match:
        raise MeasureError(f"unrecognised CPU time: {text!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def parse_footprint(text: str) -> float:
    """Read `Footprint: 146 MB` from `/usr/bin/footprint <pid>`."""
    match = FOOTPRINT_RE.search(text)
    if not match:
        raise MeasureError("no Footprint line in footprint output")
    return _to_mb(float(match.group(1)), match.group(2))


def parse_vmmap_summary(text: str) -> float:
    """Read `Physical footprint: 145.7M` from `vmmap -summary <pid>`."""
    match = VMMAP_RE.search(text)
    if not match:
        raise MeasureError("no Physical footprint line in vmmap output")
    return _to_mb(float(match.group(1)), match.group(2) + "B")


def _to_mb(value: float, unit: str) -> float:
    unit = unit.upper()
    if unit == "B":
        return value / (1024 * 1024)
    if unit == "KB":
        return value / 1024
    if unit == "GB":
        return value * 1024
    return value


def cpu_percent(cpu_seconds_delta: float, wall_seconds: float) -> float:
    """Percent of one core, the convention macOS `%cpu` uses."""
    if wall_seconds <= 0:
        raise MeasureError("sample window must be longer than zero seconds")
    return max(0.0, cpu_seconds_delta / wall_seconds * 100)


def summarize(
    samples: Sequence[Sample],
    platform: str,
    state: str,
    footprint_mb: float | None,
    source: str = "live",
) -> Measurement:
    if not samples:
        raise MeasureError("no samples")
    lifetimes = [s.cpu_percent_lifetime for s in samples if s.cpu_percent_lifetime is not None]
    return Measurement(
        platform=platform,
        state=state,
        rss_mb=round(statistics.median(s.rss_mb for s in samples), 1),
        footprint_mb=None if footprint_mb is None else round(footprint_mb, 1),
        cpu_percent=round(statistics.median(s.cpu_percent for s in samples), 2),
        cpu_percent_lifetime=None if not lifetimes else round(statistics.median(lifetimes), 2),
        samples=len(samples),
        source=source,
    )


def _run(command: Sequence[str], timeout: float = 15.0) -> str:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MeasureError(f"{' '.join(command)} failed: {error}") from error
    return result.stdout


def find_macos_pid(name: str = MACOS_PROCESS_NAME) -> int | None:
    output = _run(["ps", "-Ao", "pid=,comm="])
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = parts
        if Path(command).name == name:
            return int(pid)
    return None


def _macos_reading(pid: int) -> tuple[Sample, float]:
    fields = _run(["ps", "-p", str(pid), "-o", "rss=,cputime=,pcpu="]).split()
    if len(fields) < 3:
        raise MeasureError(f"process {pid} is not running")
    return (
        Sample(
            rss_mb=int(fields[0]) / 1024,
            cpu_percent=0.0,
            cpu_percent_lifetime=float(fields[2]),
        ),
        parse_cpu_time(fields[1]),
    )


def macos_footprint(pid: int) -> float | None:
    footprint_tool = shutil.which("footprint")
    if footprint_tool:
        try:
            return parse_footprint(_run([footprint_tool, str(pid)]))
        except MeasureError:
            pass
    vmmap_tool = shutil.which("vmmap")
    if vmmap_tool:
        try:
            return parse_vmmap_summary(_run([vmmap_tool, "-summary", str(pid)]))
        except MeasureError:
            pass
    return None


def measure_macos(
    pid: int,
    state: str,
    samples: int = DEFAULT_SAMPLES,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Measurement:
    readings: list[Sample] = []
    previous_cpu: float | None = None
    previous_wall: float | None = None
    for index in range(samples + 1):
        sample, cpu_seconds = _macos_reading(pid)
        wall = clock()
        if index:
            readings.append(
                Sample(
                    rss_mb=sample.rss_mb,
                    cpu_percent=cpu_percent(cpu_seconds - previous_cpu, wall - previous_wall),
                    cpu_percent_lifetime=sample.cpu_percent_lifetime,
                )
            )
        previous_cpu, previous_wall = cpu_seconds, wall
        if index < samples:
            sleep(interval)
    return summarize(readings, "macOS (Apple Silicon)", state, macos_footprint(pid))


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def select_process_tree(
    entries: Sequence[tuple[int, int, str]], root_names: str | Sequence[str]
) -> list[tuple[int, str]]:
    """Return the root process and every descendant.

    WebView2 renderer processes belong to the app through their parent chain;
    other applications run their own `msedgewebview2.exe` copies, so matching by
    image name alone would count foreign memory.
    """
    names = {root_names.lower()} if isinstance(root_names, str) else {name.lower() for name in root_names}
    included = {pid for pid, _parent, name in entries if name.lower() in names}
    if not included:
        return []
    changed = True
    while changed:
        changed = False
        for pid, parent, _name in entries:
            if pid not in included and parent in included:
                included.add(pid)
                changed = True
    return [(pid, name) for pid, _parent, name in entries if pid in included]


def _windows_process_entries() -> list[tuple[int, int, str]]:
    """Snapshot every process as (pid, parent pid, image name) via Toolhelp32."""
    if _platform.system() != "Windows":
        raise MeasureError("Windows process enumeration is only available on Windows")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise MeasureError("CreateToolhelp32Snapshot failed")
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    rows: list[tuple[int, int, str]] = []
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise MeasureError("Process32FirstW failed")
        while True:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def windows_process_tree() -> list[tuple[int, str]]:
    return select_process_tree(_windows_process_entries(), WINDOWS_APP_NAMES)


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows_reading(pid: int) -> tuple[float, float]:
    """Return (working set MB, CPU seconds) for one process."""
    import ctypes.wintypes as wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        raise MeasureError(f"OpenProcess failed for pid {pid}")
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise MeasureError(f"GetProcessMemoryInfo failed for pid {pid}")
        creation, exit_time, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise MeasureError(f"GetProcessTimes failed for pid {pid}")
        ticks = ((kernel.high << 32) + kernel.low) + ((user.high << 32) + user.low)
        return counters.WorkingSetSize / (1024 * 1024), ticks / 10_000_000
    finally:
        kernel32.CloseHandle(handle)


def measure_windows(
    state: str,
    samples: int = DEFAULT_SAMPLES,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Measurement:
    processes = windows_process_tree()
    if not processes:
        names = " or ".join(WINDOWS_APP_NAMES)
        raise MeasureError(f"{names} is not running")
    readings: list[Sample] = []
    previous: dict[int, tuple[float, float]] = {}
    previous_wall: float | None = None
    for index in range(samples + 1):
        current: dict[int, tuple[float, float]] = {}
        for pid, _name in processes:
            try:
                current[pid] = _windows_reading(pid)
            except MeasureError:
                continue  # the renderer may exit between the snapshot and the read
        if not current:
            raise MeasureError("no readable MenuBarMijia process")
        rss_mb = sum(rss for rss, _cpu in current.values())
        wall = clock()
        if index:
            deltas = [
                current[pid][1] - previous[pid][1]
                for pid in current
                if pid in previous
            ]
            cpu_seconds = sum(deltas)
            readings.append(
                Sample(
                    rss_mb=rss_mb,
                    cpu_percent=cpu_percent(cpu_seconds, wall - previous_wall),
                )
            )
        previous, previous_wall = current, wall
        if index < samples:
            sleep(interval)
    return summarize(readings, "Windows 11 (Tauri + WebView2)", state, None)


def resolve_state(state: str) -> str:
    chosen = state.strip().lower()
    if chosen not in STATES:
        raise MeasureError(f"unknown state {state!r}; choose one of {', '.join(STATES)}")
    return chosen


def _recorded_rows(platform_name: str) -> list[Measurement]:
    rows: list[Measurement] = []
    if platform_name == "macos":
        for state, (rss, footprint, cpu) in RECORDED_MACOS.items():
            rows.append(
                Measurement(
                    platform="macOS (Apple Silicon)",
                    state=state,
                    rss_mb=rss,
                    footprint_mb=footprint,
                    cpu_percent=cpu,
                    cpu_percent_lifetime=None,
                    samples=3,
                    source="recorded",
                )
            )
    return rows


def build_report(
    platform_name: str = "macos",
    state: str | None = "idle",
    live: bool = True,
    sampler: Callable[[str], Measurement] | None = None,
) -> dict:
    """Assemble the report. `sampler(state)` is the live path; None means baseline only."""
    selected = None if state is None else resolve_state(state)
    rows: list[Measurement] = []
    mode = "baseline"
    notes: list[str] = []

    if live and sampler is not None and selected is not None:
        try:
            rows.append(sampler(selected))
            mode = "live"
        except MeasureError as error:
            notes.append(f"live sample skipped: {error}")

    recorded = _recorded_rows(platform_name)
    live_states = {row.state for row in rows}
    rows.extend(row for row in recorded if row.state not in live_states)
    if recorded:
        notes.append("recorded rows are the committed baseline; use --live on a quiet machine to refresh them")
    if not rows and platform_name != "macos":
        raise MeasureError("Windows has no recorded baseline; run this on Windows 11 with the tray app running")

    if not rows:
        raise MeasureError("no live reading and no recorded baseline for this platform")

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds"),
        "host": {
            "system": _platform.system(),
            "release": _platform.release(),
            "machine": _platform.machine(),
            "python": _platform.python_version(),
        },
        "mode": mode,
        "state": selected,
        "provenance": PROVENANCE,
        "rows": [asdict(row) for row in rows],
        "synthesis": RECORDED_SYNTHESIS,
        "notes": notes,
    }


def _format_mb(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} MB"


def _format_cpu(value: float) -> str:
    if value < 0.1:
        return "<0.1%"
    return f"{value:.1f}%"


def render_markdown(report: dict) -> str:
    lines = [
        "| Platform | State | RSS | Physical footprint | CPU (one core) | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['platform']} | {STATE_LABELS[row['state']]} | {_format_mb(row['rss_mb'])} | "
            f"{_format_mb(row['footprint_mb'])} | {_format_cpu(row['cpu_percent'])} | {row['source']} |"
        )
    lines.extend(["", "| Platform | Devices | Median | Range | Runs |", "| --- | --- | --- | --- | --- |"])
    for platform_name, values in report["synthesis"].items():
        label = "macOS (MijiaCore)" if platform_name == "macos" else "Windows (TypeScript core)"
        if values["median_ms"] <= 0:
            lines.append(f"| {label} | {values['devices']} | not measured on this host | | |")
            continue
        lines.append(
            f"| {label} | {values['devices']} | {values['median_ms']:.2f} ms | "
            f"{values['min_ms']:.2f}-{values['max_ms']:.2f} ms | {values['runs']} |"
        )
    provenance = report.get("provenance", PROVENANCE)
    lines.extend(
        [
            "",
            f"Mode: {report['mode']}. Baseline measured {provenance['measured_on']} on "
            f"{provenance['os']}, {provenance['hardware']}, app {provenance['app_version']}.",
        ]
    )
    for note in report["notes"]:
        lines.append(f"- {note}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--state", default="idle", choices=STATES, help="state the app is in right now")
    parser.add_argument("--pid", type=int, help="measure this PID instead of auto-detecting")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="sample windows to average")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS, help="seconds per window")
    parser.add_argument("--no-live", action="store_true", help="print the recorded baseline only")
    args = parser.parse_args(argv)

    if args.samples < 1:
        parser.error("--samples must be at least 1")
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    platform_name = "windows" if _platform.system() == "Windows" else "macos"
    sampler: Callable[[str], Measurement] | None = None
    live = not args.no_live
    if live and platform_name == "macos":
        def macos_sampler(state: str) -> Measurement:
            detected = args.pid if args.pid is not None else find_macos_pid()
            if detected is None:
                raise MeasureError(f"{MACOS_PROCESS_NAME} is not running")
            return measure_macos(detected, state, args.samples, args.interval)

        sampler = macos_sampler
    elif live:
        sampler = lambda state: measure_windows(state, args.samples, args.interval)

    try:
        report = build_report(
            platform_name=platform_name,
            state=args.state,
            live=live,
            sampler=sampler,
        )
    except MeasureError as error:
        print(f"measure_footprint: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
