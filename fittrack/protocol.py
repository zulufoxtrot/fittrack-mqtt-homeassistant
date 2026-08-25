"""FitTrack Dara / MGB-family BLE protocol parser.

Pure logic only: frame decoding + a per-connection measurement state machine.
All frames reference docs/protocol.md. Values are captured-frame verified.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

HDR = 0xAC
CHAN_CMD = 0xCC
CHAN_WEIGHT_LIVE = 0xCE
CHAN_WEIGHT_STABLE = 0xCA
CHAN_DUMP = 0xCB

IDX_WEIGHT = 0x00
IDX_BMI = 0x01
IDX_FAT = 0x02
IDX_SUBCUT = 0x03
IDX_VISCERAL = 0x04
IDX_LEAN = 0x05
IDX_BMR = 0x06
IDX_BONE = 0x07
IDX_WATER = 0x08
IDX_METABOLIC_AGE = 0x09
IDX_PROTEIN = 0x0A
IDX_END = 0xFC

STABLE_FRAMES = 4
DUMP_GRACE_S = 20.0
PUBLISH_RESET_DELAY_S = 3.0


def checksum8(buf: bytes | bytearray) -> int:
    return sum(buf[2:7]) & 0xFF


def cfg_frame(b2: int, b3: int, b4: int, b5: int) -> bytes:
    buf = bytearray([HDR, 0x02, b2 & 0xFF, b3 & 0xFF, b4 & 0xFF, b5 & 0xFF, CHAN_CMD, 0])
    buf[7] = checksum8(buf)
    return bytes(buf)


def init_sequence(sex_male: bool, age: int, height_cm: int,
                  now_components: tuple[int, int, int, int, int, int]) -> list[bytes]:
    yy, mm, dd, hh, mi, ss = now_components
    sex = 1 if sex_male else 2
    return [
        cfg_frame(0xF7, 0, 0, 0),
        cfg_frame(0xFA, 0, 0, 0),
        cfg_frame(0xFB, sex, age, height_cm),
        cfg_frame(0xFD, yy, mm, dd),
        cfg_frame(0xFC, hh, mi, ss),
        cfg_frame(0xFE, 6, 0, 0),  # KG=0 always; HA handles unit display
    ]


@dataclass
class Measurement:
    weight_kg: float | None = None
    bmi: float | None = None
    body_fat_pct: float | None = None
    subcutaneous_fat_pct: float | None = None
    visceral_fat_index: float | None = None
    lean_mass_kg: float | None = None
    bmr_kcal: float | None = None
    bone_mass_kg: float | None = None
    body_water_pct: float | None = None
    metabolic_age_years: float | None = None
    protein_pct: float | None = None
    impedance_ohm: float | None = None

    def complete(self) -> bool:
        return self.weight_kg is not None

    def non_zero_sensors(self) -> dict[str, float]:
        out = {}
        for key in (
            "weight_kg", "bmi", "body_fat_pct", "subcutaneous_fat_pct",
            "visceral_fat_index", "lean_mass_kg", "bmr_kcal", "bone_mass_kg",
            "body_water_pct", "metabolic_age_years", "protein_pct", "impedance_ohm",
        ):
            val = getattr(self, key)
            if val:
                out[key] = val
        return out


def _be(buf: bytes) -> int:
    return (buf[0] << 8) | buf[1]


def is_telemetry_frame(data: bytes) -> bool:
    """True when a notification carries measurement data (vs command echoes/idle)."""
    if len(data) == 8 and data[0] == HDR and checksum8(data) == data[7]:
        return data[6] in (CHAN_WEIGHT_LIVE, CHAN_WEIGHT_STABLE, CHAN_DUMP)
    return len(data) == 20


@dataclass
class ScaleSession:
    """Accumulates one scale connection's frames into Measurements.

    feed() returns a finished Measurement exactly once per weigh-in, or None.
    A weigh-in finishes when: the dump end marker arrives, the composite pair
    completes, or the grace timeout expires after a settled weight.
    """

    _pending: Measurement = field(default_factory=Measurement)
    _live_deci: int = -1
    _stable_run: int = 0
    _settled_seen: bool = False
    _published: bool = False
    _published_at: float | None = None
    _saw_dump_data: bool = False

    def reset(self) -> None:
        self._pending = Measurement()
        self._live_deci = -1
        self._stable_run = 0
        self._settled_seen = False
        self._published = False
        self._published_at = None
        self._saw_dump_data = False

    # -- public API ---------------------------------------------------------

    def feed(self, data: bytes) -> Optional[Measurement]:
        # Lazily re-arm for the next weigh-in only after the current one's echo
        # traffic (composite mirrors etc.) has finished flowing past.
        if self._published:
            if self._since_publish() < PUBLISH_RESET_DELAY_S:
                return None
            self.reset()
        self._published_at = None

        if len(data) == 20:
            return self._feed_composite(data)

        if len(data) != 8 or data[0] != HDR:
            return None
        if checksum8(data) != data[7]:
            log.debug("bad checksum, dropping %s", data.hex(" "))
            return None

        b2 = data[2]
        chan = data[6]

        if chan in (CHAN_WEIGHT_LIVE, CHAN_WEIGHT_STABLE):
            return self._feed_weight(_be(data[2:4]), stable=chan == CHAN_WEIGHT_STABLE)
        if chan == CHAN_DUMP and b2 == 0xFD and data[3] == 0x01:
            self._pending.impedance_ohm = float(_be(data[4:6]))
            return None
        if chan == CHAN_DUMP and b2 == 0xFE:
            idx, value = data[3], _be(data[4:6])
            return self._apply_dump_index(idx, value)
        return None

    def grace_expired(self) -> Optional[Measurement]:
        """Called when the post-settle grace timer fires without a dump."""
        if self._settled_seen and not self._published and self._pending.weight_kg:
            log.info("no body-composition dump within %.0fs; publishing weight-only", DUMP_GRACE_S)
            return self._finalize()
        return None

    # -- internals ----------------------------------------------------------

    def _feed_weight(self, deci: int, stable: bool) -> Optional[Measurement]:
        if not 0 < deci < 6000:
            return None
        self._pending.weight_kg = deci / 10.0
        if stable:
            self._stable_run = STABLE_FRAMES
        else:
            self._stable_run = self._stable_run + 1 if deci == self._live_deci else 1
        self._live_deci = deci
        if self._stable_run >= STABLE_FRAMES:
            self._settled_seen = True
        return None  # driver arms grace timer when _settled() flips true

    @property
    def settled(self) -> bool:
        return self._stable_run >= STABLE_FRAMES

    def _apply_dump_index(self, idx: int, value: int) -> Optional[Measurement]:
        if idx == IDX_END:
            if self._pending.weight_kg and not self._published:
                return self._finalize()
            return None

        p = self._pending
        if idx == IDX_WEIGHT:
            if 0 < value < 6000:
                p.weight_kg = value / 10.0
        elif idx == IDX_BMI:
            p.bmi = value / 10.0 or None
        elif idx == IDX_FAT:
            p.body_fat_pct = value / 10.0 or None
        elif idx == IDX_SUBCUT:
            p.subcutaneous_fat_pct = value / 10.0 or None
        elif idx == IDX_VISCERAL:
            p.visceral_fat_index = float(value) or None
        elif idx == IDX_LEAN:
            p.lean_mass_kg = value / 10.0 or None
        elif idx == IDX_BMR:
            p.bmr_kcal = float(value) or None
        elif idx == IDX_BONE:
            p.bone_mass_kg = value / 10.0 or None
        elif idx == IDX_WATER:
            p.body_water_pct = value / 10.0 or None
        elif idx == IDX_METABOLIC_AGE:
            p.metabolic_age_years = float(value) or None
        elif idx == IDX_PROTEIN:
            p.protein_pct = value / 10.0 or None
        if idx != IDX_WEIGHT:
            self._saw_dump_data = True
        return None

    def _feed_composite(self, d: bytes) -> Optional[Measurement]:
        if d[0] == HDR and d[1] in (0x02, 0x03) and d[2] == 0xFF:
            # First fragment mirrors weight/BMI/fat/subcut — re-apply idempotently
            # in case index frames were missed.
            for i, off in ((0, 12), (1, 14), (2, 16), (3, 18)):
                self._apply_dump_index(i, _be(d[off:off + 2]))
            return None
        if d[0] == 0x01 and d[1] == 0x00:
            # Second fragment = completion signal.
            if self._pending.weight_kg and not self._published:
                return self._finalize()
        return None

    def _finalize(self) -> Measurement:
        self._published = True
        self._published_at = time.monotonic()
        m = self._pending
        log.info("measurement finalized: %s", m.non_zero_sensors())
        return m

    def _since_publish(self) -> float:
        return -1.0 if self._published_at is None else time.monotonic() - self._published_at
