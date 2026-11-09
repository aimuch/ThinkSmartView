"""The page the kiosk shows (127.0.0.1 only): static files, an event stream and button actions."""
import asyncio
import json
import logging
import re
import time

from aiohttp import web

from . import __version__, looks, update
from .app import ALBUM, PHOTOS, ROOT, Client, local_ip

log = logging.getLogger(__name__)
WEB = ROOT / 'web'
# changes on every restart (every deploy restarts the client): the page reloads itself when it
# reconnects to a different run, so the kiosk never keeps running an old app.js
BUILD = str(int(time.time()))


PAGE_PATHS = ('/', '/v1', '/v2')


@web.middleware
async def fresh_pages(request, handler):
    """The page and its scripts are always sent whole, never "304 not modified": release packages set
    every file's time to 0, so aiohttp's ETag is just the size, and a fix that kept a file's size
    (0.1.617 moved a block in index.html) left the kiosk on the broken old page. Local files, tiny."""
    if request.path in PAGE_PATHS or request.path.startswith('/static/'):
        headers = request.headers.copy()
        for h in ('If-None-Match', 'If-Modified-Since', 'If-Range'):
            headers.popall(h, None)
        request = request.clone(headers=headers)
    return await handler(request)


def make_app(client: Client):
    app = web.Application(middlewares=[fresh_pages])

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

    async def broadcast_set(request):
        body = await request.json()
        if body.get('test'):
            asyncio.create_task(client.play_broadcast())
            return web.json_response({'ok': True})

        async def change():
            keys = ('enabled', 'days', 'reminders', 'news', 'add_time', 'remove_time', 'shift')
            return {'broadcast': await client.set_broadcast(**{k: body[k] for k in keys if k in body})}
        return await wake_call(change)

    async def home_record_start(_request):
        return await wake_call(client.home_record_start)

    async def home_record_stop(_request):
        return await wake_call(client.home_record_stop)

    async def home_set(request):
        body = await request.json()

        async def change():
            if body.get('text'):   # typed or picked: look it up first
                return await client.find_place(str(body['text']))
            return {'broadcast': await client.set_home(None if body.get('clear') else body.get('place'))}
        return await wake_call(change)

    async def photo_start(request):
        body = await request.json() if request.can_read_body else {}
        return await wake_call(client.photo_start, str(body.get('question') or ''))

    async def photo_cancel(_request):
        await client.photo_cancel()
        return web.json_response({'ok': True})

    async def photo_file(request):
        name = request.match_info['name']
        path = PHOTOS / name
        if not re.fullmatch(r'[0-9A-Za-z_-]+\.jpg', name) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'max-age=86400'})

    async def looks_set(request):
        body = await request.json()

        async def change():
            if body.get('delete'):
                return {'looks': await client.delete_avatar(str(body['delete']))}
            keys = ('theme', 'avatar', 'blocks', 'move', 'toggle')
            return {'looks': await client.set_looks(**{k: body[k] for k in keys if k in body})}
        return await wake_call(change)

    async def avatar_file(request):
        name = request.match_info['name']
        path = looks.PHOTO_DIR / name
        if not re.fullmatch(r'[0-9A-Za-z_-]{1,40}\.png', name) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'max-age=31536000'})

    async def avatar_start(_request):
        return await wake_call(client.avatar_start)

    async def avatar_style(request):
        body = await request.json()
        return await wake_call(client.avatar_style, body.get('style'))

    async def avatar_use(_request):
        return await wake_call(client.avatar_use)

    app.router.add_post('/api/avatar/start', avatar_start)
    app.router.add_post('/api/avatar/style', avatar_style)
    app.router.add_post('/api/avatar/use', avatar_use)
    async def kids_set(request):
        body = await request.json()

        async def change():
            keys = ('enabled', 'age', 'daily_minutes', 'bedtime', 'voice', 'bedtime_shift', 'minutes_step')
            return {'kids': await client.set_kids(**{k: body[k] for k in keys if k in body})}
        return await wake_call(change)

    async def cook_start(request):
        body = await request.json() if request.can_read_body else {}
        return await wake_call(client.cook_start, body.get('card'))

    async def cook_go(request):
        body = await request.json()
        return await wake_call(client.cook_go, body.get('by', 0), body.get('to'))

    async def cook_stop(_request):
        return await wake_call(client.cook_stop)

    async def cook_timer(_request):
        return await wake_call(client.cook_timer)

    async def standby_set(request):
        body = await request.json()

        async def change():
            if body.get('delete'):
                return {'standby': await client.album_delete(str(body['delete']))}
            keys = ('enabled', 'minutes', 'album')
            return {'standby': await client.set_standby(**{k: body[k] for k in keys if k in body})}
        return await wake_call(change)

    async def album_start(_request):
        return await wake_call(client.album_start)

    async def album_file(request):
        name = request.match_info['name']
        path = ALBUM / name
        if not re.fullmatch(r'[0-9A-Za-z_-]{1,60}\.jpg', name) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'max-age=31536000'})

    async def daily_set(request):
        body = await request.json()
        kind = request.match_info['kind']

        async def change():
            if kind == 'scenes':
                return {'scenes': await client.delete_scene(str(body.get('delete') or ''))}
            if kind == 'notes':
                if body.get('read'):
                    return await client.note_read(str(body['read']))
                return {'notes': await client.note_done(str(body.get('done') or ''))}
            if kind == 'lists':
                if body.get('op') == 'refresh':
                    return {'lists': await client.refresh_lists()}
                return {'lists': await client.list_change(body.get('op'), body.get('name'), body.get('items') or [], body.get('done'))}
            if body.get('stop'):
                return {'media': await client.media_stop()}
            await client.media_play(body.get('kind'), body.get('name'), body.get('minutes'))
            return {'media': client.media_view()}
        return await wake_call(change)

    async def proactive_set(request):
        body = await request.json()
        kind = request.match_info['kind']

        async def change():
            if kind == 'tasks':
                return {'tasks': await client.task_delete(str(body.get('delete') or ''))}
            if kind == 'alerts':
                if body.get('clear'):
                    return {'alerts': await client.clear_alert()}
                return {'alerts': await client.set_alerts(**{k: body[k] for k in ('leak', 'door_night') if k in body})}
            return {'translate': await client.set_translate(body.get('enabled'), body.get('a'), body.get('b'))}
        return await wake_call(change)

    async def voices(request):
        body = await request.json() if request.can_read_body else {}
        act = request.match_info['act']

        async def run():
            if act == 'list':
                return {'voices': await client.voices_view()}
            if act == 'start':
                return await client.voice_record_start()
            if act == 'name':
                return await client.voice_name_stop()
            if act == 'sample':
                return await client.voice_sample_stop(str(body.get('name') or ''), bool(body.get('child')))
            return {'voices': await client.voice_remove(str(body.get('name') or ''))}
        return await wake_call(run)

    app.router.add_post('/api/voices/{act}', voices)
    app.router.add_post('/api/proactive/{kind}', proactive_set)
    app.router.add_post('/api/daily/{kind}', daily_set)
    app.router.add_post('/api/cook/start', cook_start)
    app.router.add_post('/api/cook/go', cook_go)
    app.router.add_post('/api/cook/stop', cook_stop)
    app.router.add_post('/api/cook/timer', cook_timer)
    app.router.add_post('/api/standby', standby_set)
    app.router.add_post('/api/album/start', album_start)
    app.router.add_get('/api/album/{name}', album_file)
    app.router.add_post('/api/kids', kids_set)
    app.router.add_post('/api/looks', looks_set)
    app.router.add_get('/api/avatars/{name}', avatar_file)
    app.router.add_post('/api/photo', photo_start)
    app.router.add_post('/api/photo/cancel', photo_cancel)
    app.router.add_get('/api/photos/{name}', photo_file)
    app.router.add_post('/api/broadcast', broadcast_set)
    app.router.add_post('/api/home/record/start', home_record_start)
    app.router.add_post('/api/home/record/stop', home_record_stop)
    app.router.add_post('/api/home', home_set)
    app.router.add_get('/api/health', health)
    app.router.add_get('/api/network', network)
    app.router.add_post('/api/power-off', power_off)
    app.router.add_post('/api/update/check', update_check)
    app.router.add_post('/api/update/install', update_install)
    app.router.add_static('/static/', WEB)

    async def revalidate(request, response):
        # the kiosk keeps a disk cache; without this it goes on running the old app.js/css after
        # an update (heuristic freshness from Last-Modified). no-cache = check the ETag every load.
        if request.path.startswith('/static/') or request.path in PAGE_PATHS:
            response.headers['Cache-Control'] = 'no-store'
            response.headers.popall('ETag', None)
            response.headers.popall('Last-Modified', None)
    app.on_response_prepare.append(revalidate)
    return app
