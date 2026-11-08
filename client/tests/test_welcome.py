"""The chat page's welcome screen (web/v2/app.js welcomeShown) with the hub's saved history.

Runs the page's own function with node (skipped when node isn't installed)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / 'web' / 'v2' / 'app.js'


def welcome(messages, state, connected):
    src = APP_JS.read_text()
    fn = re.search(r'function welcomeShown\(.*?\n}\n', src, re.S).group(0)
    out = subprocess.run(['node', '-e', fn + f'console.log(JSON.stringify(welcomeShown(...{json.dumps([messages, state, connected])})))'],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='needs node')
@pytest.mark.parametrize('messages,state,connected,shown', [
    (0, 'idle', False, True),          # nothing said yet
    (12, 'idle', False, True),         # the conversation is over (connection closed): history kept, welcome back
    (12, 'connecting', False, True),   # woken, still connecting
    (12, 'listening', True, False),    # woken: the chat page with the history
    (12, 'speaking', True, False),
    (12, 'idle', True, False),         # the connection still lasts (~2 minutes): stay on the chat page
    (0, 'idle', True, True),
    (0, 'listening', True, False),     # a new conversation shows the chat from the wake on
])
def test_welcome_screen(messages, state, connected, shown):
    assert welcome(messages, state, connected) is shown
