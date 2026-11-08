"""The page the kiosk shows (127.0.0.1 only): static files, an event stream and button actions."""
import asyncio
import json
import logging
import time

from aiohttp import web

from . import __version__, update
from .app import ROOT, Client, local_ip

log = logging.getLogger(__name__)
WEB = ROOT / 'web'
# changes on every restart (every deploy restarts the client): the page reloads itself when it
# reconnects to a different run, so the kiosk never keeps running an old app.js
BUILD = str(int(time.time()))


def make_app(client: Client):
    app = web.Application()

    async def index(_request):
        # the second design (orb with a face) is the UI since 2026-10-31; /v1 keeps the first one
        return web.FileResponse(WEB / 'v2' / 'index.html', headers={'Cache-Control': 'no-cache'})

    async def index_v1(_request):
        return web.FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-cache'})

    async def events(request):
        resp = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'})
        await resp.prepare(request)
        q = asyncio.Queue()
        client.events.queues.add(q)
        try:
            q.put_nowait(('snapshot', {**client.snapshot(), 'build': BUILD, 'version': __version__,
                                       'update': update.read()}))
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    await resp.write(f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode())
                except asyncio.TimeoutError:
                    await resp.write(b': keepalive\n\n')
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            client.events.queues.discard(q)
        return resp

    def action(coro_fn):
        async def handler(_request):
            asyncio.create_task(coro_fn())
            return web.json_response({'ok': True})
        return handler

    async def text(request):
        body = await request.json()
        asyncio.create_task(client.say(body.get('text', '')))
        return web.json_response({'ok': True})

    async def model(request):
        body = await request.json()
        return web.json_response(await client.set_model(body.get('kind'), body.get('vendor'), body.get('entry')))

    async def sleep_setting(request):
        body = await request.json()
        try:
            value = await client.set_sleep(**{k: body.get(k) for k in ('enabled', 'start', 'end')})
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'sleep': value})

    async def brightness(request):
        body = await request.json()
        try:
            value = await client.set_brightness(body.get('level'))
        except (TypeError, ValueError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'brightness': value})

    async def screen_off(request):
        body = await request.json()
        try:
            value = await client.set_screen_off(**{k: body.get(k) for k in ('enabled', 'minutes')})
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'screen_off': value})

    async def volume(request):
        body = await request.json()
        try:
            value = await client.set_volume(body.get('volume'))
        except (TypeError, ValueError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'volume': value})

    async def alarm_volume(request):
        body = await request.json()
        try:
            value = await client.set_alarm_volume(body.get('volume'))
        except (TypeError, ValueError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'volume': value})

    async def alarm_tone(request):
        body = await request.json()
        try:
            if body.get('stop'):
                await client._stop_preview()      # the settings closed while a tone was being tried
            elif body.get('preview'):
                await client.preview_tone(body.get('tone'))
            else:
                client.set_alarm_tone(body.get('tone'))
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True})

    async def alarm_delete(request):
        body = await request.json()
        try:
            item = await client.delete_alarm(int(body.get('id')))
        except (TypeError, ValueError):
            return web.json_response({'ok': False, 'error': 'id must be a number'}, status=400)
        return web.json_response({'ok': bool(item), **({} if item else {'error': '这一条已经没有了'})})

    async def alarm_snooze(_request):
        found = await client.snooze_alarm()
        return web.json_response({'ok': bool(found), **({} if found else {'error': '没有刚响过的闹钟'})})

    async def alarm_stop(_request):
        return web.json_response({'ok': await client.stop_alarm()})

    async def alarm_missed_clear(_request):
        client.clear_missed()
        return web.json_response({'ok': True})

    async def clear_history(request):
        body = await request.json()
        return web.json_response(await client.clear_history(bool(body.get('memory_only'))))

    async def bluetooth(request):
        body = await request.json()
        args = {k: body[k] for k in ('enabled', 'window', 'remove', 'disconnect') if k in body}
        ok = await client.bluetooth(**args)
        return web.json_response({'ok': bool(ok), **({} if ok else {'error': '蓝牙没有响应'})})

    # ---- Mijia (thinksmart_client/mijia) ----

    async def mijia_view(_request):
        return web.json_response(client.mijia.view())

    async def mijia_login(request):
        body = await request.json()
        await client.mijia.login_start(body.get('region') or 'cn')
        return web.json_response({'ok': True})

    async def mijia_login_cancel(_request):
        await client.mijia.login_cancel()
        client.mijia._changed()
        return web.json_response({'ok': True})

    async def mijia_logout(_request):
        await client.mijia.logout()
        return web.json_response({'ok': True})

    async def mijia_home(request):
        body = await request.json()
        asyncio.create_task(client.mijia.set_home(str(body.get('id') or '')))
        return web.json_response({'ok': True})

    async def mijia_visible(request):
        body = await request.json()
        await client.mijia.set_visible(bool(body.get('visible')))
        return web.json_response({'ok': True})

    async def mijia_refresh(_request):
        asyncio.create_task(client.mijia.reload())
        return web.json_response({'ok': True})

    async def mijia_control(request):
        body = await request.json()
        r = await client.mijia.control(str(body.get('id') or ''), str(body.get('op') or ''), body.get('value'),
                                       confirm=bool(body.get('confirm')), by_voice=False)
        return web.json_response({'ok': r['ok'], 'need_confirm': r.get('need_confirm', False),
                                  **({} if r['ok'] or r.get('need_confirm') else {'error': r['text']}),
                                  'text': r['text']})

    async def billing(_request):
        """The hub's model spending (today, this month, total, quota) for the settings page."""
        try:
            data = await client._hub_http('/kiosk/billing')
        except Exception:
            return web.json_response({'ok': False, 'error': '连不上小麦网关'})
        return web.json_response(data if isinstance(data, dict) else {'ok': False})

    async def rotation_set(request):
        body = await request.json()
        try:
            view = await client.rotation.set(value=body.get('value'), auto=body.get('auto'))
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'rotation': view})

    async def wake_call(fn, *args):
        try:
            return web.json_response({'ok': True, **(await fn(*args) or {})})
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)})

    async def wake_record_start(_request):
        return await wake_call(client.wake_record_start)

    async def wake_record_stop(_request):
        async def stop():
            return {'heard': await client.wake_record_stop()}
        return await wake_call(stop)

    async def wake_add(request):
        body = await request.json()

        async def add():
            view, warning = await client.add_wake_word(str(body.get('label') or ''), body.get('picks'))
            return {'wake_words': view, 'warning': warning}
        return await wake_call(add)

    async def wake_remove(request):
        body = await request.json()

        async def remove():
            return {'wake_words': await client.remove_wake_word(str(body.get('label') or ''))}
        return await wake_call(remove)

    async def wake_reset(_request):
        async def reset():
            return {'wake_words': await client.reset_wake_words()}
        return await wake_call(reset)

    async def health(_request):
        return web.json_response({'version': __version__, **client.health()})

    async def power_off(_request):
        return web.json_response(await client.power_off())

    async def network(_request):
        return web.json_response({'ip': local_ip()})

    async def update_check(_request):
        return web.json_response({'ok': update.request('check')})

    async def update_install(request):
        if client.state != 'idle':
            return web.json_response({'ok': False, 'error': '对话结束后再更新'}, status=409)
        body = await request.json()
        return web.json_response({'ok': update.request('install', str(body.get('version') or ''),
                                                       str(body.get('system') or ''))})

    watchers = []   # keeps the task referenced

    async def watch_updates(_app):
        watchers.append(asyncio.create_task(update.watch(client.events.publish)))

    app.on_startup.append(watch_updates)
    app.router.add_get('/', index)
    app.router.add_get('/v2', index)
    app.router.add_get('/v1', index_v1)
    app.router.add_get('/events', events)
    app.router.add_post('/api/wake', action(lambda: client.wake('button')))
    app.router.add_post('/api/end', action(client.end))
    app.router.add_post('/api/abort', action(client.abort))
    app.router.add_post('/api/ptt/start', action(client.ptt_start))
    app.router.add_post('/api/ptt/stop', action(client.ptt_stop))
    app.router.add_post('/api/model', model)
    app.router.add_post('/api/text', text)
    app.router.add_post('/api/sleep', sleep_setting)
    app.router.add_post('/api/brightness', brightness)
    app.router.add_post('/api/screen-off', screen_off)
    app.router.add_post('/api/volume', volume)
    app.router.add_post('/api/alarm-volume', alarm_volume)
    app.router.add_post('/api/alarm-tone', alarm_tone)
    app.router.add_post('/api/alarms/delete', alarm_delete)
    app.router.add_post('/api/alarms/snooze', alarm_snooze)
    app.router.add_post('/api/alarms/stop', alarm_stop)
    app.router.add_post('/api/alarms/missed/clear', alarm_missed_clear)
    app.router.add_post('/api/reports/play', action(client.play_reports))
    app.router.add_post('/api/history/clear', clear_history)
    app.router.add_post('/api/bluetooth', bluetooth)
    app.router.add_get('/api/mijia', mijia_view)
    app.router.add_post('/api/mijia/login/start', mijia_login)
    app.router.add_post('/api/mijia/login/cancel', mijia_login_cancel)
    app.router.add_post('/api/mijia/logout', mijia_logout)
    app.router.add_post('/api/mijia/home', mijia_home)
    app.router.add_post('/api/mijia/visible', mijia_visible)
    app.router.add_post('/api/mijia/refresh', mijia_refresh)
    app.router.add_post('/api/mijia/control', mijia_control)
    app.router.add_get('/api/billing', billing)
    app.router.add_post('/api/rotation', rotation_set)
    app.router.add_post('/api/wake-words/record/start', wake_record_start)
    app.router.add_post('/api/wake-words/record/stop', wake_record_stop)
    app.router.add_post('/api/wake-words/add', wake_add)
    app.router.add_post('/api/wake-words/remove', wake_remove)
    app.router.add_post('/api/wake-words/reset', wake_reset)
    app.router.add_get('/api/health', health)
    app.router.add_get('/api/network', network)
    app.router.add_post('/api/power-off', power_off)
    app.router.add_post('/api/update/check', update_check)
    app.router.add_post('/api/update/install', update_install)
    app.router.add_static('/static/', WEB)

    async def revalidate(request, response):
        # the kiosk keeps a disk cache; without this it goes on running the old app.js/css after
        # an update (heuristic freshness from Last-Modified). no-cache = check the ETag every load.
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'
    app.on_response_prepare.append(revalidate)
    return app
