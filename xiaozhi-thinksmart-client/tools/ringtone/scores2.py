"""Second batch of alarm piano pieces (same format as scores.py)."""
from scores import chord, midi, seq


# ---- originals ----

def coffee_time():
    """咖啡时光（原创）：84 拍、摇摆的爵士钢琴，Dm9 → G13 → Cmaj9 → A7(b9)，右手慵懒的短句。"""
    ev = []
    sw = lambda b: b + (0.17 if b % 1 == 0.5 else 0)   # swung eighths
    prog = [('D3', 'F4 A4 C5 E5'), ('G2', 'F4 B4 E5'), ('C3', 'E4 G4 B4 D5'), ('A2', 'G4 C#5 F5 Bb4')]
    for rnd in range(2):
        for i, (root, ch) in enumerate(prog):
            s = 16 * rnd + 4 * i
            ev += [(s, midi(root), 2, 64), (s + 2, midi(root) + 7, 1.5, 54)]
            ev += chord(sw(s + 0.5), ch, 1.2, 50) + chord(sw(s + 2.5), ch, 1.0, 46)
    lick = [(0, 'A5'), (0.5, 'F5'), (1, 'E5'), (1.5, 'D5'), (2.5, 'C5'), (3, 'A4'),
            (4, 'B4'), (4.5, 'D5'), (5, 'F5'), (5.5, 'A5'), (6, 'G5'), (8, 'E5'), (8.5, 'G5'), (9, 'B5'),
            (10, 'A5'), (11, 'G5'), (12, 'F5'), (12.5, 'E5'), (13, 'C#5'), (13.5, 'E5'), (14, 'G5')]
    for rnd, vel in ((0, 70), (16, 74)):
        ev += [(sw(rnd + b), midi(n), 0.6, vel + (6 if b % 2 == 0 else 0)) for b, n in lick]
    ev += [(32, midi('D2'), 4, 64)] + chord(32, 'F4 A4 C5 E5 A5', 4, 62)
    return 84, ev, 36


def morning_walk():
    """清晨散步（原创）：G 大调的抒情流行钢琴，G → D/F# → Em → C，旋律像哼着歌走在路上。"""
    ev = []
    bass = ['G2', 'F#2', 'E2', 'C2', 'G2', 'F#2', 'E2', 'D2']
    up = ['D3 G3 B3', 'D3 A3 D4', 'E3 G3 B3', 'C3 G3 E4', 'D3 G3 B3', 'D3 A3 D4', 'E3 G3 B3', 'D3 A3 C4']
    for i, (b, u) in enumerate(zip(bass, up)):
        s = 4 * i
        ev.append((s, midi(b), 3.5, 60))
        ev += seq(s + 0.5, u + ' ' + u.split()[1], 0.5, 46, beats=1.5)
    ev += seq(0, 'B4 D5 G5 F#5', 1, 72, beats=1.2) + seq(4, 'A5 - F#5 D5', 1, 70, beats=1.5)
    ev += seq(8, 'G5 B5 A5 G5', 1, 74, beats=1.2) + seq(12, 'E5 - - D5', 1, 68, beats=2)
    ev += seq(16, 'B4 D5 G5 B5', 1, 74, beats=1.2) + seq(20, 'A5 D6 C6 A5', 1, 76, beats=1.2)
    ev += seq(24, 'B5 G5 E5 G5', 1, 74, beats=1.2) + seq(28, 'F#5 - A5', 1, 72, beats=1.5)
    ev += [(32, midi('G2'), 4, 64)] + chord(32, 'D4 G4 B4 D5 G5', 4, 66)
    return 92, ev, 36


def sunny_hop():
    """阳光小跳跃（原创）：120 拍，左手跨跳（低音—和弦交替），右手断奏的跳音旋律，有点像拉格泰姆。"""
    ev = []
    lh = [('C3', 'E3 G3 C4'), ('G2', 'E3 G3 C4'), ('F2', 'F3 A3 C4'), ('C3', 'E3 G3 C4'),
          ('D3', 'F3 A3 C4'), ('G2', 'F3 G3 B3'), ('C3', 'E3 G3 C4'), ('G2', 'F3 G3 B3')]
    for i, (b, ch) in enumerate(lh):
        ev += [(2 * i, midi(b), 0.45, 66)] + chord(2 * i + 1, ch, 0.4, 54)
    ev += seq(0, 'E5 G5 C6 G5 E5 G5 - G5', 0.5, 80, beats=0.3)
    ev += seq(4, 'A5 C6 F6 C6 A5 G5 E5 C5', 0.5, 80, beats=0.3)
    ev += seq(8, 'D5 F5 A5 F5 B5 A5 G5 F5', 0.5, 80, beats=0.3)
    ev += seq(12, 'E5 G5 C6 E6', 0.5, 84, beats=0.3) + seq(14, 'D6 B5 G5 F5', 0.5, 82, beats=0.3)
    ev += [(16, midi('C3'), 2, 74)] + chord(16, 'E4 G4 C5 E5 G5 C6', 2, 78)
    return 120, ev, 19


def starlight():
    """星光（原创）：E 大调五声音阶，高音区像星星一闪一闪，下面长长的和弦垫着，最安静的一首。"""
    ev = []
    for s, ch in ((0, 'E2 B2 E3 G#3'), (6, 'C#2 G#2 C#3 E3'), (12, 'A1 E2 A2 C#3'), (18, 'B1 F#2 B2 D#3')):
        ev += chord(s, ch, 6, 46)
    sparkle = [(0, 'B5'), (1, 'G#5'), (1.5, 'E6'), (3, 'C#6'), (4, 'B5'), (6, 'G#5'), (7, 'C#6'), (7.5, 'E6'),
               (9, 'F#6'), (10, 'E6'), (12, 'C#6'), (13, 'B5'), (13.5, 'E6'), (15, 'G#6'), (16, 'F#6'),
               (18, 'D#6'), (19, 'B5'), (19.5, 'F#6'), (21, 'E6'), (22, 'D#6')]
    ev += [(b, midi(n), 2.5, 64 + (8 if b % 3 == 0 else 0)) for b, n in sparkle]
    ev += chord(24, 'E2 B2 E3 G#3 B3', 6, 50) + seq(24, 'E5 G#5 B5 E6', 0.5, 66, beats=5)
    return 76, ev, 29


# ---- public domain ----

def canon():
    """帕赫贝尔《D 大调卡农》（约 1680–1706）：那条著名的低音进行和上面的旋律。"""
    ev = []
    bass = ['D2', 'A1', 'B1', 'F#1', 'G1', 'D1', 'G1', 'A1']
    chords = ['F#3 A3 D4', 'E3 A3 C#4', 'F#3 B3 D4', 'F#3 A3 C#4', 'G3 B3 D4', 'F#3 A3 D4', 'G3 B3 D4', 'E3 A3 C#4']
    for rnd in range(2):
        for i, (b, ch) in enumerate(zip(bass, chords)):
            s = 16 * rnd + 2 * i
            ev.append((s, midi(b) + 12, 2, 60))
            ev += seq(s, ch, 0.5, 44, beats=1.6) + [(s + 1.5, midi(ch.split()[1]), 0.5, 42)]
    ev += seq(0, 'F#5 E5 D5 C#5 B4 A4 B4 C#5', 2, 70)
    ev += seq(16, 'F#5 E5 D5 C#5 B4 A4 B4 C#5', 2, 74) + seq(16, 'D5 C#5 B4 A4 G4 F#4 G4 E4', 2, 62)
    ev += [(32, midi('D2'), 4, 62)] + chord(32, 'A3 D4 F#4 A4 D5', 4, 64)
    return 72, ev, 36


def morning_mood():
    """格里格《培尔·金特》组曲〈晨景〉（1875）开头：太阳慢慢升起的那段旋律，6/8 拍。"""
    e = 0.5   # an eighth in 6/8 (beats are quarters here)
    mel = ['G5', 'E5', 'D5', 'C5', 'D5', 'E5', 'G5', 'E5', 'D5', 'C5', 'D5', 'E5',
           'D5', 'E5', 'G5', 'E5', 'G5', 'A5', 'E5', 'A5', 'G5', 'E5', 'D5', 'C5']
    ev = []
    for i, n in enumerate(mel):
        ev.append((i * e, midi(n), 0.9 if i % 6 == 0 else 0.5, 70 + (6 if i % 3 == 0 else 0)))
    ev.append((len(mel) * e, midi('C5'), 3, 68))
    # second time an octave up, brighter (the sun is out)
    off = len(mel) * e + 3
    for i, n in enumerate(mel):
        ev.append((off + i * e, midi(n) + 12, 0.9 if i % 6 == 0 else 0.5, 74 + (6 if i % 3 == 0 else 0)))
    ev += chord(off + len(mel) * e, 'C4 G4 C5 E5 G5 C6', 3.5, 74)
    for s in range(0, int(off + len(mel) * e), 3):
        ev += chord(s, 'C3 G3 E4', 3, 44) if (s // 3) % 4 != 2 else chord(s, 'F2 C3 A3', 3, 44)
    return 100, ev, off + len(mel) * e + 4


def ode_to_joy():
    """贝多芬第九交响曲〈欢乐颂〉（1824）主题，钢琴改编，第二遍加和声。"""
    tune = 'E5 E5 F5 G5 G5 F5 E5 D5 C5 C5 D5 E5'
    ev = seq(0, tune, 1, 74)
    ev += [(12, midi('E5'), 1.5, 76), (13.5, midi('D5'), 0.5, 70), (14, midi('D5'), 2, 72)]
    ev += seq(16, tune, 1, 78)
    ev += [(28, midi('D5'), 1.5, 76), (29.5, midi('C5'), 0.5, 70), (30, midi('C5'), 2, 74)]
    for s, ch in ((0, 'C3 G3 E4'), (4, 'G2 G3 B3'), (8, 'C3 G3 E4'), (12, 'G2 F3 B3'),
                  (16, 'C3 G3 C4 E4'), (20, 'G2 D3 G3 B3'), (24, 'A2 E3 A3 C4'), (28, 'G2 F3 B3'), (30, 'C3 G3 C4 E4')):
        ev += chord(s, ch, 2 if s >= 28 else 4, 52 if s < 16 else 58)
    ev += seq(16, 'C4 C4 D4 E4 E4 D4 C4 B3 A3 A3 B3 C4', 1, 54)
    return 108, ev, 33


def twinkle():
    """莫扎特《小星星变奏曲》（K.265，1781）主题：一闪一闪亮晶晶，左手换成跑动的伴奏。"""
    tune = 'C5 C5 G5 G5 A5 A5 G5 - F5 F5 E5 E5 D5 D5 C5 -'
    ev = []
    for b, n in enumerate(tune.split()):
        if n != '-':
            ev.append((b, midi(n), 1 if tune.split()[min(b + 1, 15)] != '-' else 2, 76))
    lh = ['C3 G3 E4 G3', 'C3 G3 E4 G3', 'F3 A3 C4 A3', 'C3 G3 E4 G3', 'D3 F3 B3 F3', 'C3 G3 E4 G3', 'G2 F3 B3 F3', 'C3 G3 C4 G3']
    for i, pat in enumerate(lh):
        ev += seq(2 * i, pat, 0.5, 50)
    # the first variation's running sixteenths, once, to finish
    run = 'D6 C6 B5 C6 B5 C6 B5 C6 A5 G5 F#5 G5 F#5 G5 F#5 G5'
    ev += seq(16, run, 0.25, 70, beats=0.3)
    ev += [(20, midi('C3'), 3, 64)] + chord(20, 'E4 G4 C5 E5 G5 C6', 3, 72)
    return 112, ev, 23


PIECES = [('7-咖啡时光（原创）', coffee_time), ('8-清晨散步（原创）', morning_walk), ('9-阳光小跳跃（原创）', sunny_hop),
          ('10-星光（原创）', starlight), ('11-帕赫贝尔卡农', canon), ('12-格里格晨景', morning_mood),
          ('13-欢乐颂', ode_to_joy), ('14-小星星变奏曲', twinkle)]


def reminder_chime():
    """提醒提示音：一个慢慢琶开的 Cmaj9 和弦（和「咖啡时光」同一架钢琴），约 2 秒，后面接要念的话。"""
    ev = [(0, midi('C3'), 3, 62)]
    ev += [(0.12 * i, midi(n), 3 - 0.12 * i, 66 + 3 * i) for i, n in enumerate('G3 E4 B4 D5 G5'.split())]
    return 100, ev, 3.2


CHIMES = [('提醒提示音', reminder_chime)]
