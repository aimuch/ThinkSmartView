#!/usr/bin/env python3
"""`scripts/probe_lan_gateway.py` 的回归测试：协议向量、只读门禁、脱敏与回环探测。

回环用例起一个假设备，只监听 127.0.0.1 的临时端口，不接触任何真实网关。
运行：python3 -m unittest discover -s scripts -p 'test_probe_lan_gateway.py'
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import socket
import ssl
import struct
import sys
import threading
import unittest
from pathlib import Path

# Importing the module would otherwise drop a __pycache__ entry next to it.
sys.dont_write_bytecode = True

MODULE_PATH = Path(__file__).resolve().with_name("probe_lan_gateway.py")
_spec = importlib.util.spec_from_file_location("probe_lan_gateway", str(MODULE_PATH))
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_lan_gateway"] = probe
_spec.loader.exec_module(probe)

TOKEN = b"0123456789abcdef"
DID = 0x1A2B3C4D5E6F
STAMP = 1700000000


def hello_reply(did: int = DID, stamp: int = STAMP, checksum: bytes | None = None) -> bytes:
    header = bytes([0x21, 0x31, 0x00, 0x20]) + struct.pack(">QI", did, stamp)
    return header + (checksum if checksum is not None else hashlib.md5(header + TOKEN).digest())


class LoopbackMiioDevice:
    """回环 UDP 假设备：应答 Hello，并按协议解密只读 RPC 后返回固定属性。"""

    def __init__(self, token: bytes = TOKEN, did: int = DID, stamp: int = STAMP) -> None:
        self.token = token
        self.did = did
        self.stamp = stamp
        self.requests: list[dict] = []
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "LoopbackMiioDevice":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.socket.close()
        self.thread.join(timeout=1)

    def _serve(self) -> None:
        while True:
            try:
                data, address = self.socket.recvfrom(8192)
            except OSError:
                return
            reply = self._reply(data)
            if reply is None:
                continue
            try:
                self.socket.sendto(reply, address)
            except OSError:
                return

    def _reply(self, data: bytes) -> bytes | None:
        if len(data) == 32:
            return hello_reply(self.did, self.stamp)
        message = probe.parse_packet(self.token, data)
        self.requests.append(message)
        payload = {
            "id": message["id"],
            "result": {
                "model": "demo.gateway",
                "did": str(self.did),
                "ip": "192.0.2.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "ssid": "home",
                "serial": "SN-AQARA-0001",
                "serial_number": "SN-AQARA-0002",
                "note": "echo " + self.token.decode("ascii"),
            },
        }
        return probe.build_packet(self.token, self.did, self.stamp, json.dumps(payload).encode())


class HelloPacketTests(unittest.TestCase):
    def test_hello_is_the_canonical_32_byte_probe(self) -> None:
        packet = probe.build_hello()
        self.assertEqual(len(packet), 32)
        self.assertEqual(packet[:4], bytes([0x21, 0x31, 0x00, 0x20]))
        self.assertEqual(packet[4:], b"\xff" * 28)

    def test_hello_reply_parses_did_stamp_and_checksum(self) -> None:
        reply = probe.parse_hello(hello_reply())
        self.assertEqual(reply.did, DID)
        self.assertEqual(reply.stamp, STAMP)
        self.assertEqual(reply.header, hello_reply()[:16])

    def test_hello_reply_rejects_short_payload(self) -> None:
        with self.assertRaises(probe.ProtocolError):
            probe.parse_hello(hello_reply()[:31])

    def test_hello_reply_rejects_wrong_magic(self) -> None:
        broken = bytes([0x21, 0x32]) + hello_reply()[2:]
        with self.assertRaises(probe.ProtocolError):
            probe.parse_hello(broken)

    def test_checksum_matches_only_the_right_token(self) -> None:
        reply = probe.parse_hello(hello_reply())
        self.assertTrue(reply.checksum_matches(TOKEN))
        self.assertFalse(reply.checksum_matches(bytes([0xFF]) * 16))
        self.assertFalse(reply.checksum_is_header_only())

    def test_checksum_without_token_is_recognised(self) -> None:
        header = hello_reply()[:16]
        reply = probe.parse_hello(header + hashlib.md5(header).digest())
        self.assertTrue(reply.checksum_is_header_only())
        self.assertFalse(reply.checksum_matches(TOKEN))


class CryptoVectorTests(unittest.TestCase):
    def test_aes128_matches_fips197_single_block(self) -> None:
        key = bytes(range(16))
        plaintext = bytes(range(0, 256, 17))
        ciphertext = probe.aes128_cbc_encrypt(key, bytes(16), plaintext)
        expected = bytes([0x69, 0xC4, 0xE0, 0xD8, 0x6A, 0x7B, 0x04, 0x30, 0xD8, 0xCD, 0xB7, 0x80, 0x70, 0xB4, 0xC5, 0x5A])
        self.assertEqual(ciphertext[:16], expected)

    def test_aes128_matches_nist_cbc_first_block(self) -> None:
        key = bytes([0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6, 0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C])
        plaintext = bytes([0x6B, 0xC1, 0xBE, 0xE2, 0x2E, 0x40, 0x9F, 0x96, 0xE9, 0x3D, 0x7E, 0x11, 0x73, 0x93, 0x17, 0x2A])
        expected = bytes([0x76, 0x49, 0xAB, 0xAC, 0x81, 0x19, 0xB2, 0x46, 0xCE, 0xE9, 0x8E, 0x9B, 0x12, 0xE9, 0x19, 0x7D])
        ciphertext = probe.aes128_cbc_encrypt(key, bytes(range(16)), plaintext)
        self.assertEqual(ciphertext[:16], expected)

    def test_cbc_round_trip_across_blocks(self) -> None:
        key = bytes(range(16))
        iv = bytes(range(16, 32))
        plaintext = bytes(range(48))
        ciphertext = probe.aes128_cbc_encrypt(key, iv, plaintext)
        self.assertNotEqual(ciphertext[:16], plaintext[:16])
        self.assertEqual(probe.aes128_cbc_decrypt(key, iv, ciphertext), plaintext)

    def test_pinned_key_iv_derivation(self) -> None:
        key, iv = probe.derive_key_iv(TOKEN)
        expected_key = bytes([0x40, 0x32, 0xAF, 0x8D, 0x61, 0x03, 0x51, 0x23, 0x90, 0x6E, 0x58, 0xE0, 0x67, 0x14, 0x0C, 0xC5])
        expected_iv = bytes([0xA7, 0xB1, 0xEA, 0x76, 0xA5, 0x10, 0xAE, 0xEF, 0x6D, 0x85, 0x6B, 0x3E, 0xC9, 0x10, 0x98, 0xC8])
        self.assertEqual(key, expected_key)
        self.assertEqual(iv, expected_iv)

    def test_derive_rejects_wrong_token_length(self) -> None:
        with self.assertRaises(ValueError):
            probe.derive_key_iv(b"short")

    def test_pkcs7_pads_full_block_with_a_whole_block(self) -> None:
        self.assertEqual(probe.pad_pkcs7(b"abc")[3:], bytes([13]) * 13)
        self.assertEqual(len(probe.pad_pkcs7(bytes(16))), 32)

    def test_unpad_rejects_invalid_padding(self) -> None:
        for broken in (b"", bytes(15) + bytes([0]), bytes(15) + bytes([17]), bytes(15) + bytes([2])):
            with self.subTest(padding=broken[-1:]):
                with self.assertRaises(ValueError):
                    probe.unpad_pkcs7(broken)


class ReadOnlyGateTests(unittest.TestCase):
    def test_allowlist_is_pinned(self) -> None:
        self.assertEqual(
            probe.ALLOWED_READONLY_METHODS,
            frozenset({"miIO.info", "get_properties", "get_device_prop", "get_device_list"}),
        )

    def test_known_write_methods_never_overlap_the_allowlist(self) -> None:
        self.assertTrue(probe.KNOWN_WRITE_METHODS.isdisjoint(probe.ALLOWED_READONLY_METHODS))

    def test_every_known_write_method_raises_permission_error(self) -> None:
        for method in sorted(probe.KNOWN_WRITE_METHODS):
            with self.subTest(method=method):
                with self.assertRaises(PermissionError):
                    probe.guard_read_only(method)

    def test_unknown_methods_are_refused_by_the_allowlist(self) -> None:
        for method in ("get_properties_v2", "miIO.info ", "", None, 42):
            with self.subTest(method=method):
                with self.assertRaises(PermissionError):
                    probe.guard_read_only(method)

    def test_build_rpc_packet_refuses_a_write_before_framing(self) -> None:
        for method in ("set_properties", "action", "miIO.config_router"):
            with self.subTest(method=method):
                with self.assertRaises(PermissionError):
                    probe.build_rpc_packet(TOKEN, DID, method, [{"siid": 2, "piid": 1}])

    def test_allowed_method_round_trips_through_the_frame(self) -> None:
        packet = probe.build_rpc_packet(
            TOKEN, DID, "get_properties", [{"siid": 2, "piid": 1}], msg_id=7, stamp=STAMP
        )
        message = probe.parse_packet(TOKEN, packet)
        self.assertEqual(message["id"], 7)
        self.assertEqual(message["method"], "get_properties")
        self.assertEqual(message["params"], [{"siid": 2, "piid": 1}])

    def test_probe_read_refuses_a_write_before_opening_a_socket(self) -> None:
        with self.assertRaises(PermissionError):
            probe.probe_read("127.0.0.1", 9, 0.05, TOKEN, DID, "set_properties", [])

    def test_frame_checksum_rejects_a_foreign_token(self) -> None:
        packet = probe.build_rpc_packet(TOKEN, DID, "miIO.info", msg_id=1)
        with self.assertRaises(probe.ProtocolError):
            probe.parse_packet(bytes([0x5A]) * 16, packet)

    def test_corrupt_ciphertext_is_a_protocol_error_not_a_crash(self) -> None:
        key, iv = probe.derive_key_iv(TOKEN)
        ciphertext = probe.aes128_cbc_encrypt(key, iv, bytes(16))
        header = struct.pack(">HHQI16s", 0x2131, 32 + len(ciphertext), DID, STAMP, TOKEN)
        packet = header + ciphertext
        framed = packet[:16] + hashlib.md5(packet).digest() + packet[32:]
        with self.assertRaises(probe.ProtocolError):
            probe.parse_packet(TOKEN, framed)


class RedactionTests(unittest.TestCase):
    def test_ipv4_keeps_only_the_prefix(self) -> None:
        self.assertEqual(probe.redact_ip("192.168.1.20"), "192.168.*.*")
        self.assertEqual(probe.redact_ip("192.0.2.10"), "192.0.*.*")

    def test_ipv6_keeps_only_the_prefix(self) -> None:
        self.assertEqual(probe.redact_ip("2001:db8::1"), "2001:0db8:*")

    def test_did_and_token_become_fingerprints(self) -> None:
        did_text = probe.redact_did(DID)
        token_text = probe.redact_token(TOKEN)
        self.assertTrue(did_text.startswith("did:"))
        self.assertNotIn(str(DID), did_text)
        self.assertNotIn(TOKEN.hex(), token_text)
        self.assertEqual(probe.redact_did(DID), did_text)

    def test_property_payload_masks_identifiers(self) -> None:
        redactor = probe.Redactor([str(DID), TOKEN.hex()])
        masked = redactor.value(
            {
                "model": "demo.gateway",
                "did": str(DID),
                "note": "seen at 192.0.2.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "ssid": "home",
                "serial": "SN-AQARA-0001",
                "serial_number": "SN-AQARA-0002",
                "serialNo": "SN-AQARA-0003",
                "sn": "0000000004",
                "S/N": "0000000005",
            }
        )
        rendered = json.dumps(masked, ensure_ascii=False)
        self.assertNotIn(str(DID), rendered)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertNotIn("aa:bb:cc:dd:ee:ff", rendered)
        for serial in ("SN-AQARA-0001", "SN-AQARA-0002", "SN-AQARA-0003", "0000000004", "0000000005"):
            self.assertNotIn(serial, rendered)
        self.assertEqual(masked["model"], "demo.gateway")

    def test_short_secrets_do_not_rewrite_normal_text(self) -> None:
        redactor = probe.Redactor(["1"])
        self.assertEqual(redactor.text("device 1 reports 1 event"), "device 1 reports 1 event")

    def test_known_secret_is_masked_even_when_adjacent_to_letters(self) -> None:
        redactor = probe.Redactor(["abcdefghijklmnop"])
        rendered = redactor.value({"note": "prefixabcdefghijklmnopsuffix"})
        self.assertNotIn("abcdefghijklmnop", rendered["note"])
        self.assertIn("<redacted>", rendered["note"])
        self.assertNotIn("abcdefghijklmnop", redactor.text("xabcdefghijklmnopy"))

    def test_ascii_token_is_masked_in_text_and_exact_values(self) -> None:
        ascii_token = TOKEN.decode("ascii")
        redactor = probe.Redactor([ascii_token])
        self.assertNotIn(ascii_token, redactor.text(f"note echo {ascii_token} here"))
        self.assertEqual(redactor.value(ascii_token), "<redacted>")
        self.assertEqual(redactor.value({"note": ascii_token}), {"note": "<redacted>"})
        self.assertEqual(redactor.text("plain text without secrets"), "plain text without secrets")

    def test_report_contains_no_raw_identifiers(self) -> None:
        host = "192.0.2.10"
        report = probe.Report(
            host=host,
            udp_port=54321,
            tls_port=8883,
            udp=probe.UdpProbe(status="answered", reply=probe.parse_hello(hello_reply()), token_matches=True),
            tls=probe.TlsProbe(status="client_cert_required", detail="certificate required"),
            read=probe.ReadProbe(method="miIO.info", status="ok", result={"did": str(DID)}),
            secrets=[str(DID), TOKEN.hex()],
        )
        rendered = json.dumps(probe.report_to_dict(report), ensure_ascii=False)
        self.assertNotIn(host, rendered)
        self.assertNotIn(str(DID), rendered)
        self.assertNotIn(TOKEN.hex(), rendered)
        self.assertIn("192.0.*.*", rendered)
        self.assertEqual(report.verdict(), "reachable")

    def test_text_report_is_redacted_too(self) -> None:
        report = probe.Report(
            host="192.0.2.10",
            udp_port=54321,
            tls_port=8883,
            udp=probe.UdpProbe(status="timeout"),
            tls=probe.TlsProbe(status="closed"),
        )
        rendered = probe.report_to_text(report)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertIn("192.0.*.*", rendered)
        self.assertIn("unreachable", rendered)


class UdpProbeTests(unittest.TestCase):
    def test_loopback_device_answers_and_token_verifies(self) -> None:
        with LoopbackMiioDevice() as device:
            result = probe.probe_udp_hello("127.0.0.1", device.port, timeout=1.0, token=TOKEN)
        self.assertEqual(result.status, "answered")
        self.assertIsNotNone(result.reply)
        self.assertEqual(result.reply.did, DID)
        self.assertTrue(result.token_matches)

    def test_wrong_token_is_reported_as_a_mismatch(self) -> None:
        with LoopbackMiioDevice() as device:
            result = probe.probe_udp_hello("127.0.0.1", device.port, timeout=1.0, token=bytes([0x5A]) * 16)
        self.assertEqual(result.status, "answered")
        self.assertFalse(result.token_matches)

    def test_silent_endpoint_times_out(self) -> None:
        silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent.bind(("127.0.0.1", 0))
        try:
            result = probe.probe_udp_hello("127.0.0.1", silent.getsockname()[1], timeout=0.2)
        finally:
            silent.close()
        self.assertEqual(result.status, "timeout")

    def test_read_rpc_round_trip_against_loopback_device(self) -> None:
        with LoopbackMiioDevice() as device:
            result = probe.probe_read(
                "127.0.0.1", device.port, 1.0, TOKEN, DID, "get_properties", [{"siid": 2, "piid": 1}], STAMP
            )
            requests = list(device.requests)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result["model"], "demo.gateway")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["method"], "get_properties")


class CommandLineTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = probe.main(argv)
        return code, stdout.getvalue()

    def test_json_report_is_redacted_and_read_runs(self) -> None:
        with LoopbackMiioDevice() as device:
            code, output = self.run_main(
                [
                    "--ip",
                    "127.0.0.1",
                    "--udp-port",
                    str(device.port),
                    "--skip-tls",
                    "--token",
                    TOKEN.hex(),
                    "--method",
                    "get_properties",
                    "--params",
                    '[{"siid": 2, "piid": 1}]',
                    "--json",
                ]
            )
            requests = list(device.requests)
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "reachable")
        self.assertEqual(payload["read"]["status"], "ok")
        self.assertEqual(payload["read"]["result"]["model"], "demo.gateway")
        self.assertNotIn("127.0.0.1", output)
        self.assertNotIn(str(DID), output)
        self.assertNotIn(TOKEN.hex(), output)
        self.assertNotIn(TOKEN.decode("ascii"), output)
        for serial in ("SN-AQARA-0001", "SN-AQARA-0002"):
            self.assertNotIn(serial, output)
        self.assertEqual([request["method"] for request in requests], ["get_properties"])

    def test_ascii_token_form_is_redacted_end_to_end(self) -> None:
        with LoopbackMiioDevice() as device:
            code, output = self.run_main(
                [
                    "--ip",
                    "127.0.0.1",
                    "--udp-port",
                    str(device.port),
                    "--skip-tls",
                    "--token",
                    TOKEN.decode("ascii"),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["read"]["status"], "ok")
        self.assertNotIn(TOKEN.decode("ascii"), output)
        self.assertNotIn(TOKEN.hex(), output)

    def test_without_a_token_the_read_is_skipped(self) -> None:
        with LoopbackMiioDevice() as device:
            code, output = self.run_main(
                ["--ip", "127.0.0.1", "--udp-port", str(device.port), "--skip-tls", "--json"]
            )
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["read"]["status"], "skipped")

    def test_write_method_is_not_a_valid_choice(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                probe.main(["--ip", "127.0.0.1", "--method", "set_properties", "--skip-udp", "--skip-tls"])
        self.assertEqual(caught.exception.code, 2)

    def test_token_parsing_accepts_both_documented_forms(self) -> None:
        self.assertEqual(probe.parse_token(TOKEN.hex()), TOKEN)
        self.assertEqual(probe.parse_token(TOKEN.decode("ascii")), TOKEN)
        with self.assertRaises(ValueError):
            probe.parse_token("too-short")

    def test_invalid_token_is_rejected_by_the_boundary(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                probe.main(["--ip", "127.0.0.1", "--token", "not-a-token", "--skip-udp", "--skip-tls"])
        self.assertEqual(caught.exception.code, 2)


class TlsProbeTests(unittest.TestCase):
    def test_closed_port_is_reported(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind(("127.0.0.1", 0))
            port = holder.getsockname()[1]
        result = probe.probe_tls("127.0.0.1", port, timeout=0.5)
        self.assertEqual(result.status, "closed")

    def test_plain_tcp_listener_is_rejected(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        stop = threading.Event()

        def serve() -> None:
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except OSError:
                    return
                connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            result = probe.probe_tls("127.0.0.1", port, timeout=1.0)
        finally:
            stop.set()
            listener.close()
            thread.join(timeout=1)
        self.assertEqual(result.status, "rejected")

    def test_client_certificate_alert_is_classified(self) -> None:
        error = ssl.SSLError("tlsv13 alert certificate required")
        self.assertEqual(probe.classify_tls_error(error), "client_cert_required")

    def test_handshake_failure_is_rejected(self) -> None:
        self.assertEqual(
            probe.classify_tls_error(ssl.SSLError("sslv3 alert handshake failure")), "rejected"
        )


if __name__ == "__main__":
    unittest.main()
