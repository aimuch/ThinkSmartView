"""做菜模式 (设计画布第 10 块): a recipe card's numbered steps, one per screen, read out; "下一步",
"上一步", "再说一遍", "开始计时", "退出做菜" are added to the wake word model while it runs, so they work
without the wake word and without the hub (only the step's words are synthesised by it)."""
import re

WORDS = ['下一步', '上一步', '再说一遍', '开始计时', '退出做菜']
IDLE_SECONDS = 30 * 60
_STEP = re.compile(r'^\s*(?:\d+[.、)）]|第[一二三四五六七八九十\d]+步[:：、]?)\s*(.+)$')
_MINUTES = re.compile(r'(\d+(?:\.\d+)?)\s*(?:分钟|分)')
_CN = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
_CN_MIN = re.compile(r'([一两二三四五六七八九十]{1,3})\s*分钟')


def steps(markdown):
    """The numbered steps of a recipe card's markdown (at least two), or []."""
    out = []
    for line in (markdown or '').splitlines():
        m = _STEP.match(line)
        if m:
            text = re.sub(r'\*\*|__|`', '', m.group(1)).strip()
            if text:
                out.append(text)
    return out if len(out) >= 2 else []


def _cn_number(s):
    if s == '十':
        return 10
    if s.startswith('十'):
        return 10 + _CN.get(s[1:], 0)
    if s.endswith('十'):
        return _CN.get(s[0], 0) * 10
    if '十' in s:
        a, b = s.split('十', 1)
        return _CN.get(a, 0) * 10 + _CN.get(b, 0)
    return _CN.get(s, 0)


def minutes(text):
    """The first "X 分钟" in a step (for its timer button), or None."""
    m = _MINUTES.search(text or '')
    if m:
        v = float(m.group(1))
        return v if 0 < v <= 240 else None
    m = _CN_MIN.search(text or '')
    if m:
        v = _cn_number(m.group(1))
        return v if 0 < v <= 240 else None
    return None
