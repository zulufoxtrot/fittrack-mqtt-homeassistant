"""Environment-driven configuration."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


@dataclass(frozen=True)
class Config:
    # BLE
    address: str  # empty = autodiscover by name/service
    name_filters: tuple[str, ...]

    # user profile sent to the scale (drives its body-composition math)
    sex_male: bool
    birth_year: int
    height_cm: int

    # MQTT / HA
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_pass: str
    base_topic: str
    ha_discovery_prefix: str
    expire_after: int

    @classmethod
    def from_env(cls) -> "Config":
        names = tuple(
            n.strip().upper()
            for n in _env("FITTRACK_NAME_FILTERS", "SWAN,FITTRACK").split(",")
            if n.strip()
        )
        return cls(
            address=_env("FITTRACK_ADDRESS"),
            name_filters=names,
            sex_male=_env("FITTRACK_SEX", "male").lower() != "female",
            birth_year=int(_env("FITTRACK_BIRTH_YEAR", "1990")),
            height_cm=int(_env("FITTRACK_HEIGHT_CM", "175")),
            mqtt_host=_env("MQTT_HOST", "homeassistant.local"),
            mqtt_port=int(_env("MQTT_PORT", "1883")),
            mqtt_user=_env("MQTT_USER"),
            mqtt_pass=_env("MQTT_PASS"),
            base_topic=_env("MQTT_BASE_TOPIC", "fittrack_scale"),
            ha_discovery_prefix=_env("HA_DISCOVERY_PREFIX", "homeassistant"),
            expire_after=int(_env("EXPIRE_AFTER", "86400")),
        )

    @property
    def age(self) -> int:
        """Age sent to the scale, computed fresh so birthdays roll over."""
        return max(0, min(255, dt.date.today().year - self.birth_year))
