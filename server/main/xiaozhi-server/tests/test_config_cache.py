"""[xiaozhi-hub] load_merged 的缓存：文件变了马上读新的；拿到的是拷贝，改了不影响下一次。"""
from core.admin import config_store


def test_merged_config_cache(tmp_path):
    d, c = tmp_path / "config.yaml", tmp_path / ".config.yaml"
    d.write_text("a: 1\nb: {x: 1}\n", encoding="utf-8")
    c.write_text("b: {y: 2}\n", encoding="utf-8")
    m = config_store.load_merged(d, c)
    assert m == {"a": 1, "b": {"x": 1, "y": 2}}
    m["a"] = 99
    m["b"]["x"] = 99
    assert config_store.load_merged(d, c) == {"a": 1, "b": {"x": 1, "y": 2}}   # 改拷贝不影响缓存
    c.write_text("b: {y: 3}\nzz: true\n", encoding="utf-8")
    assert config_store.load_merged(d, c)["b"]["y"] == 3                        # 文件变了：读新的
