"""Model logos (design board 42): every vendor's logo file is here, the hub's own logo wins when we have the
file, and the call names survive into the page's model list (设置 → 智能体 → 可以叫的)."""
from thinksmart_client.app import BY_MODEL, LOGO_DIR, VENDORS, describe


def test_every_logo_file_exists():
    for vendor, (logo, _) in VENDORS.items():
        assert not logo or (LOGO_DIR / f'{logo}.svg').is_file(), vendor
    assert {v for _, v in BY_MODEL} <= set(VENDORS)


def test_describe():
    o = describe({'kind': 'api', 'vendor': 'siliconflow', 'label': '硅基流动', 'entry': 'SiliconFlowLLM',
                  'model': 'Qwen/Qwen3-8B', 'calls': ['硅基流动']})
    assert o['logo'] == 'siliconcloud-color' and o['calls'] == ['硅基流动']
    # the hub names a logo we have: it wins; one we don't have: keep ours
    assert describe({'kind': 'dsh', 'vendor': 'dsh', 'label': 'DSH', 'entry': 'DshCLI', 'logo': 'deepseek-color'})['logo'] == 'deepseek-color'
    assert describe({'kind': 'api', 'vendor': 'openai', 'label': 'OpenAI', 'entry': 'OpenAILLM', 'logo': 'brand-new'})['logo'] == 'openai'
    # unknown vendor: guessed from the model name, else no logo (the page draws the first letter)
    assert describe({'kind': 'agent', 'vendor': 'agent', 'label': 'x', 'entry': 'Agent_x', 'model': 'ernie-4.0'})['logo'] == 'wenxin-color'
    o = describe({'kind': 'api', 'vendor': 'custom', 'label': '自定义', 'entry': 'CustomOpenAILLM', 'model': 'my-model'})
    assert o['logo'] == '' and o['calls'] == []
