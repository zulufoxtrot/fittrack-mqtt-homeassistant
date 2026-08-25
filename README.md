# fittrack-scale-mqtt

Bluetooth LE driver for the **FitTrack Dara** body-composition scale → **MQTT** →
**Home Assistant** (via MQTT discovery). Runs headless in Docker on Linux.

The protocol was verified live against real hardware; see
[`docs/protocol.md`](docs/protocol.md). Raw captures live in `captures/`.
Unit name quirk: many Dara units advertise as `SWAN` (eLink/Icomon MGB family),
not "FitTrack" — both are matched by default.

## Entities published to Home Assistant

Weight, BMI, body fat %, subcutaneous fat %, visceral fat index, lean body mass,
BMR, bone mass, body water %, metabolic age, protein %, impedance Ω.
All sensors share one device card ("FitTrack Scale"), are retained, expire after
24 h without a measurement, and track an availability topic.

Battery level is not exposed by this hardware (no Battery Service in its GATT).

## Guest mode

The driver exposes a **`switch.fittrack_guest_mode`** entity in Home Assistant.
Flip it ON before someone else weighs themselves:

- weight and impedance are routed to separate **`sensor.fittrack_guest_*`**
  entities — your own sensor history stays untouched
- body composition (BMI, fat %, …) is *not* published at all: the scale computes
  those using your profile, so they would be meaningless for another person

Guest mode survives driver restarts (retained state) and flips back off by
itself if the broker had no retained value. For regular second users see the
weight-window auto-attribution idea in the project notes — guest mode is the
zero-configuration option.

## Quick start

### Development (macOS/Linux, bare metal)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest

MQTT_HOST=<broker> MQTT_USER=<user> MQTT_PASS=<pass> \
FITTRACK_SEX=male FITTRACK_BIRTH_YEAR=1990 FITTRACK_HEIGHT_CM=175 \
python3 -m fittrack
```

Step on the scale barefoot. The driver scans, connects, sends the user-profile
handshake, collects the measurement, publishes to MQTT, then idles until the
scale sleeps. Back-to-back weigh-ins on one connection are supported.

> Note: Docker Desktop on macOS cannot reach the host's Bluetooth — develop
> bare-metal on the Mac, deploy the container on your Linux host.

### Production (Linux + BlueZ)

```bash
cp .env.example .env   # fill in MQTT + profile
docker compose up -d --build
```

`docker-compose.yml` uses `network_mode: host` and mounts `/var/run/dbus`
read-only; Bluetooth itself is provided by the host's BlueZ daemon.

### Configuration (environment variables)

| variable | default | description |
|---|---|---|
| `FITTRACK_ADDRESS` | *(autodiscover)* | pin a specific device address/MAC |
| `FITTRACK_NAME_FILTERS` | `SWAN,FITTRACK` | comma-separated advertised-name prefixes |
| `FITTRACK_SEX` | `male` | `male` / `female` — sent to scale, drives its BIA math |
| `FITTRACK_BIRTH_YEAR` | `1990` | age is derived each session so birthdays roll over |
| `FITTRACK_HEIGHT_CM` | `169` | cm |
| `MQTT_HOST` / `MQTT_PORT` | `homeassistant.local` / `1883` | broker |
| `MQTT_USER` / `MQTT_PASS` | *(none)* | broker auth |
| `MQTT_BASE_TOPIC` | `fittrack_scale` | state topic root |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | discovery prefix |
| `EXPIRE_AFTER` | `86400` | seconds before sensors become unavailable |
| `LOG_LEVEL` | `INFO` | |

## Layout & tests

```
fittrack/
  protocol.py    # frame parsing + measurement state machine (pure, unit-tested)
  driver.py      # bleak scan/connect/session loop
  mqtt_ha.py     # paho-mqtt publishing + HA discovery payloads
  config.py      # env config
tests/           # fixtures = frames captured from the real scale
tools/           # RE utilities: scanner, GATT dump, raw capture
docs/protocol.md # full protocol documentation
```

```bash
python3 -m pytest tests/
```

## Credits & license

Protocol knowledge cross-checked against
[openScale](https://github.com/oliexdev/openScale)'s `FitTrackDaraHandler` /
`MGBHandler` (GPL-3.0); portions of this driver are therefore licensed GPL-3.0.
