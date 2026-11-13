"""专注计时（设计画布第 47 块）：番茄钟 + 最多两层自然声音。

计时在客户端里（关了页面、屏幕黑了都照样走）。专注 15/25/45 分钟或休息 5 分钟；到点响一声、声音渐弱停下；
专注做完的分钟数记进今天（每天清零），第几个番茄也是今天的。想法来自 fubin0478/thinksmart-view（MIT）。
"""
import time

MINUTES = (15, 25, 45)
BREAK = 5
SOUNDS = ('rain', 'birds', 'ocean', 'fan', 'white')
MAX_LAYERS = 2


def _today():
    return time.strftime('%Y-%m-%d')


def normalize_sounds(value):
    out = []
    for k in value or []:
        if k in SOUNDS and k not in out:
            out.append(k)
    return out[:MAX_LAYERS]


class Focus:
    def __init__(self, saved=None):
        saved = saved if isinstance(saved, dict) else {}
        self.minutes = saved.get('minutes') if saved.get('minutes') in MINUTES else 25
        self.sounds = normalize_sounds(saved.get('sounds', ['rain']))
        today = saved.get('today') if isinstance(saved.get('today'), dict) else {}
        self.today = today if today.get('date') == _today() else {'date': _today(), 'minutes': 0, 'rounds': 0}
        self.phase = None        # 'focus' | 'break' | None
        self.ends = None         # wall clock while running
        self.left = None         # seconds left while paused
        self.length = 0          # seconds of this phase

    def saved(self):
        return {'minutes': self.minutes, 'sounds': self.sounds, 'today': self.today}

    def _roll(self):
        if self.today.get('date') != _today():
            self.today = {'date': _today(), 'minutes': 0, 'rounds': 0}

    def start(self, minutes=None, phase='focus', now=None):
        now = time.time() if now is None else now
        if phase == 'break':
            minutes = BREAK
        else:
            phase = 'focus'
            minutes = minutes if minutes in MINUTES else (int(minutes) if isinstance(minutes, (int, float)) and 1 <= minutes <= 120 else self.minutes)
            if minutes in MINUTES:
                self.minutes = minutes
        self.phase, self.length, self.left = phase, minutes * 60, None
        self.ends = now + self.length
        return self.view(now)

    def pause(self, now=None):
        now = time.time() if now is None else now
        if self.phase and self.ends:
            self.left, self.ends = max(0.0, self.ends - now), None
        return self.view(now)

    def resume(self, now=None):
        now = time.time() if now is None else now
        if self.phase and self.left is not None:
            self.ends, self.left = now + self.left, None
        return self.view(now)

    def stop(self, now=None):
        """结束这一轮；专注做了的分钟数照样算进今天。"""
        now = time.time() if now is None else now
        if self.phase == 'focus':
            done = self.length - (self.left if self.left is not None else max(0.0, (self.ends or now) - now))
            self._roll()
            self.today['minutes'] += int(done // 60)
        self.phase = self.ends = self.left = None
        return self.view(now)

    def tick(self, now=None):
        """每秒调一次；这一轮到点时返回刚结束的那一段（'focus' 或 'break'），否则 None。"""
        now = time.time() if now is None else now
        if not self.phase or self.ends is None or now < self.ends:
            return None
        ended = self.phase
        if ended == 'focus':
            self._roll()
            self.today['minutes'] += self.length // 60
            self.today['rounds'] += 1
        self.phase = self.ends = self.left = None
        return ended

    def set_sounds(self, sounds):
        self.sounds = normalize_sounds(sounds)
        return self.sounds

    def view(self, now=None):
        now = time.time() if now is None else now
        self._roll()
        left = self.left if self.left is not None else (max(0.0, self.ends - now) if self.ends else None)
        return {'phase': self.phase, 'paused': self.left is not None, 'ends': self.ends, 'left': left and round(left),
                'length': self.length, 'minutes': self.minutes, 'choices': list(MINUTES), 'break': BREAK,
                'sounds': self.sounds, 'sound_choices': list(SOUNDS), 'today': self.today}
