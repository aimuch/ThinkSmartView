"""MIoT device specs from miot-spec.org, cached on disk.

Ported from MenuBarMijia (MIT): windows/src/core/miCloud/spec.ts. A device's spec URN comes from
the device list (spec_type) or, failing that, from the released-instances index (about 2 MB,
cached for 7 days). Each spec is cached per URN forever (released specs don't change).
"""
import asyncio
import hashlib
import json
import logging
import pathlib
import time
import urllib.parse

import aiohttp

from .store import read_json, write_json

log = logging.getLogger(__name__)

INDEX_URL = 'https://miot-spec.org/miot-spec-v2/instances?status=released'
INSTANCE_URL = 'https://miot-spec.org/miot-spec-v2/instance?type='
INDEX_MAX_AGE = 7 * 86400


def short_name(urn: str) -> str:
    parts = (urn or '').split(':')
    return parts[3] if len(parts) > 3 else (urn or '')


def parse_spec(raw):
    """Keep what the synthesizer uses: {type, services: [{iid, name, desc, props: [...], actions: [...]}]}."""
    if not isinstance(raw, dict) or not isinstance(raw.get('type'), str):
        return None
    services = []
    for s in raw.get('services') or []:
        if not isinstance(s, dict) or not isinstance(s.get('iid'), int) or not isinstance(s.get('type'), str):
            continue
        props = []
        for p in s.get('properties') or []:
            if not isinstance(p, dict) or not isinstance(p.get('iid'), int) or not isinstance(p.get('type'), str):
                continue
            prop = {'iid': p['iid'], 'name': short_name(p['type']), 'desc': p.get('description') or '',
                    'format': p.get('format') or 'string',
                    'access': [a for a in p.get('access') or [] if isinstance(a, str)]}
            rng = p.get('value-range')
            if isinstance(rng, list) and len(rng) >= 2 and all(isinstance(v, (int, float)) for v in rng[:3]):
                prop['range'] = rng[:3]
            vl = p.get('value-list')
            if isinstance(vl, list):
                prop['list'] = [{'value': int(i.get('value', 0)), 'desc': str(i.get('description') or '')}
                                for i in vl if isinstance(i, dict) and isinstance(i.get('value'), (int, float))]
            if isinstance(p.get('unit'), str):
                prop['unit'] = p['unit']
            props.append(prop)
        actions = [{'iid': a['iid'], 'name': short_name(a['type']), 'desc': a.get('description') or '',
                    'in': [int(v) for v in a.get('in') or [] if isinstance(v, (int, float))]}
                   for a in s.get('actions') or []
                   if isinstance(a, dict) and isinstance(a.get('iid'), int) and isinstance(a.get('type'), str)]
        services.append({'iid': s['iid'], 'name': short_name(s['type']), 'desc': s.get('description') or '',
                         'props': props, 'actions': actions})
    return {'type': raw['type'], 'services': services} if services else None


def parse_index(raw) -> dict:
    best = {}
    for it in (raw or {}).get('instances') or [] if isinstance(raw, dict) else []:
        if not isinstance(it, dict):
            continue
        model, urn, ver = it.get('model'), it.get('type'), it.get('version', 0)
        if isinstance(model, str) and isinstance(urn, str) and (model not in best or ver > best[model][0]):
            best[model] = (ver if isinstance(ver, (int, float)) else 0, urn)
    return {m: v[1] for m, v in best.items()}


class SpecResolver:
    def __init__(self, http: aiohttp.ClientSession, cache_dir: pathlib.Path):
        self.http = http
        self.dir = cache_dir
        self.by_urn = {}
        self.index = None
        self._index_lock = asyncio.Lock()

    def _urn_path(self, urn):
        return self.dir / 'specs' / (hashlib.sha1(urn.encode()).hexdigest()[:16] + '.json')

    async def _load_index(self) -> dict:
        async with self._index_lock:
            if self.index is not None:
                return self.index
            path = self.dir / 'spec-index.json'
            cached = read_json(path)
            if cached and time.time() - cached.get('at', 0) < INDEX_MAX_AGE:
                self.index = cached.get('models') or {}
                return self.index
            try:
                async with self.http.get(INDEX_URL, timeout=aiohttp.ClientTimeout(total=45)) as r:
                    if r.status == 200:
                        self.index = parse_index(json.loads(await r.text()))
                        write_json(path, {'at': time.time(), 'models': self.index})
                        return self.index
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                log.warning('miot-spec index: %s', e.__class__.__name__)
            self.index = (cached or {}).get('models') or {}
            return self.index

    async def resolve(self, device: dict):
        urn = (device.get('spec_type') or '').strip()
        if not urn:
            urn = (await self._load_index()).get(device['model'], '')
        if not urn:
            return None
        if urn in self.by_urn:
            return self.by_urn[urn]
        path = self._urn_path(urn)
        spec = parse_spec(read_json(path))
        if spec is None:
            try:
                async with self.http.get(INSTANCE_URL + urllib.parse.quote(urn, safe=''),
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        raw = json.loads(await r.text())
                        spec = parse_spec(raw)
                        if spec:
                            write_json(path, raw)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                log.warning('miot-spec %s: %s', device['model'], e.__class__.__name__)
        if spec:
            self.by_urn[urn] = spec
        return spec

    async def resolve_all(self, devices) -> dict:
        """{model: spec or None}; one fetch per model, at most 6 at a time."""
        sem = asyncio.Semaphore(6)
        models = {}
        for d in devices:
            models.setdefault(d['model'], d)

        async def one(d):
            async with sem:
                return d['model'], await self.resolve(d)
        return dict(await asyncio.gather(*(one(d) for d in models.values())))
