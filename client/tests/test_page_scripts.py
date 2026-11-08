"""The page's scripts load together: web/v2/index.html's classic scripts share one global scope, so a
top-level name declared in two of them makes the later file fail to load as a whole (home.js once
redeclared app.js's KIND_ICON and the 家居 page, content cards and rotation all went dead).

Needs node; skipped without it. Run like test_barge_in.py.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

V2 = pathlib.Path(__file__).resolve().parent.parent / 'web' / 'v2'


def page_scripts():
    html = (V2 / 'index.html').read_text()
    return [V2 / src.split('/static/v2/', 1)[1] for src in re.findall(r'<script src="([^"]+)"', html)
            if '/vendor/' not in src]


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_scripts_parse_together(tmp_path):
    files = page_scripts()
    assert [f.name for f in files] == ['app.js', 'home.js']
    both = tmp_path / 'page.js'
    both.write_text('\n;\n'.join(f.read_text() for f in files))
    r = subprocess.run(['node', '--check', str(both)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
