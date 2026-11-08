"""Mi cloud request signing and RC4 encryption.

Ported from MenuBarMijia (MIT, jryang1997): windows/src/core/miCloudCrypto.ts and
Sources/MijiaCore/MiCloud/MiCloudCrypto.swift. Standard library only.

A request to api.io.mi.com/app<path>:
  nonce        = b64(8 random bytes + uint32_be(epoch_minutes))
  signed_nonce = b64(sha256(b64d(ssecurity) + b64d(nonce)))
  rc4_hash     = b64(sha1("POST&<path>&data=<json>&<signed_nonce>"))
  data, rc4_hash__ each RC4-encrypted with key b64d(signed_nonce), first 1024 keystream bytes dropped
  signature    = b64(sha1("POST&<path>&data=<enc>&rc4_hash__=<enc>&<signed_nonce>"))
The response body is b64 RC4 ciphertext under the same key (or plain JSON for some errors).
"""
import base64
import hashlib
import json
import math
import os
import struct
import time
import urllib.parse


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode('ascii')


def b64d(s: str) -> bytes:
    return base64.b64decode(s.strip(), validate=True)


def rc4(key: bytes, data: bytes, drop: int = 1024) -> bytes:
    if not key:
        raise ValueError('RC4 key must not be empty')
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xff
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray(len(data))
    for n in range(drop + len(data)):
        i = (i + 1) & 0xff
        j = (j + s[i]) & 0xff
        s[i], s[j] = s[j], s[i]
        if n >= drop:
            out[n - drop] = data[n - drop] ^ s[(s[i] + s[j]) & 0xff]
    return bytes(out)


def make_nonce(now_ms: float = None, random_bytes: bytes = None) -> str:
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    rnd = random_bytes if random_bytes is not None else os.urandom(8)
    return b64e(rnd + struct.pack('>I', int(now_ms // 60000) & 0xffffffff))


def signed_nonce(ssecurity: str, nonce: str) -> str:
    return b64e(hashlib.sha256(b64d(ssecurity) + b64d(nonce)).digest())


def sha1_signature(path: str, params, signed: str, method: str = 'POST') -> str:
    parts = [method.upper(), path, *(f'{k}={v}' for k, v in params), signed]
    return b64e(hashlib.sha1('&'.join(parts).encode()).digest())


def encrypt(plain: str, signed: str) -> str:
    return b64e(rc4(b64d(signed), plain.encode()))


def decrypt(body: str, signed: str) -> str:
    return rc4(b64d(signed), b64d(body)).decode()


def compact_json(value) -> str:
    """Sorted keys, no spaces, integral floats as integers (MIoT rejects 40.0 for an int property)."""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    if isinstance(value, dict):
        return '{' + ','.join(f'{json.dumps(str(k), ensure_ascii=False)}:{compact_json(value[k])}'
                              for k in sorted(value)) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(compact_json(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def encrypt_params(path: str, data: str, ssecurity: str, nonce: str):
    signed = signed_nonce(ssecurity, nonce)
    rc4_hash = sha1_signature(path, [('data', data)], signed)
    enc = [('data', encrypt(data, signed)), ('rc4_hash__', encrypt(rc4_hash, signed))]
    return [*enc, ('signature', sha1_signature(path, enc, signed)), ('ssecurity', ssecurity),
            ('_nonce', nonce)]


def form_encode(fields) -> str:
    """Only A-Za-z0-9-._~ stay as they are (space is %20)."""
    return '&'.join(f'{urllib.parse.quote(k, safe="-._~")}={urllib.parse.quote(v, safe="-._~")}'
                    for k, v in fields)
