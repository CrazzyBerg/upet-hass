# UPET / Airrobo Cat Litter Box for Home Assistant

Custom Home Assistant integration for UPET / Airrobo smart cat litter boxes.

This integration uses the vendor cloud API for account/device data and the vendor IM/MQTT channel for live work commands and work-state polling.

## Warning

This is an unofficial integration and is not affiliated with, endorsed by, or supported by UPET, Airrobo, or the vendor.

Use it at your own risk. The integration depends on private vendor APIs and may stop working or trigger vendor-side account restrictions at any time. I am not responsible for blocked accounts, lost access, device issues, or any other consequences of using this integration.

For safer use, create a separate UPET/Airrobo account and share litter box access to that account instead of using your primary account.

## Features

- Account login with UPET/Airrobo credentials.
- Device discovery from the vendor cloud account.
- Read-only box, waste-bin, deodorant, online, and firmware data.
- Cat profile sensors and cat picture URL attributes when returned by the API.
- Config controls for confirmed settings:
  - Auto clean delay.
  - Auto clean on/off.
  - Deodorant alert.
  - Empty waste-bin reminder switch, time, and weekdays.
  - Do not disturb schedule.
  - Light schedule.
- MQTT work commands:
  - Start clean.
  - Pause clean.
  - Resume clean.
  - Flatten.
  - Pause flatten.
  - Resume flatten.
  - Raise litter rake.
  - Lower litter rake.
  - Request MQTT state.
- Live MQTT work status:
  - Work mode.
  - Work state.
  - Work cause.
  - Last successful MQTT status update.
- Diagnostics download with sanitized raw API/coordinator data.
- Local brand icons for supported Home Assistant versions.

## Installation

Install through HACS as a custom repository:

1. Open HACS in Home Assistant.
2. Go to `Integrations`.
3. Open the three-dot menu and choose `Custom repositories`.
4. Add `https://github.com/CrazzyBerg/upet-hass`.
5. Select category `Integration`.
6. Install `UPET / Airrobo Cat Litter Box`.

Restart Home Assistant after installation.

Manual installation is also possible by copying this repository's
`custom_components/ubpet` directory into your Home Assistant config directory:

```bash
<config>/custom_components/ubpet
```

Add the integration from Home Assistant:

```text
Settings -> Devices & services -> Add integration -> UPET / Airrobo Cat Litter Box
```

## Configuration

Required fields:

- `Country`: the same country selected for the account in the official app.
- `Account`: UPET/Airrobo login account.
- `Password`: plain account password. The integration hashes it internally before sending it to the API.

The integration also needs vendor app credentials:

- `APP_ID`
- `APP_KEY`
- `PRODUCT`

Bundled app defaults are included with the integration, so normal setup asks
only for country, account, and password. The selected country chooses the vendor
region endpoint and is sent as the login area code. The app defaults are stored
in obfuscated form to keep raw values out of the repository.

If you maintain a private deployment and want to override the bundled app credentials,
use `custom_components/ubpet/secrets.py.example` as a template for a private
`secrets.py`.

## Entities

Device sensors:

- Box status.
- Empty waste-bin reminder days.
- Box use times.
- Waste-bin level.
- Waste-bin last reset.
- Box full max.
- Box full alert.
- Deodorant remaining days.
- Auto clean delay.
- Firmware version.
- Wi-Fi name.
- MQTT work mode.
- MQTT work state.
- MQTT work cause.
- Last REST update.
- Last MQTT update.

Device binary sensors:

- Online.
- Deodorant expired.
- Sensor enabled.
- Camera enabled.
- Child lock.

Device controls:

- Auto clean delay number.
- Auto clean switch.
- Deodorant alert switch.
- Empty waste-bin reminder switch.
- Empty waste-bin reminder time.
- Empty waste-bin reminder weekday select.
- Do not disturb switch and start/end times.
- Light schedule switch and start/end times.

Device buttons:

- REST request.
- MQTT request state.
- Reset waste-bin counter.
- Start clean.
- Pause clean.
- Resume clean.
- Flatten.
- Pause flatten.
- Resume flatten.
- Raise litter rake.
- Lower litter rake.

Cat sensors:

- Weight.
- Visits.
- Usage duration.

Cat picture URLs are exposed as cat entity attributes when returned by the vendor API.

## Visit History and Videos

The litter box camera records every visit. This integration exposes those records:

- **Media browser**: `Media -> UPET kedi tuvaleti -> <your box>` lists the latest visits (cat, time, weight, duration) and plays their videos.
- **Websocket** `ubpet/records` (`serial`, `size` up to 50, `before` = the `eventTime` cursor of the last record) returns visits without any signed vendor URLs, for custom cards.
- **Websocket** `ubpet/video` (`serial`, `record_id`) prepares a video and returns a signed, time-limited URL that a `<video>` element can play.
- **HTTP** `GET /api/ubpet/video/<serial>/<record_id>` serves the MP4 (Home Assistant auth or a signed path required; supports range requests for Safari/iOS).

- **Websocket** `ubpet/assign` (`serial`, `record_id`, `cat_id`) changes which cat a visit belongs to, like the app's correction screen. It uses `PUT /catbox-server/web/box/cat-record` for a visit that already has a cat and `POST /catbox-server/web/box/cat/record/new` for an unmatched one, both with `{"catInfoId", "recordId"}`.
- **Cat photos**: `GET /api/ubpet/cat_photo/<cat_id>` (auth or signed path) serves the cat's profile photo, and `ubpet/cats` returns signed paths for `<img>`. The cat list carries unsigned private URLs (403), so the photo is signed through the app's file service first: `GET /v2/file-service-rest/private/download?pName=airpet&validTime=3600&cloudType=2&urls=<url>`.

The vendor stores videos as Motion-JPEG AVI (240x240, ~5 fps) with a broken audio header, which browsers cannot play and ffmpeg cannot open directly. The integration extracts the JPEG frames itself and encodes them to H.264/MP4 with Home Assistant's ffmpeg (under a second per visit on a Raspberry Pi 4).

Converted videos are only a cache: they live in the system temp folder, are deleted 15 minutes after they were last watched, and the folder is cleared when Home Assistant starts. Nothing is archived. Video URLs are fetched fresh from the vendor because their signatures expire after a few hours, and only HTTPS URLs on the vendor's object storage (`*.aliyuncs.com`) are downloaded.

## MQTT Behavior

Work commands are sent through the vendor IM/MQTT path, not REST.

The integration obtains MQTT credentials and topics from the vendor API, publishes command payloads to the IM publish topic, and polls `request_state` for live work status.

Polling intervals:

- `RUNNING`: every 1 second.
- `PAUSED`: every 10 seconds.
- `PENDING`/standby/unknown: every 60 seconds.

MQTT polling starts after Home Assistant finishes setting up the integration, so startup is not blocked by MQTT traffic.

## Confirmed MQTT Commands

Current service ids and operation payload bodies:

| Service id | Meaning | Operation body |
| --- | --- | --- |
| `start_clean_up` | Start clean | `08011001` |
| `pause_clean_up` | Pause clean | `08011002` |
| `resume_clean_up` | Resume clean | `08011003` |
| `start_flatten` | Flatten / smoothing | `08031001` |
| `pause_flatten` | Pause flatten | `08031002` |
| `resume_flatten` | Resume flatten | `08031003` |
| `start_rise` | Raise litter rake | `08071001` |
| `start_drop` | Lower litter rake | `08081001` |

Confirmed operation ordinals:

- `CLEAN = 1`
- `SMOOTHING = 3`
- `RISE = 7`
- `DROP = 8`
- `START = 1`
- `PAUSE = 2`
- `RESUME = 3`

## Limitations

- The integration depends on the vendor cloud and vendor IM/MQTT service.
- Commands are confirmed by MQTT delivery/status responses, but full semantic validation of every receipt/error cause is not complete.
- Control board / child lock is currently exposed as read-only because the observed API flow returns a permission error for writes.
- Deodorize, direct light control, camera toggle, and full-alert threshold controls are not implemented yet.

## Development

Run dependency-free tests:

```bash
python3 -m unittest discover -s tests -v
```

Run quick checks:

```bash
python3 -m unittest tests.test_mqtt tests.test_api
python3 -m py_compile custom_components/ubpet/*.py
python3 -m json.tool custom_components/ubpet/strings.json >/tmp/ubpet_strings.json
python3 -m json.tool custom_components/ubpet/translations/en.json >/tmp/ubpet_en.json
python3 -m json.tool custom_components/ubpet/translations/uk.json >/tmp/ubpet_uk.json
```

The current tests cover:

- API signing and password hashing.
- Account type fallback.
- Authenticated request headers.
- Dashboard aggregation.
- Settings payload builders.
- IM credential/contact lookup.
- MQTT codec, packet helpers, RISP/protobuf payload generation, and service ordinals.
- Diagnostics redaction.

## Implementation Notes

Implemented REST endpoints:

- `PUT /user-service-rest/v2/user/login`
- `GET /user-service-rest/v2/robot/common/device/list`
- `GET /catbox-server/box/config/allConfig?serialNumber=...`
- `GET /catbox-server/box/config/box-use-times/?serialNumber=...`
- `PUT /catbox-server/box/config/box-use-times/reset`
- `GET /catbox-server/app/deodorant-block/status?serialNumber=...`
- `GET /v1/ubtechinc-im-manager/im/online/device/?sn=...`
- `POST /v1/ubtechinc-im-manager/im/login`
- `GET /v1/ubtechinc-im-manager/im/friends`
- `GET /catbox-server/web/cat/info`
- `POST /catbox-server/web/box/record/new`
- `PUT /catbox-server/box/config/switch/update`
- `PUT /catbox-server/box/config/timePeriod/update`
- `PUT /catbox-server/box/config/timePoint/update`
- `POST /catbox-server/app/deodorant-block/switch`

MQTT command format:

```text
Message -> MessageContent(custom) -> Any(BytesValue) -> CommandProto(protype=2) -> RISP frame -> AirPet protobuf body
```

MQTT state request uses RISP command `0x3fd`. Operation commands use RISP command `0x3fb`.

## Roadmap

Planned or open work:

- Decode more MQTT receipt/error causes and surface command failures more clearly.
- Implement deodorize command if available.
- Implement full-alert threshold setting.
- Implement writable control board / child lock if a permitted API flow is found.
- Add Home Assistant platform tests with `pytest-homeassistant-custom-component`.
