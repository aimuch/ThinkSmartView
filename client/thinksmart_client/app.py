"""The voice client: wake word → listen → hub replies → follow-up window → idle.

States shown to the page: idle, connecting, listening, thinking, speaking.
- Wake (voice or button): chime, connect if needed, stream the microphone ("realtime": the
  hub cuts sentences with its VAD).
- Once the hub has recognised what was said, the microphone stops uploading until the reply has
  been played: talking during a reply (to someone else, "嗯", a cough) never interrupts it. Only
  the wake word (still listened for meanwhile) or the button does: the reply stops, the hub is told
  how much of it was heard (it keeps only that in the context), and what is said next is a new turn.
  The same while the hub is still recognising what was said (the wake word is listened for again
  once its VAD says the user stopped) or thinking: the question is dropped, whatever of that turn
  still arrives (its text, its reply) is ignored, and the mic opens for a new one at once.
- After a reply the microphone stays open for follow_up_seconds, then stops uploading. With
  follow_up 'question' (the default) only a reply that asks something back opens it: otherwise
  talking on after an answer, to someone else in the room, would be taken as the next question.
  'always' opens it after every reply, 'never' after none (the wake word is then needed each time);
  翻译模式 always does (its replies are translations, never questions).
  The connection itself stays until the hub closes it (no speech for ~2 minutes, an exit
  word, the end button), so waking again within that time keeps the conversation.
- The hub keeps every model's conversation on disk (data/chat.db) and gives the next connection
  its recent part, so the page's threads survive a closed connection and a restart: they are loaded
  from the hub (/kiosk/chat/history) when not connected. Clearing them (settings, or saying
  "清除聊天记录") clears the hub's copy too.
- The screen stays on while not idle and blanks after the user's screen-off (device.Screen); the volume
  keys and the mic-mute switch are handled here too (device.Keys).
- Reports (Grok Bot finishing later) are polled from the hub: when idle the client chimes and
  connects so the hub reads them; in the sleep hours (sleep.py) they wait on the screen and the
  screen is dimmed.
- Countdowns, alarms and reminders (alarms.py) are kept and rung here; the model only sets them
  through device tools. A ring lights the screen, switches the speaker to the alarm volume until it
  stops (the wake word, 停止 or 再睡 on the page; given up on after 10 minutes, a reminder (its chime and words over and over) after 3) and stops whatever
  the conversation was doing. The volume keys then change only that ring's volume.
- Bluetooth speaker (bt.py): a phone's music is mixed under the replies, lowered from the wake to the
  end of the reply, muted while an alarm rings (the phone is asked to pause then and is not resumed).
"""
import asyncio
import datetime
import io
import json
import logging
import os
import re
import shutil
import socket
import time
import urllib.parse
from pathlib import Path

import aiohttp
import numpy as np

from . import bg
from . import alarms, broadcast, bt, camera, config, cook, perf, daily, kids, looks, mcp, media, offline, photos, rotation, sleep, update, wake_verify, wake_words
from . import __version__
from .mijia import MijiaService
from .audio import OUT_RATE, Audio, load_wav
from .device import DIM_LEVEL, Keys, Screen, normalize_screen_off
from .hub import Hub
from .kws import Kws
from .opus import Decoder, Encoder

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
# after echo cancellation, above this the user is probably still talking (keeps the mic open).
# Was 0.012 (-38 dBFS): speech from across the room (~-42 dBFS in the wake word evaluation) fell
# under it, the mic closed after 10 s while the user was mid-sentence and the hub never got the
# end of it. Room noise here is ~-57 dBFS.
SPEECH_RMS = 0.005
# halt, not poweroff: with the power cable in, the board powers straight back on after a poweroff;
# halted it stays dark with the file systems unmounted, so the cable can be pulled safely
HALT = ('sudo', '-n', 'systemctl', 'halt')
CARD_TITLE_MAX, CARD_TEXT_MAX, CARD_IMAGES_MAX, CARDS_MAX = 80, 12000, 6, 30
CARDS = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'cards.json'
PHOTOS = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'photos'   # 拍照问答 (photo_start)
PHOTO_POLL = 1.5
ALBUM = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'album'   # 待机页的家庭相册
ALBUM_MAX = 60
STANDBY_MINUTES = (1, 2, 5, 10)
HALT_AFTER = 3           # seconds to read "正在关机 … 等 10 秒再拔" before the screen goes dark
UPDATE_BUSY = ('preparing', 'downloading', 'installing', 'testing', 'rolling_back')
# installing for a page request, or finishing an interrupted one at boot (self-test, rollback)
UPDATE_UNITS = ('thinksmart-update-run.service', 'thinksmart-update-boot.service')
MAX_LISTEN = 60          # seconds: one wake never keeps the mic open longer (a TV could otherwise)
RECOGNITION_WAIT = 15    # seconds to wait for the hub's text after the user stops (long sentences)
NORMAL_STOP_TAIL = 0.6   # a normal "tts stop" leaves less than this queued; more means aborted
PUNCT = set('，。！？；：、,.!?;:…')
QUESTION_TAIL = 15       # a reply asks back when a question mark is among its last this many characters
# after the last question mark: closing quotes and brackets, a trailing emoji, or a short add-on
# such as "要不要设个闹钟？比如明早七点"
TRAILING = set(' \t\n）)」』"”’\'～~…—*')
FOLLOW_UPS = ('question', 'always', 'never')
REPORT_POLL = 10         # seconds between asking the hub for pending reports (local HTTP)
HISTORY_LIMIT = 50       # messages per model shown from the hub's saved conversations
MODEL_POLL = 30          # seconds between asking the hub for its models (changes made in its admin page)
MODEL_RETRY = 3          # until the hub answers (it takes over a minute to start after a boot)
ALARM_VOLUME = 70        # the alarms' own volume until the user sets one
TTS_TIMEOUT = 30         # seconds for the hub to synthesise a reminder's words
PREVIEW_SECONDS = 8      # the settings' 试听 plays at most this much of a tone
CLIP_GAP = 0.8           # seconds of silence after each spoken reminder in a ring
BACKGROUND_DUCKED = 0.2  # a phone's music (Bluetooth speaker) under a conversation

# Hub vendor id -> (logo file, company). A newer hub names the logo itself (design board 42); this table
# covers older hubs, and unknown vendors are guessed from the model name. Files are in web/logos/.
VENDORS = {
    'claude': ('claude-color', 'Anthropic'), 'claude-code': ('claudecode-color', 'Anthropic'),
    'claude_cli': ('claudecode-color', 'Anthropic'), 'openai': ('openai', 'OpenAI'),
    'gemini': ('gemini-color', 'Google'), 'deepseek': ('deepseek-color', 'DeepSeek'), 'dsh': ('deepseek-color', 'DeepSeek'),
    'qwen': ('qwen-color', '阿里通义'), 'doubao': ('doubao-color', '字节豆包'),
    'glm': ('zhipu-color', '智谱'), 'kimi': ('kimi-color', '月之暗面'),
    'siliconflow': ('siliconcloud-color', '硅基流动'), 'qianfan': ('wenxin-color', '百度千帆'),
    'hunyuan': ('hunyuan-color', '腾讯混元'), 'minimax': ('minimax-color', 'MiniMax'),
    'stepfun': ('stepfun-color', '阶跃星辰'), 'spark': ('spark-color', '讯飞星火'), 'xai': ('xai', 'xAI'),
    'mistral': ('mistral-color', 'Mistral AI'), 'openrouter': ('openrouter', 'OpenRouter'),
    'ollama': ('ollama', 'Ollama'), 'lmstudio': ('lmstudio', 'LM Studio'), 'magpie': ('', 'Magpie'),
    'grok_bot': ('grok', 'Grok Bot'), 'grok': ('grok', 'xAI'),
}
BY_MODEL = [
    (('claude',), 'claude'), (('deepseek',), 'deepseek'), (('gemini',), 'gemini'),
    (('qwen', 'qwq'), 'qwen'), (('doubao',), 'doubao'), (('glm',), 'glm'),
    (('kimi', 'moonshot'), 'kimi'), (('grok',), 'grok'), (('gpt', 'o1-', 'o3', 'o4-'), 'openai'),
    (('ernie',), 'qianfan'), (('hunyuan',), 'hunyuan'), (('minimax',), 'minimax'), (('step-',), 'stepfun'),
    (('mistral',), 'mistral'),
]
LOGO_DIR = Path(__file__).resolve().parent.parent / 'web' / 'logos'


def describe(option):
    vendor = option['vendor']
    if vendor not in VENDORS:
        low = (option.get('model') or '').lower()
        vendor = next((v for keys, v in BY_MODEL if any(k in low for k in keys)), vendor)
    logo, company = VENDORS.get(vendor, ('', option.get('label', '')))
    hub_logo = str(option.get('logo') or '')
    if hub_logo and (LOGO_DIR / f'{hub_logo}.svg').is_file():
        logo = hub_logo   # the hub knows its own vendors best, but only use a file this client has
    # calls: the names that summon it (「嗨 + 称呼」), shown in 设置 → 智能体 → 可以叫的
    return {'kind': option['kind'], 'vendor': option['vendor'], 'label': option['label'],
            'entry': option['entry'], 'model': option.get('model', ''), 'logo': logo, 'company': company,
            'calls': [str(c) for c in option.get('calls') or []]}


def marimba(notes, gap=0.1, peak=0.2):
    """Soft marimba notes as 48 kHz stereo: every prompt sound (wake, goodbye, didn't catch that,
    no hub, a new report) is from this one family. notes: Hz, one every `gap` seconds."""
    dur = 0.45
    t = np.arange(int(OUT_RATE * dur)) / OUT_RATE
    out = np.zeros(int(OUT_RATE * (gap * (len(notes) - 1) + dur)))
    for i, f in enumerate(notes):
        # a struck bar: the fundamental rings, the 4th and ~10th partials only for the strike
        x = (np.sin(2 * np.pi * f * t) * np.exp(-9 * t)
             + 0.35 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-28 * t)
             + 0.12 * np.sin(2 * np.pi * 9.9 * f * t) * np.exp(-45 * t)) * np.minimum(1, t / 0.0015)
        start = int(OUT_RATE * gap * i)
        out[start:start + len(x)] += x
    out[-int(OUT_RATE * 0.015):] *= np.linspace(1, 0, int(OUT_RATE * 0.015))
    x = (out * peak / np.max(np.abs(out))).astype(np.float32)
    return np.repeat(x[:, None], 2, axis=1)


def overlay(base, extra, at):
    """`extra` mixed into `base` starting `at` seconds in (both 48 kHz stereo)."""
    start = int(OUT_RATE * at)
    out = np.zeros((max(len(base), start + len(extra)), 2), np.float32)
    out[:len(base)] += base
    out[start:start + len(extra)] += extra
    return np.clip(out, -1, 1)


ZAI_AT = 0.15   # seconds into the wake chime: just after its second note (0.09 s)
C5, E5, G5, A5, B5, C6, D6, E6 = 523.25, 659.25, 783.99, 880.0, 987.77, 1046.5, 1174.66, 1318.5


def asks_back(text):
    """The reply ends by asking the user something (the hub keeps "？" on the sentences it sends;
    it strips "。！" and emoji). Upstream PR #2 (naplesblue)."""
    t = (text or '').rstrip()
    while t and (t[-1] in TRAILING or ord(t[-1]) >= 0x1F000):
        t = t[:-1]
    return t.endswith('吗') or any(ch in '？?' for ch in t[-QUESTION_TAIL:])


def has_default_route():
    """An IPv4 default route (/proc/net/route: destination 00000000), as a hint whether the device is online."""
    try:
        with open('/proc/net/route') as f:
            return any(line.split()[1] == '00000000' for line in f.readlines()[1:] if len(line.split()) > 1)
    except OSError:
        return True   # can't tell: don't claim it is offline


def local_ip():
    """The LAN address the device reaches the network from (the one to type into a computer's browser), or None offline.
    connect() on a UDP socket only picks the route and source address; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('1.1.1.1', 53))   # a unicast address: a broadcast one is refused without SO_BROADCAST
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith('127.') or ip == '0.0.0.0' else ip

class Events:
    """Fan-out to the page's Server-Sent Events streams."""

    def __init__(self):
        self.queues = set()

    def publish(self, event, data):
        for q in list(self.queues):
            if q.qsize() < 500:
                q.put_nowait((event, data))


class Client:
    photo = photo_task = photo_card = None   # 拍照问答 (set in __init__ too; tests build bare clients)
    kids = kids.DEFAULT                       # 儿童模式 (kids.py), likewise
    cooking = None                            # 做菜模式 (cook.py)
    media = None                              # 电台、助眠声 (media.py)
    notes = []                                # 留言 (daily.py)
    lists = None                              # 清单 (the hub keeps them: /kiosk/lists)
    alert = None                              # the red banner of a Mijia sensor alert
    reconnect_after_reply = False             # 翻译模式 changed: the next connection gets the new prompt
    offline_until = 0.0                       # 断网兜底: its commands are listened for until then (monotonic)
    kids_seconds = 0.0
    kids_clips = {}

    wake_rec = None   # a new wake word being said on the settings page: mic blocks (wake_words.py)

    def __init__(self, cfg):
        self.cfg = cfg
        self.events = Events()
        self.loop = None
        self.state = 'idle'
        self.uploading = False
        self.deadline = None          # monotonic: stop listening if nothing is said by then
        self.listen_since = 0.0       # monotonic: when this wake started listening
        self.listen_mode = None       # 'realtime' (the hub's VAD ends it) or 'manual' (push-to-talk)
        self.ptt_held = False         # the mic button is held down (push-to-talk)
        self.powering_off = False     # 关机 was confirmed: the halt is on its way
        self.awaiting_text = False    # the user spoke, the mic is closed, the hub is still recognising
        self.recognising = False      # the hub heard the user stop (its VAD) and is recognising, mic still open
        self.hub_pending = False      # the hub said "vad end" and hasn't answered it yet (text or "empty")
        self.vad_ended = False        # the hub is recognising, kept while the user talks on: the wake word is listened for
        self.aborted_at = None        # monotonic: the last abort, until listening starts again (logged)
        self.abort_pending = None     # monotonic: abort sent, the hub hasn't confirmed it yet (its "tts stop")
        self.question = None          # the user's message of this turn, until its reply starts
        self.heard = False            # anything recognised since this wake (so a listening window that
                                      # times out after it is a follow-up: it ends quietly)
        self.mute_audio = False       # after an abort, drop the old reply's frames
        self.listen_after_reply = False   # the mic was closed for this reply: reopen it when played
        self.reply_samples = 0        # reply audio received since "tts start" (to tell what was heard)
        self.reply_packets = 0
        self.muted_packets = 0
        self.recognised = 0           # count of "stt" messages
        self.threads = {}             # model entry -> messages (the hub's saved ones, then this connection's)
        self.history_loaded = False
        self.entry = None             # the model this connection talks to
        self.models = {'current': None, 'options': []}
        self.bot_msg = None
        self.decoder = None
        self._seq = 0
        self._send_q = None
        self.encoder = Encoder(16000, 1)
        self.kws = Kws(str(ROOT / 'models' / 'kws-zh'), cfg['wake_words'],
                       cfg['kws_score'], cfg['kws_threshold'], cfg['kws_paths'], cfg['kws_gain_db'])
        self.verifier = wake_verify.make(cfg['wake_verify'], cfg['wake_words'], cfg['hub_http'])
        self.wake_rec_t = 0.0
        self.wake_lock = asyncio.Lock()
        self.backlog = None           # mic blocks since a wake word hit, uploaded once listening starts
        self.audio = Audio(self._on_block)
        self.hub = Hub(cfg['hub_ws'], cfg['device_id'], cfg['client_id'], cfg['token'],
                       self._on_json, self._on_audio, self._on_closed)
        sounds = ROOT / 'web' / 'sounds'
        # Wake: two rising marimba notes; goodbye is the same two falling
        self.sounds = {'wake': marimba([G5, D6], 0.09), 'bye': marimba([D6, G5], 0.12, 0.16),
                       'miss': marimba([E6, C6], 0.12, 0.16), 'error': marimba([A5, E5, C5], 0.14),
                       'report': marimba([G5, B5, D6], 0.08)}
        if (sounds / 'zai.wav').exists():
            # the one-character "在" comes in as the second note rings, over its tail: played after
            # the whole chime it came ~0.4 s later
            self.sounds['wake'] = overlay(self.sounds['wake'], load_wav(sounds / 'zai.wav'), ZAI_AT)
        self.speech_end = 0.0         # monotonic: last time the user was heard talking
        self.tools_listed = asyncio.Event()   # the hub asked for our device tools on this connection
        self.connecting = asyncio.Lock()      # one connection: a second wake while connecting waits for it
        self.screen = Screen()
        self.camera = camera.Camera(rotation.current)   # 机身摄像头（设计画布第 27 块）
        self.perf = perf.Perf()                         # 性能页（设计画布第 30 块）
        self.slides = photos.Slides(photos.spool_root())   # 照片轮播 (photos.py, upstream PR #3)
        self._slides_timer = None     # closes the open show after its minutes
        try:
            self.sleep = sleep.normalize(cfg.get('sleep'))
        except ValueError:
            log.warning('invalid sleep setting %r, using the default', cfg.get('sleep'))
            self.sleep = sleep.normalize(None)
        self.sleeping = False
        try:
            self.broadcast = broadcast.normalize(cfg.get('broadcast'))
        except ValueError:
            log.warning('invalid broadcast setting %r, using the default', cfg.get('broadcast'))
            self.broadcast = broadcast.normalize(None)
        self.broadcast_fired = set()
        if cfg.get('avatar') in looks.RETIRED:       # 去掉的小动物形象（设计画布第 41 块）：回到光球
            cfg['avatar'] = 'orb'
            config.unset('avatar')
        for key, fix in (('theme', looks.normalize_theme), ('avatar', looks.normalize_avatar), ('layout', looks.normalize_layout)):
            try:
                cfg[key] = fix(cfg.get(key))
            except ValueError:
                log.warning('invalid %s %r, using the default', key, cfg.get(key))
                cfg[key] = config.DEFAULTS[key]
        cfg['home_blocks'] = looks.normalize_blocks(cfg.get('home_blocks'))
        cfg['dash_cards'] = looks.normalize_blocks(cfg.get('dash_cards'), looks.DASH_CARDS)
        try:
            cfg['dash_default'] = looks.normalize_dash_tab(cfg.get('dash_default'))
        except ValueError:
            cfg['dash_default'] = 'home'
        self.today = None             # the home page's weather and lunar date (/kiosk/today)
        # 接入网关上的服务（设计画布第 38 块）：工作台（DeskHub、机柜、仓库）和智能中枢的蓝牙灯，/kiosk/integrations
        self.integrations = None
        self.integ_visible = False    # 工作台或家居页开着：15 秒刷新一次，否则 60 秒
        self._integ_wake = asyncio.Event()
        self._rack_seen = {}          # 告警键 -> 连续看到几次（连续两次才提醒）
        self._rack_told = set()       # 已经提醒过、还没恢复的
        try:
            self.kids = kids.normalize(cfg.get('kids'))
        except ValueError:
            log.warning('invalid kids setting %r, using the default', cfg.get('kids'))
            self.kids = kids.normalize(None)
        self.kids_seconds = kids.load_usage(datetime.date.today().isoformat())
        self.kids_clips = {}          # 'limit' / 'bedtime' -> the hub's synthesised words
        self.notes = daily.load_notes()
        self.lists = None
        self.photo = None             # the photo question waiting for the phone (photo_start)
        self.photo_task = None
        self.photo_card = None        # its card, shown when the hub echoes the request   # 'YYYY-MM-DD HH:MM' done (played or given up) today
        self.home = cfg.get('home') if isinstance(cfg.get('home'), dict) and cfg['home'].get('name') else None
        self.rec_seconds = wake_words.RECORD_SECONDS   # how long the current hold-to-say may record
        self.bright_in_sleep = False  # the user set a brightness in these sleep hours: don't dim
        level = cfg.get('brightness')
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            log.warning('invalid brightness %r, using 100', level)
            level = 100
        self.screen.level = max(0, min(100, int(level)))
        try:
            self.screen_off = normalize_screen_off(cfg.get('screen_off'))
        except ValueError:
            log.warning('invalid screen_off %r, using the default', cfg.get('screen_off'))
            self.screen_off = normalize_screen_off(None)
        self.screen.idle = self._screen_off_seconds()   # written by start()'s release()
        self.reports = 0              # pending on the hub
        self.hub_seen = 0.0           # monotonic: last answer from the hub's HTTP side (health check)
        self.keys = Keys(on_step=self.volume_step, on_mic=self._on_mic)
        self.volume = None            # the device volume 0-100 (keys, voice, the page), as last read or set
        self.volume_lock = asyncio.Lock()   # one change of the speaker's volume at a time
        self.held_volume = None       # a ring or a tone preview has the speaker: the device volume to put back
        self.alarms = alarms.Alarms()
        self.clock_ok = False         # the system clock is synchronised: alarms are judged
        self.ring = None              # the ring going on: {'items', 'notes', 'started', 'volume', 'stop', 'task'}
        self.preview = None           # task playing a ring tone from the settings
        level = cfg.get('alarm_volume', ALARM_VOLUME)
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            log.warning('invalid alarm_volume %r, using %d', level, ALARM_VOLUME)
            level = ALARM_VOLUME
        self.alarm_volume = max(0, min(100, int(level)))
        self.alarm_tone = cfg.get('alarm_tone') if cfg.get('alarm_tone') in alarms.TONES else alarms.DEFAULT_TONE
        self.bt = bt.Bluetooth(self.audio.background, self.events.publish, cfg.get('bluetooth') is not False)
        self.mijia = MijiaService(self.events.publish)
        self.rotation = rotation.Rotation(cfg.get('rotation_auto'), self.events.publish, config.save)
        self.cards = []               # content cards shown in the conversations (self.screen.show_card)
        self._card_seq = 0
        self._load_cards()

    # ---- wake words (wake_words.py) ----

    def wake_label(self):
        return wake_words.parse((self.cfg.get('wake_words') or config.DEFAULTS['wake_words'])[0])['label']

    def wake_view(self):
        lines = self.cfg['wake_words']
        return {'words': [wake_words.parse(x) for x in lines], 'primary': self.wake_label(),
                'custom': lines != config.DEFAULTS['wake_words'], 'recording': self.wake_rec is not None,
                'max': wake_words.MAX_WORDS}

    def _kws_extra(self):
        """唤醒词模型里除了唤醒词还要听的：做菜模式的「下一步」等、断网时的几句口令。两种模式可能同时开着，
        所以每次重建都按现在的状态一起算（以前各自重建，会把对方的词丢掉）。"""
        extra = [wake_words.keyword(w) for w in cook.WORDS] if self.cooking else []
        if time.monotonic() < self.offline_until:
            extra += [wake_words.keyword(w) for w in offline.WORDS]
        return extra

    async def _rebuild_kws(self, lines=None):
        """按现在的唤醒词和模式重建唤醒词模型（约 1 秒，在线程里）。调用方已经改好状态。"""
        async with self.wake_lock:
            self.kws, self.verifier = await asyncio.get_running_loop().run_in_executor(
                None, self._build_kws, lines or self.cfg['wake_words'], self._kws_extra())

    def _build_kws(self, lines, extra=()):
        cfg = self.cfg
        kws = Kws(str(ROOT / 'models' / 'kws-zh'), list(lines) + list(extra),
                  cfg['kws_score'], cfg['kws_threshold'], cfg['kws_paths'], cfg['kws_gain_db'])
        return kws, wake_verify.make(cfg['wake_verify'], lines, cfg['hub_http'])

    async def set_wake_words(self, lines):
        """Use these keyword lines from now on: rebuild the wake word model and the second look off the
        event loop (loading the model takes ~1 s here), swap them in, store the list."""
        lines = wake_words.validate(list(lines))
        cfg = self.cfg
        extra = self._kws_extra()

        async with self.wake_lock:
            self.kws, self.verifier = await asyncio.get_running_loop().run_in_executor(None, self._build_kws, lines, extra)
            cfg['wake_words'] = lines
            if lines == config.DEFAULTS['wake_words']:
                config.unset('wake_words')
            else:
                config.save(wake_words=lines)
        log.info('wake words now: %s', ', '.join(wake_words.parse(x)['label'] for x in lines))
        view = self.wake_view()
        self.events.publish('wake_words', view)
        return view

    def wake_labels(self):
        return [w['label'] for w in self.wake_view()['words']]

    async def add_wake_word(self, text, picks=None):
        """Add a wake word (Chinese text; picks: reading per character). Returns (view, warning);
        ValueError with the reason when it can't be added."""
        found = wake_words.analyse(text, self.wake_labels())
        if found['error']:
            raise ValueError(found['error'])
        if len(self.cfg['wake_words']) >= wake_words.MAX_WORDS:
            raise ValueError(f'最多 {wake_words.MAX_WORDS} 个唤醒词，先删掉一个')
        line = wake_words.keyword(found['label'], picks)
        return await self.set_wake_words(self.cfg['wake_words'] + [line]), found['warning']

    async def remove_wake_word(self, label):
        lines = [x for x in self.cfg['wake_words'] if wake_words.parse(x)['label'] != label]
        if len(lines) == len(self.cfg['wake_words']):
            raise ValueError(f'没有「{label}」这个唤醒词')
        if not lines:
            raise ValueError('至少要留一个唤醒词')
        return await self.set_wake_words(lines)

    async def reset_wake_words(self):
        return await self.set_wake_words(config.DEFAULTS['wake_words'])

    async def wake_record_start(self, seconds=wake_words.RECORD_SECONDS):
        if self.wake_rec is not None:
            raise ValueError('正在录另一段，先松手')
        if not self.keys.mic_on:
            raise ValueError('麦克风关着，拨开机身侧面的开关再说')
        if self.uploading or self.in_turn() or self.state != 'idle':
            raise ValueError('正在对话，等它说完再录')
        self.screen.poke()
        self.play('report')   # a few notes, no voice (the wake chime's 在 would be recognised too)
        self.wake_rec_t = time.monotonic()
        self.rec_seconds = seconds
        self.wake_rec = []
        self.events.publish('wake_words', self.wake_view())

    def _record_audio(self):
        """Stop the hold-to-say recording: what was said (16 kHz float32, the start notes cut off),
        or None for nothing loud enough."""
        rec, self.wake_rec = self.wake_rec, None
        self.events.publish('wake_words', self.wake_view())
        if rec is None:
            raise ValueError('没在录音')
        audio = np.concatenate(rec) if rec else np.zeros(0, np.float32)
        audio = audio[int(0.3 * 16000):]   # the start notes
        if len(audio) < 0.3 * 16000 or float(np.sqrt(np.mean(audio ** 2))) < 10 ** (-55 / 20):
            return None
        return audio

    async def _record_text(self, what):
        """Stop the hold-to-say recording and recognise it with the hub's recogniser: the text, or ''
        for nothing (or nothing loud enough) said. ValueError when the hub can't be asked."""
        audio = self._record_audio()
        if audio is None:
            return ''
        checker = wake_verify.HubChecker(self.cfg['hub_http'])
        try:
            text = await asyncio.get_running_loop().run_in_executor(None, checker.text, wake_verify.prepare(audio))
        except Exception as e:
            log.warning('recognising %s failed: %s', what, e)
            raise ValueError('连不上小麦网关，没法识别，稍后再试')
        log.info('%s heard as %r', what, text)
        return text

    async def wake_record_stop(self):
        """Stop recording; recognise it with the hub's recogniser -> wake_words.analyse result."""
        return wake_words.analyse(await self._record_text('new wake word'), self.wake_labels())

    # ---- 拍照问答: the phone takes the picture (设计画布第 7 块, the hub's core/agent/photo_ask.py) ----

    def photo_view(self):
        p = self.photo
        return p and {k: p.get(k) for k in ('token', 'url', 'question', 'expires', 'state', 'error', 'image',
                                            'purpose', 'styles', 'style', 'preview', 'avatar_ready', 'received',
                                            'camera', 'shooting', 'shots')}

    def publish_photo(self):
        self.events.publish('photo', self.photo_view() or {'state': 'closed'})

    async def photo_start(self, question='', purpose='ask'):
        """Open a one-time upload on the hub and show its QR code. Returns what the model should say.
        purpose 'avatar': the photo becomes a cartoon avatar (avatar_style, avatar_use) instead."""
        try:
            data = await self._hub_http('/kiosk/photo/start', {'device': self.cfg['device_id'], 'question': question,
                                                               'purpose': purpose})
        except Exception as e:
            log.warning('cannot start a photo question: %s', e)
            raise ValueError('连不上小麦网关')
        if not data.get('ok'):
            raise ValueError(data.get('error') or '没开成')
        self.photo = {'token': data['token'], 'url': data['url'], 'question': data['question'],
                      'expires': data['expires'], 'state': 'waiting', 'error': None, 'image': None,
                      'purpose': purpose, 'styles': data.get('styles') or [], 'style': None, 'preview': None,
                      'avatar_ready': data.get('avatar_ready', False),
                      # 有机身摄像头就用它（拍照问倒数后自动拍；形象、相册点快门拍，设计画布第 33 块），没有才出二维码
                      'camera': camera.available(), 'shooting': False, 'shots': []}
        if self.photo_task and not self.photo_task.done():
            self.photo_task.cancel()
        self.photo_task = asyncio.create_task(self._photo_poller(self.photo))
        self.screen.poke()
        self.publish_photo()
        log.info('photo question %r: waiting for the %s', data['question'], 'camera' if self.photo['camera'] else 'phone')
        return {'vision_ready': data.get('vision_ready', True), 'avatar_ready': data.get('avatar_ready', False),
                'camera': self.photo['camera']}

    def _camera_can_shoot(self, p):
        if not p or not p.get('camera') or p.get('shooting'):
            return False
        if p.get('purpose') == 'avatar':      # 形象：第 1 张以后同一个码接着收姿势，最多 looks.POSES 张
            return p['state'] in ('waiting', 'received') and len(p.get('shots') or []) < looks.POSES
        return p['state'] == 'waiting'

    async def camera_shoot(self):
        """用机身摄像头拍一张，像手机一样传给网关（/photo/<token>），后面照常：拍照问轮询拿答案；
        形象拍完点「好了」选画风；相册由轮询取回。"""
        p = self.photo
        if not self._camera_can_shoot(p):
            raise ValueError('现在没在等拍照')
        p['shooting'] = True
        self.publish_photo()
        try:
            jpeg = await self.camera.shoot()
            form = aiohttp.FormData()
            form.add_field('question', p['question'])
            form.add_field('photo', jpeg, filename='camera.jpg', content_type='image/jpeg')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.post(f"{self.cfg['hub_http']}/photo/{p['token']}", data=form) as r:
                    data = await r.json(content_type=None)
            if not data.get('ok'):
                raise ValueError(data.get('error') or '没传上去')
            log.info('photo %s: shot with the camera (%d bytes)', p.get('purpose'), len(jpeg))
            if p.get('purpose') in ('avatar', 'album'):   # 下面一排小图；接着拍下一张
                PHOTOS.mkdir(parents=True, exist_ok=True)
                name = f"{int(time.time() * 1000)}-cam{len(p['shots'])}.jpg"
                (PHOTOS / name).write_bytes(jpeg)
                p['shots'] = p['shots'] + [f'/api/photos/{name}']
                p['shooting'] = False
                if p.get('purpose') == 'avatar' and p['state'] == 'waiting':
                    p['state'] = 'received'
                if p.get('purpose') == 'album':
                    p['received'] = len(p['shots'])
                if p.get('purpose') == 'avatar' and len(p['shots']) >= looks.POSES:
                    p['camera'] = False                  # 拍满了：去选画风
                self.publish_photo()
            return {}
        except Exception as e:
            log.warning('camera shot failed: %s', e)
            p['shooting'] = False
            self.publish_photo()
            raise ValueError(str(e) if isinstance(e, ValueError) else '没拍成，再试一次或者用手机拍')

    async def camera_done(self):
        """形象、相册用摄像头拍好了：形象去选画风（和手机拍的一样）；相册把还没取回的取回来，然后关上。"""
        p = self.photo
        if not p or not p.get('shots'):
            raise ValueError('还没拍')
        if p.get('purpose') == 'avatar':
            p['camera'] = False
            self.publish_photo()
            return {}
        if p.get('purpose') == 'album':
            try:
                v = await self._hub_http(f"/kiosk/photo/{p['token']}")
                await self._fetch_album(p, int(v.get('album_count') or 0))
            except Exception as e:
                log.warning('album: cannot fetch the last photos: %s', e)
            await self.photo_cancel()
        return {}

    async def camera_to_phone(self):
        """不用摄像头了（拍不清楚）：换回二维码，用手机拍。"""
        p = self.photo
        if p and p.get('camera'):
            p['camera'] = False
            self.publish_photo()
        return {}

    async def photo_cancel(self):
        p, self.photo = self.photo, None
        if p:
            try:
                await self._hub_http(f"/kiosk/photo/{p['token']}/cancel", {})
            except Exception:
                pass
        if self.photo_task and not self.photo_task.done():
            self.photo_task.cancel()
        self.publish_photo()

    async def _photo_poller(self, p):
        try:
            while self.photo is p:
                await asyncio.sleep(PHOTO_POLL)
                try:
                    v = await self._hub_http(f"/kiosk/photo/{p['token']}")
                except Exception as e:
                    log.warning('photo question: %s', e)
                    continue
                state = v.get('state') if v.get('ok') else 'expired'
                if state in ('received', 'done', 'generating', 'generated') and not p['image']:
                    p['image'] = await self._fetch_photo(p['token'])
                    p['question'] = v.get('question') or p['question']
                if p.get('purpose') == 'album' and (v.get('album_count') or 0) > p.get('album_got', 0):
                    await self._fetch_album(p, v['album_count'])
                    p['error'] = None
                    p['received'] = p.get('album_got', 0)
                    self.publish_photo()
                if state == 'generated' and not p['preview']:
                    p['preview'] = await self._fetch_photo(p['token'], avatar=True)
                    # 「原图」形象可以有 2～4 张：在听、在想、在说的样子（设计画布第 26 块）
                    p['poses'] = [p['preview']] + [await self._fetch_photo(p['token'], avatar=True, pose=i)
                                                   for i in range(1, min(int(v.get('poses') or 1), looks.POSES))]
                error = v.get('error') or (None if v.get('ok') else '二维码过期了')
                if state != p['state'] or error != p['error']:
                    p['state'], p['error'] = state, error
                    self.screen.poke()
                    self.publish_photo()
                if state == 'done' and p['purpose'] == 'ask':
                    await self._photo_answered(p, v.get('answer') or '')
                    return
                if state in ('error', 'expired', 'cancelled'):
                    if p.get('purpose') == 'album' and p.get('album_got'):
                        if self.photo is p:      # the album's code ran out after some photos: that's done
                            self.photo = None
                            self.publish_photo()
                        return
                    self.play('miss')
                    await asyncio.sleep(6)       # long enough to read why
                    if self.photo is p:
                        self.photo = None
                        self.publish_photo()
                    return
        except asyncio.CancelledError:
            pass

    async def _fetch_photo(self, token, avatar=False, pose=0):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(f"{self.cfg['hub_http']}/kiosk/photo/{token}/{'avatar' if avatar else 'image'}"
                                 + (f'?pose={pose}' if pose else '')) as r:
                    if r.status != 200:
                        return None
                    data = await r.read()
            PHOTOS.mkdir(parents=True, exist_ok=True)
            name = f"{int(time.time())}-{re.sub(r'[^0-9A-Za-z_-]', '', token)[:10]}{'-av' if avatar else ''}{pose or ''}.jpg"
            (PHOTOS / name).write_bytes(data)
            return f'/api/photos/{name}'
        except Exception as e:
            log.warning('cannot fetch the photo: %s', e)
            return None

    async def avatar_start(self):
        # 没有百炼 Key 也能开始：「原图」不用 Key（拍自己的公仔）；选别的画风时网关会说要 Key
        return await self.photo_start('卡通形象', purpose='avatar')

    async def avatar_style(self, style):
        """Draw the received photo in this style (the hub's wanx-style-repaint; ~15 s)."""
        p = self.photo
        if not p or p.get('purpose') != 'avatar':
            raise ValueError('没有在生成形象')
        data = await self._hub_http(f"/kiosk/photo/{p['token']}/avatar", {'style': int(style)})
        if not data.get('ok'):
            raise ValueError(data.get('error') or '没开始画')
        p.update(state='generating', style=int(style), preview=None, error=None)
        self.publish_photo()
        return {}

    async def avatar_use(self):
        """Keep the generated picture as an avatar and switch to it."""
        p = self.photo
        if not p or not p.get('preview'):
            raise ValueError('还没画好')
        pngs = [(PHOTOS / u.rsplit('/', 1)[1]).read_bytes() for u in (p.get('poses') or [p['preview']]) if u]
        avatar = await self.make_avatar(pngs)
        self.photo = None
        self.publish_photo()
        return {'looks': await self.set_looks(avatar=avatar)}

    async def _photo_answered(self, p, answer):
        """Put the photo and the vision model's words in the conversation (as a card, shown when the
        hub echoes the request), then ask the chat model to answer from them (spoken; follow-ups work)."""
        self.photo = None
        self.publish_photo()
        self.photo_card = (p['question'], answer, [p['image']] if p['image'] else [])
        hhmm = datetime.datetime.now().strftime('%H:%M')
        await self.say(f"【拍照问 {hhmm}】我用{'小麦的摄像头' if p.get('camera') else '手机'}拍了张照片问：「{p['question']}」。看图模型看到的是：{answer}"
                       '\n请根据这些直接回答我，一两句话说清楚，不要提「看图模型」。')

    # ---- 断网兜底 (offline.py): a few commands without the hub ----

    def _offline_clip(self, name):
        path = offline.CLIPS / f'{name}.wav'
        try:
            return load_wav(path) if path.is_file() else None
        except Exception:
            return None

    def _play_clips(self, names):
        clips = [self._offline_clip(n) for n in names]
        if all(c is not None for c in clips):
            self.audio.player.trim(0)
            for c in clips:
                self.audio.player.push(c)
            return True
        self.play('miss')
        return False

    async def offline_start(self):
        """The hub didn't answer a wake: listen for the offline commands for a while."""
        self.offline_until = time.monotonic() + offline.WINDOW
        self.events.publish('toast', {'kind': 'error', 'text': '连不上小麦网关。现在可以直接说：' + '、'.join(offline.WORDS)
                                      + f'（{offline.WINDOW} 秒内）'})
        await self._rebuild_kws()
        self._play_clips(['hint'])
        if not getattr(self, '_offline_task', None) or self._offline_task.done():   # 一个窗口只要一个收尾任务
            self._offline_task = asyncio.create_task(self._offline_end())

    async def _offline_end(self):
        while time.monotonic() < self.offline_until:
            await asyncio.sleep(0.5)
        await self._rebuild_kws()

    async def offline_command(self, label):
        self.offline_until = time.monotonic() + offline.WINDOW   # another one may follow
        self.screen.poke()
        log.info('offline command: %s', label)
        if label in ('音量大一点', '音量小一点'):
            await self.set_volume(max(0, min(100, self.volume + (15 if label == '音量大一点' else -15))), 'voice')
            self._play_clips(['ok'])
        elif label == '现在几点':
            now = datetime.datetime.now()
            self.events.publish('toast', {'kind': 'info', 'text': f'现在 {now:%H:%M}'})
            self._play_clips(offline.time_clips(now.hour, now.minute))
        else:
            on = label == '打开所有灯'
            m = self.mijia
            lights = [c for c in (m.cards.values() if m and m.session else []) if c.get('kind') == 'light' and c.get('online')]
            if not lights:
                self._play_clips(['no_home'])
                return
            results = await asyncio.gather(*(m.control(c['id'], 'on' if on else 'off') for c in lights), return_exceptions=True)
            done = sum(1 for r in results if isinstance(r, dict) and r.get('ok'))
            self.events.publish('toast', {'kind': 'info', 'text': f"{'打开' if on else '关掉'}了 {done} 盏灯"})
            self._play_clips(['ok'] if done else ['no_home'])

    async def _offline_clips_loop(self):
        """While the hub answers, have it synthesise the offline answers once (kept on disk)."""
        await asyncio.sleep(60)
        offline.CLIPS.mkdir(parents=True, exist_ok=True)
        for name, words in offline.phrases().items():
            path = offline.CLIPS / f'{name}.wav'
            if path.is_file():
                continue
            wav = await self._speech(words)
            if not wav:
                return            # the hub is away: try again at the next start
            path.write_bytes(wav)
            await asyncio.sleep(1)
        log.info('offline answers ready')

    # ---- 家人声音 (the hub's core/utils/local_voiceprint.py): who is talking ----

    VOICE_SENTENCES = ('今天天气不错，我们下午带宝宝去公园散步吧。', '小麦小麦，帮我看看明天早上要不要带伞。',
                       '晚饭想吃番茄炒蛋，再做一个清炒西兰花。')

    async def voices_view(self):
        try:
            data = await self._hub_http('/kiosk/voiceprint?' + urllib.parse.urlencode({'device': self.cfg['device_id']}))
        except Exception:
            return {'available': False, 'people': [], 'sentences': self.VOICE_SENTENCES, 'error': '连不上小麦网关'}
        return {'available': data.get('available', False), 'people': data.get('people') or [],
                'sentences': self.VOICE_SENTENCES}

    async def voice_record_start(self):
        await self.wake_record_start(seconds=9.0)

    async def voice_name_stop(self):
        """The name said on the settings page -> {'name'} or {'error'}."""
        text = (await self._record_text('a family member\'s name')).strip().rstrip('。.！!？?，,')
        if not text:
            return {'error': '没听清，按住再说一次名字'}
        return {'name': text[:12]}

    async def voice_sample_stop(self, name, child=False):
        """A sentence read by this person -> the hub adds it to their voiceprint."""
        audio = self._record_audio()
        if audio is None:
            return {'error': '没听到声音，按住再念一次'}
        pcm = np.clip(audio * 32768, -32768, 32767).astype('<i2').tobytes()
        q = urllib.parse.urlencode({'device': self.cfg['device_id'], 'name': name, 'child': '1' if child else '0'})
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.post(f"{self.cfg['hub_http']}/kiosk/voiceprint/enroll?{q}", data=pcm) as r:
                    data = await r.json(content_type=None)
        except Exception as e:
            log.warning('voiceprint enroll failed: %s', e)
            return {'error': '连不上小麦网关，稍后再试'}
        if not data.get('ok'):
            return {'error': data.get('error') or '没录上'}
        self.events.publish('voices', await self.voices_view())
        return {'samples': data['samples']}

    async def voice_remove(self, name):
        data = await self._hub_http('/kiosk/voiceprint/remove', {'device': self.cfg['device_id'], 'name': name})
        if not data.get('ok'):
            raise ValueError(data.get('error') or '没删掉')
        view = await self.voices_view()
        self.events.publish('voices', view)
        return view

    # ---- 定时任务: at a time, ask the model to do something (weather, lists, Mijia…) ----

    def tasks_view(self):
        return {'tasks': self.cfg.get('tasks') or [], 'max': 20}

    async def task_add(self, time_, prompt, days='daily'):
        prompt = str(prompt or '').strip()
        if not prompt:
            raise ValueError('要说清楚到点做什么')
        if days not in ('daily', 'workdays', 'weekend', 'once'):
            raise ValueError('days 只能是 daily、workdays、weekend、once')
        hhmm = broadcast._time(time_)
        tasks = list(self.cfg.get('tasks') or [])
        if len(tasks) >= 20:
            raise ValueError('最多 20 个定时任务，先删掉一个')
        # 毫秒时间戳做 id；同一毫秒加两个（一句话里调两次工具）会重复，删一个就把两个都删了：往后挪到没用过的
        taken, n = {t['id'] for t in tasks}, int(time.time() * 1000)
        while f't{n}' in taken:
            n += 1
        task = {'id': f't{n}', 'time': hhmm, 'days': days, 'prompt': prompt[:300]}
        self.cfg['tasks'] = tasks + [task]
        config.save(tasks=self.cfg['tasks'])
        self.events.publish('tasks', self.tasks_view())
        return task

    async def task_delete(self, task_id):
        tasks = [t for t in self.cfg.get('tasks') or [] if t['id'] != task_id]
        if len(tasks) == len(self.cfg.get('tasks') or []):
            raise ValueError('没有这个定时任务')
        self.cfg['tasks'] = tasks
        if tasks:
            config.save(tasks=tasks)
        else:
            config.unset('tasks')
        self.events.publish('tasks', self.tasks_view())
        return self.tasks_view()

    def _due_tasks(self, now):
        out = []
        for t in self.cfg.get('tasks') or []:
            days = 'daily' if t['days'] == 'once' else t['days']
            for hhmm, key, at in broadcast.due({'enabled': True, 'times': [t['time']], 'days': days}, now, self.broadcast_fired):
                out.append((t, f"{key} {t['id']}", at))
        return [(t, k, at) for t, k, at in out if k not in self.broadcast_fired]

    async def _run_task(self, task):
        log.info('scheduled task %s: %s', task['time'], task['prompt'])
        if task['days'] == 'once':
            await self.task_delete(task['id'])
        self.screen.poke()
        self.play('report')
        await self.say(f"【定时任务 {task['time']}】{task['prompt']}（这是我之前让你定时做的事，现在到点了，直接做，"
                       '需要的话调用工具查最新的数据，说得简短）')

    # ---- 主动告警: Mijia leak / door sensors watched in the background ----

    def alerts_view(self):
        sensors = [c for c in (self.mijia.cards.values() if self.mijia else []) if {'leak', 'contact'} & set(c.get('feats') or {})]
        return {**self.cfg['alerts'], 'sensors': len(sensors), 'alert': self.alert}

    async def set_alerts(self, **changes):
        a = {**self.cfg['alerts'], **{k: bool(v) for k, v in changes.items() if k in ('leak', 'door_night', 'rack')}}
        self.cfg['alerts'] = a
        if a == config.DEFAULTS['alerts']:
            config.unset('alerts')
        else:
            config.save(alerts=a)
        self.events.publish('alerts', self.alerts_view())
        return self.alerts_view()

    async def clear_alert(self):
        self.alert = None
        self.events.publish('alerts', self.alerts_view())
        return self.alerts_view()

    async def _alert_loop(self):
        last = {}   # card id -> {'leak': bool, 'contact': bool}
        while True:
            await asyncio.sleep(60)
            try:
                m = self.mijia
                if not m or not m.session or m.state != 'ready':
                    continue
                cards = [c for c in m.cards.values() if {'leak', 'contact'} & set(c.get('feats') or {})]
                if not cards:
                    continue
                if not m.visible:     # the page polls while it is open
                    await asyncio.wait_for(m.poll(cards), 15)
                for c in cards:
                    v, before = c.get('values') or {}, last.get(c['id'])
                    now = {'leak': v.get('leak'), 'contact': v.get('contact')}
                    last[c['id']] = now
                    if before is None:
                        continue      # the first look only sets what "before" was
                    name = c.get('name') or '传感器'
                    if self.cfg['alerts'].get('leak') and now['leak'] is True and before['leak'] is not True:
                        await self._raise_alert(f'{name}检测到漏水', urgent=True)
                    if (self.cfg['alerts'].get('door_night') and self.sleeping and now['contact'] is False
                            and before['contact'] is True):
                        await self._raise_alert(f'{name}被打开了', urgent=False)
            except Exception:
                log.exception('mijia alerts')

    async def _raise_alert(self, text, urgent):
        log.warning('alert: %s', text)
        self.alert = {'text': text, 'time': time.time(), 'urgent': urgent}
        self.events.publish('alerts', self.alerts_view())
        self.screen.poke()
        self.notes, _ = daily.new_note(self.notes, '家里人', f'⚠ {text}', '小麦')
        daily.save_notes(self.notes)
        self.events.publish('notes', self.notes_view())
        if self.ring is None:
            self.play('report')
            await self._say_local(f'注意，{text}。' * (2 if urgent else 1))

    # ---- 接入网关上的服务：工作台和蓝牙灯（设计画布第 38 块；网关 core/integrations） ----

    async def refresh_integrations(self, fresh=False):
        q = {'device': self.cfg['device_id']}
        if fresh:
            q['fresh'] = '1'
        try:
            data = await self._hub_http('/kiosk/integrations?' + urllib.parse.urlencode(q))
        except Exception as e:
            log.info('integrations: %s', e)
            return self.integrations
        if data and data.get('ok'):
            data.pop('ok', None)
            data['at'] = time.time()
            self.integrations = data
            self.events.publish('integrations', data)
            await self._rack_alerts(data)
        return self.integrations

    async def set_integrations_visible(self, on):
        self.integ_visible = bool(on)
        if on:
            self._integ_wake.set()
        return {'visible': self.integ_visible}

    async def _integrations_loop(self):
        await asyncio.sleep(8)
        while True:
            await self.refresh_integrations()
            self._integ_wake.clear()
            try:
                await asyncio.wait_for(self._integ_wake.wait(), 15 if self.integ_visible else 60)
            except asyncio.TimeoutError:
                pass

    async def _rack_alerts(self, data):
        rack = data.get('rack_status')
        if not isinstance(rack, dict) or 'alerts' not in rack:
            return
        now = rack['alerts']
        self._rack_seen = {k: self._rack_seen.get(k, 0) + 1 for k in now}
        self._rack_told &= set(now)
        if not self.cfg['alerts'].get('rack'):
            return
        for key, text in now.items():
            if self._rack_seen[key] >= 2 and key not in self._rack_told:
                self._rack_told.add(key)
                await self._raise_alert(text, urgent=False)

    async def smarthub_act(self, ident, action, value=None):
        r = await self._hub_http('/kiosk/smarthub', {'device': self.cfg['device_id'], 'id': ident, 'action': action, 'value': value})
        if r and r.get('ok') and self.integrations and isinstance(self.integrations.get('smart_hub'), dict):
            devs = self.integrations['smart_hub'].get('devices') or []
            self.integrations['smart_hub']['devices'] = [r['device'] if d.get('id') == ident else d for d in devs]
            self.events.publish('integrations', self.integrations)
        return r

    async def set_plugin(self, name, on):
        """设备设置里「接入的服务」的开关（设计画布第 40 块）：改网关上这台设备的插件，只改这台；改完重新拉一次。"""
        r = await self._hub_http('/kiosk/plugin', {'device': self.cfg['device_id'], 'name': name, 'on': bool(on)})
        if r and r.get('ok'):
            await self.refresh_integrations()
        return r

    async def desk_command(self, op, ident):
        r = await self._hub_http('/kiosk/deskhub', {'device': self.cfg['device_id'], 'op': op, 'id': ident})
        if r and r.get('desk') is not None and self.integrations:
            self.integrations['desk_hub'] = r['desk']
            self.events.publish('integrations', self.integrations)
        return r

    # ---- 翻译模式 (the hub's prompt; the device listens longer) ----

    def translate_view(self):
        return dict(self.cfg['translate'])

    async def set_translate(self, enabled, a=None, b=None):
        t = {'enabled': bool(enabled), 'a': str(a or self.cfg['translate'].get('a') or '中文').strip()[:12],
             'b': str(b or self.cfg['translate'].get('b') or '英文').strip()[:12]}
        if t['enabled'] and t['a'] == t['b']:
            raise ValueError('两种语言不能一样')
        self.cfg['translate'] = t
        config.save(translate=t)
        await self._hub_http('/kiosk/prefs', {'device': self.cfg['device_id'], 'translate': t})
        self.events.publish('translate', self.translate_view())
        # the prompt is set per connection: reconnect once this reply has been played
        self.reconnect_after_reply = True
        if not self.in_turn() and self.state == 'idle':
            await self.hub.close()
            self.reconnect_after_reply = False
        return self.translate_view()

    def follow_up_seconds(self):
        """How long the mic stays open after a reply: longer in 翻译模式 (two people take turns)."""
        return 30 if self.cfg.get('translate', {}).get('enabled') else self.cfg['follow_up_seconds']

    async def _after_playback(self, then):
        give_up = time.monotonic() + 8
        while self.audio.player.buffered() > 0.02 and time.monotonic() < give_up:
            await asyncio.sleep(0.05)
        await then()

    async def _reconnect_for_translate(self):
        self.reconnect_after_reply = False
        await self.hub.close()
        if self.cfg['translate']['enabled']:
            await asyncio.sleep(0.3)
            await self.wake('button', chimed=True)   # straight back to listening, on the new prompt

    # ---- 电台和助眠声 (media.py) ----

    def media_view(self):
        # 播放器第一次放的时候才建；在那之前设置页也要能列出电台和助眠声（以前是空的，屏幕上点不了）
        return self.media.view() if self.media else {'playing': None,
                                                     'stations': [{'id': k, 'name': v[0]} for k, v in media.STATIONS.items()],
                                                     'sounds': [{'id': k, 'name': v} for k, v in media.SOUNDS.items()]}

    def _media_player(self):
        if self.media is None:
            self.media = media.Player(self.audio.background, on_change=lambda v: self.events.publish('media', v),
                                      on_error=lambda t: self.events.publish('toast', {'kind': 'error', 'text': t}))
        return self.media

    async def media_play(self, kind, name, minutes=None):
        p = self._media_player()
        if kind == 'radio':
            key = media.find_station(name)
            if not key:
                raise ValueError(f"没有「{name}」这个电台，能放的有：{'、'.join(v[0] for v in media.STATIONS.values())}")
            return await p.play_radio(key)
        key = media.find_sound(name)
        if not key:
            raise ValueError(f"助眠声只有：{'、'.join(media.SOUNDS.values())}")
        return await p.play_sound(key, 30 if minutes is None else minutes)

    async def media_stop(self):
        if self.media:
            await self.media.stop()
        return self.media_view()

    # ---- 场景口令和留言 (daily.py) ----

    def scenes_view(self):
        return {'scenes': self.cfg.get('scenes') or [], 'max': daily.MAX_SCENES}

    async def save_scene(self, name, steps):
        self.cfg['scenes'] = daily.save_scene(self.cfg.get('scenes') or [], name, steps)
        config.save(scenes=self.cfg['scenes'])
        self.events.publish('scenes', self.scenes_view())
        return self.scenes_view()

    async def delete_scene(self, name):
        scene = daily.find_scene(self.cfg.get('scenes') or [], name)
        if not scene:
            raise ValueError(f'没有「{name}」这个场景')
        self.cfg['scenes'] = [s for s in self.cfg['scenes'] if s is not scene]
        if self.cfg['scenes']:
            config.save(scenes=self.cfg['scenes'])
        else:
            config.unset('scenes')
        self.events.publish('scenes', self.scenes_view())
        return self.scenes_view()

    def notes_view(self):
        return {'notes': self.notes[::-1]}

    async def leave_note(self, to, text, sender=''):
        self.notes, note = daily.new_note(self.notes, to, text, sender)
        daily.save_notes(self.notes)
        self.screen.poke()
        self.events.publish('notes', self.notes_view())
        return note

    async def note_done(self, note_id):
        before = len(self.notes)
        self.notes = [n for n in self.notes if n['id'] != note_id]
        if len(self.notes) == before:
            raise ValueError('没有这条留言')
        daily.save_notes(self.notes)
        self.events.publish('notes', self.notes_view())
        return self.notes_view()

    # ---- 孩子和学习：学一学（kids.LEARN，设计画布第 34 块） ----

    async def learn(self, kind):
        text = kids.learn_prompt(kind, self.kids.get('age'))
        self.screen.poke()
        await self.say(text)
        return {}

    # ---- 语音：这台设备回答用的声音（网关 /kiosk/voice，存在网关的按设备配置里，下一次连接生效） ----

    async def voice_view(self):
        return await self._hub_http('/kiosk/voice?' + urllib.parse.urlencode({'device': self.cfg['device_id']}))

    async def set_voice(self, voice):
        data = await self._hub_http('/kiosk/voice', {'device': self.cfg['device_id'], 'voice': voice})
        if not data.get('ok'):
            raise ValueError(data.get('error') or '没改成')
        return await self.voice_view()

    async def note_read(self, note_id):
        n = next((x for x in self.notes if x['id'] == note_id), None)
        if not n:
            raise ValueError('没有这条留言')
        await self._say_local(f"{n['from'] + '给' if n['from'] else '这是给'}{n['to']}的留言：{n['text']}")
        return {}

    # ---- 清单 (the hub keeps them, so a phone can tick them off: core/utils/device_lists.py) ----

    async def refresh_lists(self):
        try:
            data = await self._hub_http('/kiosk/lists?' + urllib.parse.urlencode({'device': self.cfg['device_id']}))
        except Exception as e:
            log.info('no lists from the hub: %s', e)
            return self.lists
        if data and data.get('ok'):
            if not self.lists or data['version'] != self.lists.get('version'):
                self.lists = {k: data[k] for k in ('version', 'lists', 'url')}
                self.events.publish('lists', self.lists)
        return self.lists

    async def list_change(self, op, name, items=(), done=None):
        data = await self._hub_http('/kiosk/lists', {'device': self.cfg['device_id'], 'op': op, 'name': name,
                                                     'items': list(items or []), 'done': done})
        if not data.get('ok'):
            raise ValueError(data.get('error') or '没改成')
        self.lists = {k: data[k] for k in ('version', 'lists', 'url')}
        self.events.publish('lists', self.lists)
        return self.lists

    async def _lists_loop(self):
        while True:
            await self.refresh_lists()
            await asyncio.sleep(20)

    # ---- 做菜模式 (cook.py): one step per screen, voice commands recognised here ----

    def cook_view(self):
        c = self.cooking
        if not c:
            return {'on': False}
        step = c['steps'][c['i']]
        return {'on': True, 'title': c['title'], 'steps': c['steps'], 'i': c['i'], 'minutes': cook.minutes(step)}

    def publish_cook(self):
        self.events.publish('cook', self.cook_view())

    async def cook_start(self, card_id=None, read=True):
        """Cook from this card (None: the newest card with numbered steps). read=False: the caller says
        the first step (the voice tool puts it in the hub's reply, which would talk over ours)."""
        if card_id is None:
            card = next((m for m in reversed(self.cards) if cook.steps((m.get('card') or {}).get('markdown'))), None)
        else:
            card = next((m for m in self.cards if str(m.get('id')) == str(card_id)), None)
        if not card:
            raise ValueError('找不到这张菜谱')
        found = cook.steps((card.get('card') or {}).get('markdown'))
        if not found:
            raise ValueError('这张卡片里没有编号的步骤')
        first = self.cooking is None
        self.cooking = {'title': card['text'], 'steps': found, 'i': 0, 'touched': time.monotonic()}
        if first:
            await self._rebuild_kws()
        log.info('cooking %r: %d steps', card['text'], len(found))
        self.publish_cook()
        if read:
            await self._read_step()
        return self.cook_view()

    async def cook_stop(self):
        if not self.cooking:
            return {'on': False}
        self.cooking = None
        await self._rebuild_kws()
        log.info('cooking mode ended')
        self.publish_cook()
        return {'on': False}

    async def cook_go(self, by=0, to=None):
        c = self.cooking
        if not c:
            raise ValueError('没在做菜模式')
        i = c['i'] + int(by) if to is None else int(to)
        if not 0 <= i < len(c['steps']):
            if i >= len(c['steps']):
                await self._say_local('已经是最后一步了，做好啦。')
            return self.cook_view()
        c['i'], c['touched'] = i, time.monotonic()
        self.publish_cook()
        await self._read_step()
        return self.cook_view()

    async def cook_timer(self):
        c = self.cooking
        m = c and cook.minutes(c['steps'][c['i']])
        if not m:
            raise ValueError('这一步没有要计时的')
        await self.create_alarm('timer', label=f"{c['title']} 第 {c['i'] + 1} 步", seconds=int(m * 60))
        await self._say_local(f'好，{m:g} 分钟倒计时开始了。')
        return self.cook_view()

    async def cook_word(self, label):
        self.screen.poke()
        try:
            if label == '下一步':
                await self.cook_go(1)
            elif label == '上一步':
                await self.cook_go(-1)
            elif label == '再说一遍':
                await self._read_step()
            elif label == '开始计时':
                await self.cook_timer()
            elif label == '退出做菜':
                await self.cook_stop()
                await self._say_local('好，退出做菜模式。')
        except ValueError as e:
            await self._say_local(str(e))

    async def _read_step(self):
        c = self.cooking
        if c:
            await self._say_local(f"第 {c['i'] + 1} 步，{c['steps'][c['i']]}")

    async def _say_local(self, text):
        """Say a short sentence without a conversation (the hub only synthesises it)."""
        wav = await self._speech(text)
        if wav:
            self.audio.player.trim(0)
            self.audio.player.push(load_wav(io.BytesIO(wav)))

    async def _cook_loop(self):
        while True:
            await asyncio.sleep(30)
            c = self.cooking
            if c and time.monotonic() - c['touched'] > cook.IDLE_SECONDS:
                log.info('cooking mode idle for %d minutes: ending it', cook.IDLE_SECONDS // 60)
                await self.cook_stop()

    # ---- 待机页 and the family album ----

    def standby_view(self):
        s = self.cfg['standby']
        photos = sorted(ALBUM.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True) if ALBUM.is_dir() else []
        return {**s, 'choices': STANDBY_MINUTES, 'photos': [f'/api/album/{p.name}' for p in photos], 'max': ALBUM_MAX}

    def publish_standby(self):
        self.events.publish('standby', self.standby_view())

    async def set_standby(self, enabled=None, minutes=None, album=None):
        s = dict(self.cfg['standby'])
        if enabled is not None:
            s['enabled'] = bool(enabled)
        if album is not None:
            s['album'] = bool(album)
        if minutes is not None:
            if int(minutes) not in STANDBY_MINUTES:
                raise ValueError(f"只能是 {'、'.join(map(str, STANDBY_MINUTES))} 分钟")
            s['minutes'] = int(minutes)
        self.cfg['standby'] = s
        if s == config.DEFAULTS['standby']:
            config.unset('standby')
        else:
            config.save(standby=s)
        self.publish_standby()
        return self.standby_view()

    async def album_start(self):
        r = await self.photo_start('家庭相册', purpose='album')
        return r

    async def album_delete(self, name):
        if not re.fullmatch(r'[0-9A-Za-z_-]{1,60}\.jpg', name or ''):
            raise ValueError('没有这张照片')
        (ALBUM / name).unlink(missing_ok=True)
        self.publish_standby()
        return self.standby_view()

    async def _fetch_album(self, p, count):
        """Copy the album photos the phone sent so far (p['album_got'] of count) into ALBUM."""
        async with self.album_lock:     # 轮询和摄像头「完成」可能同时取：不重复取同一张
            await self._fetch_album_locked(p, count)

    async def _fetch_album_locked(self, p, count):
        got = p.setdefault('album_got', 0)
        while got < count:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                    async with s.get(f"{self.cfg['hub_http']}/kiosk/photo/{p['token']}/album/{got}") as r:
                        if r.status != 200:
                            break
                        data = await r.read()
            except Exception as e:
                log.warning('cannot fetch an album photo: %s', e)
                break
            ALBUM.mkdir(parents=True, exist_ok=True)
            (ALBUM / f"{int(time.time() * 1000)}-{got}.jpg").write_bytes(data)
            got += 1
        p['album_got'] = got
        old = sorted(ALBUM.glob('*.jpg'), key=lambda x: x.stat().st_mtime, reverse=True)[ALBUM_MAX:] if ALBUM.is_dir() else []
        for x in old:
            x.unlink(missing_ok=True)
        self.publish_standby()

    # ---- 儿童模式 (kids.py): the hub makes the answers child-friendly, the limits are kept here ----

    def kids_view(self):
        return {**self.kids, 'used_minutes': int(self.kids_seconds // 60),
                'week': kids.week(datetime.date.today().isoformat()),
                'blocked': kids.blocked(self.kids, self.kids_seconds)}

    def publish_kids(self):
        self.events.publish('kids', self.kids_view())

    async def set_kids(self, **changes):
        """Change the kids setting (page; by voice only enabled=True). bedtime_shift/minutes_step move
        those by the page's ± buttons. ValueError for anything invalid."""
        if changes.get('bedtime_shift'):
            changes['bedtime'] = kids.shift_time(self.kids['bedtime'] or '20:30', int(changes.pop('bedtime_shift')))
        changes.pop('bedtime_shift', None)
        if changes.get('minutes_step'):
            changes['daily_minutes'] = max(0, min(600, self.kids['daily_minutes'] + int(changes.pop('minutes_step'))))
        changes.pop('minutes_step', None)
        self.kids = kids.normalize(self.kids, changes)
        if self.kids == kids.DEFAULT:
            config.unset('kids')
        else:
            config.save(kids=self.kids)
        log.info('kids mode: %s', self.kids)
        self.publish_kids()
        await self._sync_prefs()
        return self.kids_view()

    async def _sync_prefs(self):
        """Tell the hub (data/device_prefs.json): next connection uses the kids prompt and voice."""
        try:
            await self._hub_http('/kiosk/prefs', {'device': self.cfg['device_id'], 'kids': self.kids})
            if self.hub.connected and not self.in_turn():
                await self.hub.close()   # the prompt and the voice are set per connection
            return True
        except Exception as e:
            log.warning('could not tell the hub the kids setting: %s', e)
            return False

    async def kids_refuse(self):
        """In kids mode past a limit: say why (no hub connection). True when the wake was refused."""
        why = kids.blocked(self.kids, self.kids_seconds)
        if not why:
            return False
        log.info('kids mode: wake refused (%s)', why)
        clip = self.kids_clips.get(why)
        if clip is None:
            wav = await self._speech(kids.SAY_BEDTIME if why == 'bedtime' else kids.SAY_LIMIT)
            if wav:
                clip = self.kids_clips[why] = load_wav(io.BytesIO(wav))
        self.screen.poke()
        self.events.publish('toast', {'kind': 'info', 'text': kids.SAY_BEDTIME if why == 'bedtime' else kids.SAY_LIMIT})
        if clip is not None:
            self.audio.player.push(clip)
        else:
            self.play('miss')
        return True

    async def _kids_loop(self):
        synced = False
        while True:
            await asyncio.sleep(5)
            try:
                if not synced:
                    synced = await self._sync_prefs()
                today = datetime.date.today().isoformat()
                if getattr(self, '_kids_day', today) != today:
                    self.kids_seconds = 0.0
                    self.publish_kids()
                self._kids_day = today
                if self.kids['enabled'] and self.state in ('listening', 'thinking', 'speaking'):
                    before = int(self.kids_seconds // 60)
                    self.kids_seconds += 5
                    kids.save_usage(today, self.kids_seconds)
                    if int(self.kids_seconds // 60) != before:
                        self.publish_kids()
            except Exception:
                log.exception('kids mode')

    # ---- 外观: theme, avatar, home blocks (looks.py) and the home page's weather ----

    def looks_view(self):
        return looks.view(self.cfg)

    async def set_looks(self, theme=None, avatar=None, blocks=None, move=None, toggle=None, layout=None,
                        dash_default=None, dash_move=None, dash_toggle=None):
        """Change the looks (page or voice). move={'id', 'by': -1|1} reorders a block; toggle=id flips it.
        ValueError for anything invalid."""
        changes = {}
        if theme is not None:
            changes['theme'] = looks.normalize_theme(theme)
        if avatar is not None:
            changes['avatar'] = looks.normalize_avatar(avatar)
            if changes['avatar'].startswith('pack:'):      # 外观里光球后面列最近用过的形象库款式
                changes['avatar_recent'] = looks.remember_recent(self.cfg.get('avatar_recent'), changes['avatar'])
        if layout is not None:
            changes['layout'] = looks.normalize_layout(layout)
        if dash_default is not None:
            changes['dash_default'] = looks.normalize_dash_tab(dash_default)
        if dash_move or dash_toggle:        # 仪表盘首页的卡片：排序、开关（和首页内容同一套做法）
            cards = looks.normalize_blocks(self.cfg.get('dash_cards'), looks.DASH_CARDS)
            ids = [b for b, _ in cards]
            target = (dash_move or {}).get('id') if dash_move else dash_toggle
            if target not in ids:
                raise ValueError('没有这张卡片')
            i = ids.index(target)
            if dash_move:
                cards.insert(max(0, min(len(cards) - 1, i + int(dash_move.get('by') or 0))), cards.pop(i))
            else:
                cards[i][1] = not cards[i][1]
            changes['dash_cards'] = cards
        order = looks.normalize_blocks(blocks if blocks is not None else self.cfg['home_blocks'])
        if move:
            ids = [b for b, _ in order]
            if move.get('id') not in ids:
                raise ValueError('没有这个首页内容')
            i = ids.index(move['id'])
            j = max(0, min(len(order) - 1, i + int(move.get('by') or 0)))
            order.insert(j, order.pop(i))
        if toggle:
            hit = [b for b in order if b[0] == toggle]
            if not hit:
                raise ValueError('没有这个首页内容')
            hit[0][1] = not hit[0][1]
        if blocks is not None or move or toggle:
            changes['home_blocks'] = order
        self.cfg.update(changes)
        for key, value in changes.items():
            if value == config.DEFAULTS[key]:
                config.unset(key)
            else:
                config.save(**{key: value})
        if changes:
            log.info('looks: %s', changes)
        view = self.looks_view()
        self.events.publish('looks', view)
        return view

    async def refresh_today(self):
        try:
            data = await self._hub_http('/kiosk/today?' + urllib.parse.urlencode({'device': self.cfg['device_id']}))
        except Exception as e:
            log.info('no weather for the home page: %s', e)
            return
        if data and data.get('ok'):
            self.today = {k: data.get(k) for k in ('lunar', 'weather', 'weather_error')}
            self.events.publish('today', self.today)

    async def _today_loop(self):
        last_theme = None
        while True:
            await self.refresh_today()
            for _ in range(40):              # 20 minutes; the 'auto' theme is checked every 30 s
                await asyncio.sleep(30)
                theme = looks.theme_now(self.cfg['theme'])
                if theme != last_theme:
                    last_theme = theme
                    self.events.publish('looks', self.looks_view())

    async def make_avatar(self, png):
        """Keep a generated avatar (PNG bytes, or a list: the idle picture then the listening, thinking and
        speaking ones) -> its id. At most looks.PHOTO_MAX: the oldest goes."""
        pngs = png if isinstance(png, list) else [png]
        looks.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        name = f'a{int(time.time())}'
        (looks.PHOTO_DIR / f'{name}.png').write_bytes(pngs[0])
        if len(pngs) > 1:
            looks.POSE_DIR.mkdir(parents=True, exist_ok=True)
            for i, data in enumerate(pngs[1:looks.POSES], 1):
                (looks.POSE_DIR / f'{name}-{i}.png').write_bytes(data)
        for old in looks.photo_avatars()[looks.PHOTO_MAX:]:
            looks.remove_photo(old['id'][6:])
        self.events.publish('looks', self.looks_view())
        return f'photo:{name}'

    async def delete_avatar(self, avatar_id):
        if not avatar_id.startswith('photo:') or not looks.PHOTO_NAME.match(avatar_id[6:]):
            raise ValueError('只能删用照片生成的形象')
        looks.remove_photo(avatar_id[6:])
        if self.cfg['avatar'] == avatar_id:
            return await self.set_looks(avatar='orb')
        view = self.looks_view()
        self.events.publish('looks', view)
        return view

    # ---- home address and weather broadcasts (broadcast.py) ----

    def broadcast_view(self):
        return {**self.broadcast, 'home': self.home, 'text': broadcast.describe(self.broadcast),
                'max_times': broadcast.MAX_TIMES, 'step': broadcast.STEP_MINUTES,
                'about': {t: broadcast.about(t) for t in self.broadcast['times']}}

    def publish_broadcast(self):
        self.events.publish('broadcast', self.broadcast_view())

    async def set_broadcast(self, **changes):
        """Change the broadcast setting (page or voice); shift={'time', 'minutes'} moves one time.
        ValueError for anything invalid."""
        move = changes.pop('shift', None)
        if move:
            old = broadcast._time(move.get('time'))
            if old not in self.broadcast['times']:
                raise ValueError(f'没有 {old} 这个播报时间')
            new = broadcast.shift(old, int(move.get('minutes') or 0))
            if new in self.broadcast['times']:
                raise ValueError(f'已经有 {new} 了')
            changes.update(remove_time=old, add_time=new)
        self.broadcast = broadcast.normalize(self.broadcast, changes)
        config.save(broadcast=self.broadcast)
        log.info('weather broadcast: %s', broadcast.describe(self.broadcast))
        self.publish_broadcast()
        return self.broadcast_view()

    async def home_record_start(self):
        await self.wake_record_start(seconds=6.0)

    async def home_record_stop(self):
        """Recognise the address said and look it up -> {'heard', 'place'} or {'heard', 'error'}."""
        text = (await self._record_text('home address')).strip().rstrip('。.！!？?，,')
        if not text:
            return {'heard': '', 'error': '没听清，按住再说一次'}
        return await self.find_place(text)

    async def find_place(self, text):
        try:
            data = await self._hub_http('/kiosk/geocode?' + urllib.parse.urlencode({'q': text}))
        except Exception as e:
            log.warning('looking up %r failed: %s', text, e)
            raise ValueError('连不上小麦网关，查不了地名，稍后再试')
        if not data or not data.get('ok'):
            return {'heard': text, 'error': (data or {}).get('error') or f'没找到「{text}」这个地方'}
        return {'heard': text, 'place': data['place']}

    async def set_home(self, place):
        """Use this place ({'name', 'latitude', 'longitude'}, None: forget it) as the home address,
        here and on the hub (weather without a place, the model's idea of where the device is)."""
        if place is not None:
            place = {'name': str(place['name']).strip()[:60], 'latitude': round(float(place['latitude']), 4),
                     'longitude': round(float(place['longitude']), 4)}
            if not place['name']:
                raise ValueError('地址不能是空的')
        self.home = place
        if place:
            config.save(home=place)
        else:
            config.unset('home')
        log.info('home address: %s', place and place['name'])
        self.publish_broadcast()
        await self._sync_home()
        bg.spawn(self.refresh_today())
        return self.broadcast_view()

    async def _sync_home(self):
        """Tell the hub this device's home (it keeps a copy: data/device_homes.json). Quietly retried
        at the next start when the hub can't be reached."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.post(self.cfg['hub_http'] + '/kiosk/home',
                                  json={'device': self.cfg['device_id'], 'place': self.home}) as r:
                    if r.status != 200:
                        log.warning('the hub did not take the home address: %s %s', r.status, await r.text())
                        return False
            return True
        except Exception as e:
            log.warning('could not tell the hub the home address: %s', e)
            return False

    def _busy_for_broadcast(self):
        return (self.state != 'idle' or self.uploading or self.ring is not None or self.preview is not None
                or self.wake_rec is not None)

    def _reminders_today(self, now):
        out = []
        for v in (self.alarms.view(i) for i in self.alarms.active()):
            if v['kind'] == 'timer' or not v['at']:
                continue
            at = datetime.datetime.fromtimestamp(v['at'])
            if at.date() == now.date() and at >= now:
                out.append(f"{at:%H:%M} {v['label'] or v['type']}")
        return out

    async def _broadcast_loop(self):
        synced_once = False
        while True:
            await asyncio.sleep(5)
            try:
                if not synced_once and self.home:
                    synced_once = await self._sync_home()
                elif not self.home:
                    synced_once = True
                if not alarms.clock_synced():
                    continue
                now = datetime.datetime.now()
                today = now.date().isoformat()
                self.broadcast_fired = {k for k in self.broadcast_fired if k.startswith(today)}
                for task, key, at in self._due_tasks(now):
                    if self._busy_for_broadcast():
                        if (now - at).total_seconds() >= broadcast.WAIT_MAX - 10:
                            self.broadcast_fired.add(key)
                        break
                    self.broadcast_fired.add(key)
                    await self._run_task(task)
                    break
                for hhmm, key, at in broadcast.due(self.broadcast, now, self.broadcast_fired):
                    if self._busy_for_broadcast():
                        if (now - at).total_seconds() >= broadcast.WAIT_MAX - 10:   # the last tick of the wait
                            self.broadcast_fired.add(key)
                            self._note_missed_broadcast(hhmm, at)
                        break
                    self.broadcast_fired.add(key)
                    await self.play_broadcast(hhmm)
                    break
            except Exception:
                log.exception('weather broadcast')

    def _note_missed_broadcast(self, hhmm, at):
        log.info('weather broadcast %s skipped: busy for %d minutes', hhmm, broadcast.WAIT_MAX // 60)
        self.alarms.missed.append({'kind': 'broadcast', 'label': '天气', 'at': at.timestamp(),
                                   'why': 'busy', 'noted': time.time()})
        self.alarms.save()
        self.publish_alarms()

    async def play_broadcast(self, hhmm=None):
        """Ask the model to read the weather now (at a broadcast time, or 'test' from the page)."""
        hhmm = hhmm or datetime.datetime.now().strftime('%H:%M')
        text = broadcast.request_text(hhmm, self.broadcast, self.home and self.home['name'],
                                      self._reminders_today(datetime.datetime.now()))
        log.info('weather broadcast %s', hhmm)
        self.screen.poke()
        self.play('report')
        await self.say(text)

    # ---- startup ----

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self._send_q = asyncio.Queue(maxsize=200)
        self.audio.start()
        self.keys.start()
        self.screen.release()   # a hold left by a previous run must not keep the screen on
        self.slides.clear_all()
        self._apply_sleep()
        bg.spawn(self._sender())
        bg.spawn(self._ticker())
        bg.spawn(self._model_poller())
        bg.spawn(self._report_poller())
        bg.spawn(self._alarm_loop())
        bg.spawn(self._broadcast_loop())
        bg.spawn(self._today_loop())
        bg.spawn(self._kids_loop())
        bg.spawn(self._cook_loop())
        bg.spawn(self._lists_loop())
        bg.spawn(self._alert_loop())
        bg.spawn(self._integrations_loop())
        bg.spawn(self._offline_clips_loop())
        await self.bt.start()
        bg.spawn(self._bluetooth_loop())
        await self.mijia.start()
        self.rotation.start()

    # ---- microphone (worker thread) ----

    def _on_block(self, block, wake_block):
        rec = self.wake_rec
        if rec is not None:
            # saying a new wake word: it must not wake (it may well be the current one), and nothing
            # is streaming (wake_record_start checks). A page that never lets go stops it here
            if time.monotonic() - self.wake_rec_t > self.rec_seconds + 3:
                self.wake_rec = None
            elif sum(len(b) for b in rec) < self.rec_seconds * 16000:
                rec.append(block.copy())
            return
        # While streaming to the hub the wake word isn't needed (speech already reaches it), and
        # the CPU it would take is better left to the hub's speech recognition. wake_block: what the
        # wake word hears (during a reply, echo-cancelled without suppressing the voice; audio.py)
        # (after the hub's VAD said the user stopped, the wake word interrupts the recognition)
        hit = None if self.uploading and not self.vad_ended else self.kws.feed(wake_block)
        backlog = self.backlog
        if hit:
            log.info('wake word: %s', hit)
            if self.vad_ended:
                self.uploading = False   # keep what follows for the new question, not the old one
            # keep what follows: checking the hit, the chime and connecting take a moment, and
            # "小麦小麦，今天..." is often said in one go
            self.backlog = []
            audio = self.kws.recent()
            self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.voice_wake(hit, audio)))
        elif backlog is not None and not self.uploading:
            backlog.append(block)
            del backlog[:-200]   # 4 s, what the send queue takes at once
        elif self.uploading:
            self.backlog = None
            for b in (backlog or []) + [block]:
                packet = self.encoder.encode(np.clip(b * 32768, -32768, 32767).astype(np.int16))
                self.loop.call_soon_threadsafe(self._queue_packet, packet)

    def _queue_packet(self, packet):
        if not self._send_q.full():
            self._send_q.put_nowait(packet)

    async def _sender(self):
        while True:
            packet = await self._send_q.get()
            if self.uploading:
                await self.hub.send_audio(packet)

    # ---- state ----

    def set_state(self, state):
        self.audio.aec_hold = self.hub.connected or self.state == 'connecting'
        if state != self.state:
            self.state = state
            self.recognising = False
            log.info('state: %s', state)
            # only on a real change: set_state(self.state) just refreshes the page
            if state == 'idle':
                self.screen.release()
            else:
                self.screen.hold()
        self.events.publish('state', self.status())

    def status(self):
        return {'state': self.state, 'connected': self.hub.connected, 'uploading': self.uploading,
                'recognising': self.recognising or self.awaiting_text,
                'mic': self.keys.mic_on, 'sleep': {**self.sleep, 'active': self.sleeping},
                'brightness': self.brightness(), 'screen_off': self.screen_off,
                'reports': self.reports, 'volume': self.volume}

    def mic_off_toast(self):
        if self.keys.mic_on:
            return False
        self.play('miss')
        self.events.publish('toast', {'kind': 'info', 'text': '麦克风已关，拨开机身侧面的开关就能说话'})
        return True

    def _on_mic(self, on):
        log.info('mic switch: %s', 'on' if on else 'off')
        self.events.publish('toast', {'kind': 'info', 'text': '麦克风已打开' if on else '麦克风已关'})
        if not on and self.uploading:
            bg.spawn(self.stop_uploading())
        else:
            self.set_state(self.state)

    def health(self):
        """For the updater's self-test after installing a version (thinksmart-update)."""
        return {'audio': bool(self.audio._streams) and all(s.active for s in self.audio._streams),
                'hub': self.hub_seen > 0 and time.monotonic() - self.hub_seen < 30}

    def snapshot(self):
        return {'status': self.status(), 'models': self.models, 'alarms': self.alarm_view(), 'bluetooth': self.bt.view(),
                'mijia': self.mijia.view(), 'rotation': self.rotation.view(), 'wake_words': self.wake_view(),
                'broadcast': self.broadcast_view(), 'photo': self.photo_view(), 'slides': self.slides_view(),
                'looks': self.looks_view(), 'today': self.today, 'kids': self.kids_view(),
                'standby': self.standby_view(), 'cook': self.cook_view(), 'media': self.media_view(),
                'scenes': self.scenes_view(), 'notes': self.notes_view(), 'lists': self.lists,
                'tasks': self.tasks_view(), 'alerts': self.alerts_view(), 'translate': self.translate_view(),
                'integrations': self.integrations,
                'thread': {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), [])}}

    def view_entry(self):
        cur = self.models.get('current')
        return self.entry or (cur['entry'] if cur else None)

    def play(self, name):
        sound = self.sounds.get(name)
        if sound is not None:
            self.audio.player.push(sound)

    # ---- actions (from the wake word and the page) ----

    async def ensure_connected(self):
        async with self.connecting:
            return await self._connect()

    async def _connect(self):
        if self.hub.connected:
            return True
        self.set_state('connecting')
        self.tools_listed.clear()
        try:
            await self.hub.connect()
        except Exception as e:
            log.warning('cannot reach the hub: %s', e)
            self.play('error')
            self.events.publish('toast', {'kind': 'error', 'text': '连不上小麦网关，请检查网关是否在运行'})
            self.set_state('idle')
            return False
        self.decoder = None
        # a new connection has no old reply to drop: the hub's own announcements (reports) send no
        # "tts start", so a mute left over from the last abort/end would silence them
        self.mute_audio = False
        return True

    async def voice_wake(self, label, audio):
        if label in offline.WORDS and time.monotonic() < self.offline_until:
            await self.offline_command(label)
            return
        if self.cooking and label in cook.WORDS:
            await self.cook_word(label)   # 做菜模式: a command, not a wake (no second look: speed matters)
            return
        if self.ring is None and not self.in_turn() and await self.kids_refuse():
            return
        """The wake word model fired. With a second look (wake_verify.py), which takes 1-2 s here,
        the chime still comes at once, but connecting and listening wait for its answer: what is
        said meanwhile is kept (backlog) and sent on, and a false wake ends quietly instead of
        with the hub answering the TV or a "didn't catch that"."""
        if not self.verifier:
            await self.wake('voice')
        elif self.in_turn():
            # don't cut a reply off for what may not be the wake word
            if (await self._check_wake(label, audio))['ok']:
                await self.wake('voice')
        elif not self.mic_off_toast():
            self.screen.poke()
            self.chime('voice')
            self.set_state('connecting')
            if (await self._check_wake(label, audio))['ok']:
                await self.wake('voice', chimed=True)
            else:
                log.info('not the wake word after all: back to idle')
                self.set_state('idle')
        if not self.uploading:
            self.backlog = None

    async def _check_wake(self, label, audio):
        return await asyncio.get_running_loop().run_in_executor(None, self.verifier.check, audio, label)

    def chime(self, source):
        self.play('wake')
        self.events.publish('woke', {'source': source})

    def in_turn(self):
        """The hub is recognising, thinking or speaking: the wake word interrupts it."""
        return (self.state in ('thinking', 'speaking') or self.vad_ended) and self.hub.connected

    async def wake(self, source='button', chimed=False):
        self.screen.poke()
        if self.ring:
            await self.stop_alarm()   # the wake word stops a ring (no snooze: "再睡 10 分钟" is said next)
            if source == 'voice' and kids.blocked(self.kids, self.kids_seconds):
                return
        elif not self.in_turn() and await self.kids_refuse():
            return
        if self.preview:
            await self._stop_preview()   # a tone being tried: the answer must not queue behind it
        if self.mic_off_toast():
            return
        if self.in_turn():
            await self.abort('wake_word_detected')
        if not chimed:
            self.chime(source)
        if not await self.ensure_connected():
            await self.offline_start()
            return
        await self.start_listening('realtime')
        self.heard = False
        self.deadline = time.monotonic() + self.follow_up_seconds()

    async def start_listening(self, mode):
        await self.hub.send({'type': 'listen', 'state': 'start', 'mode': mode})
        self.uploading = True
        self.listen_mode = mode
        self.vad_ended = False
        self.listen_since = time.monotonic()
        self.awaiting_text = False
        self.hub_pending = False
        if self.aborted_at is not None:
            log.info('listening again %.0f ms after the abort', (self.listen_since - self.aborted_at) * 1000)
            self.aborted_at = None
        self.set_state('listening')

    async def stop_uploading(self, missed=False):
        self.uploading = False
        self.deadline = None
        if missed:
            self.play('miss')
            self.events.publish('toast', {'kind': 'info', 'text': f'没听清，可以再说一次「{self.wake_label()}」'})
        self.set_state('idle')

    async def abort(self, reason=None):
        now = time.monotonic()
        phase = 'recognising' if self.vad_ended or self.awaiting_text else self.state
        # how much of this reply was actually heard: the hub keeps only that in the context
        played = max(self.reply_samples / OUT_RATE - self.audio.player.buffered(), 0)
        msg = {'type': 'abort', 'played_ms': int(played * 1000)}
        if reason:
            msg['reason'] = reason
        self.audio.player.trim(0)
        self.mute_audio = True
        self.listen_after_reply = False
        if phase == 'recognising':
            # what was just said is dropped (the hub drops it too): stop sending the rest of it
            self.uploading = False
        self.vad_ended = self.recognising = self.awaiting_text = self.hub_pending = False
        self.aborted_at = self.abort_pending = now
        log.info('abort while %s (%s)', phase, reason or 'button')
        await self.hub.send(msg)
        if self.bot_msg:
            self.bot_msg['interrupted'] = True
            self.publish_message(self.bot_msg)
            self.bot_msg = None
        elif self.question:
            # interrupted before any answer: the hub leaves the question out of the context
            self.question['interrupted'] = True
            self.publish_message(self.question)
        self.question = None
        self.set_state('listening' if self.uploading else 'idle')

    async def end(self):
        """End the conversation: stop everything and close the channel, which clears the hub's context."""
        if self.in_turn():
            await self.abort()
        self.uploading = False
        self.listen_after_reply = False
        self.deadline = None
        await self.hub.close()
        self.set_state('idle')

    async def say(self, text):
        """Ask in text instead of voice (the hub answers it like recognised speech)."""
        text = (text or '').strip()
        if not text:
            return
        if self.in_turn():
            await self.abort()
        if not await self.ensure_connected():
            return
        try:
            # a question sent right after connecting reaches the model before our tool list does,
            # and then "关掉睡眠模式" can't call anything; speech takes longer than this anyway
            await asyncio.wait_for(self.tools_listed.wait(), 2)
        except asyncio.TimeoutError:
            pass
        if not self.hub.connected:
            return   # ended (or dropped) while we waited: nothing will answer, stay idle
        await self.hub.send({'type': 'listen', 'state': 'detect', 'text': text})
        self.set_state('thinking')

    async def ptt_start(self):
        if self.mic_off_toast():
            return
        if not self.in_turn() and await self.kids_refuse():
            return
        self.ptt_held = True
        if self.in_turn():
            await self.abort()
        if not await self.ensure_connected():
            return
        if not self.ptt_held:
            # let go while we were connecting: nothing would ever end a manual listen, so take it
            # as a tap (listen with the hub's VAD)
            await self.wake('button')
            return
        await self.start_listening('manual')
        self.heard = False
        self.deadline = None

    async def ptt_stop(self):
        self.ptt_held = False
        if self.uploading:
            await self.hub.send({'type': 'listen', 'state': 'stop'})
            self.uploading = False
            self.awaiting_text = True   # the hub's "nothing recognised" ends it at once
            self.set_state('thinking')
            self.deadline = time.monotonic() + 8   # back to idle if nothing comes back

    # ---- from the hub ----

    def _stale(self, msg):
        """Sent by the hub before it handled our abort (it confirms with "tts stop"): the old turn's
        question or the start of its reply, arriving late. 3 s in case the confirmation got lost."""
        if self.abort_pending is None:
            return False
        if time.monotonic() - self.abort_pending > 3:
            self.abort_pending = None
            return False
        kind, state = msg.get('type'), msg.get('state')
        if kind == 'tts' and state == 'stop' and msg.get('interrupted'):
            log.info('the hub confirmed the abort after %.0f ms', (time.monotonic() - self.abort_pending) * 1000)
            self.abort_pending = None
            return True   # our own abort: what comes next was already decided
        if kind == 'stt' or (kind == 'tts' and state in ('start', 'sentence_start')):
            log.info('dropped from the interrupted turn: %s %s', kind, state or msg.get('text'))
            return True
        return False

    async def _on_json(self, msg):
        kind = msg.get('type')
        if self._stale(msg):
            return
        if kind == 'stt':
            text = (msg.get('text') or '').strip()
            if text.startswith('% '):
                # not speech: the hub's note that the model calls a tool (for the screens of ESP32 boards)
                log.info('the model calls %s', text[2:])
            elif text:
                if self.speech_end > self.listen_since:
                    log.info('recognised %.1f s after the user stopped talking: %s',
                             time.monotonic() - self.speech_end, text)
                else:
                    # said before this listening began (right after the wake word, in its backlog):
                    # the last time we heard talking belongs to an earlier turn
                    log.info('recognised: %s', text)
                # a long sentence the client's deadline closed the mic on is still a spoken question:
                # listen again after its reply too (not after push-to-talk, which never does)
                late = self.awaiting_text and self.listen_mode == 'realtime'
                self.heard = True
                self.awaiting_text = False
                self.recognising = self.vad_ended = False
                self.hub_pending = False
                self.deadline = None
                self.recognised += 1
                self.reply_samples = 0    # an abort before this turn's audio has heard nothing of it
                if late or broadcast.tag_of(text):
                    # a broadcast: "那明天呢" right after it needs no wake word
                    self.listen_after_reply = True
                if self.uploading:
                    # nothing said during the reply counts; the wake word is listened for instead
                    self.uploading = False
                    self.listen_after_reply = True
                # on the page right away: asking the hub for its models first took 0.7-2 s
                m = self.question = self.add_message('user', text)
                if self.photo_card and (broadcast.tag_of(text) or '').startswith('拍照问'):
                    title, answer, images = self.photo_card
                    self.photo_card = None
                    self.show_card(title, answer, images)
                self.set_state('thinking')
                bg.spawn(self._refresh_after_question(m))
        elif kind == 'vad':
            # VAD events, a feature asked for in our hello. end: the hub decided the user finished and
            # is recognising; empty: it recognised nothing (a cough, too short, only echo)
            state = msg.get('state')
            if state == 'end':
                self.hub_pending = True
                if self.uploading and not self.recognising:
                    self.recognising = self.vad_ended = True
                    self.set_state(self.state)
            elif state == 'empty':
                self.hub_pending = self.vad_ended = False
                log.info('the hub recognised nothing')
                if self.awaiting_text:
                    # the mic is closed and this was all we were waiting for ("didn't catch that"
                    # after a wake; a follow-up window ends quietly, as when nobody talks in it)
                    self.awaiting_text = False
                    await self.stop_uploading(missed=not self.heard)
                elif self.recognising:
                    # the mic is still open: back to listening
                    self.recognising = False
                    self.set_state(self.state)
        elif kind == 'tts':
            state = msg.get('state')
            if state != 'sentence_start':
                log.info('reply %s (%.1f s queued)', state, self.audio.player.buffered())
            if state == 'stop':
                player = self.audio.player
                log.info('reply audio: %d packets (%d dropped muted), %.2f s; buffer ran dry %d times (last sample %s); '
                         'speaker callback flags %s', self.reply_packets, self.muted_packets, self.reply_samples / OUT_RATE,
                         len(player.ran_out), ' '.join(f'{v:.3f}' for v in player.ran_out[-8:]),
                         player.late[-8:])
                levels = self.audio.take_echo_levels()
                if levels:
                    log.info('echo while it played: mic %s dBFS, cancelled %s, for the wake word %s', *levels)
            if state == 'start':
                # The hub says "start" as soon as it has the question; the model is still
                # thinking until the first sentence arrives
                self.mute_audio = False
                self.decoder = None
                self.bot_msg = None
                self.reply_samples = self.reply_packets = self.muted_packets = 0
                self.audio.player.ran_out.clear()
                self.audio.player.late.clear()
                self.deadline = None
                self.set_state('thinking')
            elif state == 'sentence_start':
                text = (msg.get('text') or '').strip()
                if self.mute_audio and self.abort_pending is None:
                    # the hub confirmed our abort, so this is something new it says on its own: a
                    # report (e.g. the interrupted Grok Bot turn finished) sends no "tts start" to unmute
                    self.mute_audio = False
                if self.uploading and self.listen_mode == 'realtime' and not self.mute_audio:
                    # the hub speaks on its own while we listen (a pending report: no stt, no
                    # "tts start" before it). Like any reply, only the wake word interrupts it,
                    # and the follow-up window opens after it
                    self.uploading = False
                    self.listen_after_reply = True
                    self.recognising = self.vad_ended = self.hub_pending = False
                    self.deadline = None
                if not self.mute_audio:
                    self.set_state('speaking')
                if text and not self.mute_audio:
                    if self.bot_msg is None:
                        self.bot_msg = self.add_message('bot', text)
                        self.question = None
                    else:
                        # the hub sends sentences with their trailing punctuation stripped
                        prev = self.bot_msg['text']
                        self.bot_msg['text'] = prev + ('' if prev[-1:] in PUNCT else '，') + text
                        self.publish_message(self.bot_msg)
            elif state == 'stop':
                # the hub cut the reply off (a newer question); an older hub doesn't say so, but then
                # more than the usual tail is still queued
                if msg.get('interrupted', self.audio.player.buffered() > NORMAL_STOP_TAIL):
                    self.audio.player.trim(0)
                    if self.bot_msg:
                        self.bot_msg['interrupted'] = True
                        self.publish_message(self.bot_msg)
                elif self.question is not None and self.bot_msg is None and not self.mute_audio:
                    # over without a word: the model and the hub's spoken apology both failed to reach
                    # their servers (no network yet after a boot, an outage); say so on the page
                    log.warning('the turn ended without a reply')
                    self.events.publish('toast', {'kind': 'error', 'text': '网络还没连上，稍后再试' if not has_default_route()
                                                  else '没收到回答，可能是网络或模型服务连不上，稍后再试'})
                said = self.bot_msg['text'] if self.bot_msg else None
                self.bot_msg = self.question = None
                if self.reconnect_after_reply:
                    # 翻译模式 changed in this turn: the new prompt needs a new connection
                    self.listen_after_reply = False
                    bg.spawn(self._after_playback(self._reconnect_for_translate))
                elif self.listen_after_reply and not msg.get('interrupted') and not self.wants_follow_up(said):
                    # an answer that asks nothing back: what is said next isn't for us unless the wake
                    # word comes first. The connection stays, so waking again keeps the conversation
                    self.listen_after_reply = False
                    self.set_state('idle')
                elif self.listen_after_reply:
                    bg.spawn(self._listen_after_reply())
                elif self.uploading:
                    self.heard = True
                    self.deadline = time.monotonic() + self.follow_up_seconds()
                    self.set_state('listening')
                else:
                    self.set_state('idle')
        elif kind == 'mcp':
            payload = msg.get('payload') or {}
            reply = await mcp.handle(payload, client=self)
            if reply:
                await self.hub.send({'type': 'mcp', 'payload': reply})
            if payload.get('method') == 'tools/list':
                self.tools_listed.set()
        elif kind == 'chat_history' and msg.get('state') == 'cleared':
            # said "清除聊天记录" and confirmed: the hub has forgotten everything
            self.threads = {}
            self.cards = []
            self._save_cards()
            self.bot_msg = self.question = None
            self.events.publish('thread', {'entry': self.view_entry(), 'messages': [], 'reset': True})
        elif kind == 'server':
            log.info('server message: %s', msg)

    async def _refresh_after_question(self, m):
        """The hub may have switched models for this question (called one by name, or changed in its
        admin page): move the question, and anything said after it, to that model's thread."""
        old = m['entry']
        await self.refresh_models()
        thread = self.threads.get(old, [])
        if self.entry == old or m not in thread:
            return
        moved = thread[thread.index(m):]
        del thread[thread.index(m):]
        for x in moved:
            x['entry'] = self.entry
        self.threads.setdefault(self.entry, []).extend(moved)
        self.events.publish('thread', {'entry': self.entry, 'messages': self.threads[self.entry]})

    def wants_follow_up(self, said):
        """Open the mic after this reply? (said: its text; None when nothing was shown, e.g. the turn
        failed, and then the user may want to say it again at once.)"""
        if self.cfg.get('translate', {}).get('enabled'):
            return True   # 翻译模式: the replies are translations; the next sentence is for it too
        mode = self.cfg.get('follow_up', 'question')
        if mode not in FOLLOW_UPS:
            mode = 'question'
        if mode != 'question' or said is None:
            ok = mode != 'never'
        else:
            ok = asks_back(said)
        log.info('after the reply: %s', 'listening for a follow-up' if ok else 'mic closed until the wake word')
        return ok

    async def _listen_after_reply(self):
        """Reopen the mic for a follow-up once the last of the reply has been played (uploading
        earlier would send the hub its tail)."""
        give_up = time.monotonic() + 3
        while self.audio.player.buffered() > 0.02 and time.monotonic() < give_up:
            await asyncio.sleep(0.05)
        if not self.listen_after_reply or not self.hub.connected:
            return   # woken, aborted or ended meanwhile: that already decided what comes next
        self.listen_after_reply = False
        if not self.keys.mic_on:
            # switched off during the reply: nothing would be heard, don't show "listening"
            self.set_state('idle')
            return
        recognised = self.recognised
        await self.start_listening('realtime')
        if self.recognised != recognised:
            # the hub cut this reply off for a newer question that arrived while we reopened
            self.uploading, self.listen_after_reply = False, True
            self.set_state('thinking')
            return
        self.heard = True
        self.deadline = time.monotonic() + self.follow_up_seconds()

    def _on_audio(self, packet):
        if self.mute_audio:
            self.muted_packets += 1
            return
        if self.decoder is None:
            # Opus decodes to any rate whatever it was encoded at: straight to the output rate
            self.decoder = Decoder(OUT_RATE, 1)
        try:
            mono = self.decoder.decode(packet)
            self.reply_samples += len(mono)
            self.reply_packets += 1
            self.audio.player.push(np.repeat(mono[:, None], 2, axis=1))
        except Exception:
            log.exception('cannot decode reply audio')

    async def _on_closed(self):
        # the conversation is over (the hub's idle timeout, an exit word, the end button)
        self.play('bye')
        self.uploading = False
        self.vad_ended = False
        self.abort_pending = self.question = None
        self.listen_after_reply = False
        self.deadline = None
        self.awaiting_text = False
        self.mute_audio = False
        self.bot_msg = None
        self.entry = None
        self.set_state('idle')
        self.events.publish('thread', {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), [])})
        # the hub's copy has interrupted replies cut to what was heard: show that one
        bg.spawn(self.load_history())

    # ---- conversation threads (one per model, like the hub keeps them) ----

    def add_message(self, role, text, id=None, **extra):
        entry = self.view_entry()
        if entry != self.entry:
            self.entry = entry
            self.events.publish('thread', {'entry': entry, 'messages': self.threads.get(entry, [])})
        self._seq += 1
        m = {'id': id or self._seq, 'role': role, 'text': text, 'time': time.time(), 'entry': entry, **extra}
        self.threads.setdefault(entry, []).append(m)
        del self.threads[entry][:-200]
        self.publish_message(m)
        return m

    def publish_message(self, m):
        self.events.publish('message', m)

    async def load_history(self):
        """Show the recent part of the conversations the hub saved. Not while connected: this
        connection's messages are being added then (it reloads once the connection closes)."""
        if self.hub.connected:
            return
        try:
            data = await self._hub_http('/kiosk/chat/history?' + urllib.parse.urlencode(
                {'device': self.cfg['device_id'], 'limit': HISTORY_LIMIT}))
        except Exception:
            return   # the hub is down or too old to keep conversations
        if not data.get('ok') or self.hub.connected:
            return
        self.threads = {entry: [{'id': f"h{m['id']}", 'role': m['role'], 'text': m['text'], 'time': m['time'],
                                 'entry': entry, **({'interrupted': True} if m.get('interrupted') else {})}
                                for m in items]
                        for entry, items in (data.get('models') or {}).items()}
        self._merge_cards()
        self.history_loaded = True
        self.events.publish('thread', {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), []),
                                       'reset': True})

    async def power_off(self):
        """The settings' 关机: end any conversation and halt, so the power can be unplugged without
        leaving /boot (ext2, mounted read-write) to be repaired on the next boot."""
        if self.powering_off:
            return {'ok': True}   # a second tap while the first one is still on its way
        job = update.read().get('job') or {}
        if job.get('state') in UPDATE_BUSY and await self._update_running():
            return {'ok': False, 'error': '正在更新，更新完再关机'}
        self.powering_off = True
        self.events.publish('power', {'state': 'off'})   # cover the page before ending a conversation (up to 3 s)
        if self.hub.connected:
            await self.end()
        bg.spawn(self._halt())
        return {'ok': True}

    async def _update_running(self):
        """The status file can be left saying "downloading" when the updater was killed (its 3 h limit).
        Both units are oneshot: while they run they are "activating", which is-active's exit code
        doesn't count as running, so read the states it prints."""
        try:
            p = await asyncio.create_subprocess_exec('systemctl', 'is-active', *UPDATE_UNITS,
                                                     stdout=asyncio.subprocess.PIPE)
            out, _ = await p.communicate()
        except OSError:
            return True
        return any(s in ('active', 'activating', 'reloading', 'deactivating') for s in out.decode().split())

    async def _halt(self):
        await asyncio.sleep(HALT_AFTER)
        log.info('halting the system (关机 in the settings)')
        try:
            p = await asyncio.create_subprocess_exec(*HALT, stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.PIPE)
            _, err = await p.communicate()
            failed = p.returncode != 0 and (err.decode(errors='replace').strip() or f'exit {p.returncode}')
        except OSError as e:
            failed = str(e)
        if failed:
            log.error('cannot halt: %s', failed)
            self.powering_off = False
            self.events.publish('power', {'state': 'failed'})
            self.events.publish('toast', {'kind': 'error', 'text': '没能关机，请稍后再试'})

    # ---- content cards (self.screen.show_card): rich messages in the conversation ----

    def show_card(self, title, markdown=None, images=None):
        """A recipe / list / table / pictures as a message of its own in the current conversation (role
        'card'; the page renders the markdown escaped, no raw HTML). The hub doesn't keep these, so the
        client does (CARDS, last CARDS_MAX) and puts them back among the hub's history by time."""
        title = str(title or '').strip()[:CARD_TITLE_MAX]
        if not title:
            raise ValueError('title 不能为空')
        imgs = []
        for u in images or []:
            u = str(u).strip()
            local = u.startswith('/api/photos/') and re.fullmatch(r'/api/photos/[0-9A-Za-z_-]+\.jpg', u)
            if (u.startswith('https://') or local) and len(u) <= 2000 and len(imgs) < CARD_IMAGES_MAX:
                imgs.append(u)
        card = {'title': title, 'markdown': str(markdown or '')[:CARD_TEXT_MAX], 'images': imgs}
        self._card_seq += 1
        m = self.add_message('card', title, id=f'c{int(time.time())}-{self._card_seq}', card=card)
        self.cards = (self.cards + [m])[-CARDS_MAX:]
        self._save_cards()
        self.screen.poke()
        return m

    def _load_cards(self):
        try:
            data = json.loads(CARDS.read_text())
            self.cards = [m for m in data if isinstance(m, dict) and m.get('role') == 'card'][-CARDS_MAX:]
        except FileNotFoundError:
            self.cards = []
        except (OSError, ValueError):
            log.warning('%s unreadable, starting without cards', CARDS)
            self.cards = []

    def _save_cards(self):
        try:
            CARDS.parent.mkdir(parents=True, exist_ok=True)
            alarms.write_atomic(CARDS, json.dumps(self.cards, ensure_ascii=False).encode())
        except OSError as e:
            log.warning('cannot save cards: %s', e)

    def _merge_cards(self):
        """After the hub's history replaced the threads: put the saved cards back, by time."""
        for m in getattr(self, 'cards', ()):
            thread = self.threads.setdefault(m['entry'], [])
            if not any(x['id'] == m['id'] for x in thread):
                thread.append(m)
        for thread in self.threads.values():
            thread.sort(key=lambda x: x.get('time') or 0)

    async def clear_history(self, memory_only=False):
        """Clear every model's conversation on the hub (and the page). memory_only: just the summary
        the hub made of the older part, the messages stay."""
        try:
            result = await self._hub_http('/kiosk/chat/clear', {'device': self.cfg['device_id'],
                                                                'memory_only': bool(memory_only)})
        except Exception as e:
            log.warning('cannot clear the chat history: %s', e)
            return {'ok': False, 'error': '连不上小麦网关'}
        if result.get('ok') and not memory_only:
            self.threads = {}
            self.cards = []
            shutil.rmtree(PHOTOS, ignore_errors=True)   # the hub deletes its copies too
            self._save_cards()
            self.bot_msg = self.question = None
            self.events.publish('thread', {'entry': self.view_entry(), 'messages': [], 'reset': True})
        return result

    # ---- sleep hours ----

    def _apply_sleep(self):
        was = getattr(self, 'sleeping', False)
        self.sleeping = sleep.active(self.sleep)
        if not self.sleeping:
            self.bright_in_sleep = False
        self.screen.sleep_level = self.sleep.get('brightness', sleep.BRIGHTNESS)
        self.screen.dim(self.sleeping and not self.bright_in_sleep)
        self.screen._write(activity=False)   # the sleep brightness may have changed while dimmed
        saved = getattr(self, 'cfg', {}).get('sleep_saved_volume')
        # 进出睡眠时段时；客户端在时段里重启（还没记音量）或跨过结束时间重启（记的音量还没还）时也要补上
        if self.sleeping != was or (self.sleeping and saved is None) or (not self.sleeping and saved is not None):
            try:
                asyncio.get_running_loop().create_task(self._sleep_volume(self.sleeping))
            except RuntimeError:
                pass   # no loop yet (start() applies it again)

    async def _sleep_volume(self, entering):
        """睡眠时段开始：记下现在的音量，调到睡眠音量；结束：恢复记下的（客户端中途重启也记得，存在设置里）。"""
        saved = self.cfg.get('sleep_saved_volume')
        try:
            if entering:
                if saved is None:
                    now = await self.device_volume()
                    if now is not None:
                        self.cfg['sleep_saved_volume'] = now
                        config.save(sleep_saved_volume=now)
                await self.set_volume(self.sleep.get('volume', sleep.VOLUME), source='sleep')
                log.info('sleep hours: volume %s (was %s)', self.sleep.get('volume'), self.cfg.get('sleep_saved_volume'))
            elif saved is not None:
                await self.set_volume(saved, source='sleep')
                self.cfg.pop('sleep_saved_volume', None)
                config.unset('sleep_saved_volume')
                log.info('sleep hours over: volume back to %s', saved)
        except Exception as e:  # noqa: BLE001
            log.warning('sleep volume: %s', e)

    async def set_sleep(self, **changes):
        """Change the sleep setting (page or voice); raises ValueError for a bad time."""
        self.sleep = sleep.normalize(self.sleep, changes)
        config.save(sleep=self.sleep)
        log.info('sleep setting: %s', self.sleep)
        self._apply_sleep()
        if self.sleeping and changes.get('volume') is not None:
            await self.set_volume(self.sleep['volume'], source='sleep')   # 正在睡眠时段里改了睡眠音量：马上用上
        self.set_state(self.state)
        await self.poll_reports()     # tell the hub now, not at the next poll
        return {**self.sleep, 'active': self.sleeping}

    # ---- screen brightness (the blanker sets the backlight: device.Screen, idle-blanker.py) ----

    def brightness(self):
        """level: the screen's 0-100 now (the dim level while the sleep hours dim it, unless the
        user's is darker); dimmed: held dim for the sleep hours."""
        level, dimmed = self.screen.level, self.screen.dimmed
        return {'level': min(level, DIM_LEVEL) if dimmed else level, 'dimmed': dimmed}

    async def set_brightness(self, level=None, change=None):
        """Page or voice: level (0-100) or change (relative, from the level now). A level set in
        the sleep hours is kept until they end."""
        if level is None:
            if change is None:
                raise ValueError('level or change is needed')
            level = self.brightness()['level'] + int(change)
        self.screen.brightness(max(0, min(100, int(level))))
        self.bright_in_sleep = self.sleeping
        config.save(brightness=self.screen.level)
        self._apply_sleep()
        log.info('brightness: %s', self.screen.level)
        self.set_state(self.state)
        return self.brightness()

    # ---- auto screen-off (the blanker blanks: device.Screen, idle-blanker.py) ----

    def _screen_off_seconds(self):
        return self.screen_off['minutes'] * 60 if self.screen_off['enabled'] else 0

    async def set_screen_off(self, **changes):
        """Page: enabled and/or minutes (one of device.SCREEN_OFF_MINUTES); raises ValueError."""
        self.screen_off = normalize_screen_off(self.screen_off, changes)
        config.save(screen_off=self.screen_off)
        self.screen.blank_after(self._screen_off_seconds())
        log.info('screen-off: %s', self.screen_off)
        self.set_state(self.state)
        return self.screen_off

    # ---- photo slideshow (photos.py: POST /api/photos from programs on the device; upstream PR #3) ----

    def slides_view(self):
        show = self.slides.current if self.slides else None
        return show.view() if show else None

    def show_photos(self, show):
        """A show was committed (web.py): put it on the page, keep the screen on, close it after its minutes."""
        if self._slides_timer:
            self._slides_timer.cancel()
        self._slides_timer = asyncio.get_running_loop().call_later(
            show.minutes * 60, lambda: asyncio.create_task(self.close_photos(show.id)))
        self.screen.photos(True)
        self.events.publish('slides', show.view())

    async def close_photos(self, show_id=None):
        """Page, voice (mcp) or the timer. show_id: only if that show is still the open one (a timer of a
        replaced show does nothing). True if a show was open."""
        show = self.slides.current if self.slides else None
        if show is None or (show_id is not None and show.id != show_id):
            return False
        if self._slides_timer:
            self._slides_timer.cancel()
            self._slides_timer = None
        self.slides.close()
        self.screen.photos(False)
        self.events.publish('slides', None)
        return True

    # ---- reports (the hub's local-only /kiosk/reports endpoint) ----

    async def poll_reports(self, play=False):
        try:
            # version: 网关后台按这个心跳显示设备 待机/对话中/离线 和客户端版本
            data = await self._hub_http('/kiosk/reports', {'device': self.cfg['device_id'], 'version': __version__,
                                                           'quiet': self.sleeping, 'play': play})
        except Exception:
            return None
        self.hub_seen = time.monotonic()
        if data.get('admin'):
            self.hub_admin = str(data['admin'])   # 设置页「本机 IP」那里写网关后台的地址（网关不在设备上）
        count = int(data.get('count') or 0)
        new = count > self.reports
        if count != self.reports:
            self.reports = count
            self.set_state(self.state)
        if new:
            self._on_new_report()
        return data

    def _on_new_report(self):
        if self.sleeping:
            # just light the screen with the notice; it blanks again after the user's screen-off
            self.screen.poke()
        elif self.state == 'idle' and not self.hub.connected and not self.ring:
            # connected and idle, the hub reads it by itself
            self.play('report')
            bg.spawn(self.play_reports())

    async def play_reports(self):
        """Have the hub read the pending reports now (also in the sleep hours: the user asked)."""
        self.screen.poke()
        if not await self.ensure_connected():
            return
        if self.state == 'connecting':
            self.set_state('idle')
        # a fresh connection gets them from the hub itself after its greeting delay (~6 s)
        await self.poll_reports(play=True)

    async def _report_poller(self):
        while True:
            if sleep.active(self.sleep) != self.sleeping:
                self._apply_sleep()
                log.info('sleep hours %s', 'begin' if self.sleeping else 'end')
                self.set_state(self.state)
            await self.poll_reports()
            await asyncio.sleep(REPORT_POLL)

    # ---- volume: the device's (keys, voice, the page) and the alarms' own ----

    async def device_volume(self):
        """The device volume, also while a ring has the speaker at the alarm volume."""
        if self.held_volume is not None:
            return self.held_volume
        volume = await mcp.get_volume()
        if volume is not None:
            self.volume = volume
        return volume

    async def set_volume(self, volume, source='page'):
        """The device volume (page, voice, keys). While a ring or a preview has the speaker, it is what
        comes back afterwards."""
        async with self.volume_lock:
            volume = await self._apply_volume(volume)
        self.events.publish('volume', {'volume': volume, 'source': source})
        return volume

    async def _apply_volume(self, volume):
        """set_volume under volume_lock."""
        volume = max(0, min(100, int(volume)))
        if self.held_volume is not None:
            self.held_volume = volume
            self._write_ring_state()
        else:
            await mcp.set_volume(volume)
        self.volume = volume
        return volume

    async def volume_step(self, delta):
        """A volume key. Key repeat fires every ~100 ms: one change at a time, each from the current
        volume. While an alarm rings only that ring is louder or quieter: the alarm volume setting and
        the device volume (put back when it stops) stay."""
        if self.ring:
            async with self.volume_lock:
                if not self.ring:
                    return
                self.ring['volume'] = max(0, min(100, self.ring['volume'] + delta))
                if self.held_volume is not None:
                    await mcp.set_volume(self.ring['volume'])
                # else the ring hasn't switched the speaker yet: it will, to this volume (_hold_speaker)
            self.events.publish('volume', {'volume': self.ring['volume'], 'source': 'key', 'ring': True})
            return
        async with self.volume_lock:   # read and write as one step, or two quick presses make one
            current = self.held_volume if self.held_volume is not None else await mcp.get_volume()
            if current is None:
                return
            volume = await self._apply_volume(current + delta)
        self.events.publish('volume', {'volume': volume, 'source': 'key'})

    def _ring_state_path(self):
        return self.alarms.dir / 'ring.json'

    def _write_ring_state(self):
        try:
            alarms.write_atomic(self._ring_state_path(), json.dumps({'volume': self.held_volume}).encode())
        except OSError as e:
            log.warning('cannot save the volume to put back: %s', e)

    async def _hold_speaker(self, level):
        """Switch the speaker to `level` (or level() read under the lock: a ring's volume the keys may
        have changed meanwhile) for a ring or a preview; the device volume is saved first, so that a
        crash or a power cut in between still gets it back (_restore_volume)."""
        async with self.volume_lock:
            level = level() if callable(level) else level
            if self.held_volume is None:
                before = await mcp.get_volume()
                self.held_volume = before if before is not None else self.volume
                if self.held_volume is not None:
                    self._write_ring_state()
            await mcp.set_volume(level)

    async def _release_speaker(self):
        async with self.volume_lock:
            if self.held_volume is None:
                return
            volume, self.held_volume = self.held_volume, None
            await mcp.set_volume(volume)
            self.volume = volume
            try:
                self._ring_state_path().unlink()
            except FileNotFoundError:
                pass

    async def _restore_volume(self):
        """At start: a ring that never ended (the client crashed, the power was cut) left the speaker at
        the alarm volume. Put the device volume back."""
        path = self._ring_state_path()
        try:
            before = json.loads(path.read_text())['volume']
        except FileNotFoundError:
            before = None
        except (ValueError, KeyError, TypeError):
            log.warning('%s is unreadable', path)
            before = None
        if isinstance(before, (int, float)) and not isinstance(before, bool):
            log.info('the last ring did not end: volume back to %d', before)
            await mcp.set_volume(before)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        await self.device_volume()

    async def set_alarm_volume(self, volume=None, change=None):
        """Page or voice: volume (0-100) or change (from the alarm volume now)."""
        if volume is None:
            if change is None:
                raise ValueError('volume or change is needed')
            volume = self.alarm_volume + int(change)
        self.alarm_volume = max(0, min(100, int(volume)))
        config.save(alarm_volume=self.alarm_volume)
        log.info('alarm volume: %d', self.alarm_volume)
        self.publish_alarms()
        return self.alarm_volume

    def set_alarm_tone(self, name):
        if name not in alarms.TONES:
            raise ValueError(f'tone must be one of {list(alarms.TONES)}')
        self.alarm_tone = name
        config.save(alarm_tone=name)
        self.publish_alarms()
        return name

    async def preview_tone(self, name):
        """The settings' 试听: one phrase of the tone at the alarm volume."""
        if name not in alarms.TONES:
            raise ValueError(f'tone must be one of {list(alarms.TONES)}')
        if self.ring:
            return
        # cancel the old one now (a task cancelled before it first runs never would) and let the new
        # one wait for it to put the volume back
        previous = self.preview
        if previous:
            previous.cancel()
        self.preview = asyncio.create_task(self._play_preview(name, previous))

    async def _play_preview(self, name, previous=None):
        if previous and not previous.done():
            await asyncio.wait([previous])   # not `await previous`: a cancel of this one must still stop it
        sound = alarms.tone(name)
        n = min(len(sound) - int(OUT_RATE * alarms.TONE_GAP), int(OUT_RATE * PREVIEW_SECONDS))
        sound = sound[:n].copy()                       # a piece: only its first seconds, faded out
        fade = min(n, int(OUT_RATE * 0.6))
        sound[n - fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        try:
            await self._hold_speaker(self.alarm_volume)
            self.audio.player.push(sound)
            await asyncio.sleep(n / OUT_RATE + 0.1)
        finally:
            self.audio.player.trim(0)
            # cancelled (another tap, the settings closed, a ring) halfway through putting the volume
            # back would leave the speaker at the alarm volume: let it finish
            await asyncio.shield(self._release_speaker())

    async def _stop_preview(self):
        task, self.preview = self.preview, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            async with self.volume_lock:
                pass   # its shielded _release_speaker has put the volume back

    # ---- countdowns, alarms and reminders (alarms.py) ----

    # ---- Bluetooth speaker (bt.py) ----

    def background_target(self):
        """How loud a phone's music plays: muted while an alarm rings or a tone is tried, lowered while
        talking (from the wake to the end of the reply) or a local sound plays, else full."""
        # a preview that played to its end stays in self.preview (finished) until the next one:
        # only one still playing mutes the phone
        if self.ring or (self.preview and not self.preview.done()):
            return 0.0
        if self.state != 'idle' or self.audio.player.buffered() > 0:
            return BACKGROUND_DUCKED
        return 1.0

    async def _bluetooth_loop(self):
        paused_for = None   # the ring the phones were asked to pause for
        while True:
            await asyncio.sleep(0.1)
            self.audio.background.target = self.background_target()
            if self.ring and self.ring is not paused_for:
                paused_for = self.ring
                if self.bt.streaming():
                    log.info('alarm: asking the phone to pause')
                    await self.bt.pause()   # it doesn't resume by itself: the user plays on from the phone

    async def bluetooth(self, enabled=None, window=None, remove=None, disconnect=None):
        """The page's Bluetooth controls: the switch (stored), the "add a phone" window, forgetting a phone."""
        ok = True
        if enabled is not None:
            ok = await self.bt.set_enabled(bool(enabled))
            config.save(bluetooth=bool(enabled))
        if window is not None:
            ok = await self.bt.open_window(bool(window))
        if remove:
            ok = await self.bt.remove(remove)
        if disconnect:
            ok = await self.bt.disconnect(disconnect)
        return ok

    def alarm_view(self):
        now = time.time()
        ring = self.ring
        return {'synced': self.clock_ok, 'items': [self.alarms.view(i, now) for i in self.alarms.active()],
                'missed': self.alarms.missed[::-1],
                'ringing': ring and {'items': [self.alarms.view(i, now) for i in ring['items']],
                                     'notes': ring['notes'], 'started': ring['started']},
                'volume': self.alarm_volume, 'tone': self.alarm_tone, 'tones': alarms.TONES}

    def publish_alarms(self):
        self.events.publish('alarms', self.alarm_view())

    async def create_alarm(self, kind, label='', say='', repeat=None, **when):
        """From the model (mcp.py). Raises ValueError with a sentence for it."""
        if not alarms.clock_synced():
            raise ValueError('设备时间还没和网络对上（刚开机或没联网），现在设不准，请过一会儿再试')
        item = self.alarms.create(kind, label, say, repeat, **when)
        if kind == 'reminder':
            bg.spawn(self._make_clip(item))
        self.publish_alarms()
        return self.alarms.view(item)

    async def delete_alarm(self, item_id):
        """Page or voice. A ringing entry stops ringing first."""
        if self.ring and any(i['id'] == item_id for i in self.ring['items']):
            if len(self.ring['items']) == 1:
                await self.stop_alarm()
            else:
                self.ring['items'] = [i for i in self.ring['items'] if i['id'] != item_id]
        item = self.alarms.remove(item_id)
        self.publish_alarms()
        return item and self.alarms.view(item)

    async def snooze_alarm(self, minutes=alarms.SNOOZE_MINUTES):
        """再睡: the entry ringing now, or rung in the last 15 minutes, rings again in `minutes`
        (hung on that entry: its repeat and words stay). [] when there is none."""
        ringing = list(self.ring['items']) if self.ring else []
        await self.stop_alarm()
        found = self.alarms.snooze(minutes, ringing=ringing)
        self.publish_alarms()
        return [self.alarms.view(i) for i in found]

    def clear_missed(self):
        self.alarms.clear_missed()
        self.publish_alarms()

    async def _make_clip(self, item):
        """A reminder's words, synthesised by the hub now and kept with it: when it is due it is
        played without the hub. If that fails it only rings, and the screen shows the words."""
        text = item['say'] or (f"提醒你，{item['label']}" if item['label'] else '提醒时间到了')
        wav = await self._speech(text)
        if self.alarms.set_clip(item['id'], wav):
            self.publish_alarms()

    async def _speech(self, text):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TTS_TIMEOUT)) as s:
                async with s.post(self.cfg['hub_http'] + '/kiosk/tts', json={'text': text}) as r:
                    if r.status == 200:
                        return await r.read()
                    log.warning('the hub did not synthesise %r: %s %s', text, r.status, await r.text())
        except Exception as e:
            log.warning('the hub did not synthesise %r: %s', text, e)
        return None

    async def _alarm_loop(self):
        await self._restore_volume()
        last_tidy = 0.0
        while True:
            try:
                now = time.time()
                if self.clock_ok and now - last_tidy > 60:   # by a clock that is right
                    last_tidy = now
                    before = (len(self.alarms.items), len(self.alarms.missed))
                    self.alarms.tidy(now)
                    if (len(self.alarms.items), len(self.alarms.missed)) != before:
                        self.publish_alarms()
                await self._alarm_tick(now)
            except Exception:
                log.exception('alarm scheduler')
            await asyncio.sleep(1)

    async def _alarm_tick(self, now):
        synced = alarms.clock_synced()
        if synced != self.clock_ok:
            self.clock_ok = synced
            log.info('system clock %s', 'synchronised: alarms on' if synced else 'not synchronised: alarms paused')
            self.publish_alarms()
        if not synced:
            return
        if self.ring and self.ring['closing']:
            return   # the ring is ending: what is due now rings on its own once it has
        before = len(self.alarms.missed)
        items, notes = self.alarms.take_due(now, self.ring['items'] if self.ring else ())
        if items and self.ring:
            # due while another rings: they ring together, and the ring lasts its full time from now
            self.ring['items'] += items
            self.ring['notes'] += notes
            self.ring['fired'].update((i['id'], now) for i in items)
            self.ring['joined'].append((time.monotonic(), all(i['kind'] == 'reminder' for i in items)))
            log.info('joined the ring: %s', [(i['id'], i['kind'], i['label']) for i in items])
            self.publish_alarms()
        elif items:
            self.ring = {'items': items, 'notes': notes, 'started': now, 'volume': self.alarm_volume,
                         'stop': asyncio.Event(), 'joined': [], 'fired': {}, 'closing': False}
            self.ring['task'] = asyncio.create_task(self._ringing(self.ring))
        elif len(self.alarms.missed) != before:
            self.publish_alarms()

    async def _ringing(self, ring):
        answered = False
        log.info('ringing: %s%s', [(i['id'], i['kind'], i['label']) for i in ring['items']],
                 f" ({'；'.join(ring['notes'])})" if ring['notes'] else '')
        try:
            await self._stop_preview()
            if self.in_turn():
                await self.abort()
            self.uploading = self.listen_after_reply = False
            self.deadline = None
            self.set_state('idle')
            self.screen.alarm(True)
            self.publish_alarms()
            await self._hold_speaker(lambda: ring['volume'])
            note = asyncio.create_task(self._speech('，'.join(ring['notes']))) if ring['notes'] else None
            clips, phrase, chime = {}, None, None
            start = time.monotonic()
            end, reminder_end, round_over = start + alarms.RING_SECONDS, start + alarms.REMINDER_SECONDS, 0.0
            while True:
                now = time.monotonic()
                for at, only_reminders in ring['joined']:   # each one that joined rings its full time too
                    end = max(end, at + (alarms.REMINDER_SECONDS if only_reminders else alarms.RING_SECONDS))
                    if only_reminders:
                        reminder_end = max(reminder_end, at + alarms.REMINDER_SECONDS)
                # only reminders: the chime and their words a few times; an alarm or countdown among
                # them: the tone over and over, up to RING_SECONDS
                reminders = all(i['kind'] == 'reminder' for i in ring['items'])
                if reminders and now >= reminder_end and now >= round_over:   # its words finished, not cut off
                    break
                if not reminders and now >= end:
                    break
                if self.audio.player.buffered() < 0.3 and not (reminders and now >= reminder_end):
                    words = []
                    if note and note.done():
                        words += self._clip_sound(note.result())   # once, in the first round it is ready for
                        note = None
                    for item in ring['items']:
                        if item['kind'] == 'reminder' and item.get('clip'):
                            if item['id'] not in clips:
                                clips[item['id']] = self._clip_sound(self.alarms.clip_path(item))
                            words += clips[item['id']]
                    if reminders:
                        chime = alarms.reminder_chime() if chime is None else chime
                        sound = overlay(chime, np.concatenate(words), alarms.CHIME_SPEECH_AT) if words else chime
                        round_over = now + self.audio.player.buffered() + len(sound) / OUT_RATE
                        sound = np.concatenate([sound, np.zeros((int(OUT_RATE * alarms.REMINDER_PAUSE), 2), np.float32)])
                    else:
                        phrase = alarms.tone(self.alarm_tone) if phrase is None else phrase
                        sound = np.concatenate([phrase] + words)
                    self.audio.player.push(sound)
                try:
                    await asyncio.wait_for(ring['stop'].wait(), 0.1)
                    answered = True
                    break
                except asyncio.TimeoutError:
                    pass
            if not answered:
                log.info('nobody stopped the ring in %.0f s: noted as missed', time.monotonic() - start)
        except Exception:
            # whoever awaits this (stop_alarm from the wake word) must not fail with it
            log.exception('the ring failed')
        finally:
            ring['closing'] = True   # nothing joins any more: it would be finished without having rung
            self.audio.player.trim(0)
            self.screen.alarm(False)
            await self._release_speaker()
            self.alarms.finish(ring['items'], ring['started'], answered, fired=ring['fired'])
            self.ring = None
            self.publish_alarms()
            self.set_state(self.state)

    @staticmethod
    def _clip_sound(source):
        """A spoken clip (a wav file, or the hub's wav bytes) and a pause after it, as a list to concatenate."""
        if not source:
            return []
        try:
            x = load_wav(io.BytesIO(source) if isinstance(source, bytes) else source)
        except Exception as e:
            log.warning('cannot play a reminder clip: %s', e)
            return []
        return [x, np.zeros((int(OUT_RATE * CLIP_GAP), 2), np.float32)]

    async def stop_alarm(self):
        """停止 on the page, or the wake word: the ring ends (a one-off is done, a repeating one waits
        for its next time)."""
        ring = self.ring
        if not ring:
            return False
        ring['stop'].set()
        await asyncio.shield(ring['task'])
        return True

    # ---- models (the hub's local-only /kiosk/llm endpoints) ----

    async def _hub_http(self, path, body=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            if body is None:
                async with s.get(self.cfg['hub_http'] + path) as r:
                    return await r.json(content_type=None)
            async with s.post(self.cfg['hub_http'] + path, json=body) as r:
                return await r.json(content_type=None)

    async def refresh_models(self):
        """Ask the hub for its models; False when it didn't answer (down, or still starting)."""
        try:
            data = await self._hub_http('/kiosk/llm?' + urllib.parse.urlencode({'device': self.cfg['device_id']}))
        except Exception:
            return False
        self._apply_models(data)
        return True

    def _apply_models(self, data):
        options = [describe(o) for o in data.get('options', [])]
        models = {'current': next((o for o in options if o['entry'] == data.get('selected')), None),
                  'options': options}
        if models != self.models:
            self.models = models
            self.events.publish('model', models)
            if self.hub.connected and self.entry and models['current'] and models['current']['entry'] != self.entry:
                self.entry = models['current']['entry']
                self.events.publish('thread', {'entry': self.entry, 'messages': self.threads.get(self.entry, [])})
            elif not self.entry:
                # also while connected with nothing said yet (e.g. just after an interrupt reconnected)
                self.events.publish('thread', {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), [])})

    async def set_model(self, kind, vendor, entry=None):
        # entry names the model on the hub; a Grok Bot is only identified by it (its vendor is shared)
        if self.in_turn():
            # the answer in flight belongs to the old model: stop it rather than file it under the new one
            await self.abort()
        try:
            result = await self._hub_http('/kiosk/llm/use', {'kind': kind, 'vendor': vendor, 'entry': entry,
                                                             'device': self.cfg['device_id']})
        except Exception as e:
            result = {'ok': False, 'error': str(e)}
        if result.get('ok') and 'options' in result:
            self._apply_models(result)   # the hub answers with the new list (an older hub doesn't)
        else:
            await self.refresh_models()
        return {k: v for k, v in result.items() if k not in ('options', 'selected', 'default', 'kind', 'vendor')}

    async def _model_poller(self):
        # often while the hub is down (after a boot the page would show no models for over a minute),
        # then only to notice changes made in the hub's admin page
        while True:
            up = await self.refresh_models()
            if up and not self.history_loaded:
                await self.load_history()   # at start, or once the hub is up
            await asyncio.sleep(MODEL_POLL if up else MODEL_RETRY)

    # ---- timers ----

    async def _ticker(self):
        last_level = 0.0
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            stalled = self.audio.stalled()
            if stalled:
                # PipeWire restarted (or died): the streams won't come back by themselves. systemd
                # restarts the service, which waits for PipeWire's socket and opens them again.
                log.error('audio is dead (%s): exiting to be restarted', stalled)
                os._exit(1)
            if self.uploading and self.audio.mic_level > SPEECH_RMS:
                self.speech_end = now
                if self.recognising:   # talking again: the hub listens on
                    self.recognising = False
                    self.set_state(self.state)
                if self.deadline:   # still talking
                    self.deadline = min(max(self.deadline, now + 2.5), self.listen_since + MAX_LISTEN)
            if self.uploading and self.listen_mode == 'manual' and now > self.listen_since + MAX_LISTEN:
                # push-to-talk held (or a release lost) for too long: the hub buffers all of it
                log.info('push-to-talk open for %d s: stopping it', MAX_LISTEN)
                await self.ptt_stop()
            if self.deadline and now > self.deadline:
                if self.state == 'listening' and self.uploading:
                    # a hub that sends VAD events has answered everything it heard unless one is pending.
                    # In a follow-up window (heard) only that counts: an older hub would make any noise
                    # after a reply wait 15 s
                    waiting = self.hub_pending or (not self.heard and not self.hub.features.get('vad_end'))
                    if self.speech_end > self.listen_since and waiting:
                        # they did talk; a long sentence takes the hub a while to recognise, so
                        # close the mic but wait for its text before saying "didn't catch that"
                        # (or, in a follow-up window, before going idle: its answer would be lost)
                        self.uploading = False
                        self.awaiting_text = True
                        self.deadline = now + RECOGNITION_WAIT
                        self.set_state('thinking')
                    else:
                        await self.stop_uploading(missed=not self.heard)
                elif self.state == 'thinking' and not self.uploading:
                    missed, self.awaiting_text = self.awaiting_text and not self.heard, False
                    await self.stop_uploading(missed=missed)
            if (self.state in ('listening', 'speaking') or self.wake_rec is not None) and now - last_level >= 0.1:
                last_level = now
                self.events.publish('level', {'mic': round(self.audio.mic_level, 4),
                                              'out': round(self.audio.player.level, 4)})


def dumps(data):
    return json.dumps(data, ensure_ascii=False)
