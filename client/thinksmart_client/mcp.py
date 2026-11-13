"""Device tools the hub's models may call (MCP over the xiaozhi channel): speaker volume, sleep hours,
screen brightness, closing a photo slideshow, countdowns / alarms / reminders and their volume.

For alarms the model passes what the user meant (a duration, or a clock time with a date, a repeat
rule, what it is about); the device works out the time point and answers with it for the model to
repeat (alarms.py).

Also the self.home.* Mijia tools (mijia/tools.py) and self.screen.show_card: rich
content (a recipe, a list, a table, pictures) shown as a message in the conversation while the
reply itself stays one short spoken sentence."""
import asyncio
import json
import logging
import re
import time

from . import study
from .mijia import tools as home_tools

log = logging.getLogger(__name__)

TOOLS = [
    {
        'name': 'self.audio_speaker.set_volume',
        'description': '设置设备扬声器的音量。用户说「声音大一点」「音量调到 50」这类话时调用；'
                       '相对调整时先用 get_volume 读当前音量。',
        'inputSchema': {'type': 'object',
                        'properties': {'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
                        'required': ['volume']},
    },
    {
        'name': 'self.audio_speaker.get_volume',
        'description': '读取设备扬声器当前的音量（0-100）。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.sleep_mode.set',
        'description': '设置设备的睡眠时段：时段内后台任务做完的汇报不主动念，只在屏幕上提示，'
                       '等用户点一下或唤醒再念；屏幕调暗；唤醒和对话照常，音量不变。'
                       '用户说「晚上十点到早上七点别念了」时 enabled=true, start="22:00", end="07:00"；'
                       '说「关掉睡眠模式」时 enabled=false。时间用 24 小时制 HH:MM，可以跨午夜；只改开关时不用传时间。',
        'inputSchema': {'type': 'object',
                        'properties': {'enabled': {'type': 'boolean'},
                                       'start': {'type': 'string', 'description': '开始时间 HH:MM'},
                                       'end': {'type': 'string', 'description': '结束时间 HH:MM'}},
                        'required': ['enabled']},
    },
    {
        'name': 'self.sleep_mode.get',
        'description': '读取睡眠时段的设置，以及现在是否在睡眠时段内。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.screen.set_brightness',
        'description': '调节设备屏幕亮度，两个参数只传一个。亮度 0-100：0 是最暗（仍看得清），100 最亮。'
                       '「屏幕亮一点」change=20，「暗一点」change=-20（「再亮一点」同样传 change，不用先读）；'
                       '「调到最亮」brightness=100，「调到最暗」brightness=0，「亮度调到 60」brightness=60。'
                       '设备没有自动亮度。调过之后一直保持；睡眠时段屏幕会自动调暗，时段内调的亮度用到这次时段结束。',
        'inputSchema': {'type': 'object',
                        'properties': {'brightness': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                                       'change': {'type': 'integer', 'minimum': -100, 'maximum': 100,
                                                  'description': '在当前亮度上加减'}}},
    },
    {
        'name': 'self.screen.get_brightness',
        'description': '读取屏幕亮度：level 当前亮度 0-100，dimmed 是否因睡眠时段调暗。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.device.performance',
        'description': '用户问小麦这台设备自己的状态时调用：「设备温度多少」「性能怎么样」「CPU 占多少」「内存够不够」「怎么这么烫」「是不是卡了」。'
                       '会在屏幕上打开性能页，并返回一句话（CPU、最热温度、内存）。直接念返回的那句，不要编数字。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.screen.close_photos',
        'description': '关掉屏幕上正在轮播的照片，回到对话界面。用户说「关掉照片」「不看了」「把照片关了」时调用。'
                       '照片是设备上别的程序放上去的，这个工具只能关，不能打开或找照片。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.alarm.create',
        'description': '新建倒计时、闹钟或提醒，设备到点响铃。只传用户的意思，具体几点由设备算好返回，'
                       '你照返回的 when（和 repeat）跟用户复述一遍，例如「好的，明天早上 7 点叫你」。\n'
                       'kind：「倒计时 5 分钟」「计时 3 分钟」是 timer；「明早 7 点叫我」「定个 6 点半的闹钟」是 alarm；'
                       '「10 分钟后提醒我晾衣服」「下午 3 点提醒我开会」是 reminder（到点念 say）。\n'
                       '时间二选一：多久以后传 hours/minutes/seconds（「一个半小时后」hours=1, minutes=30）；'
                       '某个钟点传 time（24 小时制 HH:MM，「早上 7 点」07:00、「晚上 7 点」19:00），'
                       '是哪天用 days_from_today（今天 0、明天 1、后天 2）或 date（YYYY-MM-DD），都不传就是最近的那个钟点。'
                       '用户没说早上还是晚上、又猜不出时先问清楚。\n'
                       '循环用 repeat：daily 每天；workdays 工作日（只按周一到周五算，不管法定节假日和调休）；'
                       'weekly 每周几，配 weekdays（1 是周一……7 是周日，可多选）；monthly 每月几号，配 month_day'
                       '（这个月没有这一天时在当月最后一天响）。循环的必须传 time，不传日期。\n'
                       'label 是屏幕上显示的简短内容（「晾衣服」「开会」），没说就不传；'
                       'say 是提醒到点念的一句话（「该晾衣服了」），只用于 reminder，不传就念「提醒你，<label>」。',
        'inputSchema': {'type': 'object',
                        'properties': {'kind': {'type': 'string', 'enum': ['timer', 'alarm', 'reminder']},
                                       'hours': {'type': 'number'}, 'minutes': {'type': 'number'},
                                       'seconds': {'type': 'number'},
                                       'time': {'type': 'string', 'description': 'HH:MM，24 小时制'},
                                       'days_from_today': {'type': 'integer', 'minimum': 0},
                                       'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                                       'repeat': {'type': 'string', 'enum': ['daily', 'workdays', 'weekly', 'monthly']},
                                       'weekdays': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1, 'maximum': 7}},
                                       'month_day': {'type': 'integer', 'minimum': 1, 'maximum': 31},
                                       'label': {'type': 'string'}, 'say': {'type': 'string'}},
                        'required': ['kind']},
    },
    {
        'name': 'self.alarm.list',
        'description': '列出设备上还没响的倒计时、闹钟和提醒（「我有哪些闹钟」「明天几点叫我」），'
                       '每条有 id、type、when（下次响的时间）、in（还有多久）、repeat、label；还有最近错过的条数。'
                       '取消某一条前先用它找到 id。',
        'inputSchema': {'type': 'object',
                        'properties': {'kind': {'type': 'string', 'enum': ['timer', 'alarm', 'reminder'],
                                                'description': '只看这一种，不传就全部'}}},
    },
    {
        'name': 'self.alarm.cancel',
        'description': '取消倒计时、闹钟或提醒：传 id（先用 self.alarm.list 找到）；用户明确说「全部取消」时传 all=true，'
                       '可以配 kind 只取消这一种。用户说的那条不确定是哪个时先问清楚，不要猜。正在响的也能取消。',
        'inputSchema': {'type': 'object',
                        'properties': {'id': {'type': 'integer'}, 'all': {'type': 'boolean'},
                                       'kind': {'type': 'string', 'enum': ['timer', 'alarm', 'reminder']}}},
    },
    {
        'name': 'self.alarm.snooze',
        'description': '再睡一会儿：用户叫停刚响的闹钟后说「再睡 10 分钟」「过 5 分钟再叫我」时调用，'
                       '让正在响或 15 分钟内响过的那一条过 minutes 分钟（默认 10）再响一次，内容和循环都不变。'
                       '返回 success=false 表示没有这样的一条，这时问用户要不要设一个几分钟后的闹钟。',
        'inputSchema': {'type': 'object',
                        'properties': {'minutes': {'type': 'integer', 'minimum': 1, 'maximum': 60}}},
    },
    {
        'name': 'self.alarm.set_volume',
        'description': '设置闹钟音量（倒计时、闹钟、提醒响铃的音量，和平时的设备音量分开），两个参数只传一个：'
                       '「闹钟声音大一点」change=10，「闹钟音量调到 80」volume=80。平时说话的音量用 self.audio_speaker.set_volume。',
        'inputSchema': {'type': 'object',
                        'properties': {'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                                       'change': {'type': 'integer', 'minimum': -100, 'maximum': 100}}},
    },
    {
        'name': 'self.alarm.get_volume',
        'description': '读取闹钟音量（0-100）。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]

WAKE_TOOLS = [
    {
        'name': 'self.wake_word.update',
        'description': '查看或修改设备的唤醒词（叫醒设备说的词，如「小麦小麦」）。'
                       '「现在有哪些唤醒词」action=list；「唤醒词加一个小天小天」action=add, word="小天小天"；'
                       '「把哈喽小麦删掉」action=remove, word="哈喽小麦"；「唤醒词恢复默认」action=reset。'
                       'word 只写中文的词本身，不带引号和标点；至少三个字，四个字最好。改完马上生效。'
                       '返回 success=false 时把 reason 告诉用户；有 warning 也提一句。'
                       '这里只改唤醒词，不改设备自称的名字。',
        'inputSchema': {'type': 'object',
                        'properties': {'action': {'type': 'string', 'enum': ['list', 'add', 'remove', 'reset']},
                                       'word': {'type': 'string', 'description': '要加或删的唤醒词（中文）'}},
                        'required': ['action']},
    },
]

CARD_TOOLS = [
    {
        'name': 'self.screen.show_card',
        'description': '把适合看的内容做成一条图文消息，显示在屏幕的对话里：菜谱、做法步骤、清单、对比表格、行程、'
                       '有图片的介绍。完整内容放进 markdown，嘴上只说一句简短的话（如「菜谱放在屏幕上了，一共 5 步」），'
                       '不要把内容念出来。markdown 支持标题、有序/无序列表、表格、粗体、引用；'
                       'images 放图片的 https 链接（只放你确定存在的链接，没有就不传）。',
        'inputSchema': {'type': 'object',
                        'properties': {'title': {'type': 'string'},
                                       'markdown': {'type': 'string'},
                                       'images': {'type': 'array', 'items': {'type': 'string'}}},
                        'required': ['title', 'markdown']},
    },
]
BROADCAST_TOOLS = [
    {
        'name': 'self.weather_broadcast.update',
        'description': '查看或修改天气定时播报（到点设备自己念住宅地址的天气）。'
                       '「每天早上七点半播报天气」enabled=true, add_time="07:30"；「晚上九点也播一下」add_time="21:00"；'
                       '「不要九点那次了」remove_time="21:00"；「工作日才播」days="workdays"；「关掉天气播报」enabled=false；'
                       '「早上顺便说一下新闻」news=true；「顺便说今天的提醒」reminders=true。只查看就什么参数都不传。'
                       '14 点以后的播报讲明天的天气。改完用一句话复述 text。',
        'inputSchema': {'type': 'object',
                        'properties': {'enabled': {'type': 'boolean'},
                                       'add_time': {'type': 'string', 'description': '加一个播报时间，24 小时制 HH:MM'},
                                       'remove_time': {'type': 'string', 'description': '删掉一个播报时间 HH:MM'},
                                       'days': {'type': 'string', 'enum': ['daily', 'workdays', 'weekend']},
                                       'reminders': {'type': 'boolean', 'description': '中午前的播报顺便说今天的闹钟和提醒'},
                                       'news': {'type': 'boolean', 'description': '中午前的播报顺便说三条新闻'}}},
    },
    {
        'name': 'self.location.set_home',
        'description': '设置住宅地址（设备所在地）：问天气没说地点时就查这里，天气播报也播这里。'
                       '用户说「住宅地址改成杭州西湖区」「我家在浦东新区」「我搬到苏州了」时调用，place 写用户说的地名。'
                       '只查看现在的地址时不传 place。返回 success=false 时把 reason 告诉用户。',
        'inputSchema': {'type': 'object',
                        'properties': {'place': {'type': 'string', 'description': '地名，国内写省市区，如 杭州市西湖区；国外写英文名'}}},
    },
]
PHOTO_TOOLS = [
    {
        'name': 'self.photo.ask',
        'description': '用户想让你看一样东西时调用：「帮我看下这是什么」「这个字怎么读」「看看这道题」「这个能吃吗」。'
                       '每次都要调用、重新拍一张：之前拍过的照片（哪怕刚拍过、拍得不清楚）不能拿来回答这一次。'
                       '有机身摄像头时屏幕上出现摄像头画面，倒数后自动拍；没有时出现二维码，用手机扫码拍。看完后答案会自动送回对话。'
                       'question 写用户想问的（没说就写「这是什么」）。调用后只说工具返回的那一句，'
                       '不要猜照片内容。返回 success=false 时把 reason 告诉用户。',
        'inputSchema': {'type': 'object', 'properties': {'question': {'type': 'string'}}},
    },
    {
        'name': 'self.photo.cancel',
        'description': '用户说「不拍了」「算了」时，关掉屏幕上等手机拍照的二维码。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]
LOOKS_TOOLS = [
    {
        'name': 'self.appearance.set',
        'description': '换设备首页的主题、布局或对话形象。主题：星夜（默认深蓝）、晨光（浅色）、薰衣草（浅紫）、森林（深绿）、童趣（粉蓝）、'
                       '纯黑（最暗）、跟着时间（白天晨光、晚上星夜）。形象：光球（默认），或形象库里的角色，按用户说的名字原样传，'
                       '例如「换成星星人」avatar="星星人"、「换成 LABUBU 春节的」avatar="LABUBU 春节"、「换成 Claude 的形象」avatar="Claude"；'
                       '有好几款对得上时会返回候选，念给用户挑。「换成宝宝的形象」这类用照片生成的，传 avatar="photo"（用最新生成的那个）。'
                       '「换回光球」avatar="光球"；「换个亮一点的主题」theme="晨光"。布局 layout：对话（光球和对话记录）、'
                       '仪表盘（一屏看天气、待办、米家设备、场景；「换成仪表盘」layout="仪表盘"）。只查看就什么都不传。',
        'inputSchema': {'type': 'object',
                        'properties': {'theme': {'type': 'string'}, 'avatar': {'type': 'string'}, 'layout': {'type': 'string'}}},
    },
]
KIDS_TOOLS = [
    {
        'name': 'self.kids_mode.get',
        'description': '查看儿童模式：开没开、孩子几岁、每天最多聊多久、今天聊了多久、几点以后不陪聊。'
                       '用户说「打开儿童模式」时带 enable=true 打开。关掉儿童模式只能在设置页（防止孩子自己关），'
                       '有人说「关掉儿童模式」时告诉他去设置页关。',
        'inputSchema': {'type': 'object', 'properties': {'enable': {'type': 'boolean'}}},
    },
]
COOK_TOOLS = [
    {
        'name': 'self.cooking.start',
        'description': '进入做菜模式：把屏幕上最新那张有编号步骤的菜谱卡片一步一屏显示、一步一步念。用户说「开始做菜」'
                       '「一步一步教我做」时调用（先用 self.screen.show_card 给出带编号步骤的菜谱）。进入后用户直接说'
                       '「下一步」「上一步」「再说一遍」「开始计时」「退出做菜」就行，不用叫醒；调用后只说一句「好，我们开始」。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]
DAILY_TOOLS = [
    {
        'name': 'self.scene.save',
        'description': '保存一个场景口令：用户说「以后我说『我出门了』，就把灯和空调都关了，再说一下天气」时调用，'
                       'name="我出门了"，steps 用一句话写清楚要做的事（「关掉所有灯和空调，播报今天的天气」）。同名的会覆盖。',
        'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'steps': {'type': 'string'}},
                        'required': ['name', 'steps']},
    },
    {
        'name': 'self.scene.run',
        'description': '用户说的话正好是一个已保存的场景口令（如「我出门了」「我睡了」）时先调用它，拿到这个场景的步骤后，'
                       '用你已有的工具一步一步做完（米家 self.home.control、助眠声 self.media.play、天气 get_weather 等），'
                       '最后用一句话说做了什么。name 传用户说的口令。也可以 action="list" 查看所有场景、action="delete" 删除。',
        'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'},
                                                        'action': {'type': 'string', 'enum': ['run', 'list', 'delete']}}},
    },
    {
        'name': 'self.message.leave',
        'description': '给家里人留言：「给爸爸留言：晚上不回来吃饭」to="爸爸"，text="晚上不回来吃饭"，知道是谁留的就写 sender。'
                       '留言显示在首页的便签上，点一下会念出来。「有我的留言吗」「有什么留言」时用 action="list" 查看（可带 to 过滤），'
                       '念给用户听；用户说「知道了」后用 action="done" 和 id 划掉。',
        'inputSchema': {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['leave', 'list', 'done']},
                                                        'to': {'type': 'string'}, 'text': {'type': 'string'},
                                                        'sender': {'type': 'string'}, 'id': {'type': 'string'}}},
    },
    {
        'name': 'self.list.update',
        'description': '购物清单、待办等清单：「把酱油和鸡蛋加到购物清单」op="add", name="购物清单", items=["酱油","鸡蛋"]；'
                       '「酱油买了」op="check"；「把鸡蛋删了」op="remove"；「购物清单里有什么」op="show"（念出没划掉的）；'
                       '「清掉买过的」op="clear_done"。待办用 name="待办"。也能在手机上看（设置页「清单 → 手机看」）。',
        'inputSchema': {'type': 'object', 'properties': {'op': {'type': 'string', 'enum': ['add', 'remove', 'check', 'show', 'clear_done']},
                                                        'name': {'type': 'string'},
                                                        'items': {'type': 'array', 'items': {'type': 'string'}}},
                        'required': ['op']},
    },
    {
        'name': 'self.media.play',
        'description': '在设备上放网络电台或助眠声（说话时自动变小，闹钟响时静音）。电台：中国之声、经济之声、东方广播（上海）、'
                       '第一财经广播、江苏交通广播、北京新闻广播，kind="radio", name=电台名。助眠声：雨声、海浪、风扇、白噪音，'
                       'kind="sound", name=声音名, minutes=多久后停（默认 30，0 表示一直放）。「停」「别放了」用 action="stop"。'
                       '调用后只说一句「好，开始放…」。没有的电台如实说有哪些。',
        'inputSchema': {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['play', 'stop']},
                                                        'kind': {'type': 'string', 'enum': ['radio', 'sound']},
                                                        'name': {'type': 'string'}, 'minutes': {'type': 'number'}}},
    },
]
FOCUS_TOOLS = [   # 专注计时（focus.py，设计画布第 47 块）
    {
        'name': 'self.focus.control',
        'description': '专注计时（番茄钟）：「开始专注」「专注 45 分钟」action="start", minutes=分钟数（默认 25）；'
                       '「休息一下」action="break"（5 分钟）；「暂停专注」「继续」action="pause"/"resume"；「结束专注」action="stop"；'
                       '「专注的时候放雨声和鸟叫」sounds=["rain","birds"]（可选 rain 雨声、birds 鸟叫、ocean 海浪、fan 风扇、white 白噪音，'
                       '最多两种，空列表表示不放）；「今天专注了多久」action="status"。调用后只说一句短的确认。',
        'inputSchema': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['start', 'break', 'pause', 'resume', 'stop', 'status']},
            'minutes': {'type': 'number'}, 'sounds': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['action']},
    },
]
STUDY_TOOLS = [   # 学习陪伴（study.py，设计画布第 48 块）
    {
        'name': 'self.study.control',
        'description': '学习陪伴：孩子写作业时用设备的摄像头每分钟看一眼，驼背、离太近、玩手机、犯困时温和提醒。'
                       '「打开学习陪伴」「看着我写作业」action="on"；「关掉学习陪伴」action="off"；'
                       '「今天学得怎么样」「今天专心吗」action="status"（回答今天陪了多久、专心比例、提醒了几次）。'
                       '打开、关上时直接念工具返回的那句话。',
        'inputSchema': {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['on', 'off', 'status']}},
                        'required': ['action']},
    },
]
PROACTIVE_TOOLS = [
    {
        'name': 'self.schedule.update',
        'description': '定时任务：到点由你（大模型）去做一件事，可以查天气、看清单、调米家，比固定文字的提醒更聪明。'
                       '「每天早上七点告诉我今天要不要带伞」action="add", time="07:00", days="daily", '
                       'prompt="查一下今天会不会下雨，下雨就提醒带伞，不下就说不用带"。只提醒一句固定的话用 self.alarm.create。'
                       'days：daily 每天、workdays 工作日、weekend 周末、once 只一次。查看 action="list"，删除 action="delete" 和 id。',
        'inputSchema': {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['add', 'list', 'delete']},
                                                        'time': {'type': 'string'}, 'days': {'type': 'string'},
                                                        'prompt': {'type': 'string'}, 'id': {'type': 'string'}},
                        'required': ['action']},
    },
    {
        'name': 'self.translate.set',
        'description': '翻译模式：「帮我们中英互译」「我要和日本人说话，帮我翻译」时 enabled=true, a="中文", b="英文"（或日文等）。'
                       '打开后你只说译文，设备连续听 30 秒不用叫醒。「退出翻译」「不用翻译了」时 enabled=false。'
                       '调用后只说一句「好，开始中英互译」或「好，翻译结束」。',
        'inputSchema': {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}, 'a': {'type': 'string'}, 'b': {'type': 'string'}},
                        'required': ['enabled']},
    },
]
TOOLS += WAKE_TOOLS + CARD_TOOLS + BROADCAST_TOOLS + PHOTO_TOOLS + LOOKS_TOOLS + KIDS_TOOLS + COOK_TOOLS + DAILY_TOOLS + FOCUS_TOOLS + STUDY_TOOLS + PROACTIVE_TOOLS + home_tools.TOOLS

ALARM_WHEN = ('hours', 'minutes', 'seconds', 'time', 'date', 'days_from_today')


def _alarm_args(args):
    """self.alarm.create's arguments as Client.create_alarm takes them."""
    rule = args.get('repeat')
    repeat = rule and {'type': rule, 'days': args.get('weekdays'), 'day': args.get('month_day')}
    return {'kind': args.get('kind'), 'label': args.get('label') or '', 'say': args.get('say') or '',
            'repeat': repeat, **{k: args[k] for k in ALARM_WHEN if args.get(k) is not None}}


async def _daily(client, name, args):
    if name == 'self.scene.save':
        await client.save_scene(args.get('name'), args.get('steps'))
        return {'success': True, 'note': f"以后说「{args.get('name')}」就会{args.get('steps')}"}
    if name == 'self.scene.run':
        action = args.get('action') or 'run'
        scenes = client.scenes_view()['scenes']
        if action == 'list':
            return {'scenes': scenes} if scenes else {'scenes': [], 'note': '还没有场景，可以说「以后我说『我出门了』，就……」'}
        if action == 'delete':
            await client.delete_scene(args.get('name'))
            return {'success': True}
        from .daily import find_scene
        scene = find_scene(scenes, args.get('name'))
        if not scene:
            return {'success': False, 'reason': f"没有「{args.get('name')}」这个场景", 'scenes': [x['name'] for x in scenes]}
        return {'scene': scene['name'], 'steps': scene['steps'],
                'instruction': '用你已有的工具把这些步骤一步一步做完，做完用一句话告诉用户做了什么；做不了的如实说'}
    if name == 'self.message.leave':
        action = args.get('action') or 'leave'
        if action == 'list':
            to = str(args.get('to') or '').strip()
            notes = [n for n in client.notes_view()['notes'] if not to or to in n['to'] or n['to'] in to]
            return {'notes': [{'id': n['id'], 'to': n['to'], 'from': n['from'], 'text': n['text']} for n in notes]}
        if action == 'done':
            await client.note_done(str(args.get('id') or ''))
            return {'success': True}
        note = await client.leave_note(args.get('to'), args.get('text'), args.get('sender'))
        return {'success': True, 'note': f"已经给{note['to']}留言，显示在首页的便签上"}
    if name == 'self.list.update':
        op, list_name = args.get('op'), args.get('name') or '购物清单'
        if op == 'show':
            data = await client.refresh_lists()
            items = ((data or {}).get('lists') or {}).get(list_name)
            if items is None:
                return {'success': False, 'reason': f'没有「{list_name}」这个清单'}
            return {'name': list_name, 'todo': [x['text'] for x in items if not x['done']],
                    'done': [x['text'] for x in items if x['done']]}
        data = await client.list_change(op, list_name, args.get('items') or [])
        items = data['lists'].get(list_name) or []
        return {'success': True, 'name': list_name, 'todo': [x['text'] for x in items if not x['done']]}
    # self.media.play
    if (args.get('action') or 'play') == 'stop':
        await client.media_stop()
        return {'action': 'RESPONSE', 'response': '好，停了。'}
    now = await client.media_play(args.get('kind') or 'radio', args.get('name'), args.get('minutes'))
    tail = f"，{int(args['minutes'])} 分钟后停" if now['kind'] == 'sound' and args.get('minutes') else (
        '，30 分钟后停' if now['kind'] == 'sound' and args.get('minutes') is None else '')
    return {'action': 'RESPONSE', 'response': f"好，开始放{now['name']}{tail}。"}


async def _appearance(client, theme, avatar, layout=''):
    if avatar == 'photo':
        photos = client.looks_view()['photos']
        if not photos:
            return {'success': False, 'reason': '还没有用照片生成的形象：在设置页「外观」里点「用照片生成」'}
        avatar = photos[0]['id']
    try:
        view = await client.set_looks(theme=theme or None, avatar=avatar or None, layout=layout or None)
    except ValueError as e:
        return {'success': False, 'reason': str(e)}
    names = {a['id']: a['name'] for a in view['avatars'] + view['photos'] + view['pack']['recent']}
    return {'success': True, 'theme': view['themes'][view['theme']], 'avatar': names.get(view['avatar'], view['avatar']),
            'layout': view['layouts'][view['layout']]}


async def _set_home(client, place):
    if not place:
        home = client.home
        return {'home': home['name'] if home else None,
                **({} if home else {'note': '还没设住宅地址，可以说「住宅地址设成……」，或在设置页「天气播报」里按住说'})}
    try:
        found = await client.find_place(place)
    except ValueError as e:
        return {'success': False, 'reason': str(e)}
    if found.get('error'):
        return {'success': False, 'reason': found['error']}
    view = await client.set_home(found['place'])
    return {'success': True, 'home': view['home']['name']}


async def _wake_word(client, action, word):
    try:
        warning = None
        if action == 'list':
            view = client.wake_view()
        elif action == 'add':
            view, warning = await client.add_wake_word(word)
        elif action == 'remove':
            view = await client.remove_wake_word(word)
        elif action == 'reset':
            view = await client.reset_wake_words()
        else:
            return {'success': False, 'reason': 'action 只能是 list、add、remove、reset'}
    except ValueError as e:
        return {'success': False, 'reason': str(e), 'wake_words': client.wake_labels()}
    out = {'success': True, 'wake_words': [w['label'] for w in view['words']]}
    if action == 'add':
        out['added'] = next((w for w in view['words'] if w['label'] == ''.join(re.findall(r'[\u4e00-\u9fff]', word))), None)
    if warning:
        out['warning'] = warning
    if action != 'list':
        out['note'] = '已经生效；设置页「唤醒词」里能看、能删'
    return out


def _brief(view):
    return {k: view[k] for k in ('id', 'type', 'label', 'when', 'in', 'repeat') if view.get(k) not in (None, '')}


async def _wpctl(*args):
    p = await asyncio.create_subprocess_exec('wpctl', *args, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    return out.decode()


async def get_volume():
    m = re.search(r'([\d.]+)', await _wpctl('get-volume', '@DEFAULT_AUDIO_SINK@'))
    return round(float(m.group(1)) * 100) if m else None


async def set_volume(volume):
    await _wpctl('set-volume', '@DEFAULT_AUDIO_SINK@', f'{max(0, min(100, int(volume))) / 100:.2f}')


def _dumps(value):
    return json.dumps(value, ensure_ascii=False)


async def handle(payload, client=None):
    """One JSON-RPC request from the hub; returns the reply payload (None for notifications)."""
    method, req_id, params = payload.get('method'), payload.get('id'), payload.get('params') or {}
    if req_id is None:
        return None

    def result(value):
        return {'jsonrpc': '2.0', 'id': req_id, 'result': value}

    def error(message):
        return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': message}}

    if method == 'initialize':
        return result({'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                       'serverInfo': {'name': 'thinksmart-client', 'version': '0.1.0'}})
    if method == 'tools/list':
        return result({'tools': TOOLS})
    if method == 'tools/call':
        name, args = params.get('name'), params.get('arguments') or {}
        try:
            if name == 'self.audio_speaker.set_volume' and client:
                text = json.dumps({'success': True, 'volume': await client.set_volume(args['volume'], 'voice')})
            elif name == 'self.audio_speaker.get_volume' and client:
                text = json.dumps({'volume': await client.device_volume()})
            elif name == 'self.sleep_mode.set' and client:
                text = json.dumps({'success': True, 'sleep': await client.set_sleep(
                    enabled=args.get('enabled'), start=args.get('start'), end=args.get('end'))},
                    ensure_ascii=False)
            elif name == 'self.sleep_mode.get' and client:
                text = json.dumps({**client.sleep, 'active': client.sleeping}, ensure_ascii=False)
            elif name == 'self.screen.set_brightness' and client:
                text = json.dumps({'success': True, **await client.set_brightness(
                    level=args.get('brightness'), change=args.get('change'))})
            elif name == 'self.device.performance' and client:
                say, _ = await asyncio.get_running_loop().run_in_executor(None, client.perf.brief)
                client.events.publish('perf_open', {})
                text = _dumps({'action': 'RESPONSE', 'response': say})
            elif name == 'self.screen.get_brightness' and client:
                text = json.dumps(client.brightness())
            elif name == 'self.screen.close_photos' and client:
                closed = await client.close_photos()
                text = _dumps({'success': True, 'closed': closed} if closed else
                              {'success': True, 'closed': False, 'note': '屏幕上本来就没有在放照片'})
            elif name == 'self.alarm.create' and client:
                text = _dumps({'success': True, **_brief(await client.create_alarm(**_alarm_args(args)))})
            elif name == 'self.alarm.list' and client:
                view = client.alarm_view()
                items = [_brief(i) for i in view['items'] if not args.get('kind') or i['kind'] == args['kind']]
                text = _dumps({'items': items, 'missed_recently': len(view['missed']),
                               **({} if view['synced'] else {'note': '设备时间还没同步，闹钟暂停中'})})
            elif name == 'self.alarm.cancel' and client:
                if args.get('all'):
                    ids = [i['id'] for i in client.alarm_view()['items'] if not args.get('kind') or i['kind'] == args['kind']]
                elif args.get('id') is not None:
                    ids = [int(args['id'])]
                else:
                    raise ValueError('要传 id，或者 all=true')
                gone = [_brief(v) for v in [await client.delete_alarm(i) for i in ids] if v]
                text = _dumps({'success': True, 'cancelled': gone} if gone or args.get('all') else
                              {'success': False, 'reason': f"没有 id 为 {args['id']} 的，先用 self.alarm.list 查"})
            elif name == 'self.alarm.snooze' and client:
                minutes = int(args.get('minutes') or 10)
                found = await client.snooze_alarm(minutes)
                text = _dumps({'success': True, 'minutes': minutes, 'snoozed': [_brief(v) for v in found]} if found else
                              {'success': False, 'reason': '现在没有正在响、也没有 15 分钟内响过的闹钟',
                               'suggest': f'问用户要不要设一个 {minutes} 分钟后的闹钟（self.alarm.create）'})
            elif name == 'self.alarm.set_volume' and client:
                text = json.dumps({'success': True, 'volume': await client.set_alarm_volume(
                    volume=args.get('volume'), change=args.get('change'))})
            elif name == 'self.alarm.get_volume' and client:
                text = json.dumps({'volume': client.alarm_volume})
            elif name == 'self.wake_word.update' and client:
                text = _dumps(await _wake_word(client, args.get('action'), str(args.get('word') or '').strip()))
            elif name == 'self.weather_broadcast.update' and client:
                keys = ('enabled', 'add_time', 'remove_time', 'days', 'reminders', 'news')
                try:
                    view = await client.set_broadcast(**{k: args[k] for k in keys if args.get(k) is not None})
                    text = _dumps({'success': True, 'text': view['text'],
                                   'home': view['home'] and view['home']['name']})
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e), 'text': client.broadcast_view()['text']})
            elif name == 'self.location.set_home' and client:
                text = _dumps(await _set_home(client, str(args.get('place') or '').strip()))
            elif name in ('self.scene.save', 'self.scene.run', 'self.message.leave', 'self.list.update', 'self.media.play') and client:
                try:
                    text = _dumps(await _daily(client, name, args))
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e)})
            elif name == 'self.focus.control' and client:
                action = args.get('action') or 'status'
                sounds = args.get('sounds')
                view = await client.focus_action(None if action == 'status' else action, args.get('minutes'),
                                                 sounds if isinstance(sounds, list) else None)
                text = _dumps({'success': True, 'phase': view['phase'], 'minutes_left': view['left'] and round(view['left'] / 60),
                               'today_minutes': view['today']['minutes'], 'today_rounds': view['today']['rounds'],
                               'sounds': view['sounds']})
            elif name == 'self.study.control' and client:
                action = args.get('action') or 'status'
                try:
                    view = await client.study_control(enabled={'on': True, 'off': False}.get(action), speak=False)
                    day = client.study.day(time.strftime('%Y-%m-%d'))
                    text = _dumps({'success': True, 'enabled': view['enabled'], 'today_minutes': day['present_minutes'],
                                   'focused_percent': day['focused_pct'], 'reminders': day['reminder_total'],
                                   'reminders_by_kind': {day['names'][k]: n for k, n in day['reminders'].items() if n},
                                   **({'action': 'RESPONSE', 'response': study.SWITCH[action]} if action in ('on', 'off') else {})})
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e)})
            elif name == 'self.schedule.update' and client:
                try:
                    action = args.get('action')
                    if action == 'add':
                        t = await client.task_add(args.get('time'), args.get('prompt'), args.get('days') or 'daily')
                        text = _dumps({'success': True, 'task': t})
                    elif action == 'delete':
                        await client.task_delete(str(args.get('id') or ''))
                        text = _dumps({'success': True})
                    else:
                        text = _dumps(client.tasks_view())
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e)})
            elif name == 'self.translate.set' and client:
                try:
                    t = await client.set_translate(args.get('enabled'), args.get('a'), args.get('b'))
                    text = _dumps({'action': 'RESPONSE', 'response': f"好，开始{t['a']}和{t['b']}互译，直接说就行。" if t['enabled'] else '好，翻译结束。'})
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e)})
            elif name == 'self.cooking.start' and client:
                try:
                    v = await client.cook_start(read=False)
                    text = _dumps({'action': 'RESPONSE', 'response': f"好，我们开始做{v['title']}，一共 {len(v['steps'])} 步。"
                                                                     f"第 1 步，{v['steps'][0]}"})
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e), 'suggest': '先用 self.screen.show_card 给出带编号步骤的菜谱'})
            elif name == 'self.kids_mode.get' and client:
                if args.get('enable') is True and not client.kids['enabled']:
                    await client.set_kids(enabled=True)
                v = client.kids_view()
                text = _dumps({'enabled': v['enabled'], 'age': v['age'], 'daily_minutes': v['daily_minutes'] or '不限',
                               'used_minutes_today': v['used_minutes'], 'bedtime': v['bedtime'] or '不限',
                               'note': '关掉儿童模式要在设置页里关' if v['enabled'] else ''})
            elif name == 'self.appearance.set' and client:
                text = _dumps(await _appearance(client, args.get('theme'), args.get('avatar'), args.get('layout')))
            elif name == 'self.photo.ask' and client:
                try:
                    started = await client.photo_start(str(args.get('question') or ''))
                    say = ('好，把东西对着屏幕上方的摄像头，我数三下就拍。' if started.get('camera')
                           else '好，用手机扫一下屏幕上的二维码，拍张照片。')
                    if not started.get('vision_ready'):
                        say += '不过网关还没配置看图模型，需要先在网关后台的大模型页里填一下。'
                    text = _dumps({'action': 'RESPONSE', 'response': say})
                except ValueError as e:
                    text = _dumps({'success': False, 'reason': str(e)})
            elif name == 'self.photo.cancel' and client:
                await client.photo_cancel()
                text = _dumps({'action': 'RESPONSE', 'response': '好，不拍了。'})
            elif name == 'self.screen.show_card' and client:
                client.show_card(args.get('title'), args.get('markdown'), args.get('images'))
                text = _dumps({'success': True, 'note': '已经显示在屏幕的对话里了，回答只用一句话，不要念内容'})
            elif name in home_tools.NAMES and client:
                try:
                    text = await home_tools.handle(name, args, client.mijia)
                except asyncio.TimeoutError:
                    text = _dumps({'success': False, 'reason': '米家云没有及时响应，稍后再试'})
            else:
                return error(f'Unknown tool: {name}')
        except Exception as e:
            log.exception('tool %s failed', name)
            return result({'content': [{'type': 'text', 'text': str(e)}], 'isError': True})
        return result({'content': [{'type': 'text', 'text': text}], 'isError': False})
    return error(f'Method not implemented: {method}')
