"""[xiaozhi-hub] 识别出来的一句话要不要交给大模型（设计画布第 52 块）。

设备在 hello 里声明 features.turn_filter（和它的唤醒词 features.wake_words）时才用：
- 只有唤醒词（唤醒后又喊了一遍「小麦小麦」）→ "wake"：设备叮一声接着听；
- 开头带着唤醒词（「小麦小麦今天天气」）→ 去掉唤醒词再问；
- 只有「嗯」「好的」「哦」这类应答 → "filler"：不算问题，设备接着听；
- 「谢谢」「好了」「没事了」→ "close"：设备收尾、关麦；
- 接着听（follow_up）且开了「只听刚才说话的人」，声纹认出是别人 → "other_speaker"。
都不调大模型。
"""
import re

PUNCT = re.compile(r"[\s，,。．.!！?？、~～…·:：;；\"'“”‘’（）()\[\]【】<>《》-]+")
FILLERS = {"嗯", "嗯嗯", "嗯嗯嗯", "恩", "哦", "噢", "喔", "哦哦", "啊", "呃", "额", "唉", "哎", "诶", "欸",
           "好", "好的", "好吧", "好呀", "好嘞", "行", "行吧", "可以", "对", "对对", "对的", "是", "是的", "嗯好",
           "哈哈", "哈哈哈", "呵呵", "嘿嘿", "ok", "okay", "嗯ok", "收到", "明白", "了解"}
CLOSERS = {"谢谢", "谢谢你", "谢谢啦", "谢啦", "谢了", "多谢", "好了", "好了谢谢", "好的谢谢", "行了", "没事了", "没事",
           "不用了", "不用", "知道了", "我知道了", "好的知道了", "就这样", "就这样吧", "先这样", "嗯谢谢",
           "谢谢小麦", "好的谢谢你", "ok谢谢"}
DEFAULT_WAKE = ("小麦小麦",)


def norm(text: str) -> str:
    return PUNCT.sub("", str(text or "")).lower()


def _wake_forms(wake_words, halves=False) -> list:
    """唤醒词本身；halves：叠词的一半（「小麦小麦」→「小麦」）。长的先比。
    开头只去掉完整的唤醒词：「小麦粉怎么做」的「小麦」不是在叫它。"""
    forms = set()
    for w in list(wake_words or []) + list(DEFAULT_WAKE):
        w = norm(w)
        if len(w) >= 2:
            forms.add(w)
            half = len(w) // 2
            if halves and len(w) % 2 == 0 and w[:half] == w[half:] and half >= 2:
                forms.add(w[:half])
    return sorted(forms, key=len, reverse=True)


def strip_wake(text: str, wake_words) -> tuple:
    """去掉开头的唤醒词（中间夹着标点也行）：(剩下的原文, 去掉过没有)。"""
    forms = _wake_forms(wake_words)
    if not forms:
        return text, False
    alt = "|".join("".join(re.escape(ch) + r"[\s，,。.!！?？、]*" for ch in f) for f in forms)
    m = re.match(rf"^[\s，,。.!！?？、]*(?:(?:{alt}))+", str(text or ""), re.I)
    if not m:
        return text, False
    return text[m.end():].lstrip(" ，,。.!！?？、"), True


def judge(text: str, wake_words=(), follow_up=False) -> tuple:
    """(结论, 要交给大模型的话)。结论 None 表示照常交给大模型。"""
    rest, had_wake = strip_wake(text, wake_words)
    n = norm(rest)
    # 整句只剩唤醒词（叠词的一半也算：「小麦」「小麦小麦小麦」）
    left = n
    for f in _wake_forms(wake_words, halves=True):
        left = left.replace(f, "")
    if n and not left and len(n) <= 16:
        return "wake", ""
    if had_wake and not n:
        return "wake", ""
    if n in CLOSERS:
        return "close", ""
    if n in FILLERS:
        return "filler", ""
    return None, rest if had_wake else text


def other_speaker(follow_up: bool, lock: bool, first_speaker, speaker) -> bool:
    """接着听、开了「只听刚才说话的人」、刚才那个人认得出来：这句不是他说的（认不出是谁也算）。"""
    return bool(follow_up and lock and first_speaker and (speaker or "") != first_speaker)
