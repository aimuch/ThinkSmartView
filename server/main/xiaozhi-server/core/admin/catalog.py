"""管理后台用到的固定清单：大模型厂商预设、语音识别选项、EdgeTTS 中文音色、插件说明、Claude CLI 选项。"""

# ---------- 大模型：API 厂商（统一走 OpenAI 兼容协议） ----------
# entry：保存到配置里的条目名。已有的上游条目直接复用（保留用户填过的 Key），其余是新条目。
# models：模型名的候选，第一个是默认值；页面上仍可手填其他模型。
LLM_VENDORS = [
    {"id": "claude", "label": "Claude", "entry": "ClaudeAPI",
     "base_url": "https://api.anthropic.com/v1/",
     "models": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]},
    {"id": "openai", "label": "OpenAI", "entry": "OpenAILLM",
     "base_url": "https://api.openai.com/v1",
     "models": ["gpt-5-mini", "gpt-5"]},
    {"id": "gemini", "label": "Gemini", "entry": "GeminiAPI",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "models": ["gemini-2.5-flash", "gemini-2.5-pro"]},
    {"id": "deepseek", "label": "DeepSeek", "entry": "DeepSeekLLM",
     "base_url": "https://api.deepseek.com",
     "models": ["deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"]},
    {"id": "qwen", "label": "通义千问", "entry": "AliLLM",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-flash", "qwen-plus", "qwen-max"]},
    {"id": "doubao", "label": "豆包", "entry": "DoubaoLLM",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "models": ["doubao-seed-2-0-lite-260215"]},
    {"id": "glm", "label": "智谱 GLM", "entry": "ChatGLMLLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4/",
     "models": ["glm-4-flash", "glm-4-plus"]},
    {"id": "kimi", "label": "Kimi", "entry": "KimiLLM",
     "base_url": "https://api.moonshot.cn/v1",
     "models": ["kimi-latest", "moonshot-v1-8k"]},
    # [xiaozhi-hub] 2026-11-08 加的厂商：接口地址按各家文档核对过；模型名只是候选（各家更新快），页面上可以手填。
    # calls：默认称呼（呼名页）；key_optional：本机服务，可以不填 Key。
    {"id": "siliconflow", "label": "硅基流动", "entry": "SiliconFlowLLM", "calls": ["硅基流动"],
     "base_url": "https://api.siliconflow.cn/v1",
     "models": ["Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3"],
     "hint": "模型名写成「厂商/模型」，全部可用的模型在硅基流动控制台的模型广场里看"},
    {"id": "qianfan", "label": "百度千帆", "entry": "QianfanLLM", "calls": ["文心", "文心一言"],
     "base_url": "https://qianfan.baidubce.com/v2",
     "models": ["ernie-4.0-turbo-8k", "ernie-3.5-8k"],
     "hint": "Key 用千帆控制台创建的 API Key；新出的文心模型名以千帆的模型列表为准"},
    {"id": "hunyuan", "label": "腾讯混元", "entry": "HunyuanLLM", "calls": ["混元"],
     "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
     "models": ["hunyuan-turbos-latest"],
     "hint": "腾讯云说混元正逐步迁到 TokenHub，新开通前先看一下腾讯云控制台的说明"},
    {"id": "minimax", "label": "MiniMax", "entry": "MiniMaxLLM", "calls": ["minimax"],
     "base_url": "https://api.minimaxi.com/v1",
     "models": ["MiniMax-M3", "MiniMax-M2.7"],
     "hint": "国内账号用 api.minimaxi.com，海外账号改成 https://api.minimax.io/v1；模型名区分大小写"},
    {"id": "stepfun", "label": "阶跃星辰", "entry": "StepFunLLM", "calls": ["阶跃"],
     "base_url": "https://api.stepfun.com/v1",
     "models": ["step-3.5-flash", "step-3.7-flash"]},
    {"id": "spark", "label": "讯飞星火", "entry": "SparkLLM", "calls": ["星火"],
     "base_url": "https://spark-api-open.xf-yun.com/v1",
     "models": ["4.0Ultra", "lite"],
     "hint": "Key 填讯飞控制台对应版本页面的 APIPassword（不是 APIKey / APISecret）"},
    {"id": "xai", "label": "xAI Grok", "entry": "XaiLLM", "calls": ["grok"],
     "base_url": "https://api.x.ai/v1",
     "models": ["grok-4.5"],
     "hint": "最新的模型名以 docs.x.ai 为准；国内可能需要代理"},
    {"id": "mistral", "label": "Mistral", "entry": "MistralLLM", "calls": ["mistral"],
     "base_url": "https://api.mistral.ai/v1",
     "models": ["mistral-small-latest", "mistral-large-latest"]},
    {"id": "openrouter", "label": "OpenRouter", "entry": "OpenRouterLLM", "calls": ["openrouter"],
     "base_url": "https://openrouter.ai/api/v1",
     "models": ["openrouter/auto"],
     "hint": "一个 Key 用很多家的模型，模型名写成「厂商/模型」（openrouter.ai/models）；国内可能需要代理"},
    {"id": "magpie", "label": "Magpie", "entry": "MagpieLLM", "calls": ["magpie"], "key_optional": True,
     "base_url": "http://127.0.0.1:3425/v1", "models": [],
     "hint": "Magpie 本地网关（github.com/yetone/magpie）：在 Magpie 里配好的所有供应商都能用，模型名写成「供应商/模型」，"
             "例如 deepseek/deepseek-v4-pro。Magpie 在别的电脑上时，打开它的「局域网共享」，地址填 http://那台电脑的IP:3425/v1，"
             "Key 填 Magpie 给这个客户端发的网关 Key"},
    {"id": "ollama", "label": "Ollama", "entry": "OllamaAPI", "calls": ["ollama"], "key_optional": True,
     "base_url": "http://127.0.0.1:11434/v1", "models": ["qwen3:8b"],
     "hint": "本机或局域网里的 Ollama；模型名用 ollama list 里的名字，Key 不用填"},
    {"id": "lmstudio", "label": "LM Studio", "entry": "LMStudioAPI", "calls": ["lm studio"], "key_optional": True,
     "base_url": "http://127.0.0.1:1234/v1", "models": [],
     "hint": "LM Studio 的本地服务（Developer → Start Server）；模型名用它显示的模型 ID，Key 不用填"},
    {"id": "custom", "label": "自定义", "entry": "CustomOpenAILLM", "key_optional": True,
     "base_url": "", "models": [],
     "hint": "其他 OpenAI 兼容服务，例如本机代理、中转站"},
]
VENDOR_BY_ID = {v["id"]: v for v in LLM_VENDORS}
VENDOR_BY_ENTRY = {v["entry"]: v for v in LLM_VENDORS}

# ---------- [xiaozhi-hub] 大模型：DeepSeek Harness（DSH） ----------
# 模型名和档位来自 DeepSeek 的 /models（2026-11-09）；deepseek-flash 就是 V4.1 Flash
DSH_MODELS = [
    {"value": "deepseek-flash", "label": "V4.1 Flash", "hint": "快，能看图，适合语音"},
    {"value": "deepseek-v4-pro", "label": "V4 Pro", "hint": "更聪明，较慢"},
]
DSH_EFFORTS = [
    {"value": "low", "label": "低"},
    {"value": "high", "label": "高", "slow": True},
    {"value": "max", "label": "最高", "slow": True},
]
DSH_PERMISSIONS = [
    {"value": "read-only", "label": "只读", "desc": "只能看、搜工作目录里的文件"},
    {"value": "workspace-write", "label": "可写", "desc": "还能在工作目录里改文件、在沙箱里跑命令"},
    {"value": "danger-full-access", "label": "完全访问", "desc": "不限制，能动整台电脑，慎用"},
]

# ---------- [xiaozhi-hub] 智能体页（设计画布第 18 块）：自己加的智能体有三种 ----------
AGENT_KINDS = [
    {"id": "openai", "type": "agent_api", "label": "OpenAI 兼容", "hint": "Hermes Agent 等自己带工具和记忆的智能体",
     "base_url": "http://127.0.0.1:8642/v1"},
    {"id": "dify", "type": "dify", "label": "Dify", "hint": "Dify 的对话应用或工作流（流水线）", "base_url": "https://api.dify.ai/v1"},
    {"id": "coze", "type": "coze", "label": "Coze", "hint": "扣子（coze.cn）上发布的智能体", "base_url": ""},
]
AGENT_KIND_BY_ID = {k["id"]: k for k in AGENT_KINDS}
AGENT_KIND_BY_TYPE = {k["type"]: k for k in AGENT_KINDS}
DIFY_MODES = [{"value": "chat-messages", "label": "对话应用"}, {"value": "workflows/run", "label": "工作流"},
              {"value": "completion-messages", "label": "文本生成"}]

# ---------- 大模型：Claude Code CLI ----------
CLAUDE_MODELS = [
    {"value": "haiku", "label": "Haiku", "hint": "最快，适合语音"},
    {"value": "sonnet", "label": "Sonnet", "hint": "更聪明"},
    {"value": "opus", "label": "Opus", "hint": "很强，较慢"},
    {"value": "fable", "label": "Fable", "hint": "最强，最慢"},
]
CLAUDE_EFFORTS = [
    {"value": "low", "label": "低"},
    {"value": "medium", "label": "中"},
    {"value": "high", "label": "高"},
    {"value": "xhigh", "label": "超高", "slow": True},
    {"value": "max", "label": "最高", "slow": True},
]
# 权限级别：决定开放哪些工具，以及 Claude Code 的权限模式
CLAUDE_READ_TOOLS = ["Read", "Grep", "Glob"]
CLAUDE_WEB_TOOLS = ["WebSearch", "WebFetch"]
CLAUDE_LEVELS = [
    {"value": "read", "label": "只读", "mode": "default", "tools": CLAUDE_READ_TOOLS,
     "desc": "只能查看、搜索工作目录里的文件"},
    {"value": "edit", "label": "可编辑", "mode": "acceptEdits", "tools": CLAUDE_READ_TOOLS + ["Write", "Edit"],
     "desc": "还能在工作目录里新建、修改文件，自动通过"},
    {"value": "auto", "label": "Auto", "mode": "auto",
     "tools": CLAUDE_READ_TOOLS + ["Write", "Edit", "Bash", "TodoWrite"],
     "desc": "还能执行命令等，由 Claude Code 自己判断：低风险的自动放行，需要确认的一律不执行"},
]
CLAUDE_LEVEL_BY_VALUE = {lv["value"]: lv for lv in CLAUDE_LEVELS}
CLAUDE_TOOLS = [
    {"name": "Read", "label": "读文件", "risky": False},
    {"name": "Grep", "label": "搜内容", "risky": False},
    {"name": "Glob", "label": "找文件", "risky": False},
    {"name": "WebSearch", "label": "联网搜索", "risky": False},
    {"name": "WebFetch", "label": "打开网页", "risky": False},
    {"name": "Bash", "label": "执行命令", "risky": True},
    {"name": "Write", "label": "写文件", "risky": True},
    {"name": "Edit", "label": "改文件", "risky": True},
    {"name": "TodoWrite", "label": "记待办", "risky": False},
]

# ---------- 语音识别：后台可以选的两种 ----------
# 本地只开放默认的 X-ASR（安装脚本已下载）；其他本地模型选中时会自动下载，不放进后台
LOCAL_ASR_ENTRY = "SherpaXASR"
DOUBAO_ASR_ENTRY = "DoubaoStreamASRV2"
DOUBAO_RESOURCES = [
    {"value": "volc.seedasr.sauc.duration", "label": "2.0 小时版", "hint": "按使用时长计费"},
    {"value": "volc.seedasr.sauc.concurrent", "label": "2.0 并发版", "hint": "按并发路数计费"},
    {"value": "volc.bigasr.sauc.duration", "label": "1.0 小时版", "hint": "上一代模型"},
    {"value": "volc.bigasr.sauc.concurrent", "label": "1.0 并发版", "hint": "上一代模型"},
]
DOUBAO_RESOURCE_VALUES = {r["value"] for r in DOUBAO_RESOURCES}

# ---------- 语音合成：EdgeTTS 中文音色 ----------
EDGE_VOICES = [
    {"value": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "gender": "女", "style": "温暖", "group": "普通话"},
    {"value": "zh-CN-XiaoyiNeural", "name": "晓伊", "gender": "女", "style": "活泼", "group": "普通话"},
    {"value": "zh-CN-YunxiNeural", "name": "云希", "gender": "男", "style": "活泼、阳光", "group": "普通话"},
    {"value": "zh-CN-YunxiaNeural", "name": "云夏", "gender": "男", "style": "可爱，偏童声", "group": "普通话"},
    {"value": "zh-CN-YunjianNeural", "name": "云健", "gender": "男", "style": "激情，偏解说", "group": "普通话"},
    {"value": "zh-CN-YunyangNeural", "name": "云扬", "gender": "男", "style": "专业稳重，偏播报", "group": "普通话"},
    {"value": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "gender": "女", "style": "幽默", "group": "方言：东北话"},
    {"value": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮", "gender": "女", "style": "明亮", "group": "方言：陕西话"},
    {"value": "zh-HK-HiuGaaiNeural", "name": "曉佳", "gender": "女", "style": "友好", "group": "粤语"},
    {"value": "zh-HK-HiuMaanNeural", "name": "曉曼", "gender": "女", "style": "友好", "group": "粤语"},
    {"value": "zh-HK-WanLungNeural", "name": "雲龍", "gender": "男", "style": "友好", "group": "粤语"},
    {"value": "zh-TW-HsiaoChenNeural", "name": "曉臻", "gender": "女", "style": "友好", "group": "台湾"},
    {"value": "zh-TW-HsiaoYuNeural", "name": "曉雨", "gender": "女", "style": "友好", "group": "台湾"},
    {"value": "zh-TW-YunJheNeural", "name": "雲哲", "gender": "男", "style": "友好", "group": "台湾"},
]
EDGE_VOICE_VALUES = {v["value"] for v in EDGE_VOICES}
# [xiaozhi-hub] 千问 TTS（阿里云百炼 qwen3-tts-flash）：方言音色直接用方言念普通话文字
QWEN3_TTS_ENTRY = "Qwen3TTS"
QWEN3_TTS_DEFAULTS = {"type": "qwen3_tts", "model": "qwen3-tts-flash", "language_type": "Chinese", "output_dir": "tmp/"}
QWEN3_TTS_VOICES = [
    {"value": "Jada", "name": "阿珍", "gender": "女", "style": "上海阿姐", "group": "方言：上海话"},
    {"value": "Dylan", "name": "晓东", "gender": "男", "style": "北京胡同少年", "group": "方言：北京话"},
    {"value": "Peter", "name": "李彼得", "gender": "男", "style": "天津相声味", "group": "方言：天津话"},
    {"value": "li", "name": "老李", "gender": "男", "style": "南京耐心教练", "group": "方言：南京话"},
    {"value": "Marcus", "name": "秦川", "gender": "男", "style": "陕西实在", "group": "方言：陕西话"},
    {"value": "Sunny", "name": "晴儿", "gender": "女", "style": "四川甜妹", "group": "方言：四川话"},
    {"value": "Eric", "name": "程川", "gender": "男", "style": "四川成都男声", "group": "方言：四川话"},
    {"value": "Roy", "name": "阿杰", "gender": "男", "style": "闽南诙谐", "group": "方言：闽南话"},
    {"value": "Rocky", "name": "阿强", "gender": "男", "style": "幽默风趣", "group": "粤语"},
    {"value": "Kiki", "name": "阿清", "gender": "女", "style": "港妹闺蜜", "group": "粤语"},
    {"value": "Cherry", "name": "芊悦", "gender": "女", "style": "阳光亲切", "group": "普通话"},
    {"value": "Serena", "name": "苏瑶", "gender": "女", "style": "温柔", "group": "普通话"},
    {"value": "Ethan", "name": "晨煦", "gender": "男", "style": "阳光温暖", "group": "普通话"},
    {"value": "Chelsie", "name": "千雪", "gender": "女", "style": "二次元", "group": "普通话"},
]
QWEN3_TTS_VOICE_VALUES = {v["value"] for v in QWEN3_TTS_VOICES}

# 后台「语音合成」能选的引擎：id → 配置条目、类型、音色表
TTS_ENGINES = [
    {"id": "edge", "entry": "EdgeTTS", "type": "edge", "label": "EdgeTTS", "mark": "免费",
     "desc": "微软的在线语音，免费、不需要 Key。有东北话、陕西话、粤语，没有上海话。", "voices": EDGE_VOICES},
    {"id": "qwen3", "entry": QWEN3_TTS_ENTRY, "type": "qwen3_tts", "label": "千问 TTS", "mark": "阿里云百炼",
     "desc": "通义千问 qwen3-tts-flash，有上海话、北京话、四川话、粤语等方言音色。按字数计费，需要百炼的 API Key。",
     "link": "https://bailian.console.aliyun.com/?tab=model#/api-key", "voices": QWEN3_TTS_VOICES},
]
# [xiaozhi-hub] 局域网里的 OpenAI 兼容合成服务（Kokoro-FastAPI、Speaches 等）：地址、模型、音色都手填
LAN_TTS_ENTRY = "LanTTS"
LAN_TTS_DEFAULTS = {"type": "openai", "api_url": "http://192.168.1.10:8880/v1/audio/speech", "model": "kokoro",
                    "voice": "zf_xiaobei", "format": "wav", "speed": 1.0, "api_key": "none", "output_dir": "tmp/"}
TTS_ENGINES.append({"id": "lan", "entry": LAN_TTS_ENTRY, "type": "openai", "label": "局域网合成服务", "mark": "OpenAI 兼容",
                    "desc": "家里另一台电脑上的合成服务，例如 Kokoro-FastAPI（中文音色 zf_xiaobei、zm_yunxi 等）、Speaches。",
                    "link": "https://github.com/remsky/Kokoro-FastAPI", "voices": [], "custom": True})
TTS_ENGINE_BY_ID = {e["id"]: e for e in TTS_ENGINES}

VOICE_SAMPLE = {
    "普通话": "你好，今天想聊点什么？",
    "粤语": "你好，今日想傾啲乜嘢？",
    "方言：上海话": "侬好，今朝想讲点啥？",
}


def voice_sample(group: str) -> str:
    return VOICE_SAMPLE.get(group, VOICE_SAMPLE["普通话"])


# ---------- 意图识别：插件 ----------
# 原项目总会加载"退出对话"和"查日期"，不需要勾选
ALWAYS_ON_PLUGINS = [
    {"name": "handle_exit_intent", "label": "退出对话", "desc": "说“再见”“退下吧”时结束对话"},
    {"name": "get_lunar", "label": "查日期", "desc": "今天几号、星期几、农历和节气"},
]
PLUGINS = [
    {"name": "get_weather", "label": "查天气", "desc": "实时天气、空气质量和 7 天预报（Open-Meteo，免费、不用 Key），点「设置」填设备所在地",
     "config": "get_weather", "settings": "get_weather"},
    {"name": "get_news_from_newsnow", "label": "热点新闻", "desc": "澎湃新闻、百度热搜、财联社等热榜"},
    {"name": "get_news_from_chinanews", "label": "中新网新闻", "desc": "中国新闻网的社会、国际、财经新闻"},
    {"name": "play_music", "label": "放音乐", "desc": "播放电脑上 music 目录里的歌曲"},
    {"name": "web_search", "label": "联网搜索", "desc": "用 Anthropic 协议接口自带的搜索查实时信息，点「设置」填接口和 Key",
     "config": "web_search", "settings": "web_search"},
    {"name": "change_role", "label": "切换角色", "desc": "换人设和说话风格"},
    {"name": "hass_get_state", "label": "查智能家居状态", "desc": "需要在配置文件的 plugins.home_assistant 里填写地址和令牌",
     "config": "home_assistant"},
    {"name": "hass_set_state", "label": "控制智能家居", "desc": "需要在配置文件的 plugins.home_assistant 里填写地址和令牌",
     "config": "home_assistant"},
    {"name": "hass_play_music", "label": "智能音箱放歌", "desc": "需要在配置文件的 plugins.home_assistant 里填写地址和令牌",
     "config": "home_assistant"},
    {"name": "search_from_ragflow", "label": "查知识库", "desc": "需要在配置文件的 plugins.search_from_ragflow 里填写 RAGFlow 地址和 Key",
     "config": "search_from_ragflow"},
]
PLUGIN_NAMES = {p["name"] for p in PLUGINS}

# 插件设置（后台插件旁边的「设置」）：按字段清单自动生成表单，值存在 plugins.<config> 下。
# PLUGINS 条目加 "settings": <id> 指向这里。每份设置 {"note": 整体说明, "fields": [...]}，字段：
#   key、label；type：text / secret（只写不读，留空表示不改）/ number（可带 min、max）；
#   required：没填时插件显示"还没配置"（不写就是选填）；default：留空时实际用的值，页面上当占位提示；
#   hint：字段旁边的说明文字。
# legacy_keys：旧版插件才有的字段，配置里还有它们时，已保存的密钥当成没填（是给别的服务的），
#   保存时删掉这些字段并清掉旧密钥；legacy_note 是页面上的提示。
# 要有「测试」按钮，在 core/admin/plugin_settings.py 的 TESTS 里登记测试函数。
PLUGIN_SETTINGS = {
    "get_weather": {
        "note": "默认用 Open-Meteo，免费、不用注册。设备通过网关上网，按 IP 只能猜到网关所在的城市，"
                "所以最好填上设备所在地；问别的城市的天气不受影响。",
        "fields": [
            {"key": "home_location", "label": "设备所在地", "type": "text",
             "hint": "写区县或城市的全称，比如「青浦区」「杭州市」；留空按网关的公网 IP 推断"},
            {"key": "qweather_key", "label": "和风天气 Key（选填）", "type": "secret",
             "hint": "填了 Key 和 API Host 就改用和风天气（在 console.qweather.com 申请）"},
            {"key": "qweather_host", "label": "和风天气 API Host（选填）", "type": "text",
             "hint": "和风控制台「设置」里的 API Host，形如 xxxx.re.qweatherapi.com"},
        ],
    },
    "web_search": {
        "note": "需要 Anthropic 协议、支持自带搜索（web_search）的接口，比如 DeepSeek、Anthropic 官方。"
                "聊天模型用哪家都可以，和这里无关。注意：网关装在设备上时，127.0.0.1 指设备自己。",
        "fields": [
            {"key": "base_url", "label": "接口地址", "type": "text", "default": "https://api.deepseek.com/anthropic/v1",
             "hint": "写到 /anthropic 或 /anthropic/v1 都可以；Anthropic 官方填 https://api.anthropic.com"},
            {"key": "api_key", "label": "API Key", "type": "secret", "required": True},
            {"key": "model", "label": "模型", "type": "text", "default": "deepseek-v4-flash",
             "hint": "用来搜索和总结的模型，比如 deepseek-v4-flash、claude-haiku-5-5"},
            {"key": "max_uses", "label": "单次最多搜索次数", "type": "number", "default": 5, "min": 1, "max": 10,
             "hint": "一个问题里模型最多搜几轮；越多越全，也越慢"},
        ],
        # 旧版联网搜索（秘塔、Tavily、Serply）的字段：那时的 Key 不能发给新接口
        "legacy_keys": ["provider", "max_results"],
        "legacy_note": "配置里还留着旧版联网搜索（秘塔、Tavily、Serply）的设置，那时填的 Key 不能用在新接口上，"
                       "新版不会使用它。请重新填写 API Key 并保存，旧设置会一起清掉。",
    },
}
INTENT_MODES = ("nointent", "function_call")

# ---------- 情绪（表情测试） ----------
EMOTION_LABELS = {
    "neutral": "平静", "happy": "开心", "laughing": "大笑", "funny": "搞笑", "sad": "难过", "angry": "生气",
    "crying": "哭泣", "loving": "喜爱", "embarrassed": "尴尬", "surprised": "惊讶", "shocked": "震惊",
    "thinking": "思考", "winking": "眨眼", "cool": "酷", "relaxed": "放松", "delicious": "好吃",
    "kissy": "亲亲", "confident": "自信", "sleepy": "困", "silly": "傻乐", "confused": "困惑",
}

# ---------- 语音识别：后台能选的其他服务（本地 X-ASR、豆包有自己的页面，见 LOCAL_ASR_ENTRY / DOUBAO_ASR_ENTRY） ----------
# [xiaozhi-hub] 2026-11-08。fields：页面上要填的（secret 只写不读）；defaults：新建条目时写进配置的其他值；
# download：本地模型从 HuggingFace 下载的仓库和文件（* 表示整个仓库，跳过 test_wavs）。
X_ASR_VARIANTS = [
    {"value": "fast", "label": "快速版", "hint": "int8，约 175MB",
     "model_dir": "models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03",
     "repo": "csukuangfj2/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03",
     "files": ["encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx", "joiner-epoch-99-avg-1.int8.onnx",
               "tokens.txt", "bpe.model"]},
    {"value": "full", "label": "精度版", "hint": "fp32，约 600MB，服务器在电脑上时推荐",
     "model_dir": "models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-2026-06-03",
     "repo": "csukuangfj2/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-2026-06-03",
     "files": ["encoder-epoch-99-avg-1.onnx", "decoder-epoch-99-avg-1.onnx", "joiner-epoch-99-avg-1.onnx",
               "tokens.txt", "bpe.model"]},
]
ASR_PROVIDERS = [
    {"id": "qwen3asr", "group": "local", "label": "Qwen3-ASR 0.6B", "mark": "Q", "entry": "SherpaQwen3ASR",
     "type": "sherpa_onnx_local", "desc": "通义千问的本地识别模型，在这台电脑上识别；比 X-ASR 慢一些（每句约 0.8 秒）",
     "defaults": {"model_dir": "models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25", "model_type": "qwen3_asr",
                  "output_dir": "tmp/", "num_threads": 4},
     "download": {"repo": "csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25", "files": ["*"], "size": "约 940MB"}},
    {"id": "sensevoice", "group": "local", "label": "SenseVoice", "mark": "S", "entry": "SherpaASR",
     "type": "sherpa_onnx_local", "desc": "阿里达摩院的多语种模型（中英日韩粤），在这台电脑上识别",
     "defaults": {"model_dir": "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17", "model_type": "sense_voice",
                  "output_dir": "tmp/"},
     "download": {"repo": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
                  "files": ["model.int8.onnx", "tokens.txt"], "size": "约 240MB"}},
    {"id": "qwen3flash", "group": "cloud", "label": "阿里百炼 Qwen3-ASR-Flash", "mark": "阿", "entry": "Qwen3ASRFlash",
     "type": "qwen3_asr_flash", "desc": "阿里云百炼的云端识别，说完一句识别一句；和通义千问用同一个 DashScope Key",
     "link": "https://bailian.console.aliyun.com/",
     "fields": [{"key": "api_key", "label": "百炼 API Key", "secret": True}],
     "defaults": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_name": "qwen3-asr-flash",
                  "output_dir": "tmp/", "enable_lid": True, "enable_itn": True, "context": ""}},
    {"id": "paraformer", "group": "cloud", "label": "阿里百炼 Paraformer 实时", "mark": "阿", "entry": "AliyunBLStreamASR",
     "type": "aliyunbl_stream", "desc": "阿里云百炼的实时识别，边说边识别；DashScope Key",
     "link": "https://bailian.console.aliyun.com/",
     "fields": [{"key": "api_key", "label": "百炼 API Key", "secret": True}],
     "defaults": {"model": "paraformer-realtime-v2", "format": "pcm", "sample_rate": 16000,
                  "disfluency_removal_enabled": False, "semantic_punctuation_enabled": False,
                  "max_sentence_silence": 200}},
    {"id": "tencent", "group": "cloud", "label": "腾讯云语音识别", "mark": "腾", "entry": "TencentASR", "type": "tencent",
     "desc": "腾讯云一句话识别", "link": "https://console.cloud.tencent.com/asr",
     "fields": [{"key": "appid", "label": "AppID"}, {"key": "secret_id", "label": "SecretId"},
                {"key": "secret_key", "label": "SecretKey", "secret": True}],
     "defaults": {"output_dir": "tmp/"}},
    {"id": "xunfei", "group": "cloud", "label": "讯飞语音听写", "mark": "讯", "entry": "XunfeiStreamASR", "type": "xunfei_stream",
     "desc": "讯飞开放平台的流式听写（大模型版）", "link": "https://console.xfyun.cn/",
     "fields": [{"key": "app_id", "label": "APPID"}, {"key": "api_key", "label": "APIKey", "secret": True},
                {"key": "api_secret", "label": "APISecret", "secret": True}],
     "defaults": {"domain": "slm", "language": "zh_cn", "accent": "mandarin", "output_dir": "tmp/"}},
    {"id": "baidu", "group": "cloud", "label": "百度语音识别", "mark": "百", "entry": "BaiduASR", "type": "baidu",
     "desc": "百度智能云短语音识别（普通话）", "link": "https://console.bce.baidu.com/ai/#/ai/speech/overview/index",
     "fields": [{"key": "app_id", "label": "AppID"}, {"key": "api_key", "label": "API Key", "secret": True},
                {"key": "secret_key", "label": "Secret Key", "secret": True}],
     "defaults": {"dev_pid": 1537, "output_dir": "tmp/"}},
    {"id": "openai", "group": "cloud", "label": "OpenAI", "mark": "O", "entry": "OpenaiASR", "type": "openai",
     "desc": "OpenAI 的转写接口；国内可能需要代理", "link": "https://platform.openai.com/api-keys",
     "fields": [{"key": "api_key", "label": "API Key", "secret": True},
                {"key": "model_name", "label": "模型", "default": "gpt-4o-mini-transcribe"},
                {"key": "base_url", "label": "接口地址", "default": "https://api.openai.com/v1/audio/transcriptions"}],
     "defaults": {"output_dir": "tmp/"}},
    {"id": "groq", "group": "cloud", "label": "Groq Whisper", "mark": "G", "entry": "GroqASR", "type": "openai",
     "desc": "Groq 上的 Whisper，很快；国内可能需要代理", "link": "https://console.groq.com/keys",
     "fields": [{"key": "api_key", "label": "API Key", "secret": True},
                {"key": "model_name", "label": "模型", "default": "whisper-large-v3-turbo"},
                {"key": "base_url", "label": "接口地址", "default": "https://api.groq.com/openai/v1/audio/transcriptions"}],
     "defaults": {"output_dir": "tmp/"}},
    # [xiaozhi-hub] 局域网里的 OpenAI 兼容识别服务（Speaches、faster-whisper-server 等），设计画布第 15 块 D
    {"id": "lanasr", "group": "lan", "label": "局域网识别服务", "mark": "LAN", "entry": "LanASR", "type": "openai",
     "desc": "家里另一台电脑上的 OpenAI 兼容识别服务，例如 Speaches（faster-whisper）；有显卡的电脑上很快",
     "link": "https://speaches.ai/",
     "fields": [{"key": "base_url", "label": "接口地址", "default": "http://192.168.1.10:8000/v1/audio/transcriptions"},
                {"key": "model_name", "label": "模型", "default": "Systran/faster-whisper-small"},
                {"key": "api_key", "label": "API Key（不用就留空）", "secret": True, "optional": True}],
     "defaults": {"output_dir": "tmp/", "api_key": "none"}},
]
ASR_PROVIDER_BY_ID = {p["id"]: p for p in ASR_PROVIDERS}
ASR_PROVIDER_BY_ENTRY = {p["entry"]: p for p in ASR_PROVIDERS}
