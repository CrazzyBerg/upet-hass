from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


MQTT_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "ubpet" / "mqtt.py"
spec = importlib.util.spec_from_file_location("ubpet_mqtt", MQTT_PATH)
mqtt = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = mqtt
spec.loader.exec_module(mqtt)


def parse_packet(packet: bytes) -> tuple[int, int, bytes]:
    remaining = 0
    multiplier = 1
    index = 1
    while True:
        digit = packet[index]
        index += 1
        remaining += (digit & 0x7F) * multiplier
        if not (digit & 0x80):
            break
        multiplier *= 128
    return packet[0] >> 4, packet[0] & 0x0F, packet[index : index + remaining]


class FakeConnection:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.sent: list[tuple[int, int, bytes]] = []

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self, length: int) -> bytes:
        if not self.buffer:
            raise mqtt.socket.timeout()
        out = bytes(self.buffer[:length])
        del self.buffer[:length]
        return out

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self) -> None:
        pass

    def sendall(self, packet: bytes) -> None:
        packet_type, flags, payload = parse_packet(packet)
        self.sent.append((packet_type, flags, payload))
        if packet_type == 1:
            self.buffer += mqtt.mqtt_packet(0x20, b"\x00\x00")
        elif packet_type == 8:
            packet_id = int.from_bytes(payload[:2], "big")
            self.buffer += mqtt.mqtt_packet(0x90, packet_id.to_bytes(2, "big") + b"\x01")


class FakeReplyConnection(FakeConnection):
    def sendall(self, packet: bytes) -> None:
        packet_type, flags, payload = parse_packet(packet)
        self.sent.append((packet_type, flags, payload))
        if packet_type == 1:
            self.buffer += mqtt.mqtt_packet(0x20, b"\x00\x00")
        elif packet_type == 8:
            packet_id = int.from_bytes(payload[:2], "big")
            self.buffer += mqtt.mqtt_packet(0x90, packet_id.to_bytes(2, "big") + b"\x01")
        elif packet_type == 3:
            self.buffer += mqtt.mqtt_packet(
                0x32,
                mqtt.mqtt_utf8("sub/topic") + (9).to_bytes(2, "big") + b"reply",
            )


def build_receipt_im_message(cid: str, status: str, cause: str = "") -> bytes:
    receipt = mqtt.field_string(1, status) + mqtt.field_string(2, cause)
    receipt_any = mqtt.encode_any(mqtt.ANY_RECEIPT_NOTIFICATION_TYPE_URL, receipt)
    message_content = mqtt.encode_message_content("receipt", receipt_any)
    return b"".join(
        [
            mqtt.field_string(1, cid),
            mqtt.field_string(2, "100"),
            mqtt.field_varint(3, 1700000000123),
            mqtt.field_string(4, "device-uid"),
            mqtt.field_string(5, "account-uid"),
            mqtt.field_varint(6, 2),
            mqtt.field_bytes(7, message_content),
            mqtt.field_string(8, mqtt.MESSAGE_PROTOCOL_VERSION),
        ]
    )


class MqttCodecUnitTests(unittest.TestCase):
    def test_operation_payload_hex_matches_recovered_handoff(self):
        parts = mqtt.build_payload_parts(kind="operation", op_mode=1, op_state=1, seq=1)

        self.assertEqual(parts.op_body.hex(), "08011001")
        self.assertEqual(parts.risp_frame.hex(), "db0b080210fb072004280138018b08011001de")
        self.assertEqual(parts.command_proto.hex(), "08021213db0b080210fb072004280138018b08011001de")

    def test_all_state_payload_hex_matches_recovered_handoff(self):
        parts = mqtt.build_payload_parts(kind="all-state", seq=1)

        self.assertEqual(parts.op_body.hex(), "")
        self.assertEqual(parts.risp_frame.hex(), "db09080210fd0728013801f6")
        self.assertEqual(parts.command_proto.hex(), "0802120cdb09080210fd0728013801f6")

    def test_all_state_response_decodes_app_state_names(self):
        decoded = mqtt.summarize_all_state_body(bytes.fromhex("185a9001b6beafd106"))

        self.assertEqual(decoded["w_mode"], 0)
        self.assertEqual(decoded["w_mode_name"], "idle")
        self.assertEqual(decoded["w_mode_app_name"], "IDLE")
        self.assertEqual(decoded["w_state"], 0)
        self.assertEqual(decoded["w_state_name"], "idle")
        self.assertEqual(decoded["w_state_app_name"], "PENDING")
        self.assertEqual(decoded["w_cause"], 90)
        self.assertEqual(decoded["timestamp"], 1781260086)

    def test_service_ids_map_to_app_operation_ordinals(self):
        services = mqtt.SERVICE_ID_MAP

        self.assertEqual(services["start_clean_up"]["op_mode_value"], 1)
        self.assertEqual(services["start_clean_up"]["op_state_value"], 1)
        self.assertEqual(services["pause_clean_up"]["op_mode_value"], 1)
        self.assertEqual(services["pause_clean_up"]["op_state_value"], 2)
        self.assertEqual(services["resume_clean_up"]["op_mode_value"], 1)
        self.assertEqual(services["resume_clean_up"]["op_state_value"], 3)
        self.assertEqual(services["start_flatten"]["op_mode_value"], 3)
        self.assertEqual(services["start_flatten"]["op_state_value"], 1)
        self.assertEqual(services["pause_flatten"]["op_mode_value"], 3)
        self.assertEqual(services["pause_flatten"]["op_state_value"], 2)
        self.assertEqual(services["resume_flatten"]["op_mode_value"], 3)
        self.assertEqual(services["resume_flatten"]["op_state_value"], 3)
        self.assertEqual(services["start_rise"]["op_mode_value"], 7)
        self.assertEqual(services["start_rise"]["op_state_value"], 1)
        self.assertEqual(services["start_drop"]["op_mode_value"], 8)
        self.assertEqual(services["start_drop"]["op_state_value"], 1)
        self.assertNotIn("start_clean_up_enum1", services)
        self.assertNotIn("pause_clean_up_enum1", services)
        self.assertNotIn("start_exchange_sand", services)
        self.assertNotIn("pause_rise", services)
        self.assertNotIn("resume_rise", services)
        self.assertNotIn("pause_drop", services)
        self.assertNotIn("resume_drop", services)
        self.assertNotIn("start_clean_up_mode2_state2", services)
        self.assertNotIn("start_clean_up_mode2_state1", services)
        self.assertNotIn("request_state_gid100", services)
        self.assertNotIn("start_clean_up_gid100", services)

    def test_resolve_service_payload_builds_expected_clean_command(self):
        self.assertEqual(mqtt.resolve_service_payload("start_clean_up", seq=1).op_body.hex(), "08011001")
        self.assertEqual(mqtt.resolve_service_payload("pause_clean_up", seq=1).op_body.hex(), "08011002")
        self.assertEqual(mqtt.resolve_service_payload("resume_clean_up", seq=1).op_body.hex(), "08011003")
        self.assertEqual(
            mqtt.resolve_service_payload("start_clean_up", seq=1).risp_frame.hex(),
            "db0b080210fb072004280138018b08011001de",
        )

    def test_resolve_service_payload_builds_expected_rake_commands(self):
        self.assertEqual(mqtt.resolve_service_payload("start_rise", seq=1).op_body.hex(), "08071001")
        self.assertEqual(mqtt.resolve_service_payload("start_drop", seq=1).op_body.hex(), "08081001")

    def test_resolve_service_payload_builds_expected_flatten_command(self):
        self.assertEqual(mqtt.resolve_service_payload("start_flatten", seq=1).op_body.hex(), "08031001")
        self.assertEqual(mqtt.resolve_service_payload("pause_flatten", seq=1).op_body.hex(), "08031002")
        self.assertEqual(mqtt.resolve_service_payload("resume_flatten", seq=1).op_body.hex(), "08031003")

    def test_im_message_wraps_command_proto_when_uids_are_supplied(self):
        parts = mqtt.build_payload_parts(
            kind="all-state",
            seq=1,
            from_uid="account-uid",
            to_uid="device-uid",
            cid="cid-1",
            gid="100",
            created_at_ms=1700000000123,
        )

        self.assertIsNotNone(parts.im_message)
        self.assertIn(parts.command_proto.hex(), parts.im_message.hex())
        self.assertIn(b"account-uid", parts.im_message)
        self.assertIn(b"device-uid", parts.im_message)

    def test_generated_cid_matches_app_timestamp_plus_suffix_shape(self):
        with (
            mock.patch.object(mqtt.time, "time", return_value=1700000000.123),
            mock.patch.object(mqtt.random, "random", return_value=0.234567),
        ):
            parts = mqtt.build_payload_parts(
                kind="all-state",
                seq=1,
                from_uid="account-uid",
                to_uid="device-uid",
            )

        self.assertEqual(parts.cid, "1700000000123+311110")

    def test_generated_gid_matches_app_timestamp_shape(self):
        mqtt._GID_COUNTER = 0

        self.assertEqual(mqtt.generate_gid(1780900866300), "1867409906781388801")

    def test_generate_gid_increments_timestamp_shape_counter(self):
        mqtt._GID_COUNTER = 0

        with mock.patch.object(mqtt.time, "time", return_value=1780900866.300):
            first = mqtt.generate_gid()
            second = mqtt.generate_gid()

        self.assertEqual(first, "1867409906781388801")
        self.assertEqual(second, "1867409906781388802")

    def test_mqtt_packet_helpers(self):
        self.assertEqual(mqtt.mqtt_remaining_length(321).hex(), "c102")
        self.assertEqual(mqtt.mqtt_packet(0xC0, b"").hex(), "c000")

    def test_parse_mqtt_publish_qos1(self):
        payload = mqtt.mqtt_utf8("sub/topic") + (7).to_bytes(2, "big") + b"reply"

        topic, qos, message, packet_id = mqtt.parse_mqtt_publish(0b0010, payload)

        self.assertEqual(topic, "sub/topic")
        self.assertEqual(qos, 1)
        self.assertEqual(message, b"reply")
        self.assertEqual(packet_id, 7)

    def test_publish_service_on_connection_connects_subscribes_and_publishes(self):
        conn = FakeConnection()
        credentials = {
            "clientId": "client-id",
            "mqttUserName": "mqtt-user",
            "mqttPassword": "mqtt-pass",
            "pubTopic": "pub/topic",
            "subTopic": "sub/topic",
            "uid": "account-uid",
        }

        parts = mqtt.publish_service_on_connection(
            conn,
            credentials=credentials,
            target_uid="device-uid",
            service_id="start_clean_up",
            seq=1,
            timeout=1,
        )

        self.assertEqual([packet_type for packet_type, _flags, _payload in conn.sent], [1, 8, 3, 14])
        self.assertEqual(parts.risp_frame.hex(), "db0b080210fb072004280138018b08011001de")
        publish_topic, publish_payload_offset = self._decode_utf8(conn.sent[2][2])
        self.assertEqual(publish_topic, "pub/topic")
        self.assertIn(parts.command_proto, conn.sent[2][2][publish_payload_offset:])

    def test_publish_service_on_connection_collects_and_acks_incoming_publish(self):
        conn = FakeReplyConnection()
        credentials = {
            "clientId": "client-id",
            "mqttUserName": "mqtt-user",
            "mqttPassword": "mqtt-pass",
            "pubTopic": "pub/topic",
            "subTopic": "sub/topic",
            "uid": "account-uid",
        }

        parts = mqtt.publish_service_on_connection(
            conn,
            credentials=credentials,
            target_uid="device-uid",
            service_id="request_state",
            seq=1,
            timeout=1,
        )

        self.assertEqual(parts.received_messages[0]["topic"], "sub/topic")
        self.assertEqual(parts.received_messages[0]["payload_hex"], "7265706c79")
        self.assertIn(4, [packet_type for packet_type, _flags, _payload in conn.sent])

    def test_decode_mqtt_payload_summarizes_receipt(self):
        decoded = mqtt.decode_mqtt_payload_safely(build_receipt_im_message("cid-1", "receipt/success"))

        self.assertEqual(decoded["cid"], "cid-1")
        self.assertEqual(decoded["from_uid"], "device-uid")
        self.assertEqual(decoded["to_uid"], "account-uid")
        self.assertEqual(decoded["content_type"], "receipt")
        self.assertEqual(decoded["receipt_status"], "receipt/success")

    def test_publish_service_on_connection_accepts_from_uid_override(self):
        conn = FakeConnection()
        credentials = {
            "client_id": "client-id",
            "username": "mqtt-user",
            "password": "mqtt-pass",
            "pub_topic": "pub/topic",
            "sub_topic": "sub/topic",
        }

        parts = mqtt.publish_service_on_connection(
            conn,
            credentials=credentials,
            target_uid="device-uid",
            from_uid="100",
            service_id="request_state",
            seq=1,
            timeout=1,
        )

        self.assertIsNotNone(parts.im_message)
        self.assertIn(b"100", parts.im_message)
        self.assertIn(b"device-uid", parts.im_message)

    def test_mqtt_host_from_base_url_strips_scheme_and_path(self):
        self.assertEqual(mqtt.mqtt_host_from_base_url("https://apis.airrobo-home.com/foo"), "apis.airrobo-home.com")
        self.assertEqual(mqtt.mqtt_host_from_base_url("apis.airrobo-home.com/"), "apis.airrobo-home.com")

    def test_mqtt_endpoint_uses_base_url_like_android_app(self):
        self.assertEqual(
            mqtt.mqtt_endpoint("https://apis-eu.airrobo-home.com", {"url": "ssl://broker.example.test:21001"}),
            ("apis-eu.airrobo-home.com", 21000, "base_url"),
        )
        self.assertEqual(
            mqtt.mqtt_endpoint("https://apis-eu.airrobo-home.com", {}),
            ("apis-eu.airrobo-home.com", 21000, "base_url"),
        )

    def _decode_utf8(self, payload: bytes, offset: int = 0) -> tuple[str, int]:
        length = int.from_bytes(payload[offset : offset + 2], "big")
        start = offset + 2
        end = start + length
        return payload[start:end].decode("utf-8"), end


if __name__ == "__main__":
    unittest.main()
