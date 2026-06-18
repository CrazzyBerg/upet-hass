from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import random
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

try:
    from .mqtt import mqtt_endpoint, publish_service
except ImportError:
    _MQTT_PATH = Path(__file__).with_name("mqtt.py")
    _mqtt_spec = importlib.util.spec_from_file_location("ubpet_mqtt", _MQTT_PATH)
    if _mqtt_spec is None or _mqtt_spec.loader is None:
        raise
    _mqtt = importlib.util.module_from_spec(_mqtt_spec)
    sys.modules[_mqtt_spec.name] = _mqtt
    _mqtt_spec.loader.exec_module(_mqtt)
    publish_service = _mqtt.publish_service
    mqtt_endpoint = _mqtt.mqtt_endpoint

USER_AGENT = "CatLitterBoxOverseasEu/2.1.12 (com.ubtrobot.airpet.overseaseu; build:699; iOS 26.5.0) Alamofire/5.5.0"
NONCE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
QUICK_MQTT_REPLY_SERVICES = {
    "start_clean_up",
    "pause_clean_up",
    "resume_clean_up",
    "start_flatten",
    "pause_flatten",
    "resume_flatten",
    "start_rise",
    "start_drop",
}
_LOGGER = logging.getLogger(__name__)


class UbpetApiError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        code = payload.get("code") if isinstance(payload, dict) else None
        message = None
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("msg")
        super().__init__(f"UPET API error status={status} code={code} message={message}")


@dataclass(slots=True)
class UbpetAuth:
    token: str
    refresh_token: str | None
    expires_at_ms: int | None
    user_id: int | None


class UbtV2Signer:
    def __init__(self, app_key: str) -> None:
        self.app_key = app_key

    def sign(self, *, device_id: str) -> str:
        timestamp = str(int(time.time()))
        nonce = "".join(random.choice(NONCE_ALPHABET) for _ in range(10))
        preimage = timestamp + self.app_key + nonce + device_id
        digest = hashlib.md5(preimage.encode("utf-8")).hexdigest()
        return f"{digest} {timestamp} {nonce} v2"


class UbpetClient:
    def __init__(
        self,
        *,
        account: str,
        password: str,
        app_key: str,
        device_id: str,
        base_url: str,
        app_id: str,
        product: str,
    ) -> None:
        self.account = account
        self.password = password
        self.device_id = device_id
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.product = product
        self.signer = UbtV2Signer(app_key)
        self.auth: UbpetAuth | None = None

    def ensure_login(self) -> UbpetAuth:
        now_ms = int(time.time() * 1000)
        if self.auth is None or (self.auth.expires_at_ms and self.auth.expires_at_ms - now_ms < 300_000):
            _LOGGER.info("UPET auth token is missing or expiring, logging in")
            return self.login()
        return self.auth

    def login(self) -> UbpetAuth:
        last_error: UbpetApiError | None = None
        for account_type in _account_type_candidates(self.account):
            try:
                _LOGGER.info("Trying UPET login with accountType=%s", account_type)
                return self._login_with_account_type(account_type)
            except UbpetApiError as err:
                _LOGGER.warning("UPET login failed with accountType=%s: %s", account_type, err)
                last_error = err
        if last_error is not None:
            raise last_error
        raise RuntimeError("no account type candidates")

    def _login_with_account_type(self, account_type: str) -> UbpetAuth:
        payload = {
            "account": self.account,
            "password": _md5_password(self.password),
            "accountType": account_type,
            "areaCode": "",
            "appId": self.app_id,
        }
        data = self._request("PUT", "/user-service-rest/v2/user/login", payload=payload, auth=False)
        token_data = data.get("token") if isinstance(data, dict) else None
        user_data = data.get("user") if isinstance(data, dict) else None
        if not isinstance(token_data, dict) or not token_data.get("token"):
            raise UbpetApiError(200, data)
        self.auth = UbpetAuth(
            token=token_data["token"],
            refresh_token=token_data.get("refreshToken"),
            expires_at_ms=token_data.get("expireAt"),
            user_id=user_data.get("userId") if isinstance(user_data, dict) else None,
        )
        _LOGGER.info("UPET login succeeded with accountType=%s", account_type)
        return self.auth

    def get_devices(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/user-service-rest/v2/robot/common/device/list", auth=True)
        return _require_list(data)

    def get_all_config(self, serial_number: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/catbox-server/box/config/allConfig?serialNumber={_quote(serial_number)}",
            auth=True,
        )
        return _require_dict(data)

    def get_box_use_times(self, serial_number: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/catbox-server/box/config/box-use-times/?serialNumber={_quote(serial_number)}",
            auth=True,
        )
        return _require_dict(data)

    def get_deodorant_status(self, serial_number: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/catbox-server/app/deodorant-block/status?serialNumber={_quote(serial_number)}",
            auth=True,
        )
        return _require_dict(data)

    def get_device_online(self, serial_number: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/v1/ubtechinc-im-manager/im/online/device/?sn={_quote(serial_number)}",
            auth=True,
        )
        return _require_dict(data)

    def get_im_credentials(self) -> dict[str, Any]:
        data = self._request("POST", "/v1/ubtechinc-im-manager/im/login", payload={"type": 2}, auth=True)
        return _require_dict(data)

    def get_im_friends(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/ubtechinc-im-manager/im/friends", auth=True)
        return _require_list(data)

    def send_mqtt_service(self, serial_number: str, service_id: str) -> dict[str, Any]:
        credentials = self.get_im_credentials()
        contact = _find_im_contact(self.get_im_friends(), serial_number)
        publish_credentials, publish_sources = _mqtt_publish_credentials(credentials)
        target_uid = str(contact["userId"])
        from_uid = _find_account_uid(credentials, self.auth)
        parts = publish_service(
            base_url=self.base_url,
            credentials=publish_credentials,
            target_uid=target_uid,
            from_uid=from_uid,
            service_id=service_id,
            seq=None,
            listen_seconds=_mqtt_listen_seconds(service_id),
        )
        result = parts.as_dict()
        result["from_uid"] = from_uid
        result["target_uid"] = target_uid
        result["im_login_keys"] = sorted(str(key) for key in credentials)
        mqtt_host, mqtt_port, mqtt_host_source = mqtt_endpoint(self.base_url, credentials)
        result["mqtt_host"] = mqtt_host
        result["mqtt_port"] = mqtt_port
        result["mqtt_host_source"] = mqtt_host_source
        result["publish_topic"] = publish_credentials.get("pubTopic")
        result["publish_topic_source"] = publish_sources["topic"]
        result["publish_username_source"] = publish_sources["username"]
        result["publish_password_source"] = publish_sources["password"]
        result["publish_url_source"] = publish_sources["url"]
        result["matched_contact"] = _safe_contact_summary(contact)
        result.update(_summarize_mqtt_result(result, target_uid=target_uid))
        return result

    def get_cats(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/catbox-server/web/cat/info", auth=True)
        return _require_list(data)

    def get_box_records(self, serial_number: str, message_type: int, size: int = 1) -> list[dict[str, Any]]:
        payload = {
            "eventTime": "0",
            "serialNumber": serial_number,
            "size": size,
            "messageType": message_type,
        }
        data = self._request("POST", "/catbox-server/web/box/record/new", payload=payload, auth=True)
        records = data.get("data", {}).get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise UbpetApiError(200, data)
        return [record for record in records if isinstance(record, dict)]

    def set_auto_clean_delay(self, serial_number: str, minutes: int) -> None:
        if minutes < 1:
            raise ValueError("auto clean delay must be at least 1 minute")
        payload = {
            "serialNumber": serial_number,
            "functionType": 2,
            "controlSwitch": 1,
            "optionInt": minutes * 60,
        }
        self._request("PUT", "/catbox-server/box/config/switch/update", payload=payload, auth=True)

    def set_auto_clean_enabled(self, serial_number: str, enabled: bool, lazy_time_seconds: int | None) -> None:
        payload = {
            "serialNumber": serial_number,
            "functionType": 2,
            "controlSwitch": 1 if enabled else 0,
            "optionInt": lazy_time_seconds or 480,
        }
        self._request("PUT", "/catbox-server/box/config/switch/update", payload=payload, auth=True)

    def set_deodorant_alert(self, serial_number: str, enabled: bool) -> None:
        payload = {
            "serialNumber": serial_number,
            "alertSwitch": 1 if enabled else 0,
        }
        self._request("POST", "/catbox-server/app/deodorant-block/switch", payload=payload, auth=True)

    def set_time_point(
        self,
        serial_number: str,
        *,
        config_id: int,
        function_type: int,
        open_switch: int,
        time_hour: int,
        time_minute: int,
        option_int: int,
    ) -> None:
        payload = {
            "serialNumber": serial_number,
            "configId": config_id,
            "functionType": function_type,
            "openSwitch": open_switch,
            "timeHour": time_hour,
            "timeMinute": time_minute,
            "optionInt": option_int,
        }
        self._request("PUT", "/catbox-server/box/config/timePoint/update", payload=payload, auth=True)

    def set_time_period(
        self,
        serial_number: str,
        *,
        config_id: int,
        function_type: int,
        open_switch: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
    ) -> None:
        payload = {
            "serialNumber": serial_number,
            "functionType": function_type,
            "openSwitch": open_switch,
            "configId": config_id,
            "startTimeHour": start_hour,
            "startTimeMinute": start_minute,
            "endTimeHour": end_hour,
            "endTimeMinute": end_minute,
        }
        self._request("PUT", "/catbox-server/box/config/timePeriod/update", payload=payload, auth=True)

    def get_dashboard(self) -> dict[str, Any]:
        self.ensure_login()
        devices = self.get_devices()
        _LOGGER.info("UPET device list returned %s device(s)", len(devices))
        by_serial: dict[str, dict[str, Any]] = {}
        for device in devices:
            serial = device.get("serialNumber")
            if not serial:
                continue
            by_serial[serial] = {
                "device": device,
                "config": _safe_dict_call(self.get_all_config, serial),
                "box_use_times": _safe_dict_call(self.get_box_use_times, serial),
                "deodorant": _safe_dict_call(self.get_deodorant_status, serial),
                "online": _safe_dict_call(self.get_device_online, serial),
                "cat_records": _safe_list_call(self.get_box_records, serial, 0, 1),
                "device_records": _safe_list_call(self.get_box_records, serial, 1, 1),
            }
        return {
            "devices": by_serial,
            "cats": _safe_list_call(self.get_cats),
            "user_id": self.auth.user_id if self.auth else None,
        }

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, auth: bool) -> Any:
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        headers = {
            "x-ubt-language": "en",
            "x-ubt-deviceid": self.device_id,
            "content-type": "application/json; charset=utf-8",
            "accept": "application/json",
            "x-ubt-appid": self.app_id,
            "priority": "u=3, i",
            "user-agent": USER_AGENT,
            "x-ubt-sign": self.signer.sign(device_id=self.device_id),
        }
        if auth:
            if self.auth is None:
                self.ensure_login()
            if self.auth is None:
                raise RuntimeError("login failed")
            headers["authorization"] = self.auth.token
            headers["product"] = self.product

        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        started = time.monotonic()
        _LOGGER.debug("UPET HTTP %s %s auth=%s", method, path, auth)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = _decode_response(resp.read())
                _raise_for_api_error(resp.status, data)
                _LOGGER.debug(
                    "UPET HTTP %s %s completed with status=%s in %.2fs",
                    method,
                    path,
                    resp.status,
                    time.monotonic() - started,
                )
                return data
        except urllib.error.HTTPError as err:
            data = _decode_response(err.read())
            _LOGGER.warning("UPET HTTP %s %s failed with status=%s", method, path, err.code)
            raise UbpetApiError(err.code, data) from err


def _md5_password(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def _account_type_candidates(account: str) -> tuple[str, ...]:
    if "@" in account:
        return ("1", "0", "2")
    return ("0", "1", "2")


def _decode_response(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _raise_for_api_error(status: int, data: Any) -> None:
    if status >= 400:
        raise UbpetApiError(status, data)
    if isinstance(data, dict):
        code = data.get("code")
        if code not in (None, 0):
            raise UbpetApiError(status, data)


def _require_dict(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise UbpetApiError(200, payload)
    return data


def _require_list(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise UbpetApiError(200, payload)
    return data


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _find_im_contact(friends: list[dict[str, Any]], serial_number: str) -> dict[str, Any]:
    for friend in friends:
        if friend.get("userName") == serial_number and friend.get("userId"):
            return friend
    raise UbpetApiError(200, {"code": "missing_im_contact", "message": f"No IM contact found for {serial_number}"})


def _safe_contact_summary(contact: dict[str, Any]) -> dict[str, Any]:
    return {
        "userName": contact.get("userName"),
        "userId": contact.get("userId"),
        "onlineStatus": contact.get("onlineStatus"),
        "alias": contact.get("alias"),
        "nickName": contact.get("nickName"),
    }


def _mqtt_publish_credentials(
    credentials: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    publish_credentials = dict(credentials)
    sources = {
        "topic": "im_login.pubTopic",
        "username": "im_login.mqttUserName",
        "password": "im_login.mqttPassword",
        "url": "base_url",
    }
    return publish_credentials, sources


def _mqtt_listen_seconds(service_id: str) -> int:
    if service_id == "request_state":
        return 5
    if service_id in QUICK_MQTT_REPLY_SERVICES:
        return 2
    return 10


def _find_account_uid(credentials: dict[str, Any], auth: UbpetAuth | None) -> str:
    for key in ("uid", "userId", "imUserId", "accountUid"):
        value = credentials.get(key)
        if value not in (None, ""):
            return str(value)
    if auth is not None and auth.user_id is not None:
        return str(auth.user_id)
    raise UbpetApiError(
        200,
        {
            "code": "missing_im_sender",
            "message": "No sender uid found in IM login response or auth session",
            "im_login_keys": sorted(str(key) for key in credentials),
        },
    )


def _summarize_mqtt_result(result: dict[str, Any], *, target_uid: str) -> dict[str, Any]:
    delivery_status = "not_observed"
    target_reply_status = "not_observed"
    receipt_cause = None
    messages = result.get("received_messages")
    if not isinstance(messages, list):
        messages = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        decoded = message.get("decoded")
        if not isinstance(decoded, dict):
            continue
        receipt_status = decoded.get("receipt_status")
        if receipt_status == "receipt/success":
            delivery_status = "delivered"
            receipt_cause = decoded.get("receipt_cause")
        elif receipt_status == "receipt/unreachable":
            delivery_status = "unreachable"
            receipt_cause = decoded.get("receipt_cause")
        if decoded.get("from_uid") == target_uid:
            target_reply_status = "observed"
    return {
        "delivery_status": delivery_status,
        "target_reply_status": target_reply_status,
        "receipt_cause": receipt_cause,
    }


def _safe_dict_call(func, *args) -> dict[str, Any]:
    try:
        return func(*args)
    except (OSError, RuntimeError, UbpetApiError) as err:
        _LOGGER.debug("UPET optional endpoint failed: %s", err)
        return {}


def _safe_list_call(func, *args) -> list[dict[str, Any]]:
    try:
        return func(*args)
    except (OSError, RuntimeError, UbpetApiError) as err:
        _LOGGER.debug("UPET optional endpoint failed: %s", err)
        return []
