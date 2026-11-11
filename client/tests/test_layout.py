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
