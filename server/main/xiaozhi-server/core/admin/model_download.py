"""[xiaozhi-hub] Download a local ASR model from HuggingFace in the background (admin page 语音识别).

One download per model directory at a time; files go to <name>.part and are renamed when complete,
so an interrupted download leaves no half file that looks finished, and a retry skips files that
are already there with the right size. HF_ENDPOINT (e.g. https://hf-mirror.com) is honoured, like
scripts/install.sh.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from config.config_loader import get_project_dir
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()
CHUNK = 1 << 20
_jobs = {}            # model_dir -> state dict
_lock = threading.Lock()


def endpoint():
    return (os.environ.get('HF_ENDPOINT') or 'https://huggingface.co').rstrip('/')


def state(model_dir: str) -> dict:
    with _lock:
        return dict(_jobs.get(model_dir) or {'state': 'idle'})


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'xiaozhi-hub-server'})
    return urllib.request.urlopen(req, timeout=timeout)


def _tree(repo):
    with _get(f'{endpoint()}/api/models/{repo}/tree/main?recursive=true') as r:
        return [f for f in json.loads(r.read()) if f.get('type') == 'file']


def start(model_dir: str, repo: str, files: list) -> dict:
    with _lock:
        if (_jobs.get(model_dir) or {}).get('state') == 'running':
            return dict(_jobs[model_dir])
        _jobs[model_dir] = {'state': 'running', 'done': 0, 'total': 0, 'file': '', 'started': time.time()}
    threading.Thread(target=_run, args=(model_dir, repo, files), name='model-download', daemon=True).start()
    return state(model_dir)


def _set(model_dir, **kw):
    with _lock:
        _jobs[model_dir].update(kw)


def _run(model_dir, repo, files):
    target = Path(get_project_dir()) / model_dir
    try:
        tree = _tree(repo)
        wanted = [f for f in tree if not f['path'].startswith(('test_wavs/', '.git'))
                  and ('*' in files or f['path'] in files)]
        if not wanted:
            raise RuntimeError(f'{repo} 里没有要下载的文件')
        total = sum(int(f.get('size') or 0) for f in wanted)
        _set(model_dir, total=total)
        done = 0
        for f in wanted:
            path, size = target / f['path'], int(f.get('size') or 0)
            if path.exists() and (not size or path.stat().st_size == size):
                done += size
                _set(model_dir, done=done)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + '.part')
            _set(model_dir, file=f['path'])
            url = f'{endpoint()}/{repo}/resolve/main/{urllib.parse.quote(f["path"])}'
            with _get(url, timeout=60) as r, open(part, 'wb') as out:
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    _set(model_dir, done=done)
            if size and part.stat().st_size != size:
                raise RuntimeError(f'{f["path"]} 大小不对（{part.stat().st_size} / {size}），请重试')
            os.replace(part, path)
        _set(model_dir, state='done', file='', finished=time.time())
        logger.bind(tag=TAG).info(f'模型下载完成：{model_dir}（{total // (1 << 20)}MB）')
    except Exception as e:
        _set(model_dir, state='failed', error=f'{type(e).__name__}: {e}'[:300])
        logger.bind(tag=TAG).warning(f'模型下载失败：{model_dir}：{e}')
