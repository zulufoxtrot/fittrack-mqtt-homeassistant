"""BLE driver: scan → connect → init → collect → publish → repeat."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from bleak import BleakClient, BleakScanner

from .config import Config
from .mqtt_ha import MqttPublisher
from .protocol import DUMP_GRACE_S, ScaleSession, init_sequence

log = logging.getLogger(__name__)

SVC_FFB0 = "0000ffb0-0000-1000-8000-00805f9b34fb"
CHR_CFG = "0000ffb1-0000-1000-8000-00805f9b34fb"
CHR_DATA = "0000ffb2-0000-1000-8000-00805f9b34fb"

SCAN_INTERVAL_S = 5.0
IDLE_TIMEOUT_S = 30.0
RECONNECT_COOLDOWN_S = 15.0


class Driver:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.publisher = MqttPublisher(cfg)
        self._grace_task: asyncio.Task | None = None
        self._last_frame_ts: float = 0.0

    async def run(self) -> None:
        self.publisher.start()
        while True:
            try:
                device = await self._find_scale()
                if device is None:
                    await asyncio.sleep(SCAN_INTERVAL_S)
                    continue
                await self._run_session(device)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("session failed; retrying")
                await asyncio.sleep(RECONNECT_COOLDOWN_S)

    # -- discovery ----------------------------------------------------------

    async def _find_scale(self):
        devices = await BleakScanner.discover(timeout=4.0, return_adv=True)
        for addr, (dev, adv) in devices.items():
            if self.cfg.address and addr == self.cfg.address:
                return dev
            name = (dev.name or adv.local_name or "").upper()
            if any(name.startswith(f) for f in self.cfg.name_filters) or (
                SVC_FFB0 in [u.lower() for u in adv.service_uuids]
            ):
                return dev
        return None

    # -- session ------------------------------------------------------------

    async def _run_session(self, device) -> None:
        session = ScaleSession()
        log.info("connecting to %s (%s)", device.name, device.address)
        async with BleakClient(device, timeout=20.0) as client:
            log.info("connected, mtu=%d", client.mtu_size)
            self._last_frame_ts = asyncio.get_event_loop().time()

            def on_data(_char, data: bytearray) -> None:
                self._last_frame_ts = asyncio.get_event_loop().time()
                measurement = session.feed(bytes(data))
                if session.settled and self._grace_task is None:
                    self._grace_task = asyncio.create_task(self._grace_timer(session))
                if measurement is not None:
                    self.publisher.publish_measurement(measurement)
                    session.reset()
                    if self._grace_task:
                        self._grace_task.cancel()
                        self._grace_task = None

            await client.start_notify(CHR_DATA, on_data)
            now = dt.datetime.now()
            seq = init_sequence(
                self.cfg.sex_male,
                self.cfg.age,
                self.cfg.height_cm,
                (
                    now.year - 2000, now.month, now.day,
                    now.hour, now.minute, now.second,
                ),
            )
            for pkt in seq:
                await client.write_gatt_char(CHR_CFG, pkt, response=False)
                await asyncio.sleep(0.25)
            log.info("init sequence sent; waiting for user to step on the scale")

            while True:
                await asyncio.sleep(1.0)
                idle = asyncio.get_event_loop().time() - self._last_frame_ts
                if idle > IDLE_TIMEOUT_S:
                    log.info("scale idle for %.0fs, disconnecting", idle)
                    break

            if self._grace_task:
                self._grace_task.cancel()
                self._grace_task = None

        await asyncio.sleep(RECONNECT_COOLDOWN_S)

    async def _grace_timer(self, session: ScaleSession) -> None:
        try:
            await asyncio.sleep(DUMP_GRACE_S)
            m = session.grace_expired()
            if m is not None:
                self.publisher.publish_measurement(m)
                session.reset()
        except asyncio.CancelledError:
            pass
