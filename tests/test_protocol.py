"""Protocol fixtures: 8-byte control frames and dump sequences (opaque hex vectors)."""

from fittrack.protocol import (
    ScaleSession,
    cfg_frame,
    checksum8,
    init_sequence,
)


def H(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


def frame(b2: int, b3: int, b4: int, b5: int, chan: int) -> bytes:
    buf = bytearray([0xAC, 0x02, b2, b3, b4, b5, chan, 0])
    buf[7] = checksum8(buf)
    return bytes(buf)


def live(deci: int) -> bytes:
    return frame(deci >> 8, deci & 0xFF, 0, 0, 0xCE)


def stable(deci: int) -> bytes:
    return frame(deci >> 8, deci & 0xFF, 0, 0, 0xCA)


def feed_all(s: ScaleSession, frames) -> object:
    result = None
    for f in frames:
        result = s.feed(f)
    return result


# -- primitives --------------------------------------------------------------


def test_checksum_matches_captured_command_frame():
    assert checksum8(H("AC 02 FE 00 00 00 CC CA")) == 0xCA


def test_checksum_matches_captured_live_weight_frame():
    assert checksum8(H("AC 02 05 51 00 00 CE 24")) == 0x24


def test_cfg_frame_bytes():
    assert cfg_frame(0xF7, 0, 0, 0) == H("AC 02 F7 00 00 00 CC C3")
    assert cfg_frame(0xFB, 1, 35, 178) == H("AC 02 FB 01 23 B2 CC 9D")


def test_init_sequence_shape():
    seq = init_sequence(True, 35, 178, (26, 8, 25, 11, 23, 45))
    assert len(seq) == 6
    assert seq[2] == H("AC 02 FB 01 23 B2 CC 9D")
    assert seq[3] == H("AC 02 FD 1A 08 19 CC 04")
    assert seq[5][:5] == H("AC 02 FE 06 00")  # unit always KG


# -- full body-composition session (captured weigh-in #2) ---------------------


def dump_frames() -> list[bytes]:
    return [
        H("AC 02 FE 00 02 7E CB 49"),  # weight
        H("AC 02 FE 01 00 CA CB 94"),  # bmi 20.2
        H("AC 02 FE 02 00 77 CB 42"),  # fat 11.9
        H("AC 02 FE 03 00 6A CB 36"),  # subcut fat 10.6
        H("AC 02 FE 04 00 03 CB D0"),  # visceral 3
        H("AC 02 FE 05 02 17 CB E7"),  # lean 53.5
        H("AC 02 FE 06 06 32 CB 07"),  # bmr 1586
        H("AC 02 FE 07 00 1C CB EC"),  # bone 2.8
        H("AC 02 FE 08 02 7D CB 50"),  # water 63.7
        H("AC 02 FE 09 00 22 CB F4"),  # metabolic age 34
        H("AC 02 FE 0A 00 CB CB 9E"),  # protein 20.3
    ]


def test_full_session_with_dump():
    s = ScaleSession()
    result = feed_all(s, [
        H("AC 02 02 7C 00 00 CE 4C"),
        H("AC 02 02 7D 00 00 CE 4D"),
        H("AC 02 02 7E 00 00 CE 4E"),
        H("AC 02 02 7E 00 00 CE 4E"),
        H("AC 02 02 7E 00 00 CA 4A"),  # stable
        H("AC 02 FD 00 00 00 CB C8"),  # impedance start
        H("AC 02 FD 01 02 C4 CB 8F"),  # 708 ohm
    ] + dump_frames() + [
        H("AC 02 FE FC 00 00 CB C5"),  # end marker
    ])

    assert result is not None
    assert s.settled
    m = result.non_zero_sensors()
    assert m["weight_kg"] == 63.8
    assert m["bmi"] == 20.2
    assert m["body_fat_pct"] == 11.9
    assert m["subcutaneous_fat_pct"] == 10.6
    assert m["visceral_fat_index"] == 3
    assert m["lean_mass_kg"] == 53.5
    assert m["bmr_kcal"] == 1586
    assert m["bone_mass_kg"] == 2.8
    assert m["body_water_pct"] == 63.7
    assert m["metabolic_age_years"] == 34
    assert m["protein_pct"] == 20.3
    assert m["impedance_ohm"] == 708


def test_composite_pair_completes_measurement():
    s = ScaleSession()
    result = feed_all(s, [
        live(0x027E), live(0x027E), live(0x027E),
        stable(0x027E),
        H("AC 02 FF 00 02 21 1A 08 19 11 25 16 02 7E 00 CA 00 77 00 6A"),
        H("01 00 03 02 17 06 32 00 1C 02 7D 22 00 CB 00 01 23 B2 02 C4"),
    ])
    assert result is not None
    assert result.weight_kg == 63.8
    assert result.bmi == 20.2


# -- degraded sessions --------------------------------------------------------


def test_weight_only_via_grace_timer():
    s = ScaleSession()
    for _ in range(4):
        s.feed(live(0x027E))
    assert s.settled
    assert s.feed(dump_frames()[0]) is None  # stray index doesn't publish alone
    m = s.grace_expired()
    assert m is not None and m.weight_kg == 63.8
    assert m.bmi is None


def test_transient_noise_never_publishes():
    s = ScaleSession()
    # step-on transients seen between two real weigh-ins
    s.feed(H("AC 02 00 CC 00 00 CE 9A"))  # bogus "20.4 kg"
    s.feed(H("AC 02 01 81 00 00 CE 50"))  # 38.5 kg
    assert not s.settled
    assert s.grace_expired() is None


def test_bad_checksum_dropped():
    s = ScaleSession()
    assert s.feed(H("AC 02 02 7E 00 00 CE FF")) is None
    assert not s.settled


def test_zero_and_idle_frames_ignored():
    s = ScaleSession()
    assert s.feed(bytes(8)) is None
    assert s.feed(H("AC 02 FB 1A 08 19 CB 01")) is None  # profile echo on dump chan


def test_two_weighins_on_one_connection():
    s = ScaleSession()
    for _ in range(4):
        s.feed(live(0x027E))
    first = s.grace_expired()
    assert first is not None

    s.reset()
    result = feed_all(s, [
        live(640), live(641), live(642), live(643),
        stable(644),
        H("AC 02 FE FC 00 00 CB C5"),
    ])
    assert result is not None and result.weight_kg == 64.4


def test_composites_after_dump_do_not_double_publish():
    # Regression: real session published on end marker, then the composite
    # mirror pair arrived ~1s later and must not re-publish a partial set.
    s = ScaleSession()
    for f in [live(0x027E)] * 4:
        s.feed(f)
    first = s.feed(H("AC 02 FE FC 00 00 CB C5"))
    assert first is not None and first.weight_kg == 63.8

    for f in [
        H("AC 02 FF 00 02 21 1A 08 19 11 25 16 02 7E 00 CA 00 77 00 6A"),
        H("01 00 03 02 17 06 32 00 1C 02 7D 22 00 CB 00 01 23 B2 02 C4"),
    ]:
        assert s.feed(f) is None


def test_session_rearms_after_reset_delay(monkeypatch=None):
    import time as _time

    s = ScaleSession()
    for _ in range(4):
        s.feed(live(0x027E))
    assert s.grace_expired() is not None

    # within the echo window: swallowed
    assert s.feed(live(0x027E)) is None

    # simulate delay expiry by backdating the publish timestamp
    s._published_at = _time.monotonic() - 10.0
    for f in [live(640), live(641), live(642), live(643), stable(644)]:
        result = s.feed(f)
    assert result is None  # live frames never complete by themselves
    assert s.settled and s._pending.weight_kg == 64.4
