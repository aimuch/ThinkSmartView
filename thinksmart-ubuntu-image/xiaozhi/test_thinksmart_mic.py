"""thinksmart-mic's PipeSource against a real FIFO: idle readers drop audio, a reader that is gone
for good (pipewire-pulse restarted, the pipe source with it) makes it exit for systemd to restart.

  uv run --no-project --with pytest python -m pytest -q xiaozhi
"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name('thinksmart-mic')


@pytest.fixture
def mic(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    loader = importlib.machinery.SourceFileLoader(f'thinksmart_mic_{tmp_path.name}', str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    m.pactl = lambda *args: 0
    clock = [1000.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    os.mkfifo(tmp_path / 'test.fifo')
    src = m.PipeSource('test', 'test.fifo', 1, 'Test')

    class Mic:
        source = src

        def reader(self):
            return os.open(src.fifo, os.O_RDONLY | os.O_NONBLOCK)

        def run(self, seconds, step=0.02):
            """Feed 20 ms chunks for this long (fake time)."""
            for _ in range(round(seconds / step)):
                src.write(b'\0' * 64)
                clock[0] += step
    return Mic()


def test_no_reader_from_the_start_is_not_an_error(mic):
    mic.run(30)                       # the module is still loading: keeps retrying, never exits
    assert mic.source.fd is None


def test_idle_reader_drops_audio(mic):
    r = mic.reader()
    mic.run(30)                       # nobody reads: the FIFO fills, writes would block, dropped
    assert mic.source.fd is not None
    os.close(r)


def test_reader_gone_for_good_exits(mic):
    r = mic.reader()
    mic.run(1)
    os.close(r)                       # pipewire-pulse restarted: the source and its reader are gone
    with pytest.raises(SystemExit):
        mic.run(10)


def test_reader_back_within_seconds_keeps_running(mic):
    r = mic.reader()
    mic.run(1)
    os.close(r)
    mic.run(3)
    r = mic.reader()
    mic.run(10)
    assert mic.source.fd is not None
    os.close(r)
