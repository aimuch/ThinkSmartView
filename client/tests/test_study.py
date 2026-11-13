"""学习陪伴（study.py，设计画布第 48 块）：什么时候提醒、提醒什么、一天的记录。"""
import asyncio
import json
import sys
import types

import numpy as np
import pytest
import pytest_asyncio

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):   # 和别的测试一样：不用真的声卡、识别库
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'')
    opus.Decoder = lambda *a: types.SimpleNamespace(decode=lambda p: np.zeros(0, np.float32))
    sys.modules['thinksmart_client.opus'] = opus
try:
    import thinksmart_client.speex_aec  # noqa: F401  (needs libspeexdsp)
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import study  # noqa: E402

GOOD = {'present': True, 'posture': 'good', 'too_close': False, 'activity': 'studying', 'focused': True, 'note': '在写作业'}
SLOUCH = {**GOOD, 'posture': 'slouch', 'note': '背有点弯'}
PHONE = {**GOOD, 'activity': 'phone', 'focused': False, 'note': '在看手机'}
AWAY = {'present': False, 'posture': 'unknown', 'too_close': False, 'activity': 'absent', 'focused': False, 'note': ''}


def test_normalize():
    assert study.normalize(None) == study.DEFAULT
    v = study.normalize({'interval': 120, 'auto_off': 0, 'issues': ['focus', 'posture', 'nope']})
    assert v == {'interval': 120, 'auto_off': 0, 'issues': ['posture', 'focus']}
    assert study.normalize({'interval': 7, 'auto_off': 9})['interval'] == 60


def test_issues_and_kinds():
    assert study.issues_of(GOOD) == []
    assert study.issues_of({**SLOUCH, 'too_close': True, 'activity': 'sleeping'}) == ['sleep', 'eyes', 'posture']
    assert study.issues_of({**AWAY, 'activity': 'phone'}) == []          # 人不在什么都不算
    assert [study.kind_of(r) for r in (GOOD, SLOUCH, PHONE, AWAY)] == ['focus', 'focus', 'bad', 'absent']
    assert study.kind_of({**GOOD, 'activity': 'daydreaming', 'focused': False}) == 'drift'


def test_reminds_after_two_in_a_row_then_cools_down(tmp_path):
    s = study.Study(None, tmp_path)
    s.start(1000)
    assert s.judge(SLOUCH, 1060) == (None, None)               # 第一次只记着
    say, issue = s.judge(SLOUCH, 1120)
    assert issue == 'posture' and say in study.ISSUES['posture'][1]
    assert s.judge(SLOUCH, 1180) == (None, None)               # 连续次数重新数
    assert s.judge(SLOUCH, 1240) == (None, None)               # 够两次了，但同类 5 分钟内不再说
    assert s.judge(SLOUCH, 1420)[1] == 'posture'               # 一直驼背：冷却一过马上再说


def test_streak_breaks_and_min_gap(tmp_path):
    s = study.Study(None, tmp_path)
    s.start(0)
    s.judge(SLOUCH, 60)
    s.judge(GOOD, 120)                                         # 中间好了一次：断了
    assert s.judge(SLOUCH, 180) == (None, None)
    assert s.judge(SLOUCH, 240)[1] == 'posture'
    s.judge(PHONE, 260)
    assert s.judge(PHONE, 280) == (None, None)                 # 别的问题也要等 MIN_GAP
    assert s.judge(PHONE, 240 + study.MIN_GAP)[1] == 'phone'


def test_only_chosen_issues(tmp_path):
    s = study.Study({'issues': ['phone']}, tmp_path)
    s.start(0)
    s.judge(SLOUCH, 60)
    assert s.judge(SLOUCH, 120) == (None, None)
    playing = {**GOOD, 'activity': 'playing', 'focused': False}
    s.judge(playing, 300)
    assert s.judge(playing, 360)[1] == 'playing'               # 「玩手机」那一项也管玩别的


def test_switch_auto_off_and_schedule():
    s = study.Study({'auto_off': 1})
    assert not s.due(0)
    assert s.start(100, source='focus') and s.source == 'focus'
    assert not s.due(104) and s.due(105)
    s.scheduled(105)
    assert not s.due(150) and s.due(165)
    assert s.start(200) is False and s.source is None          # 手动再开：不跟着专注关
    assert not s.auto_off_due(100 + 3599) and s.auto_off_due(100 + 3600)
    assert s.stop() and not s.enabled and not s.stop()
    s.settings['auto_off'] = 0
    s.start(0)
    assert not s.auto_off_due(10 ** 6)


def test_log_and_day_summary(tmp_path):
    s = study.Study(None, tmp_path)
    s.start(0)
    t0 = 1794560000                                           # 2026-11-13 傍晚（本地时间）
    for i, r in enumerate([GOOD, GOOD, SLOUCH, SLOUCH, PHONE, AWAY, GOOD]):
        s.judge(r, t0 + i * 60)
    date = study.time.strftime('%Y-%m-%d', study.time.localtime(t0))
    lines = (tmp_path / f'{date}.jsonl').read_text().splitlines()
    assert len(lines) == 7 and 'photo' not in lines[0] and json.loads(lines[3])['said'] == 'posture'
    d = s.day(date)
    assert d['checks'] == 7 and d['minutes'] == 7 and d['present_minutes'] == 6
    assert d['focused_pct'] == round(100 * 5 / 6)          # 驼背也算在专心写
    assert d['reminders']['posture'] == 1 and d['reminder_total'] == 1
    assert [x['kind'] for x in d['spans']] == ['focus', 'bad', 'absent', 'focus']
    assert [(x['text'], x['said']) for x in d['recent']] == [
        ('在写作业', None), ('人不在桌前', None), ('在看手机', None), ('背有点弯', '坐姿'), ('背有点弯', None), ('在写作业', None)]
    assert d['recent'][-1]['t'] == t0 and d['recent'][-1]['until'] == t0 + 60      # 头两次一样：合成一行
    assert s.days() == [date]


def test_day_rejects_odd_dates(tmp_path):
    (tmp_path / 'x.jsonl').write_text('{"t": 1}\n')
    s = study.Study(None, tmp_path / 'study')
    assert s.day('../x.jsonl')['checks'] == 0 and s.day('../../x0000')['checks'] == 0


def test_view():
    s = study.Study()
    v = s.view(0)
    assert v['enabled'] is False and v['last'] is None and v['issue_names']['eyes'] == '离太近'
    s.start(0)
    s.judge(SLOUCH, 30)
    v = s.view(90)
    assert v['auto_off_left'] == 3 * 3600 - 90
    assert v['last'] == {'ago': 60, 'text': '背有点弯', 'kind': 'focus'}


# ---- 设备上：定时看、连拍交给网关、跟着专注开关（app.Client 的那几步） ----

def _client(tmp_path, monkeypatch, hub_url):
    from thinksmart_client import app, camera, config
    monkeypatch.setattr(camera, 'available', lambda: True)
    monkeypatch.setattr(study, 'GAP', 0)
    monkeypatch.setattr(config, 'save', lambda **kw: None)
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.published = []
    c.events.publish = lambda kind, data: c.published.append((kind, data))
    c.cfg = {'device_id': 'dev1', 'hub_http': hub_url}
    c.state, c.vad_ended, c.ring, c.photo = 'idle', False, None, None
    c.hub = types.SimpleNamespace(connected=False)
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.study = study.Study(None, tmp_path)
    c.focus = app.focus.Focus()
    c.media = None
    c.said = []

    async def say_local(text):
        c.said.append(text)
    c._say_local = say_local

    class Cam:
        users = shots = 0

        async def acquire(self):
            self.users += 1

        def release(self):
            self.users -= 1

        async def shoot(self):
            self.shots += 1
            return b'\xff\xd8jpeg%d' % self.shots
    c.camera = Cam()
    return c


@pytest_asyncio.fixture
async def hub():
    from aiohttp import web
    seen = {'calls': [], 'result': SLOUCH}

    async def check(request):
        parts = []
        reader = await request.multipart()
        while (part := await reader.next()) is not None:
            parts.append((part.name, await part.read()))
        seen['calls'].append((dict(request.query), parts))
        return web.json_response({'ok': True, 'result': seen['result']})
    app_ = web.Application()
    app_.router.add_post('/kiosk/study/check', check)
    runner = web.AppRunner(app_)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    seen['url'] = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    yield seen
    await runner.cleanup()


@pytest.mark.asyncio
async def test_checks_burst_and_reminds(tmp_path, monkeypatch, hub):
    c = _client(tmp_path, monkeypatch, hub['url'])
    await c.study_control(enabled=True)
    await asyncio.sleep(0)
    assert c.study.enabled and c.said == [study.SWITCH['on']]
    now = c.study.next_at
    for i in range(2):                                      # 两次都驼背：第二次提醒
        await c._study_tick(now + i * 60)
        await c.study_task
    query, parts = hub['calls'][0]
    assert query == {'device': 'dev1', 'gap': '0'} and [p[0] for p in parts] == ['photo'] * study.BURST
    assert parts[0][1] == b'\xff\xd8jpeg1' and c.camera.users == 0
    assert c.said[-1] in study.ISSUES['posture'][1]
    said = [d['said'] for k, d in c.published if k == 'study' and d.get('said')]
    assert said == [{'text': c.said[-1], 'issue': '坐姿'}]


@pytest.mark.asyncio
async def test_skips_while_talking_and_auto_off(tmp_path, monkeypatch, hub):
    c = _client(tmp_path, monkeypatch, hub['url'])
    await c.study_control(enabled=True, speak=False)
    c.state, c.hub.connected = 'speaking', True              # 正在对话：这一次不看
    await c._study_tick(c.study.next_at)
    assert c.study_task is None and hub['calls'] == []
    c.state, c.hub.connected = 'idle', False
    await c._study_tick(c.study.enabled_at + 3 * 3600)        # 开了 3 小时：自动关
    await asyncio.sleep(0)
    assert not c.study.enabled and c.said == [study.SWITCH['auto_off']]


@pytest.mark.asyncio
async def test_rides_along_with_focus(tmp_path, monkeypatch, hub):
    c = _client(tmp_path, monkeypatch, hub['url'])
    await c.focus_action('start', 25, with_study=True)
    assert c.study.enabled and c.study.source == 'focus'
    await c.focus_action('stop')
    assert not c.study.enabled                              # 跟着专注开的，跟着关
    await c.study_control(enabled=True, speak=False)          # 自己开的：专注结束不关
    await c.focus_action('start', 25)
    await c.focus_action('stop')
    assert c.study.enabled


@pytest.mark.asyncio
async def test_hub_error_is_shown(tmp_path, monkeypatch, hub):
    c = _client(tmp_path, monkeypatch, 'http://127.0.0.1:9')     # 网关连不上
    await c.study_control(enabled=True, speak=False)
    await c._study_tick(c.study.next_at)
    await c.study_task
    assert c.study.view(0)['error'] == '连不上小麦网关' and c.said == []
