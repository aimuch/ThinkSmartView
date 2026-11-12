"""外观 (设置页「外观」, 设计画布第 8 块): the colour theme, the conversation avatar and which blocks the
home page shows in which order. The page applies them (body[data-theme], body[data-avatar], CSS order);
this only keeps them valid. Avatars made from a photo live in AVATARS as PNGs ('photo:<name>').

形象库（设计画布第 41 块）：光球之外的形象来自 Virtual-Assistant-Avatar，scripts/build-avatar-pack.py 做成小图包，
装在 PACK_DIR（catalog.json + img/*.webp），id 是 'pack:<款的 id>'。每款三张：待机、唤醒（在听、在想、在说）、睡觉。"""
import datetime
import json
import re
from pathlib import Path

THEMES = {'night': '星夜', 'dawn': '晨光', 'lavender': '薰衣草', 'forest': '森林', 'candy': '童趣', 'black': '纯黑', 'auto': '跟着时间'}
# 首页布局（设计画布第 35 块）：对话（光球和对话记录）或仪表盘（首页、家居、日程，对话是其中一页）
LAYOUTS = {'chat': '对话', 'dashboard': '仪表盘'}
# 仪表盘首页的卡片（设计画布第 36 块）：能开关、能排序。早安小结和常用设备是宽卡片（横屏在中间那一列）
DASH_CARDS = {'wx': '时钟和天气', 'brief': '早安小结', 'dev': '常用设备', 'todo': '待办', 'alarm': '闹钟和提醒',
              'scene': '场景', 'media': '电台和助眠', 'nums': '今天的数字', 'work': '工作台', 'lights': '蓝牙灯'}
DASH_TABS = {'home': '首页', 'smart': '家居', 'plan': '日程', 'chat': '对话'}   # 打开时先看哪一页
AUTO_DAY = (7, 19)          # 'auto': 晨光 from 7:00 to 19:00, 星夜 otherwise
AVATARS = {'orb': '光球'}
RETIRED = {'cat', 'dog', 'bear', 'rabbit', 'panda', 'wheat', 'robot'}   # 以前画出来的小动物，选过的回到光球
BLOCKS = {'clock': '问候和时钟', 'date': '日期和农历', 'weather': '天气', 'alarms': '下一个闹钟和倒计时',
          'suggest': '试试这样问我', 'devices': '常用设备（米家）', 'work': '工作台摘要', 'lights': '蓝牙灯（智能中枢）'}
OFF_BY_DEFAULT = {'work', 'lights'}   # 接入网关上的服务才有内容（设计画布第 38 块），默认关
PHOTO_DIR = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'avatars'
PHOTO_MAX = 6
POSES = 5                   # 一个照片形象最多几张：待机、在听、在想、在说、睡觉（设计画布第 26、29 块）
POSE_DIR = PHOTO_DIR / 'poses'
PHOTO_NAME = re.compile(r'^[0-9A-Za-z_-]{1,40}$')
PACK_DIR = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'avatar-pack'
PACK_FILE = re.compile(r'^[0-9A-Za-z._-]{1,120}\.webp$')
RECENT_MAX = 5              # 外观里光球后面列最近用过的几款
_pack_cache = {'mtime': None, 'data': None}


def pack():
    """形象库的 catalog（没装就是 None）：{'version', 'groups': [{name, count}], 'avatars': [{id, name, short, group, tag, series, files}]}，
    多一个 'by_id'。文件改了才重读。"""
    path = PACK_DIR / 'catalog.json'
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _pack_cache['mtime'] != mtime:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            data['by_id'] = {a['id']: a for a in data.get('avatars') or []}
        except (OSError, ValueError, KeyError, TypeError):
            data = None
        _pack_cache.update(mtime=mtime, data=data)
    return _pack_cache['data']


def pack_entry(a, version=''):
    """给页面的一款：id 带 'pack:'，四个地址（/api/avatar-pack/…）。"""
    q = f'?v={version}' if version else ''
    url = {k: f'/api/avatar-pack/{v}{q}' for k, v in a['files'].items()}
    return {'id': 'pack:' + a['id'], 'name': a['name'], 'short': a.get('short') or a['name'], 'group': a.get('group', ''),
            'tag': a.get('tag', ''), 'series': a.get('series', ''), 'alias': a.get('alias', ''), 'thumb': url['thumb'],
            # 和照片形象一样的五个样子：待机、在听、在想、在说、睡觉
            'poses': [url['idle'], url['awake'], url['awake'], url['awake'], url['sleep']]}


def pack_view():
    """形象库给页面的全部（形象库那一页用）。"""
    p = pack()
    if not p:
        return {'available': False, 'groups': [], 'avatars': []}
    return {'available': True, 'version': p.get('version', ''), 'groups': p.get('groups') or [],
            'avatars': [pack_entry(a, p.get('version', '')) for a in p['avatars']]}


def _norm(text):
    return re.sub(r'[\s·・.()（）_-]+', '', str(text or '')).lower()


def find_pack(text):
    """按名字找形象库里的一款（语音「换成星星人」「换成 LABUBU 春节的」）-> (款的 id 或 None, [候选的全名])。
    说的是分组名就用这一组的经典款（没有经典款用第一款）。"""
    p = pack()
    if not p:
        return None, []
    want = _norm(text).replace('形象', '').replace('的', '')
    if not want:
        return None, []
    avs = p['avatars']
    for a in avs:
        if want in (_norm(a['name']), _norm(a['id']), _norm(a.get('alias'))):
            return a['id'], []
    groups = [a for a in avs if _norm(a.get('group')) == want]
    if groups:
        classic = [a for a in groups if a.get('short') == '经典']
        return (classic or groups)[0]['id'], []
    hits = [a for a in avs if all(part in _norm(a['name'] + a.get('group', '') + a.get('series', '') + a.get('tag', '') + a.get('alias', ''))
                                  for part in re.split(r'\s+', str(text).replace('的', ' ').replace('形象', ' ').lower()) if part)]
    if not hits:
        hits = [a for a in avs if want in _norm(a['name']) or _norm(a['short']) == want or (a.get('alias') and want in _norm(a['alias']))]
    if len(hits) == 1:
        return hits[0]['id'], []
    return None, [a['name'] for a in hits[:6]]


def photo_avatars():
    """The avatars made from photos, newest first: [{'id': 'photo:<name>', 'name', 'url'}]."""
    if not PHOTO_DIR.is_dir():
        return []
    files = sorted(PHOTO_DIR.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files:
        poses = [f'/api/avatars/{p.name}'] + [f'/api/avatars/poses/{p.stem}-{i}.png' for i in range(1, POSES)
                                               if (POSE_DIR / f'{p.stem}-{i}.png').is_file()]
        out.append({'id': f'photo:{p.stem}', 'name': '照片形象', 'url': poses[0], 'poses': poses, 'cutout': has_alpha(p)})
    return out


def has_alpha(path):
    """A PNG with an alpha channel (a cut-out figure: shown whole, not in a circle)."""
    try:
        with open(path, 'rb') as f:
            head = f.read(26)
        return head[:8] == b'\x89PNG\r\n\x1a\n' and head[25] in (4, 6)
    except OSError:
        return False


def remove_photo(name):
    if not PHOTO_NAME.match(name):
        return
    (PHOTO_DIR / f'{name}.png').unlink(missing_ok=True)
    for i in range(1, POSES):
        (POSE_DIR / f'{name}-{i}.png').unlink(missing_ok=True)


def normalize_theme(value):
    value = str(value or '').strip()
    by_name = {v: k for k, v in THEMES.items()}
    value = by_name.get(value, value)
    if value not in THEMES:
        raise ValueError(f"主题只能是 {'、'.join(THEMES.values())}")
    return value


def normalize_layout(value):
    value = str(value or '').strip()
    value = {v: k for k, v in LAYOUTS.items()}.get(value, value)
    if value not in LAYOUTS:
        raise ValueError(f"首页布局只能是 {'、'.join(LAYOUTS.values())}")
    return value


def normalize_avatar(value):
    value = str(value or '').strip()
    by_name = {v: k for k, v in AVATARS.items()}
    value = by_name.get(value, value)
    if value in AVATARS:
        return value
    if value.startswith('photo:') and any(a['id'] == value for a in photo_avatars()):
        return value
    if value.startswith('pack:'):
        p = pack()
        if p and value[5:] in p['by_id']:
            return value
        raise ValueError('形象库里没有这一款')
    found, cands = find_pack(value)
    if found:
        return 'pack:' + found
    if cands:
        raise ValueError(f"有好几款：{'、'.join(cands)}，要哪一个？")
    raise ValueError('没找到这个形象：可以说光球、形象库里的名字（比如星星人、LABUBU），或者设置页里用照片生成的')


def remember_recent(recent, avatar):
    """最近用过的形象库款式，新的在前，最多 RECENT_MAX 款。"""
    out = [x for x in (recent or []) if isinstance(x, str) and x.startswith('pack:') and x != avatar]
    if avatar.startswith('pack:'):
        out.insert(0, avatar)
    return out[:RECENT_MAX]


def current_pack(avatar):
    """在用的是形象库的一款时给它的样子（页面显示用），否则 None。"""
    if not str(avatar).startswith('pack:'):
        return None
    p = pack()
    a = p and p['by_id'].get(avatar[5:])
    return pack_entry(a, p.get('version', '')) if a else None


def normalize_dash_tab(value):
    value = str(value or '').strip()
    value = {v: k for k, v in DASH_TABS.items()}.get(value, value)
    if value not in DASH_TABS:
        raise ValueError(f"默认页只能是 {'、'.join(DASH_TABS.values())}")
    return value


def normalize_blocks(blocks, BLOCKS=None):
    """[[id, on], ...] covering every block once, in the given order (unknown ids dropped, missing ones
    added at the end, on unless in OFF_BY_DEFAULT). BLOCKS: which set (the chat home's blocks, or DASH_CARDS)."""
    BLOCKS = BLOCKS or globals()['BLOCKS']
    out, seen = [], set()
    for item in blocks or []:
        if isinstance(item, dict):
            item = [item.get('id'), item.get('on', True)]
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        bid, on = str(item[0]), bool(item[1])
        if bid in BLOCKS and bid not in seen:
            out.append([bid, on])
            seen.add(bid)
    for b in BLOCKS:
        if b in seen:
            continue
        # 后来加的「常用设备」放在天气后面（放最后会被挤出屏幕）；其余缺的加在最后
        at = next((i + 1 for i, (x, _) in enumerate(out) if x == 'weather'), len(out)) if b == 'devices' else len(out)
        out.insert(at, [b, b not in OFF_BY_DEFAULT])
    return out


def theme_now(theme, now=None):
    """The theme to show now ('auto' picks by the hour)."""
    if theme != 'auto':
        return theme
    h = (now or datetime.datetime.now()).hour
    return 'dawn' if AUTO_DAY[0] <= h < AUTO_DAY[1] else 'night'


def view(cfg, now=None):
    p = pack()
    recent = [current_pack(x) for x in remember_recent(cfg.get('avatar_recent'), cfg['avatar'])]
    return {'theme': cfg['theme'], 'theme_now': theme_now(cfg['theme'], now), 'themes': THEMES,
            'avatar': cfg['avatar'], 'avatars': [{'id': k, 'name': v} for k, v in AVATARS.items()],
            'photos': photo_avatars(), 'photo_max': PHOTO_MAX,
            # 形象库：在用的和最近用过的（新的在前）；全部的在 /api/avatar-pack
            'pack': {'available': bool(p), 'count': len(p['avatars']) if p else 0, 'version': (p or {}).get('version', ''),
                     'recent': [x for x in recent if x]},
            'layout': cfg.get('layout', 'chat'), 'layouts': LAYOUTS,
            'dash_default': cfg.get('dash_default', 'home'), 'dash_tabs': DASH_TABS,
            'dash_cards': [{'id': b, 'name': DASH_CARDS[b], 'on': on}
                           for b, on in normalize_blocks(cfg.get('dash_cards'), DASH_CARDS)],
            'blocks': [{'id': b, 'name': BLOCKS[b], 'on': on} for b, on in normalize_blocks(cfg['home_blocks'])]}
