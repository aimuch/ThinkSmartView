"""Switching models from the page: a hub that answers with the new list is asked once; an older one twice.

Run like test_barge_in.py (same stubs).
"""
import pytest

from test_barge_in import make_client

OPTIONS = [{'kind': 'api', 'vendor': 'deepseek', 'label': 'DeepSeek', 'entry': 'DeepSeekLLM', 'model': 'deepseek-chat'},
           {'kind': 'cli', 'vendor': 'claude-code', 'label': 'Claude Code', 'entry': 'ClaudeCLI', 'model': ''}]


def client_with_hub(answer):
    c = make_client()
    del c.refresh_models   # the real one, asking the fake hub below
    c.cfg['device_id'] = 'dev-1'
    calls = []

    async def hub_http(path, body=None):
        calls.append((path.split('?')[0], body))
        if body is None:
            return {'ok': True, 'selected': 'ClaudeCLI', 'options': OPTIONS}
        return answer
    c._hub_http = hub_http
    return c, calls


@pytest.mark.asyncio
async def test_hub_answering_with_the_list_is_asked_once():
    c, calls = client_with_hub({'ok': True, 'note': '已切换', 'selected': 'ClaudeCLI', 'default': 'DeepSeekLLM',
                                'kind': 'api', 'vendor': 'deepseek', 'options': OPTIONS})
    seen = []
    c.events.publish = lambda kind, data: seen.append(kind)
    result = await c.set_model('cli', 'claude-code', 'ClaudeCLI')
    assert result == {'ok': True, 'note': '已切换'}   # the page needs no list
    assert [p for p, _ in calls] == ['/kiosk/llm/use']
    assert c.models['current']['entry'] == 'ClaudeCLI' and 'model' in seen


@pytest.mark.asyncio
async def test_older_hub_then_asked_for_the_list():
    c, calls = client_with_hub({'ok': True, 'applied': True, 'note': '已生效'})
    await c.set_model('cli', 'claude-code', 'ClaudeCLI')
    assert [p for p, _ in calls] == ['/kiosk/llm/use', '/kiosk/llm']
    assert c.models['current']['entry'] == 'ClaudeCLI'


@pytest.mark.asyncio
async def test_failed_switch_reports_the_error_and_rereads_the_list():
    c, calls = client_with_hub({'ok': False, 'error': '切换失败：claude 启动失败'})
    result = await c.set_model('cli', 'claude-code', 'ClaudeCLI')
    assert result == {'ok': False, 'error': '切换失败：claude 启动失败'}
    assert [p for p, _ in calls] == ['/kiosk/llm/use', '/kiosk/llm']


@pytest.mark.asyncio
async def test_poller_retries_quickly_until_the_hub_answers(monkeypatch):
    import asyncio
    from thinksmart_client import app as app_mod
    c, calls = client_with_hub(None)
    answers = [OSError('refused'), OSError('refused'), {'ok': True, 'selected': 'ClaudeCLI', 'options': OPTIONS}]
    real_http = c._hub_http

    async def hub_http(path, body=None):
        if path.startswith('/kiosk/llm') and answers:
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        return await real_http(path, body)
    c._hub_http = hub_http
    histories = []
    c.history_loaded = False

    async def load_history():
        histories.append(1)
        c.history_loaded = True
    c.load_history = load_history
    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 4:
            raise asyncio.CancelledError
    monkeypatch.setattr(app_mod.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await c._model_poller()
    # down, down: retry soon; up: the list and the history, then the slow pace stays
    assert sleeps == [app_mod.MODEL_RETRY, app_mod.MODEL_RETRY, app_mod.MODEL_POLL, app_mod.MODEL_POLL]
    assert c.models['current']['entry'] == 'ClaudeCLI' and histories == [1]


@pytest.mark.asyncio
async def test_switch_while_connected_before_anything_said_shows_that_models_thread():
    # connected (e.g. reconnected after an interrupt) but nothing said yet: the page still switches thread
    c, _ = client_with_hub({'ok': True, 'selected': 'DeepSeekLLM', 'options': OPTIONS})
    c._apply_models({'selected': 'ClaudeCLI', 'options': OPTIONS})
    c.threads = {'ClaudeCLI': [{'id': 'h1', 'text': 'claude'}], 'DeepSeekLLM': [{'id': 'h2', 'text': 'deepseek'}]}
    c.hub.connected = True
    c.entry = None
    seen = []
    c.events.publish = lambda kind, data: seen.append((kind, data))
    await c.set_model('api', 'deepseek', 'DeepSeekLLM')
    threads = [d for k, d in seen if k == 'thread']
    assert threads and threads[-1]['entry'] == 'DeepSeekLLM' and threads[-1]['messages'][0]['id'] == 'h2'
