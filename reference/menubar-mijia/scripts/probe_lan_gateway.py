#!/usr/bin/env python3
"""小米中枢网关与多模网关的局域网只读探测（#21，结论见 ADR-0016）。

只做两件事，都不改变设备状态：

1. 向指定 IP 的 UDP 54321 发 32 字节 miIO Hello，解析设备应答（did、时间戳、校验字段），
   手上有 token 时校验 ``md5(header + token)`` 并可选地执行一次白名单内的只读 RPC。
2. 对 8883（中枢网关本地 MIPS 端口）做一次 TLS 握手探测，只判断端口是否开着以及是否要求
   客户端证书，不发 MQTT CONNECT，不建立会话。

写方法一律拒绝：``build_rpc_packet`` 是所有请求的唯一出口，方法名不在
``ALLOWED_READONLY_METHODS`` 里就直接抛 ``PermissionError``，不存在调用写指令的代码路径。

用法::

    python3 scripts/probe_lan_gateway.py --ip 192.168.1.10
    python3 scripts/probe_lan_gateway.py --ip 192.168.1.10 --token <32-hex-token> --json

退出码：0 表示探测到 UDP 应答或 8883 端口有 TLS 监听；1 表示两项都没探到；2 表示参数错误。

输出自动脱敏：IP 只保留前两段，did 与 token 只给 sha256 指纹，属性值里的 IP、MAC、SSID
和凭据类键名同样打码。没有关闭脱敏的开关。

仅用 Python 3 标准库（AES-128-CBC 自带实现），macOS、Windows、Linux 行为一致。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import ssl
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

MIIO_MAGIC = 0x2131
MIIO_HEADER_LEN = 32
MIIO_UDP_PORT = 54321
CENTRAL_MIPS_PORT = 8883

# 只读方法的唯一白名单。新增条目等于扩大对真实设备的请求面，必须先确认它是只读语义。
ALLOWED_READONLY_METHODS = frozenset(
    {
        "miIO.info",
        "get_properties",
        "get_device_prop",
        "get_device_list",
    }
)

# 明确已知的写方法，只为给出准确的拒绝原因；任何不在白名单里的方法同样被拒绝。
KNOWN_WRITE_METHODS = frozenset(
    {
        "set_properties",
        "set_property",
        "set_device_prop",
        "action",
        "miIO.action",
        "miIO.set",
        "miIO.config_router",
        "miIO.ota",
        "miIO.rename",
        "miIO.set_room",
        "miIO.set_scene",
        "miIO.toggle",
        "miIO.start",
        "miIO.stop",
        "miIO.reboot",
        "miIO.reset",
    }
)

HELLO_PACKET = bytes.fromhex("21310020") + b"\xff" * 28
_HEADER = struct.Struct(">HHQI16s")


class ProtocolError(ValueError):
    """对端返回的报文不符合 miIO 帧结构。"""


# ---------------------------------------------------------------------------
# 只读门禁
# ---------------------------------------------------------------------------


def guard_read_only(method: Any) -> str:
    """返回合法的只读方法名，否则抛 ``PermissionError``。

    这是方法名进入网络层的唯一关口：写方法在这里被拦下，请求报文根本不会构造出来。
    """
    if not isinstance(method, str):
        raise PermissionError(f"refusing miIO method of type {type(method).__name__}")
    if method in KNOWN_WRITE_METHODS:
        raise PermissionError(f"refusing write method {method!r}: this probe is read-only")
    if method not in ALLOWED_READONLY_METHODS:
        raise PermissionError(
            f"refusing miIO method {method!r}: not in ALLOWED_READONLY_METHODS"
        )
    return method


# ---------------------------------------------------------------------------
# miIO 帧：Hello、密钥派生、PKCS#7、RPC
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelloReply:
    """32 字节 Hello 应答的稳定字段。原始报文不进入任何输出。"""

    did: int
    stamp: int
    checksum: bytes
    header: bytes

    def checksum_matches(self, token: bytes) -> bool:
        """已配置 token 的设备应答 ``md5(header + token)``，可据此确认 token 是否配对。"""
        if len(token) != 16:
            raise ValueError("token must be 16 bytes")
        return hmac.compare_digest(self.checksum, hashlib.md5(self.header + token).digest())

    def checksum_is_header_only(self) -> bool:
        """未配置 token 的设备可能回 ``md5(header)``，用于区分「缺 token」与「token 不符」。"""
        return hmac.compare_digest(self.checksum, hashlib.md5(self.header).digest())


def build_hello() -> bytes:
    """构造 python-miio 与上游实现都接受的 32 字节 Hello：``21 31 00 20`` + 28 个 ``ff``。"""
    return HELLO_PACKET


def parse_hello(payload: bytes) -> HelloReply:
    if len(payload) != MIIO_HEADER_LEN:
        raise ProtocolError(f"hello reply must be {MIIO_HEADER_LEN} bytes, got {len(payload)}")
    magic, length, did, stamp, checksum = _HEADER.unpack(payload)
    if magic != MIIO_MAGIC:
        raise ProtocolError(f"unexpected magic 0x{magic:04x}")
    if length != MIIO_HEADER_LEN:
        raise ProtocolError(f"unexpected length field {length}")
    return HelloReply(did=did, stamp=stamp, checksum=checksum, header=payload[:16])


def derive_key_iv(token: bytes) -> tuple[bytes, bytes]:
    """miIO 的 AES-128 密钥与 IV：``key = md5(token)``，``iv = md5(key + token)``。"""
    if len(token) != 16:
        raise ValueError(f"token must be 16 bytes, got {len(token)}")
    key = hashlib.md5(token).digest()
    return key, hashlib.md5(key + token).digest()


def pad_pkcs7(data: bytes, block_size: int = 16) -> bytes:
    if not 1 <= block_size <= 255:
        raise ValueError(f"invalid block size {block_size}")
    padding = block_size - len(data) % block_size
    return data + bytes([padding]) * padding


def unpad_pkcs7(data: bytes, block_size: int = 16) -> bytes:
    if not 1 <= block_size <= 255:
        raise ValueError(f"invalid block size {block_size}")
    if not data or len(data) % block_size:
        raise ValueError("padded data must be a non-empty multiple of the block size")
    padding = data[-1]
    if padding == 0 or padding > block_size or data[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid PKCS#7 padding")
    return data[:-padding]


def build_rpc_packet(
    token: bytes,
    did: int,
    method: str,
    params: Any = None,
    msg_id: int | None = None,
    stamp: int = 0,
) -> bytes:
    """构造一条只读 RPC 请求。写方法在这里抛 ``PermissionError``，不会生成任何字节。"""
    guard_read_only(method)
    request = {
        "id": msg_id if msg_id is not None else secrets.randbelow(1 << 31),
        "method": method,
        "params": [] if params is None else params,
    }
    return build_packet(token, did, stamp, json.dumps(request).encode("utf-8"))


def build_packet(token: bytes, did: int, stamp: int, clear: bytes) -> bytes:
    """把明文载荷封装成 miIO 帧：先 PKCS#7 填充，再 AES-128-CBC，最后补整包 md5。

    请求只经 ``build_rpc_packet`` 进入这里；本函数是纯帧封装，应答与测试夹具也用它。
    """
    if len(token) != 16:
        raise ValueError(f"token must be 16 bytes, got {len(token)}")
    key, iv = derive_key_iv(token)
    ciphertext = aes128_cbc_encrypt(key, iv, pad_pkcs7(clear))
    total = MIIO_HEADER_LEN + len(ciphertext)
    header = _HEADER.pack(MIIO_MAGIC, total, did, stamp, token)
    packet = header + ciphertext
    return packet[:16] + hashlib.md5(packet).digest() + packet[MIIO_HEADER_LEN:]


def parse_packet(token: bytes, payload: bytes) -> dict:
    """校验整包 md5 后解密，返回 JSON 载荷。"""
    if len(token) != 16:
        raise ValueError(f"token must be 16 bytes, got {len(token)}")
    if len(payload) < MIIO_HEADER_LEN:
        raise ProtocolError(f"packet shorter than header: {len(payload)} bytes")
    magic, length, _did, _stamp, checksum = _HEADER.unpack(payload[:MIIO_HEADER_LEN])
    if magic != MIIO_MAGIC:
        raise ProtocolError(f"unexpected magic 0x{magic:04x}")
    if length != len(payload):
        raise ProtocolError(f"length field {length} does not match {len(payload)} received")
    expected = hashlib.md5(payload[:16] + token + payload[MIIO_HEADER_LEN:]).digest()
    if not hmac.compare_digest(checksum, expected):
        raise ProtocolError("packet checksum mismatch")
    key, iv = derive_key_iv(token)
    try:
        clear = unpad_pkcs7(aes128_cbc_decrypt(key, iv, payload[MIIO_HEADER_LEN:]))
    except ValueError as error:
        raise ProtocolError(f"payload decryption failed: {error}") from error
    try:
        message = json.loads(clear.rstrip(b"\x00").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError(f"payload is not JSON: {error}") from error
    if not isinstance(message, dict):
        raise ProtocolError("payload is not a JSON object")
    return message


# ---------------------------------------------------------------------------
# AES-128-CBC（FIPS-197 / SP 800-38A），仅标准库
# ---------------------------------------------------------------------------

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)
_INV_SBOX = bytearray(256)
for _index, _value in enumerate(_SBOX):
    _INV_SBOX[_value] = _index
del _index, _value


def _xtime(value: int) -> int:
    value <<= 1
    return (value ^ 0x11B) & 0xFF if value & 0x100 else value


def _mul(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        left = _xtime(left)
        right >>= 1
    return result


def _expand_key(key: bytes) -> list[bytes]:
    if len(key) != 16:
        raise ValueError(f"AES-128 needs a 16-byte key, got {len(key)}")
    words = [list(key[offset : offset + 4]) for offset in range(0, 16, 4)]
    round_constant = 1
    for index in range(4, 44):
        temp = list(words[index - 1])
        if index % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[byte] for byte in temp]
            temp[0] ^= round_constant
            round_constant = _xtime(round_constant)
        words.append([words[index - 4][position] ^ temp[position] for position in range(4)])
    schedule = []
    for round_index in range(11):
        round_words = words[round_index * 4 : round_index * 4 + 4]
        schedule.append(bytes(byte for word in round_words for byte in word))
    return schedule


def _state(block: bytes) -> list[int]:
    return [block[4 * column + row] for column in range(4) for row in range(4)]


def _block(state: list[int]) -> bytes:
    return bytes(state[4 * column + row] for column in range(4) for row in range(4))


def _add_round_key(state: list[int], round_key: bytes) -> None:
    for position in range(16):
        state[position] ^= round_key[position]


def _sub_bytes(state: list[int]) -> None:
    for position in range(16):
        state[position] = _SBOX[state[position]]


def _inv_sub_bytes(state: list[int]) -> None:
    for position in range(16):
        state[position] = _INV_SBOX[state[position]]


def _shift_rows(state: list[int]) -> None:
    previous = list(state)
    for row in range(1, 4):
        for column in range(4):
            state[row + 4 * column] = previous[row + 4 * ((column + row) % 4)]


def _inv_shift_rows(state: list[int]) -> None:
    previous = list(state)
    for row in range(1, 4):
        for column in range(4):
            state[row + 4 * column] = previous[row + 4 * ((column - row) % 4)]


def _mix_columns(state: list[int]) -> None:
    for column in range(4):
        a0, a1, a2, a3 = state[4 * column : 4 * column + 4]
        state[4 * column] = _mul(a0, 2) ^ _mul(a1, 3) ^ a2 ^ a3
        state[4 * column + 1] = a0 ^ _mul(a1, 2) ^ _mul(a2, 3) ^ a3
        state[4 * column + 2] = a0 ^ a1 ^ _mul(a2, 2) ^ _mul(a3, 3)
        state[4 * column + 3] = _mul(a0, 3) ^ a1 ^ a2 ^ _mul(a3, 2)


def _inv_mix_columns(state: list[int]) -> None:
    for column in range(4):
        a0, a1, a2, a3 = state[4 * column : 4 * column + 4]
        state[4 * column] = _mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9)
        state[4 * column + 1] = _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13)
        state[4 * column + 2] = _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11)
        state[4 * column + 3] = _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14)


def _encrypt_block(schedule: list[bytes], block: bytes) -> bytes:
    state = _state(block)
    _add_round_key(state, schedule[0])
    for round_index in range(1, 10):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, schedule[round_index])
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, schedule[10])
    return _block(state)


def _decrypt_block(schedule: list[bytes], block: bytes) -> bytes:
    state = _state(block)
    _add_round_key(state, schedule[10])
    for round_index in range(9, 0, -1):
        _inv_shift_rows(state)
        _inv_sub_bytes(state)
        _add_round_key(state, schedule[round_index])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _inv_sub_bytes(state)
    _add_round_key(state, schedule[0])
    return _block(state)


def aes128_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    if len(iv) != 16:
        raise ValueError(f"IV must be 16 bytes, got {len(iv)}")
    if not plaintext or len(plaintext) % 16:
        raise ValueError("plaintext must be a non-empty multiple of 16 bytes")
    schedule = _expand_key(key)
    previous = iv
    ciphertext = bytearray()
    for offset in range(0, len(plaintext), 16):
        block = bytes(left ^ right for left, right in zip(plaintext[offset : offset + 16], previous))
        previous = _encrypt_block(schedule, block)
        ciphertext += previous
    return bytes(ciphertext)


def aes128_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    if len(iv) != 16:
        raise ValueError(f"IV must be 16 bytes, got {len(iv)}")
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError("ciphertext must be a non-empty multiple of 16 bytes")
    schedule = _expand_key(key)
    previous = iv
    plaintext = bytearray()
    for offset in range(0, len(ciphertext), 16):
        block = ciphertext[offset : offset + 16]
        decrypted = _decrypt_block(schedule, block)
        plaintext += bytes(left ^ right for left, right in zip(decrypted, previous))
        previous = block
    return bytes(plaintext)


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

# 比较前先把键名归一化成小写字母数字，因此 serial_number、serialNo、S/N 都会命中。
_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "did",
        "deviceid",
        "mac",
        "ip",
        "bssid",
        "ssid",
        "password",
        "passwd",
        "key",
        "ssecurity",
        "serial",
        "serialnumber",
        "serialno",
        "sn",
    }
)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_MAC = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")


def _is_sensitive_key(key: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", key.lower()) in _SENSITIVE_KEYS


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact_ip(host: str) -> str:
    """只保留地址前缀：IPv4 前两段，IPv6 前两组。"""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "<host:" + _fingerprint(host) + ">"
    if address.version == 4:
        parts = str(address).split(".")
        return f"{parts[0]}.{parts[1]}.*.*"
    parts = address.exploded.split(":")
    return f"{parts[0]}:{parts[1]}:*"


def redact_did(did: int) -> str:
    return "did:" + _fingerprint(str(did))


def redact_token(token: bytes) -> str:
    return f"token:{len(token)}B:sha256:" + _fingerprint(token.hex())


def _scrub_text(text: str) -> str:
    scrubbed = _IPV4.sub(lambda match: redact_ip(match.group(0)), text)
    scrubbed = _MAC.sub(lambda match: "mac:" + _fingerprint(match.group(0).lower()), scrubbed)
    words = []
    for word in scrubbed.split(" "):
        candidate = word.strip("\"'(),[]{};")
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            words.append(word)
        else:
            words.append(word.replace(candidate, redact_ip(candidate)))
    return " ".join(words)


class Redactor:
    """输出边界的脱敏器：已知秘密按整词替换，字段按键名脱敏，其余按模式替换。"""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # 太短的秘密做字面替换会把正常文字吃掉（例如 did 为 1 时替换掉所有 "1"）。
        candidates = {secret for secret in secrets if len(secret) >= 8}
        self._secrets = sorted(candidates, key=len, reverse=True)
        self._values = frozenset(candidates)

    def text(self, message: str) -> str:
        scrubbed = message
        for secret in self._secrets:
            # 已知秘密一律字面替换。邻接字母数字时也要抹掉，宁可过度脱敏。
            scrubbed = scrubbed.replace(secret, "<redacted>")
        return _scrub_text(scrubbed)

    def value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    "<redacted>"
                    if isinstance(key, str) and _is_sensitive_key(key)
                    else self.value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            if value in self._values:
                return "<redacted>"
            return self.text(value)
        return value


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UdpProbe:
    status: str
    reply: HelloReply | None = None
    token_matches: bool | None = None
    detail: str = ""


@dataclass(frozen=True)
class TlsProbe:
    status: str
    detail: str = ""


@dataclass(frozen=True)
class ReadProbe:
    method: str
    status: str
    result: Any = None
    detail: str = ""


@dataclass
class Report:
    host: str
    udp_port: int
    tls_port: int
    udp: UdpProbe | None = None
    tls: TlsProbe | None = None
    read: ReadProbe | None = None
    secrets: list[str] = field(default_factory=list, repr=False)

    def verdict(self) -> str:
        udp_reachable = self.udp is not None and self.udp.status == "answered"
        tls_reachable = self.tls is not None and self.tls.status in (
            "tls_ok",
            "client_cert_required",
        )
        return "reachable" if udp_reachable or tls_reachable else "unreachable"


def _socket_family(host: str) -> int:
    return socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET


def _describe_os_error(error: OSError) -> str:
    code = error.errno if error.errno is not None else "?"
    return f"{type(error).__name__} (errno {code})"


def probe_udp_hello(host: str, port: int = MIIO_UDP_PORT, timeout: float = 2.0, token: bytes | None = None) -> UdpProbe:
    """发一次 Hello，不重试。超时与拒绝都要能被调用方区分。"""
    with socket.socket(_socket_family(host), socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            sock.send(build_hello())
            payload = sock.recv(2048)
        except TimeoutError:
            return UdpProbe(status="timeout", detail=f"no reply within {timeout:g}s")
        except ConnectionRefusedError as error:
            return UdpProbe(status="closed", detail=_describe_os_error(error))
        except OSError as error:
            return UdpProbe(status="error", detail=_describe_os_error(error))
    try:
        reply = parse_hello(payload)
    except ProtocolError as error:
        return UdpProbe(status="error", detail=str(error))
    matches = reply.checksum_matches(token) if token is not None else None
    return UdpProbe(status="answered", reply=reply, token_matches=matches)


def classify_tls_error(error: ssl.SSLError) -> str:
    message = " ".join(str(part) for part in error.args).lower()
    if "certificate required" in message or "cert required" in message:
        return "client_cert_required"
    return "rejected"


def probe_tls(host: str, port: int = CENTRAL_MIPS_PORT, timeout: float = 2.0) -> TlsProbe:
    """只做一次 TLS 握手。不校验证书（探测目的），也不发送应用层数据。"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        raw_socket = socket.create_connection((host, port), timeout=timeout)
    except TimeoutError:
        return TlsProbe(status="timeout", detail=f"no reply within {timeout:g}s")
    except ConnectionRefusedError as error:
        return TlsProbe(status="closed", detail=_describe_os_error(error))
    except OSError as error:
        return TlsProbe(status="error", detail=_describe_os_error(error))
    # 端口已接受连接，之后的失败都属于"不是可用的 TLS 端点"，不能再报成网络错误。
    try:
        with raw_socket:
            with context.wrap_socket(raw_socket) as tls_socket:
                cipher = tls_socket.cipher()
                return TlsProbe(status="tls_ok", detail=f"{tls_socket.version()} {cipher[0]}")
    except ssl.SSLError as error:
        return TlsProbe(status=classify_tls_error(error), detail=str(error))
    except TimeoutError:
        return TlsProbe(status="timeout", detail=f"no reply within {timeout:g}s")
    except OSError as error:
        return TlsProbe(status="rejected", detail=_describe_os_error(error))


def probe_read(
    host: str,
    port: int,
    timeout: float,
    token: bytes,
    did: int,
    method: str,
    params: Any,
    stamp: int = 0,
) -> ReadProbe:
    """发一条白名单内的只读 RPC。写方法在构包前抛 ``PermissionError``。"""
    packet = build_rpc_packet(token, did, method, params, stamp=stamp)
    with socket.socket(_socket_family(host), socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            sock.send(packet)
            payload = sock.recv(8192)
        except TimeoutError:
            return ReadProbe(method=method, status="timeout", detail=f"no reply within {timeout:g}s")
        except OSError as error:
            return ReadProbe(method=method, status="error", detail=_describe_os_error(error))
    try:
        message = parse_packet(token, payload)
    except ProtocolError as error:
        return ReadProbe(method=method, status="error", detail=str(error))
    if "error" in message:
        return ReadProbe(method=method, status="error", result=message["error"])
    return ReadProbe(method=method, status="ok", result=message.get("result"))


# ---------------------------------------------------------------------------
# 输出与命令行
# ---------------------------------------------------------------------------


def report_to_dict(report: Report) -> dict:
    redactor = Redactor(report.secrets)
    payload: dict[str, Any] = {
        "target": {"host": redact_ip(report.host), "udpPort": report.udp_port, "tlsPort": report.tls_port},
        "verdict": report.verdict(),
    }
    if report.udp is not None:
        udp: dict[str, Any] = {"status": report.udp.status, "detail": redactor.text(report.udp.detail)}
        if report.udp.reply is not None:
            udp["did"] = redact_did(report.udp.reply.did)
            udp["checksum"] = _checksum_state(report.udp)
        payload["udp"] = udp
    if report.tls is not None:
        payload["tls"] = {"status": report.tls.status, "detail": redactor.text(report.tls.detail)}
    if report.read is not None:
        payload["read"] = {
            "method": report.read.method,
            "status": report.read.status,
            "result": redactor.value(report.read.result),
            "detail": redactor.text(report.read.detail),
        }
    return payload


def _checksum_state(udp: UdpProbe) -> str:
    if udp.token_matches is True:
        return "md5(header+token) verified"
    if udp.token_matches is False:
        return "md5(header+token) mismatch"
    if udp.reply is not None and udp.reply.checksum_is_header_only():
        return "md5(header) only, token not in reply"
    return "unverified, pass --token to check"


def report_to_text(report: Report) -> str:
    payload = report_to_dict(report)
    lines = [
        f"target: {payload['target']['host']} (udp {report.udp_port}, tls {report.tls_port})",
        f"verdict: {payload['verdict']}",
    ]
    if "udp" in payload:
        udp = payload["udp"]
        extra = ""
        if "did" in udp:
            extra = f", {udp['did']}, {udp['checksum']}"
        elif udp["detail"]:
            extra = f", {udp['detail']}"
        lines.append(f"udp hello: {udp['status']}{extra}")
    if "tls" in payload:
        tls = payload["tls"]
        suffix = f", {tls['detail']}" if tls["detail"] else ""
        lines.append(f"tls port {report.tls_port}: {tls['status']}{suffix}")
    if "read" in payload:
        read = payload["read"]
        if read["status"] == "ok":
            body = json.dumps(read["result"], ensure_ascii=False, sort_keys=True)
            lines.append(f"read {read['method']}: ok {body}")
        else:
            detail = read["detail"] or json.dumps(read["result"], ensure_ascii=False)
            lines.append(f"read {read['method']}: {read['status']} {detail}".rstrip())
    return "\n".join(lines)


def parse_token(raw: str) -> bytes:
    text = raw.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", text):
        return bytes.fromhex(text)
    encoded = text.encode("utf-8")
    if len(encoded) == 16:
        return encoded
    raise ValueError("token must be 32 hex characters or 16 raw ASCII characters")


def parse_params(raw: str | None) -> Any:
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid --params JSON at position {error.pos}: {error.msg}") from error
    if not isinstance(value, (list, dict)):
        raise ValueError("--params must be a JSON list or object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读探测小米网关的局域网 miIO(54321) 与中枢网关 MIPS(8883) 端口。",
    )
    parser.add_argument("--ip", required=True, help="网关或设备的局域网 IP 字面量")
    parser.add_argument("--udp-port", type=int, default=MIIO_UDP_PORT, help="miIO UDP 端口，默认 54321")
    parser.add_argument("--tls-port", type=int, default=CENTRAL_MIPS_PORT, help="MIPS TLS 端口，默认 8883")
    parser.add_argument("--timeout", type=float, default=2.0, help="单次探测超时秒数，默认 2")
    parser.add_argument("--token", help="16 字节设备 token，32 位十六进制或 16 个 ASCII 字符")
    parser.add_argument("--did", type=int, help="读取时使用的设备 did，默认取 Hello 应答")
    parser.add_argument(
        "--method",
        choices=sorted(ALLOWED_READONLY_METHODS),
        default="miIO.info",
        help="只读方法，默认 miIO.info",
    )
    parser.add_argument("--params", help="只读方法的 JSON 参数，默认空列表")
    parser.add_argument("--skip-udp", action="store_true", help="跳过 UDP Hello 探测")
    parser.add_argument("--skip-tls", action="store_true", help="跳过 8883 TLS 探测")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        host = str(ipaddress.ip_address(arguments.ip))
        token = parse_token(arguments.token) if arguments.token else None
        params = parse_params(arguments.params)
        if not 0 < arguments.udp_port < 65536 or not 0 < arguments.tls_port < 65536:
            raise ValueError("ports must be between 1 and 65535")
        if arguments.timeout <= 0:
            raise ValueError("--timeout must be positive")
        guard_read_only(arguments.method)
    except (ValueError, PermissionError) as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")

    report = Report(host=host, udp_port=arguments.udp_port, tls_port=arguments.tls_port)
    if token is not None:
        report.secrets.append(token.hex())
        if all(32 <= byte < 127 for byte in token):
            report.secrets.append(token.decode("ascii"))
    if arguments.token:
        report.secrets.append(arguments.token)
    if not arguments.skip_udp:
        report.udp = probe_udp_hello(host, arguments.udp_port, arguments.timeout, token)
        if report.udp.reply is not None:
            report.secrets.append(str(report.udp.reply.did))
    if not arguments.skip_tls:
        report.tls = probe_tls(host, arguments.tls_port, arguments.timeout)
    if token is not None and not arguments.skip_udp:
        reply = report.udp.reply if report.udp is not None else None
        did = arguments.did if arguments.did is not None else (reply.did if reply else None)
        if did is None:
            report.read = ReadProbe(
                method=arguments.method, status="skipped", detail="no did, pass --did"
            )
        else:
            report.secrets.append(str(did))
            stamp = reply.stamp if reply is not None else int(time.time())
            report.read = probe_read(
                host,
                arguments.udp_port,
                arguments.timeout,
                token,
                did,
                arguments.method,
                params,
                stamp,
            )
    elif report.udp is not None and report.udp.status == "answered":
        report.read = ReadProbe(method=arguments.method, status="skipped", detail="no token")

    output = (
        json.dumps(report_to_dict(report), ensure_ascii=False, indent=2)
        if arguments.json
        else report_to_text(report)
    )
    print(output)
    return 0 if report.verdict() == "reachable" else 1


if __name__ == "__main__":
    sys.exit(main())
