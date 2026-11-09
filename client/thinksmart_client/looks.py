"""外观 (设置页「外观」, 设计画布第 8 块): the colour theme, the conversation avatar and which blocks the
home page shows in which order. The page applies them (body[data-theme], body[data-avatar], CSS order);
this only keeps them valid. Avatars made from a photo live in AVATARS as PNGs ('photo:<name>')."""
import datetime
import re
from pathlib import Path

THEMES = {'night': '星夜', 'dawn': '晨光', 'forest': '森林', 'candy': '童趣', 'black': '纯黑', 'auto': '跟着时间'}
AUTO_DAY = (7, 19)          # 'auto': 晨光 from 7:00 to 19:00, 星夜 otherwise
AVATARS = {'orb': '光球', 'cat': '小猫', 'dog': '小狗', 'bear': '小熊', 'rabbit': '兔子', 'panda': '熊猫',
           'wheat': '麦穗宝宝', 'robot': '机器人'}
BLOCKS = {'clock': '问候和时钟', 'date': '日期和农历', 'weather': '天气', 'alarms': '下一个闹钟和倒计时',
          'suggest': '试试这样问我'}
PHOTO_DIR = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'avatars'
PHOTO_MAX = 6
PHOTO_NAME = re.compile(r'^[0-9A-Za-z_-]{1,40}$')


def photo_avatars():
    """The avatars made from photos, newest first: [{'id': 'photo:<name>', 'name', 'url'}]."""
    if not PHOTO_DIR.is_dir():
        return []
    files = sorted(PHOTO_DIR.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{'id': f'photo:{p.stem}', 'name': '照片形象', 'url': f'/api/avatars/{p.name}'} for p in files]


def normalize_theme(value):
    value = str(value or '').strip()
    by_name = {v: k for k, v in THEMES.items()}
    value = by_name.get(value, value)
    if value not in THEMES:
        raise ValueError(f"主题只能是 {'、'.join(THEMES.values())}")
    return value


def normalize_avatar(value):
    value = str(value or '').strip()
    by_name = {v: k for k, v in AVATARS.items()}
    value = by_name.get(value, value)
    if value in AVATARS:
        return value
    if value.startswith('photo:') and any(a['id'] == value for a in photo_avatars()):
        return value
    raise ValueError(f"形象只能是 {'、'.join(AVATARS.values())}，或者设置页里用照片生成的")


def normalize_blocks(blocks):
    """[[id, on], ...] covering every block once, in the given order (unknown ids dropped, missing ones
    added at the end, on)."""
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
    out += [[b, True] for b in BLOCKS if b not in seen]
    return out


def theme_now(theme, now=None):
    """The theme to show now ('auto' picks by the hour)."""
    if theme != 'auto':
        return theme
    h = (now or datetime.datetime.now()).hour
    return 'dawn' if AUTO_DAY[0] <= h < AUTO_DAY[1] else 'night'


def view(cfg, now=None):
    return {'theme': cfg['theme'], 'theme_now': theme_now(cfg['theme'], now), 'themes': THEMES,
            'avatar': cfg['avatar'], 'avatars': [{'id': k, 'name': v} for k, v in AVATARS.items()],
            'photos': photo_avatars(), 'photo_max': PHOTO_MAX,
            'blocks': [{'id': b, 'name': BLOCKS[b], 'on': on} for b, on in normalize_blocks(cfg['home_blocks'])]}
