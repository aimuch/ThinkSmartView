"""电台清单从网关来（设计画布第 56 块）：media.set_stations 原地换 STATIONS，空的或不对就回到自带的。

Run like test_barge_in.py.
"""
import pytest

from thinksmart_client import media


@pytest.fixture(autouse=True)
def builtin():
    yield
    media.set_stations([])


def test_hub_list_replaces_and_falls_back():
    same = media.STATIONS
    assert media.set_stations([{'id': 'zgzs', 'name': '中国之声', 'url': 'http://a/zgzs.m3u8'},
                               {'id': 'c1a2b3', 'name': '上海交通广播', 'url': 'http://x/shjt.m3u8'},
                               {'id': 'bad', 'name': '坏的', 'url': 'ftp://x'}, 'junk']) is True
    assert media.STATIONS is same and list(media.STATIONS) == ['zgzs', 'c1a2b3']
    assert media.find_station('上海交通广播') == 'c1a2b3' and media.find_station('上海交通') == 'c1a2b3'
    assert media.find_station('东方广播') is None          # 别名指向的台被关掉了
    assert media.set_stations([{'id': 'zgzs', 'name': '中国之声', 'url': 'http://a/zgzs.m3u8'},
                               {'id': 'c1a2b3', 'name': '上海交通广播', 'url': 'http://x/shjt.m3u8'}]) is False   # 没变
    assert media.set_stations(None) is True and media.STATIONS == media.BUILTIN
    assert media.find_station('东方广播') == 'dfgb'
