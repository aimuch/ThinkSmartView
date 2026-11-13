"""学习陪伴（设计画布第 48 块）：写作业时每隔一会儿看一眼，驼背、离太近、玩手机、犯困时小麦温和提醒一句。

手动打开（专注页那一行、设置、或者说「打开学习陪伴」），到时间自动关（默认 3 小时）。开着时每 interval 秒
用机身摄像头连拍 BURST 张（隔 GAP 秒），交给网关后台选好的看图模型（/kiosk/study/check），回来一句文字结果。
同一个问题连续 ISSUES[问题][0] 次才提醒，同一类 COOLDOWN 秒内只说一次，任意两次提醒至少隔 MIN_GAP 秒。
只记文字结果（每天一个 jsonl，留 KEEP_DAYS 天），照片在设备和网关上都不保存。
想法、提示词和提醒的话改写自 fubin0478/thinksmart-view 的 device/study/thinksmart-study.py（MIT）。
"""
import json
import logging
import random
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

INTERVALS = (60, 120, 300)        # 秒：多久看一次
AUTO_OFF = (1, 2, 3, 0)           # 小时：开多久自动关（0 = 不关）
BURST = 3                         # 一次连拍几张
GAP = 4                           # 连拍间隔（秒）：一瞬间的动作（拿笔、抬头）不算
COOLDOWN = 300                    # 同一类提醒至少隔这么久
MIN_GAP = 90                      # 任意两次提醒至少隔这么久
KEEP_DAYS = 30
CHOICES = ('posture', 'eyes', 'phone', 'sleep', 'focus')     # 设置里能选的「提醒哪些」
DEFAULT = {'interval': 60, 'auto_off': 3, 'issues': ['posture', 'eyes', 'phone', 'sleep']}

# 问题 → (连续几次才提醒, 说的话)；排在前面的先说
ISSUES = {
    'sleep':   (2, ['是不是有点累了？站起来活动一下再继续吧。', '困了的话，先伸个懒腰、喝口水再学习哦。']),
    'phone':   (2, ['先把手机放一放，专心写完作业再玩吧。', '手机先休息一下，作业写完再看哦。']),
    'playing': (2, ['先专心学习，学完再玩哦。', '东西先放一边，我们专心写作业吧。']),
    'eyes':    (2, ['眼睛离桌面远一点，保护好视力哦。', '头抬高一点，眼睛离书本一尺远哦。']),
    'posture': (2, ['坐姿要端正哦，把背挺直一点。', '小提醒：坐直一点，身体会更舒服哦。']),
    'focus':   (3, ['注意力回来啦，加油继续学习！', '专心一点哦，你可以的，加油！']),
}
NAMES = {'sleep': '犯困', 'phone': '玩手机', 'playing': '玩东西', 'eyes': '离太近', 'posture': '坐姿', 'focus': '走神'}
SETTING_OF = {'playing': 'phone'}  # 「玩手机」那一项也管玩别的东西
SWITCH = {'on': '学习陪伴打开啦，加油哦！', 'off': '学习陪伴关上了，辛苦啦！',
          'auto_off': '学习陪伴已经开了很久，先自动关上啦，休息一下吧。'}
POSTURE_TEXT = {'good': '坐得端正', 'slouch': '有点驼背', 'lying': '趴在桌上', 'tilted': '身体歪着', 'unknown': ''}
ACTIVITY_TEXT = {'studying': '在写作业', 'reading': '在看书', 'phone': '在看手机', 'playing': '在玩东西',
                 'daydreaming': '在发呆', 'sleeping': '像是睡着了', 'absent': '人不在桌前', 'other': ''}


def normalize(value):
    v = {**DEFAULT, 'issues': list(DEFAULT['issues'])}
    if isinstance(value, dict):
        if value.get('interval') in INTERVALS:
            v['interval'] = value['interval']
        if value.get('auto_off') in AUTO_OFF:
            v['auto_off'] = value['auto_off']
        if isinstance(value.get('issues'), list):
            v['issues'] = [k for k in CHOICES if k in value['issues']]
    return v


def issues_of(r):
    """一次结果里看到的问题（按 ISSUES 的先后）。"""
    if not r or not r.get('present'):
        return []
    found = {'sleep': r.get('activity') == 'sleeping',
             'phone': r.get('activity') == 'phone',
             'playing': r.get('activity') == 'playing',
             'eyes': bool(r.get('too_close')),
             'posture': r.get('posture') in ('slouch', 'lying', 'tilted'),
             'focus': r.get('activity') == 'daydreaming'}
    return [k for k in ISSUES if found[k]]


def kind_of(r):
    """时间线上的颜色：focus 专心 / drift 走神发呆 / bad 玩手机趴着睡着 / absent 不在。"""
    if not r or not r.get('present'):
        return 'absent'
    if r.get('activity') in ('phone', 'playing', 'sleeping') or r.get('posture') == 'lying':
        return 'bad'
    if r.get('focused'):
        return 'focus'
    return 'drift'


def describe(r):
    """「在写作业，有点驼背」这样一句（看图模型的 note 优先）。"""
    if not r:
        return ''
    if r.get('note'):
        return r['note']
    parts = [ACTIVITY_TEXT.get(r.get('activity'), ''), POSTURE_TEXT.get(r.get('posture'), '')]
    return '，'.join(p for p in parts if p)


class Study:
    def __init__(self, settings=None, log_dir=None):
        self.settings = normalize(settings)
        self.log_dir = Path(log_dir) if log_dir else None
        self.enabled_at = None    # 开着时：什么时候开的
        self.source = None        # 'focus'：跟着专注开的，专注结束就关
        self.next_at = 0.0
        self.streak = {}          # 问题 → 连续看到几次
        self.said = {}            # 问题 → 上次提醒的时间
        self.last_said = float('-inf')
        self.last = None          # {'at', 'result', 'text'} 最近一次看到的
        self.error = None         # 最近一次没看成的原因

    @property
    def enabled(self):
        return self.enabled_at is not None

    def start(self, now, source=None):
        if self.enabled:
            if source is None:
                self.source = None       # 手动又开了一次：专注结束时别关
            return False
        self.enabled_at, self.source, self.next_at = now, source, now + 5   # 开了 5 秒后看第一眼
        self.streak, self.error = {}, None
        return True

    def stop(self):
        if not self.enabled:
            return False
        self.enabled_at = self.source = None
        self.streak = {}
        return True

    def auto_off_due(self, now):
        hours = self.settings['auto_off']
        return self.enabled and hours > 0 and now - self.enabled_at >= hours * 3600

    def due(self, now):
        return self.enabled and now >= self.next_at

    def scheduled(self, now):
        """开始看这一次了：下一次从现在算。"""
        self.next_at = now + self.settings['interval']

    def judge(self, result, now):
        """一次结果 → 要说的话（或 None）。记进今天的日志。"""
        found = issues_of(result)
        self.streak = {k: self.streak.get(k, 0) + 1 for k in found}   # 没看到的就断了
        allowed = set(self.settings['issues'])
        say = issue = None
        if now - self.last_said >= MIN_GAP:
            for k in found:
                need, phrases = ISSUES[k]
                if SETTING_OF.get(k, k) not in allowed or self.streak[k] < need:
                    continue
                if now - self.said.get(k, float('-inf')) < COOLDOWN:
                    continue
                say, issue = random.choice(phrases), k
                self.said[k] = self.last_said = now
                self.streak[k] = 0
                break
        self.last = {'at': now, 'result': result, 'text': describe(result)}
        self.error = None
        self._log({'t': round(now), **{k: result.get(k) for k in ('present', 'posture', 'too_close', 'activity', 'focused', 'note')},
                   'said': issue})
        return say, issue

    def failed(self, reason, now):
        self.error = {'at': now, 'text': reason}

    def view(self, now):
        left = None
        if self.enabled and self.settings['auto_off']:
            left = max(0, round(self.enabled_at + self.settings['auto_off'] * 3600 - now))
        last = self.last and {'ago': round(now - self.last['at']), 'text': self.last['text'],
                              'kind': kind_of(self.last['result'])}
        return {**self.settings, 'enabled': self.enabled, 'source': self.source, 'auto_off_left': left,
                'last': last, 'error': self.error and self.error['text'],
                'intervals': list(INTERVALS), 'auto_off_choices': list(AUTO_OFF), 'issue_choices': list(CHOICES),
                'issue_names': {k: NAMES[k] for k in CHOICES}}

    # ---- 每天的记录（只有文字） ----

    def _log(self, entry):
        if not self.log_dir:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            day = time.strftime('%Y-%m-%d', time.localtime(entry['t']))
            with open(self.log_dir / f'{day}.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            old = sorted(self.log_dir.glob('*.jsonl'))[:-KEEP_DAYS]
            for p in old:
                p.unlink(missing_ok=True)
        except OSError as e:
            log.warning('study log: %s', e)

    def days(self):
        if not self.log_dir or not self.log_dir.is_dir():
            return []
        return sorted((p.stem for p in self.log_dir.glob('*.jsonl')), reverse=True)

    def day(self, date):
        entries = []
        if self.log_dir and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date or ''):
            try:
                with open(self.log_dir / f'{date}.jsonl', encoding='utf-8') as f:
                    for line in f:
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(e, dict) and isinstance(e.get('t'), (int, float)):
                            entries.append(e)
            except OSError:
                pass
        return summarize(entries, self.settings['interval'])


def summarize(entries, interval=60):
    """一天的记录 → 「今天的学习」：陪伴分钟、专心比例、提醒次数、时间线、最近几条。"""
    entries = sorted(entries, key=lambda e: e['t'])
    present = [e for e in entries if e.get('present')]
    focused = [e for e in present if e.get('focused')]
    reminders = {k: 0 for k in CHOICES}
    for e in entries:
        if e.get('said'):
            reminders[SETTING_OF.get(e['said'], e['said'])] = reminders.get(SETTING_OF.get(e['said'], e['said']), 0) + 1
    # 时间线：每次检查占 interval 秒；隔了太久（关过）就断开成新的一段
    spans = []
    for e in entries:
        kind = kind_of(e)
        if spans and e['t'] - spans[-1]['end'] <= interval * 1.5 and spans[-1]['kind'] == kind:
            spans[-1]['end'] = e['t'] + interval
        else:
            spans.append({'start': e['t'], 'end': e['t'] + interval, 'kind': kind})
    # 最近：新的在上；连着几次一样（没提醒）的合成一行「20:40–20:50 在写作业」
    recent = []
    for e in reversed(entries):
        text, said = describe(e) or ACTIVITY_TEXT.get(e.get('activity'), ''), e.get('said') and NAMES.get(e['said'])
        last = recent[-1] if recent else None
        if last and not said and not last['said'] and last['text'] == text:
            last['t'] = e['t']
            continue
        if len(recent) == 8:
            break
        recent.append({'t': e['t'], 'until': e['t'], 'text': text, 'said': said})
    return {'checks': len(entries), 'minutes': round(len(entries) * interval / 60),
            'present_minutes': round(len(present) * interval / 60),
            'focused_pct': round(100 * len(focused) / len(present)) if present else None,
            'reminders': reminders, 'reminder_total': sum(reminders.values()),
            'names': {k: NAMES[k] for k in CHOICES}, 'spans': spans, 'recent': recent}
