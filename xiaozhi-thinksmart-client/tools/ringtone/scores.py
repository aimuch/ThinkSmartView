"""Alarm piano pieces as note lists: (beat, note, beats, velocity 1-127). Originals and public-domain
classics (the compositions are long out of copyright; this is our own performance)."""

NAMES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def midi(name):
    """'C4' = 60, 'F#5', 'Bb3'."""
    n = NAMES[name[0]]
    rest = name[1:]
    while rest and rest[0] in '#b':
        n += 1 if rest[0] == '#' else -1
        rest = rest[1:]
    return n + 12 * (int(rest) + 1)


def seq(start, notes, step, vel, beats=None):
    """Notes one after another every `step` beats ('-' is a rest)."""
    out, b = [], start
    for n in notes.split():
        if n != '-':
            out.append((b, midi(n), beats or step, vel))
        b += step
    return out


def chord(start, notes, beats, vel):
    return [(start, midi(n), beats, vel) for n in notes.split()]


# ---- originals ----

def morning_light():
    """晨曦（原创）：72 拍，C 大调，左手分解和弦 Cmaj9 → Am9 → Fmaj7 → Gsus4-G，右手一条往上走的旋律。"""
    ev = []
    lh = [('C3 G3 D4 E4 G4 E4 D4 G3', 0), ('A2 E3 B3 C4 E4 C4 B3 E3', 4),
          ('F2 C3 G3 A3 C4 A3 G3 C3', 8), ('G2 D3 G3 C4 D4 B3 G3 D3', 12)]
    for notes, b in lh:
        ev += seq(b, notes, 0.5, 52, beats=2.5)
    ev += seq(0, 'E5 - G5 D5', 1, 72, beats=1.5) + seq(3.5, 'E5', 0.5, 64)
    ev += seq(4, 'C5 - E5 B4', 1, 70, beats=1.5) + seq(7.5, 'C5', 0.5, 62)
    ev += seq(8, 'A4 C5 F5 E5', 1, 74, beats=1.5)
    ev += seq(12, 'D5 G5', 1, 78, beats=2) + chord(14, 'B4 D5 G5', 2.5, 70)
    ev += chord(16, 'C3 G3 E4 C5 E5 G5 C6', 4, 66)
    return 72, ev, 20


def morning_waltz():
    """早安圆舞曲（原创）：3/4 拍，F 大调，左手「咚-嚓-嚓」，右手轻快地跳。"""
    ev = []
    bass = [('F2', 'A3 C4 F4'), ('C3', 'G3 Bb3 E4'), ('D3', 'A3 C4 F4'), ('Bb2', 'D4 F4 Bb4'),
            ('F2', 'A3 C4 F4'), ('G2', 'Bb3 D4 E4'), ('C3', 'G3 Bb3 E4'), ('F2', 'A3 C4 F4')]
    for i, (b, ch) in enumerate(bass):
        ev += [(3 * i, midi(b), 2.5, 60)] + chord(3 * i + 1, ch, 0.8, 42) + chord(3 * i + 2, ch, 0.8, 40)
    mel = 'C5 F5 A5 G5 - E5 F5 A5 C6 Bb5 - F5 D5 G5 Bb5 A5 F5 C5 E5 G5 C6 Bb5 G5 E5 F5 - -'
    pos = [0, 1, 2, 3, 4, 4.5, 6, 7, 8, 9, 10, 10.5, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 22.5, 24]
    for n, b in zip([x for x in mel.split() if x != '-'], pos):
        ev.append((b, midi(n), 1, 76 if b % 3 == 0 else 66))
    ev += chord(24, 'F4 A4 C5 F5', 3, 70)
    return 138, ev, 27


def bright_start():
    """元气早晨（原创）：112 拍，D 大调，切分的流行钢琴和弦，旋律短促有精神。"""
    ev = []
    prog = [('D3', 'F#4 A4 D5'), ('A2', 'E4 A4 C#5'), ('B2', 'F#4 B4 D5'), ('G2', 'G4 B4 D5')]
    for bar in range(2):
        for i, (b, ch) in enumerate(prog):
            s = 16 * bar + 4 * i
            ev += [(s, midi(b), 1.5, 72), (s + 1.5, midi(b), 1, 64), (s + 2.5, midi(b) + 12, 1.5, 60)]
            ev += chord(s, ch, 0.4, 60) + chord(s + 1.5, ch, 0.4, 56) + chord(s + 2.5, ch, 0.9, 58)
    ev += seq(0, 'A5 - A5 B5 A5 F#5 - D5', 0.5, 86) + seq(4, 'E5 - E5 F#5 E5 C#5 - A4', 0.5, 82)
    ev += seq(8, 'F#5 - F#5 G5 F#5 D5 - B4', 0.5, 86) + seq(12, 'G5 - F#5 - E5 - D5 E5', 0.5, 82)
    ev += seq(16, 'A5 - A5 B5 A5 F#5 - D6', 0.5, 90) + seq(20, 'C#6 - B5 A5 - E5 F#5 A5', 0.5, 86)
    ev += seq(24, 'B5 - A5 F#5 - D5 E5 F#5', 0.5, 86) + seq(28, 'G5 - F#5 - E5', 0.5, 84) + seq(30, 'D5', 1, 90, beats=2)
    ev += chord(32, 'D2 D3 A3 F#4 A4 D5 F#5 A5 D6', 3, 80)
    return 112, ev, 35


# ---- public domain ----

def bach_prelude():
    """巴赫《平均律》第一首 C 大调前奏曲（BWV 846，1722）开头 4 小节。"""
    bars = [('C4', 'E4', 'G4 C5 E5'), ('C4', 'D4', 'A4 D5 F5'), ('B3', 'D4', 'G4 D5 F5'), ('C4', 'E4', 'G4 C5 E5')]
    ev = []
    for i, (low, mid, top) in enumerate(bars):
        for half in range(2):
            s = 4 * i + 2 * half
            ev.append((s, midi(low), 2, 58))
            ev.append((s + 0.25, midi(mid), 1.75, 54))
            ev += seq(s + 0.5, top + ' ' + top, 0.25, 62, beats=0.4)
    ev += chord(16, 'C3 C4 E4 G4 C5', 3, 60)
    return 66, ev, 19


def fur_elise():
    """贝多芬《致爱丽丝》（1810）开头主题。"""
    ev = seq(0, 'E5 D#5 E5 D#5 E5 B4 D5 C5', 0.5, 70) + [(4, midi('A4'), 1, 74)]
    ev += [(4, midi('A2'), 0.5, 52), (4.5, midi('E3'), 0.5, 46), (5, midi('A3'), 0.5, 48)]
    ev += seq(5.5, 'C4 E4 A4', 0.5, 62) + [(7, midi('B4'), 1, 70)]
    ev += [(7, midi('E2'), 0.5, 50), (7.5, midi('E3'), 0.5, 46), (8, midi('G#3'), 0.5, 48)]
    ev += seq(8.5, 'E4 G#4 B4', 0.5, 62) + [(10, midi('C5'), 1, 72)]
    ev += [(10, midi('A2'), 0.5, 52), (10.5, midi('E3'), 0.5, 46), (11, midi('A3'), 0.5, 48)]
    ev += seq(11.5, 'E4 E5 D#5 E5 D#5 E5 B4 D5 C5', 0.5, 70) + [(16, midi('A4'), 1, 74)]
    ev += [(16, midi('A2'), 0.5, 52), (16.5, midi('E3'), 0.5, 46), (17, midi('A3'), 0.5, 48)]
    ev += seq(17.5, 'C4 E4 A4', 0.5, 62) + [(19, midi('B4'), 1, 70)]
    ev += [(19, midi('E2'), 0.5, 50), (19.5, midi('E3'), 0.5, 46), (20, midi('G#3'), 0.5, 48)]
    ev += seq(20.5, 'E4 C5 B4', 0.5, 62) + [(22, midi('A4'), 2.5, 72)]
    ev += [(22, midi('A2'), 2.5, 52), (22.5, midi('E3'), 2, 46), (23, midi('A3'), 1.5, 48)]
    return 150, ev, 25


def turkish_march():
    """莫扎特《土耳其进行曲》（K.331 第三乐章，1783）开头主题，轻快有劲。"""
    ev = seq(0, 'B4 A4 G#4 A4', 0.25, 72) + [(1, midi('C5'), 1, 80)]
    ev += seq(2, 'D5 C5 B4 C5', 0.25, 72)
    ev += [(3, midi('E5'), 1, 80)]
    ev += seq(4, 'F5 E5 D#5 E5 B5 A5 G#5 A5 B5 A5 G#5 A5', 0.25, 76) + [(7, midi('C6'), 1, 86)]
    ev += seq(8, 'A5 C6', 0.5, 80) + seq(9, 'B5 A5 G5 A5', 0.5, 76) + seq(11, 'B5 A5 G5 A5', 0.5, 76)
    ev += seq(13, 'B5 A5 G5 F#5', 0.5, 76) + [(15, midi('E5'), 1, 82)]
    for b, root, ch in ((1, 'A3', 'C4 E4'), (2, 'A3', 'C4 E4'), (3, 'A3', 'C4 E4'), (5, 'A3', 'C4 E4'),
                        (6, 'A3', 'C4 E4'), (7, 'A3', 'C4 E4'), (8, 'A3', 'C4 E4'), (9, 'E3', 'B3 E4'),
                        (10, 'A3', 'C4 E4'), (11, 'E3', 'B3 E4'), (12, 'A3', 'C4 E4'), (13, 'E3', 'B3 E4'),
                        (14, 'B2', 'B3 D#4'), (15, 'E3', 'G#3 B3 E4')):
        ev.append((b, midi(root), 0.5, 56))
        ev += chord(b + 0.5, ch, 0.4, 50)
    return 116, ev, 17


PIECES = [('1-晨曦（原创）', morning_light), ('2-早安圆舞曲（原创）', morning_waltz), ('3-元气早晨（原创）', bright_start),
          ('4-巴赫C大调前奏曲', bach_prelude), ('5-致爱丽丝', fur_elise), ('6-土耳其进行曲', turkish_march)]
