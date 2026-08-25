"""MQTT publishing + Home Assistant MQTT discovery."""

from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

from .protocol import Measurement

log = logging.getLogger(__name__)

# key, friendly name, unit, device_class, icon
SENSORS: list[tuple[str, str, str | None, str | None, str]] = [
    ("weight_kg", "Weight", "kg", "weight", "mdi:scale-bathroom"),
    ("bmi", "BMI", None, None, "mdi:human"),
    ("body_fat_pct", "Body fat", "%", None, "mdi:human"),
    ("subcutaneous_fat_pct", "Subcutaneous fat", "%", None, "mdi:human"),
    ("visceral_fat_index", "Visceral fat", None, None, "mdi:food"),
    ("lean_mass_kg", "Lean body mass", "kg", None, "mdi:weight"),
    ("bmr_kcal", "BMR", "kcal", None, "mdi:fire"),
    ("bone_mass_kg", "Bone mass", "kg", None, "mdi:bone"),
    ("body_water_pct", "Body water", "%", None, "mdi:water-percent"),
    ("metabolic_age_years", "Metabolic age", None, None, "mdi:calendar-heart"),
    ("protein_pct", "Protein", "%", None, "mdi:nutrition"),
    ("impedance_ohm", "Impedance", "Ω", None, "mdi:omega"),
]

# Guests get weight + impedance only: body composition is computed by the
# scale using the configured owner profile, so it would be wrong for anyone else.
GUEST_KEYS = ("weight_kg", "impedance_ohm")

FMT = {
    "weight_kg": "{:.1f}",
    "lean_mass_kg": "{:.1f}",
    "bone_mass_kg": "{:.1f}",
}


def format_value(key: str, value: float) -> str:
    fmt = FMT.get(key)
    if fmt:
        return fmt.format(value)
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


class MqttPublisher:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._discovered = False
        self._discovered_guest = False
        self._restored = False
        self.guest_mode = False
        base = cfg.base_topic
        self.status_topic = f"{base}/status"
        self.guest_state_topic = f"{base}/guest_mode"
        self.guest_set_topic = f"{base}/guest_mode/set"
        self.client = mqtt.Client(client_id="fittrack-scale-mqtt")
        if cfg.mqtt_user:
            self.client.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass or None)
        self.client.will_set(self.status_topic, "offline", qos=1, retain=True)
        self.client.on_message = self._on_message

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self.client.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.client.loop_start()
        # Restore guest mode from the retained state (falls back to OFF below).
        self.client.subscribe(self.guest_state_topic)
        self.client.subscribe(self.guest_set_topic)
        self._restore_timer_start()
        self.publish_status("online")
        self._publish_discovery()

    def stop(self) -> None:
        self.publish_status("offline")
        self.client.loop_stop()
        self.client.disconnect()

    def _restore_timer_start(self) -> None:
        import threading

        def fallback_off() -> None:
            if not self._restored:
                self.set_guest_mode(False)
        timer = threading.Timer(2.0, fallback_off)
        timer.daemon = True
        timer.start()

    # -- MQTT callbacks -----------------------------------------------------

    def _on_message(self, _client, _userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace").strip().lower()
        if msg.topic == self.guest_set_topic:
            self.set_guest_mode(payload in ("on", "true", "1"))
        elif msg.topic == self.guest_state_topic:
            # retained value seen at startup
            if not self._restored:
                self._restored = True
                self.guest_mode = payload == "on"

    def set_guest_mode(self, enabled: bool) -> None:
        if enabled != self.guest_mode or not self._restored:
            self.guest_mode = enabled
            self._restored = True
            self.client.publish(
                self.guest_state_topic,
                "on" if enabled else "off",
                qos=1,
                retain=True,
            )
            log.info("guest mode %s", "ON" if enabled else "OFF")

    # -- publishing ---------------------------------------------------------

    def publish_status(self, state: str) -> None:
        self.client.publish(self.status_topic, state, qos=1, retain=True)

    def _sensor_payload(self, key: str, name: str, unit: str | None,
                        dev_class: str | None, icon: str,
                        state_topic: str, unique_suffix: str) -> str:
        payload = {
            "name": name,
            "unique_id": f"fittrack_scale_{unique_suffix}",
            "state_topic": state_topic,
            "availability": {
                "topic": self.status_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            },
            "device": DEVICE,
            "icon": icon,
            "state_class": "measurement",
            "expire_after": self.cfg.expire_after,
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if dev_class:
            payload["device_class"] = dev_class
        return json.dumps(payload)

    def _publish_discovery(self) -> None:
        base, prefix = self.cfg.base_topic, self.cfg.ha_discovery_prefix

        for key, name, unit, dev_class, icon in SENSORS:
            self.client.publish(
                f"{prefix}/sensor/fittrack_scale/fittrack_scale_{key}/config",
                self._sensor_payload(key, name, unit, dev_class, icon,
                                     f"{base}/{key}", key),
                retain=True,
            )

        self.client.publish(
            f"{prefix}/switch/fittrack_scale/fittrack_scale_guest_mode/config",
            json.dumps({
                "name": "Guest mode",
                "unique_id": "fittrack_scale_guest_mode",
                "state_topic": self.guest_state_topic,
                "command_topic": self.guest_set_topic,
                "payload_on": "on",
                "payload_off": "off",
                "retain": True,
                "availability": {
                    "topic": self.status_topic,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
                "device": DEVICE,
                "icon": "mdi:account-plus",
            }),
            retain=True,
        )
        log.info("published HA discovery configs (%d sensors + guest switch)", len(SENSORS))
        self._discovered = True

    def _ensure_guest_discovery(self) -> None:
        if self._discovered_guest:
            return
        base, prefix = self.cfg.base_topic, self.cfg.ha_discovery_prefix
        for key in GUEST_KEYS:
            name, unit, dev_class, icon = next(
                (s[1], s[2], s[3], s[4]) for s in SENSORS if s[0] == key)
            self.client.publish(
                f"{prefix}/sensor/fittrack_scale/fittrack_guest_{key}/config",
                self._sensor_payload(key, f"Guest {name.lower()}", unit, dev_class,
                                     icon, f"{base}/guest/{key}", f"guest_{key}"),
                retain=True,
            )
        self._discovered_guest = True
        log.info("published guest sensor discovery configs")

    def publish_measurement(self, m: Measurement) -> dict[str, float]:
        if not self._discovered:
            self._publish_discovery()

        values = m.non_zero_sensors()
        user = "guest" if self.guest_mode else "owner"
        if self.guest_mode:
            self._ensure_guest_discovery()
            values = {k: v for k, v in values.items() if k in GUEST_KEYS}
            root = f"{self.cfg.base_topic}/guest"
        else:
            root = self.cfg.base_topic

        for key, value in values.items():
            self.client.publish(f"{root}/{key}", format_value(key, value), retain=True)

        self.client.publish(
            f"{self.cfg.base_topic}/measurement",
            json.dumps({"user": user, "values": values}, default=str),
            retain=True,
        )
        log.info("published to MQTT (%s): %s", user, values)
        return values


DEVICE = {
    "identifiers": ["fittrack_scale"],
    "name": "FitTrack Scale",
    "manufacturer": "FitTrack",
    "model": "Dara (MGB/eLink)",
}
