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
    assert [f.name for f in files] == ['app.js', 'home.js', 'work.js', 'pack.js', 'wf.js', 'dash.js', 'screen.js']
    both = tmp_path / 'page.js'
    both.write_text('\n;\n'.join(f.read_text() for f in files))
    r = subprocess.run(['node', '--check', str(both)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_colour_variable_used_has_a_default():
    """深色主题没有 --track 时，关着的开关只剩一个白点（设计画布第 85 块 ①）：var(--x) 不带默认值的，
    tokens.css 的 :root 里都要有。"""
    root = (V2 / 'tokens.css').read_text()
    defined = set(re.findall(r'(--[\w-]+)\s*:', root))
    for css in ('app.css', 'looks.css'):
        text = re.sub(r'/\*.*?\*/', '', (V2 / css).read_text(), flags=re.S)
        defined |= set(re.findall(r'(--[\w-]+)\s*:', text.split('body[data-theme')[0]))
    for css in sorted(V2.glob('*.css')):
        text = re.sub(r'/\*.*?\*/', '', css.read_text(), flags=re.S)
        for name in ('--track', '--card', '--card2', '--line', '--ink', '--ink2', '--ink3', '--accent'):
            if re.search(r'var\(' + name + r'\)', text):
                assert name in defined, f'{css.name} 用了 var({name})，tokens.css 里没有默认值'


def test_stylesheets_have_balanced_braces():
    """少一个 } 后面的规则就全进了上一个 @media（0.1.715：删旧样式时把 `@media (orientation: portrait) { … }`
    删掉了一半，后面的样式在横屏全都不生效）。"""
    for css in sorted(V2.glob('*.css')):
        text = re.sub(r'/\*.*?\*/', '', css.read_text(), flags=re.S)
        depth = 0
        for n, line in enumerate(text.splitlines(), 1):
            depth += line.count('{') - line.count('}')
            assert depth >= 0, f'{css.name}:{n} 多了一个 }}'
        assert depth == 0, f'{css.name}: 少了 {depth} 个 }}'
