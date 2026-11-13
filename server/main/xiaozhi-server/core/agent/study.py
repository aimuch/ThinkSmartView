"""学习陪伴（设计画布第 48 块）：设备每分钟连拍 3 张，这里交给后台选好的看图模型判断孩子的坐姿和在做什么。

只回一个小 JSON（有没有人、坐姿、是不是离太近、在做什么、专不专心、一句话），照片不保存。
要不要提醒、提醒什么由设备决定（连续几次、冷却时间都在设备上）。
提示词和判断规则改写自 fubin0478/thinksmart-view 的 device/study/thinksmart-study.py（MIT）。
"""
import json
import re

from core.agent import photo_ask

MAX_FRAMES = 4
ACTIVITIES = ("studying", "reading", "phone", "playing", "daydreaming", "sleeping", "absent", "other")
POSTURES = ("good", "slouch", "lying", "tilted", "unknown")

PROMPT = """你是陪伴孩子学习的助手，正在通过书桌上正对孩子的摄像头查看孩子的学习状态。
{frames}只输出一个 JSON 对象，不要输出任何其他文字：
{{"present": 书桌前有没有孩子(true/false),
 "posture": "good"(坐姿端正) / "slouch"(明显驼背、弯腰) / "lying"(趴在桌上) / "tilted"(身体或头明显歪斜) / "unknown"(看不清),
 "too_close": 眼睛离书本或桌面明显过近(少于一拳到一尺)时为 true,
 "activity": "studying"(写作业) / "reading"(看书) / "phone"(看或玩手机、平板) / "playing"(玩玩具或其他东西) / "daydreaming"(发呆、东张西望) / "sleeping"(闭眼、睡着) / "absent"(人不在) / "other",
 "focused": 是否在专心学习(true/false),
 "note": 不超过 20 个字描述你看到的情况}}
判断要保守：不要把拿文具、正常低头写字、短暂抬头或相机偏低造成的透视当成问题。
只有能清楚辨认手机/玩具并持续使用时才判 phone/playing；不要猜测看不清的物体。
看不清、画面全黑或被挡住时用 present=false、activity=absent，不要假装确认了正在学习。
单目画面不能精确测量厘米；只有眼睛和书本都清楚且明显贴近时才判 too_close。"""

FRAMES_ONE = "只根据这张图片判断。\n"
FRAMES_MANY = ("下面是按时间先后、每隔约 {gap} 秒连拍的 {n} 张照片。综合这几张判断孩子这段时间的状态："
               "只有在大多数照片里都持续出现的情况才算（例如每张都在玩手机、每张都趴着）；"
               "只在一张里出现的一瞬间动作（拿起文具、抬头、伸手、转头）不算问题。\n")


def build_prompt(n: int, gap: float) -> str:
    return PROMPT.format(frames=FRAMES_MANY.format(n=n, gap=round(gap)) if n > 1 else FRAMES_ONE)


def parse_result(text: str) -> dict:
    """看图模型的回答 → 规整的结果；不是 JSON 时抛 ValueError。"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("看图模型没按格式回答")
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        raise ValueError("看图模型没按格式回答")
    if not isinstance(raw, dict):
        raise ValueError("看图模型没按格式回答")
    present = raw.get("present") is True
    activity = raw.get("activity") if raw.get("activity") in ACTIVITIES else "other"
    if not present:
        activity = "absent"
    return {
        "present": present,
        "posture": raw.get("posture") if present and raw.get("posture") in POSTURES else "unknown",
        "too_close": present and raw.get("too_close") is True,
        "activity": activity,
        "focused": present and raw.get("focused") is True and activity in ("studying", "reading"),
        "note": str(raw.get("note") or "").strip()[:40],
    }


def check(merged: dict, images: list, gap: float = 4.0, device: str = None) -> dict:
    """同步：几张连拍 → 结果。看图模型出错抛 photo_ask.PhotoError，格式不对抛 ValueError。"""
    images = [i for i in images if i][:MAX_FRAMES]
    if not images:
        raise ValueError("没有照片")
    text = photo_ask.ask_vision_images(merged, build_prompt(len(images), gap), images, device, purpose="study")
    return parse_result(text)
