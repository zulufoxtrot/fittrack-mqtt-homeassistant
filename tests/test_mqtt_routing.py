"""Guest-mode routing tests using an injected fake MQTT client."""

from fittrack.config import Config
from fittrack.mqtt_ha import GUEST_KEYS, MqttPublisher, format_value
from fittrack.protocol import Measurement


class FakeClient:
    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.subscribed: list[str] = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, str(payload)))

    def subscribe(self, topic):
        self.subscribed.append(topic)

    def username_pw_set(self, *a):
        pass

    def will_set(self, *a, **k):
        pass

    def connect(self, *a, **k):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def make_publisher(**overrides) -> tuple[MqttPublisher, FakeClient]:
    cfg = Config(
        address="", name_filters=("SWAN",),
        sex_male=True, birth_year=1990, height_cm=175,
        mqtt_host="localhost", mqtt_port=1883, mqtt_user="u", mqtt_pass="p",
        base_topic="fittrack_scale", ha_discovery_prefix="homeassistant",
        expire_after=86400,
    )
    pub = MqttPublisher(cfg)
    fake = FakeClient()
    pub.client = fake  # type: ignore[assignment]
    return pub, fake


def full_measurement() -> Measurement:
    return Measurement(
        weight_kg=63.8, bmi=20.2, body_fat_pct=11.9,
        subcutaneous_fat_pct=10.6, visceral_fat_index=3.0,
        lean_mass_kg=53.5, bmr_kcal=1586.0, bone_mass_kg=2.8,
        body_water_pct=63.7, metabolic_age_years=34.0,
        protein_pct=20.3, impedance_ohm=708.0,
    )


def state_topics(pub: MqttPublisher, fake: FakeClient) -> dict[str, str]:
    base = pub.cfg.base_topic
    skip = {"status", "guest_mode", "guest_mode/set"}
    return {
        t[len(base):].lstrip("/"): p
        for t, p in fake.published
        if (t == base or t.startswith(base + "/"))
        and t[len(base):].lstrip("/") not in skip
        and not t.endswith("/measurement")
    }


def test_owner_mode_publishes_everything_to_primary_topics():
    pub, fake = make_publisher()
    pub.set_guest_mode(False)
    pub.client.published.clear()
    pub.publish_measurement(full_measurement())

    states = state_topics(pub, fake)
    assert states["weight_kg"] == "63.8"
    assert states["bmi"] == "20.2"
    assert states["impedance_ohm"] == "708"
    assert len(states) >= 12
    assert not any(k.startswith("guest/") for k in states)


def test_guest_mode_routes_only_weight_and_impedance_to_guest_topics():
    pub, fake = make_publisher()
    pub.set_guest_mode(True)
    pub.client.published.clear()
    values = pub.publish_measurement(full_measurement())

    assert set(values) == set(GUEST_KEYS)
    states = state_topics(pub, fake)
    assert states["guest/weight_kg"] == "63.8"
    assert states["guest/impedance_ohm"] == "708"
    assert not any(not k.startswith("guest/") for k in states), states


def test_measurement_blob_tags_user():
    pub, fake = make_publisher()
    import json

    pub.publish_measurement(full_measurement())
    owner_blob = [p for t, p in fake.published if t.endswith("/measurement")][-1]
    assert json.loads(owner_blob)["user"] == "owner"

    pub.set_guest_mode(True)
    pub.publish_measurement(full_measurement())
    guest_blob = [p for t, p in fake.published if t.endswith("/measurement")][-1]
    assert json.loads(guest_blob)["user"] == "guest"


def test_switch_command_updates_state():
    pub, fake = make_publisher()
    pub._on_message(None, None, type("M", (), {"topic": pub.guest_set_topic, "payload": b"ON"})())
    assert pub.guest_mode is True
    assert ("fittrack_scale/guest_mode", "on") in fake.published


def test_retained_state_restores_guest_mode():
    pub, _ = make_publisher()
    pub.start()
    pub._on_message(None, None, type("M", (), {"topic": pub.guest_state_topic, "payload": b"on"})())
    assert pub.guest_mode is True
    assert pub._restored is True


def test_format_value():
    assert format_value("weight_kg", 63.84) == "63.8"
    assert format_value("visceral_fat_index", 3.0) == "3"
    assert format_value("protein_pct", 20.34) == "20.3"


def test_last_weighin_sensor_published_with_timestamp():
    import json as _json

    from datetime import datetime

    pub, fake = make_publisher()
    pub.publish_measurement(full_measurement())
    topics = {t: p for t, p in fake.published}
    raw = topics["fittrack_scale/last_weighin"]
    datetime.fromisoformat(raw)  # must be RFC3339-parseable
    assert _json.loads(topics["fittrack_scale/measurement"])["timestamp"] == raw
