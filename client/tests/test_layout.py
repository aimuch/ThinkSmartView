"""首页布局和薰衣草主题（设计画布第 35 块）。"""
import pytest

from thinksmart_client import config, looks


def test_layout_names():
    assert looks.normalize_layout('仪表盘') == 'dashboard'
    assert looks.normalize_layout('chat') == 'chat'
    with pytest.raises(ValueError):
        looks.normalize_layout('瀑布流')
    assert config.DEFAULTS['layout'] == 'chat'


def test_lavender_theme_and_view():
    assert looks.normalize_theme('薰衣草') == 'lavender'
    v = looks.view({**config.DEFAULTS, 'layout': 'dashboard'})
    assert v['layout'] == 'dashboard' and v['layouts']['dashboard'] == '仪表盘'


def test_dash_cards_order_and_default_page():
    cards = looks.normalize_blocks([['dev', True], ['nope', True], ['wx', False]], looks.DASH_CARDS)
    assert cards[:2] == [['dev', True], ['wx', False]] and len(cards) == len(looks.DASH_CARDS)
    assert looks.normalize_blocks(None)[0][0] == 'clock'          # 对话首页的内容不受影响
    assert looks.normalize_dash_tab('对话') == 'chat'
    with pytest.raises(ValueError):
        looks.normalize_dash_tab('设置')
    v = looks.view({**config.DEFAULTS, 'dash_default': 'chat'})
    assert v['dash_default'] == 'chat' and [c['id'] for c in v['dash_cards']][:3] == ['wx', 'brief', 'dev']
