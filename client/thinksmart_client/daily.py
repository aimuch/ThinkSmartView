"""场景口令和留言 (设计画布第 11 块).

A scene is a trigger phrase and its steps in plain words ("关掉所有灯和空调，说一下今天的天气"). Saying the
phrase (after the wake word) makes the model call self.scene.run, which hands it the steps to carry out
with the tools it has (Mijia, sounds, weather). Kept in the user config.

A note is left for someone ("给爸爸留言：…"): it shows on the home page until someone taps 知道了; tapping
it reads it out. Kept in NOTES (the client can't tell who is speaking yet, so nothing is read out on its own)."""
import json
import re
import time
from pathlib import Path

NOTES = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'notes.json'
MAX_SCENES = 20
MAX_NOTES = 30


def norm_phrase(text):
    return re.sub(r'[\s，。！？,.!?「」"“”]', '', str(text or ''))


def save_scene(scenes, name, steps):
    name, steps = str(name or '').strip().strip('「」"“”。.！!？?，,').strip('「」"“”'), str(steps or '').strip()
    if not norm_phrase(name):
        raise ValueError('场景要有一句口令，比如「我出门了」')
    if not steps:
        raise ValueError('要说清楚这个场景做哪些事')
    out = [s for s in scenes if norm_phrase(s['name']) != norm_phrase(name)]
    if len(out) >= MAX_SCENES:
        raise ValueError(f'最多 {MAX_SCENES} 个场景，先删掉一个')
    return out + [{'name': name[:30], 'steps': steps[:500]}]


def find_scene(scenes, name):
    key = norm_phrase(name)
    for s in scenes:
        if norm_phrase(s['name']) == key:
            return s
    for s in scenes:   # 「出门了」 for 「我出门了」
        if key and (key in norm_phrase(s['name']) or norm_phrase(s['name']) in key):
            return s
    return None


def load_notes():
    try:
        data = json.loads(NOTES.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_notes(notes):
    NOTES.parent.mkdir(parents=True, exist_ok=True)
    tmp = NOTES.with_suffix('.tmp')
    tmp.write_text(json.dumps(notes[-MAX_NOTES:], ensure_ascii=False))
    tmp.replace(NOTES)


def new_note(notes, to, text, sender=''):
    text = str(text or '').strip()
    if not text:
        raise ValueError('留言的内容是空的')
    note = {'id': f'n{int(time.time() * 1000)}', 'to': str(to or '').strip()[:20] or '家里人',
            'from': str(sender or '').strip()[:20], 'text': text[:300], 'time': time.time()}
    return notes + [note], note
