"""config.json: a damaged file is moved aside, not overwritten by the next save (代码评审 2026-11-12)."""
import json

from thinksmart_client import config


def test_damaged_file_is_kept_aside(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(config, 'PATH', path)
    path.write_text('{"theme": "dawn", "avatar": "pack:x"')        # cut off mid-write
    config.save(volume=40)
    assert json.loads(path.read_text()) == {'volume': 40}
    bad = list(tmp_path.glob('config.json.bad-*'))
    assert len(bad) == 1 and bad[0].read_text().startswith('{"theme": "dawn"')
    # a good file keeps every other key
    config.save(theme='night')
    assert json.loads(path.read_text()) == {'volume': 40, 'theme': 'night'}
    config.unset('theme')
    assert json.loads(path.read_text()) == {'volume': 40}
    assert not list(tmp_path.glob('*.tmp'))
