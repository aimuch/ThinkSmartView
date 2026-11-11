"""形象、相册用机身摄像头拍（设计画布第 33 块）：什么时候还能接着拍。"""
from thinksmart_client import app, looks

can = app.Client._camera_can_shoot


def p(**kw):
    return {'camera': True, 'shooting': False, 'shots': [], 'state': 'waiting', 'purpose': 'ask', **kw}


def test_ask_shoots_once():
    assert can(None, p())
    assert not can(None, p(state='received'))
    assert not can(None, p(shooting=True))
    assert not can(None, p(camera=False))
    assert not can(None, None)


def test_avatar_takes_up_to_the_poses():
    assert can(None, p(purpose='avatar'))
    assert can(None, p(purpose='avatar', state='received', shots=['a']))
    assert not can(None, p(purpose='avatar', state='received', shots=['a'] * looks.POSES))
    assert not can(None, p(purpose='avatar', state='generating', shots=['a']))


def test_album_keeps_shooting_while_waiting():
    assert can(None, p(purpose='album', shots=['a'] * 7))
    assert not can(None, p(purpose='album', state='expired'))
