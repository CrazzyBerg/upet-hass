from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


API_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "ubpet" / "api.py"
spec = importlib.util.spec_from_file_location("ubpet_api", API_PATH)
api = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class UrlopenRecorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=20):
        self.requests.append(req)
        if not self.responses:
            raise AssertionError("No fake response queued")
        response = self.responses.pop(0)
        return FakeResponse(response[0], response[1])

    def payloads(self):
        return [json.loads(req.data.decode("utf-8")) if req.data else None for req in self.requests]


class ApiUnitTests(unittest.TestCase):
    def make_client(self, account="user@example.com", password="secret"):
        return api.UbpetClient(
            account=account,
            password=password,
            app_key="app-key",
            device_id="device-id",
            base_url="https://example.test",
            app_id="test-app-id",
            product="test-product",
        )

    def test_password_is_always_md5_hashed_for_login(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "token-1", "refreshToken": "refresh-1", "expireAt": 9999999999999},
                        "user": {"userId": 123},
                    },
                )
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            auth = self.make_client(password="plain-password").login()

        self.assertEqual(auth.token, "token-1")
        payload = recorder.payloads()[0]
        self.assertEqual(payload["password"], hashlib.md5(b"plain-password").hexdigest())
        self.assertNotEqual(payload["password"], "plain-password")

    def test_email_login_tries_email_account_type_first_then_falls_back(self):
        recorder = UrlopenRecorder(
            [
                (200, {"code": 2004, "message": "user not found"}),
                (
                    200,
                    {
                        "token": {"token": "token-2", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 456},
                    },
                ),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            auth = self.make_client(account="email@example.com").login()

        self.assertEqual(auth.user_id, 456)
        self.assertEqual([payload["accountType"] for payload in recorder.payloads()], ["1", "0"])

    def test_phone_login_tries_phone_account_type_first(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "token-3", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 789},
                    },
                )
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client(account="380001112233").login()

        self.assertEqual(recorder.payloads()[0]["accountType"], "0")

    def test_authenticated_device_request_sends_auth_headers(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": [
                            {
                                "serialNumber": "SN123",
                                "deviceStatus": 1,
                                "deviceName": "Smart Cat Litter Box",
                            }
                        ],
                    },
                ),
                (200, {"code": 0, "data": {}}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            devices = self.make_client().get_devices()

        self.assertEqual(devices[0]["serialNumber"], "SN123")
        headers = {key.lower(): value for key, value in recorder.requests[1].headers.items()}
        self.assertEqual(headers["authorization"], "auth-token")
        self.assertEqual(headers["product"], "test-product")
        self.assertEqual(headers["x-ubt-appid"], "test-app-id")
        self.assertEqual(headers["x-ubt-deviceid"], "device-id")
        self.assertIn(" v2", headers["x-ubt-sign"])

    def test_set_auto_clean_delay_sends_switch_update_payload_in_seconds(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (200, {"code": 0, "message": "success", "data": None, "success": True}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client().set_auto_clean_delay("SN123", 10)

        self.assertEqual(recorder.requests[1].selector, "/catbox-server/box/config/switch/update")
        self.assertEqual(recorder.requests[1].get_method(), "PUT")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "serialNumber": "SN123",
                "functionType": 2,
                "controlSwitch": 1,
                "optionInt": 600,
            },
        )

    def test_set_auto_clean_enabled_sends_switch_payload_with_existing_delay(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (200, {"code": 0, "message": "success", "data": None, "success": True}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client().set_auto_clean_enabled("SN123", False, 480)

        self.assertEqual(recorder.requests[1].selector, "/catbox-server/box/config/switch/update")
        self.assertEqual(recorder.requests[1].get_method(), "PUT")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "serialNumber": "SN123",
                "functionType": 2,
                "controlSwitch": 0,
                "optionInt": 480,
            },
        )

    def test_reset_box_use_times_sends_confirmed_payload(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (200, {"code": 0, "message": "success", "data": "reset success", "success": True}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client().reset_box_use_times("SN123")

        self.assertEqual(recorder.requests[1].selector, "/catbox-server/box/config/box-use-times/reset")
        self.assertEqual(recorder.requests[1].get_method(), "PUT")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "boxUseTimes": 0,
                "serialNumber": "SN123",
            },
        )

    def test_set_deodorant_alert_sends_deodorant_switch_payload(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (200, {"code": 0, "message": "success", "data": None, "success": True}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client().set_deodorant_alert("SN123", True)

        self.assertEqual(recorder.requests[1].selector, "/catbox-server/app/deodorant-block/switch")
        self.assertEqual(recorder.requests[1].get_method(), "POST")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "serialNumber": "SN123",
                "alertSwitch": 1,
            },
        )

    def test_get_box_records_sends_record_query_payload(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": {
                            "records": [
                                {
                                    "eventType": 3,
                                    "eventTime": 1780553570000,
                                    "timeStr": "2026-06-04 14:12:50",
                                }
                            ]
                        },
                    },
                ),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            records = self.make_client().get_box_records("SN123", 0, 1)

        self.assertEqual(records[0]["eventType"], 3)
        self.assertEqual(recorder.requests[1].selector, "/catbox-server/web/box/record/new")
        self.assertEqual(recorder.requests[1].get_method(), "POST")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "eventTime": "0",
                "serialNumber": "SN123",
                "size": 1,
                "messageType": 0,
            },
        )

    def test_get_im_credentials_sends_type_2_payload(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": {
                            "clientId": "client-id",
                            "mqttUserName": "mqtt-user",
                            "mqttPassword": "mqtt-pass",
                            "pubTopic": "pub/topic",
                            "subTopic": "sub/topic",
                            "uid": "account-uid",
                        },
                    },
                ),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            credentials = self.make_client().get_im_credentials()

        self.assertEqual(credentials["clientId"], "client-id")
        self.assertEqual(recorder.requests[1].selector, "/v1/ubtechinc-im-manager/im/login")
        self.assertEqual(recorder.requests[1].get_method(), "POST")
        self.assertEqual(recorder.payloads()[1], {"type": 2})

    def test_get_im_friends_returns_contact_list(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": [
                            {
                                "userName": "SN123",
                                "userId": "device-uid",
                                "onlineStatus": 1,
                            }
                        ],
                    },
                ),
                (200, {"code": 0, "data": {}}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            friends = self.make_client().get_im_friends()

        self.assertEqual(friends[0]["userName"], "SN123")
        self.assertEqual(recorder.requests[1].selector, "/v1/ubtechinc-im-manager/im/friends")
        self.assertEqual(recorder.requests[1].get_method(), "GET")

    def test_send_mqtt_service_resolves_contact_and_calls_transport(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": {
                            "clientId": "client-id",
                            "mqttUserName": "mqtt-user",
                            "mqttPassword": "mqtt-pass",
                            "pubTopic": "pub/topic",
                            "subTopic": "sub/topic",
                            "uid": "account-uid",
                        },
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": [
                            {
                                "userName": "SN123",
                                "userId": "device-uid",
                            }
                        ],
                    },
                ),
            ]
        )

        class FakeParts:
            def as_dict(self):
                return {"seq": 7, "cid": "cid-1"}

        with (
            patch.object(api.urllib.request, "urlopen", recorder),
            patch.object(api, "publish_service", return_value=FakeParts()) as publish,
        ):
            result = self.make_client().send_mqtt_service("SN123", "start_clean_up")

        self.assertEqual(result["seq"], 7)
        self.assertEqual(result["cid"], "cid-1")
        self.assertEqual(result["from_uid"], "account-uid")
        self.assertEqual(result["target_uid"], "device-uid")
        self.assertEqual(result["publish_topic"], "pub/topic")
        self.assertEqual(result["publish_topic_source"], "im_login.pubTopic")
        self.assertEqual(result["publish_username_source"], "im_login.mqttUserName")
        self.assertEqual(result["publish_password_source"], "im_login.mqttPassword")
        self.assertEqual(result["publish_url_source"], "base_url")
        publish.assert_called_once()
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "https://example.test")
        self.assertEqual(kwargs["credentials"]["pubTopic"], "pub/topic")
        self.assertEqual(kwargs["credentials"]["mqttUserName"], "mqtt-user")
        self.assertEqual(kwargs["credentials"]["mqttPassword"], "mqtt-pass")
        self.assertNotIn("url", kwargs["credentials"])
        self.assertEqual(kwargs["target_uid"], "device-uid")
        self.assertEqual(kwargs["service_id"], "start_clean_up")
        self.assertIsNone(kwargs["seq"])

    def test_send_mqtt_service_uses_auth_user_id_when_im_uid_is_missing(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": {
                            "clientId": "client-id",
                            "mqttUserName": "mqtt-user",
                            "mqttPassword": "mqtt-pass",
                            "pubTopic": "pub/topic",
                            "subTopic": "sub/topic",
                        },
                    },
                ),
                (
                    200,
                    {
                        "code": 0,
                        "data": [
                            {
                                "userName": "SN123",
                                "userId": "device-uid",
                            }
                        ],
                    },
                ),
                (200, {"code": 0, "data": {}}),
            ]
        )

        class FakeParts:
            def as_dict(self):
                return {"seq": 7, "cid": "cid-1"}

        with (
            patch.object(api.urllib.request, "urlopen", recorder),
            patch.object(api, "publish_service", return_value=FakeParts()) as publish,
            patch.object(api.random, "randint", return_value=7),
        ):
            self.make_client().send_mqtt_service("SN123", "request_state")

        self.assertEqual(publish.call_args.kwargs["from_uid"], "100")

    def test_mqtt_result_distinguishes_delivery_receipt_from_target_reply(self):
        result = api._summarize_mqtt_result(
            {
                "received_messages": [
                    {
                        "decoded": {
                            "from_uid": "198463",
                            "to_uid": "197002",
                            "receipt_status": "receipt/success",
                            "receipt_cause": "send message success",
                        }
                    }
                ]
            },
            target_uid="197002",
        )

        self.assertEqual(result["delivery_status"], "delivered")
        self.assertEqual(result["target_reply_status"], "not_observed")
        self.assertEqual(result["receipt_cause"], "send message success")

    def test_set_time_point_sends_time_point_payload(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (200, {"code": 0, "message": "success", "data": None, "success": True}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client().set_time_point(
                "SN123",
                config_id=6874,
                function_type=1,
                open_switch=1,
                time_hour=19,
                time_minute=30,
                option_int=28,
            )

        self.assertEqual(recorder.requests[1].selector, "/catbox-server/box/config/timePoint/update")
        self.assertEqual(recorder.requests[1].get_method(), "PUT")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "serialNumber": "SN123",
                "configId": 6874,
                "functionType": 1,
                "openSwitch": 1,
                "timeHour": 19,
                "timeMinute": 30,
                "optionInt": 28,
            },
        )

    def test_set_time_period_sends_time_period_payload(self):
        recorder = UrlopenRecorder(
            [
                (
                    200,
                    {
                        "token": {"token": "auth-token", "refreshToken": None, "expireAt": 9999999999999},
                        "user": {"userId": 100},
                    },
                ),
                (200, {"code": 0, "message": "success", "data": None, "success": True}),
            ]
        )

        with patch.object(api.urllib.request, "urlopen", recorder):
            self.make_client().set_time_period(
                "SN123",
                config_id=10940,
                function_type=1,
                open_switch=1,
                start_hour=23,
                start_minute=0,
                end_hour=6,
                end_minute=30,
            )

        self.assertEqual(recorder.requests[1].selector, "/catbox-server/box/config/timePeriod/update")
        self.assertEqual(recorder.requests[1].get_method(), "PUT")
        self.assertEqual(
            recorder.payloads()[1],
            {
                "serialNumber": "SN123",
                "functionType": 1,
                "openSwitch": 1,
                "configId": 10940,
                "startTimeHour": 23,
                "startTimeMinute": 0,
                "endTimeHour": 6,
                "endTimeMinute": 30,
            },
        )

    def test_signer_matches_recovered_algorithm_with_fixed_time_and_nonce(self):
        signer = api.UbtV2Signer("app-key")
        with patch.object(api.time, "time", return_value=1234567890), patch.object(api.random, "choice", return_value="A"):
            signature = signer.sign(device_id="device-id")

        digest = hashlib.md5("1234567890app-keyAAAAAAAAAAdevice-id".encode("utf-8")).hexdigest()
        self.assertEqual(signature, f"{digest} 1234567890 AAAAAAAAAA v2")

    def test_dashboard_keeps_required_device_when_optional_endpoint_fails(self):
        client = self.make_client()
        client.auth = api.UbpetAuth(token="token", refresh_token=None, expires_at_ms=9999999999999, user_id=1)
        client.get_devices = lambda: [{"serialNumber": "SN123", "deviceName": "Box"}]
        client.get_all_config = lambda serial: {"wifiName": "wifi"}
        client.get_box_use_times = lambda serial: {"boxUseTimes": 10}
        client.get_deodorant_status = lambda serial: (_ for _ in ()).throw(api.UbpetApiError(500, {"code": 1}))
        client.get_device_online = lambda serial: {"online": True}
        client.get_cats = lambda: [{"catInfoId": 5, "nickname": "Cat"}]

        data = client.get_dashboard()

        self.assertEqual(data["devices"]["SN123"]["device"]["deviceName"], "Box")
        self.assertEqual(data["devices"]["SN123"]["config"]["wifiName"], "wifi")
        self.assertEqual(data["devices"]["SN123"]["deodorant"], {})
        self.assertEqual(data["cats"][0]["nickname"], "Cat")

    def test_require_helpers_reject_bad_shapes(self):
        with self.assertRaises(api.UbpetApiError):
            api._require_dict({"code": 0, "data": []})
        with self.assertRaises(api.UbpetApiError):
            api._require_list({"code": 0, "data": {}})


if __name__ == "__main__":
    unittest.main()


def load_diagnostics_module():
    import types

    hass_module = types.ModuleType("homeassistant")
    config_entries_module = types.ModuleType("homeassistant.config_entries")
    core_module = types.ModuleType("homeassistant.core")
    config_entries_module.ConfigEntry = object
    core_module.HomeAssistant = object
    sys.modules.setdefault("homeassistant", hass_module)
    sys.modules.setdefault("homeassistant.config_entries", config_entries_module)
    sys.modules.setdefault("homeassistant.core", core_module)

    const_module = types.ModuleType("ubpet_diagnostics_test.const")
    const_module.DOMAIN = "ubpet"
    sys.modules["ubpet_diagnostics_test.const"] = const_module

    path = Path(__file__).resolve().parents[1] / "custom_components" / "ubpet" / "diagnostics.py"
    diag_spec = importlib.util.spec_from_file_location("ubpet_diagnostics_test.diagnostics", path)
    diagnostics = importlib.util.module_from_spec(diag_spec)
    assert diag_spec.loader is not None
    sys.modules[diag_spec.name] = diagnostics
    diag_spec.loader.exec_module(diagnostics)
    return diagnostics


class DiagnosticsUnitTests(unittest.TestCase):
    def test_redact_removes_credentials_but_keeps_wifi_and_config_values(self):
        diagnostics = load_diagnostics_module()

        payload = {
            "account": "user@example.com",
            "password": "secret",
            "device_id": "device-id",
            "config": {
                "wifiName": "My_IoT",
                "gmtTimeZone": "GMT+03:00",
                "boxFunctionVOList": [{"firmwareVersion": "main_v1"}],
            },
            "cats": [{"nickname": "Cat", "icon": "https://private.example/cat.jpg"}],
        }

        redacted = diagnostics._redact(payload)

        self.assertEqual(redacted["account"], diagnostics.REDACTED)
        self.assertEqual(redacted["password"], diagnostics.REDACTED)
        self.assertEqual(redacted["device_id"], diagnostics.REDACTED)
        self.assertEqual(redacted["config"]["wifiName"], "My_IoT")
        self.assertEqual(redacted["config"]["gmtTimeZone"], "GMT+03:00")
        self.assertEqual(redacted["cats"][0]["nickname"], "Cat")
        self.assertEqual(redacted["cats"][0]["icon"], diagnostics.REDACTED)
