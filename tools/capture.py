"""Capture raw notifications from the scale while replaying the openScale init sequence."""

import asyncio
import datetime as dt
import sys

from bleak import BleakClient

if len(sys.argv) < 2:
    sys.exit("usage: capture.py <BLE-address> [duration-s]");
ADDR = sys.argv[1]
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0

# Profile sent to the scale during RE; override via env if needed.
import os
SEX_MALE = 1 if os.environ.get('RE_SEX', 'male') != 'female' else 2
AGE = int(os.environ.get('RE_AGE', '35'))
HEIGHT_CM = int(os.environ.get('RE_HEIGHT_CM', '175'))
UNIT_KG = 0  # KG=0, LB=1, ST=2 (FitTrack Dara mapping)

SVC_FFB0 = "0000ffb0-0000-1000-8000-00805f9b34fb"
CHR_CFG = "0000ffb1-0000-1000-8000-00805f9b34fb"   # write w/o response
CHR_DATA = "0000ffb2-0000-1000-8000-00805f9b34fb"  # notify
CHR_X = "0000ffb3-0000-1000-8000-00805f9b34fb"     # undocumented: read/notify/write


def checksum8(buf: bytes) -> int:
    return sum(buf[2:7]) & 0xFF


def cfg(b2: int, b3: int, b4: int, b5: int) -> bytes:
    buf = bytes([0xAC, 0x02, b2 & 0xFF, b3 & 0xFF, b4 & 0xFF, b5 & 0xFF, 0xCC, 0])
    return buf[:7] + bytes([checksum8(buf)])


def ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def main() -> None:
    log_path = f"captures/capture_{dt.datetime.now():%Y%m%d_%H%M%S}.log"

    def on_data(char, data: bytearray) -> None:
        line = f"[{ts()}] {char.uuid} NOTIFY {len(data):2d}B {data.hex(' ').upper()}"
        print(line)
        log.write(line + "\n")
        log.flush()

    async with BleakClient(ADDR, timeout=15.0) as client:
        print(f"Connected: {client.is_connected} mtu={client.mtu_size}")
        log = open(log_path, "w")

        try:
            initial = await client.read_gatt_char(CHR_X)
            line = f"[{ts()}] {CHR_X} READ  {len(initial):2d}B {initial.hex(' ').upper()}"
            print(line)
            log.write(line + "\n")
        except Exception as exc:
            print(f"FFB3 read failed: {exc}")

        await client.start_notify(CHR_DATA, on_data)
        await client.start_notify(CHR_X, on_data)
        print(f">>> subscribed; logging to {log_path}")
        print(">>> STEP ON THE SCALE NOW (barefoot, stand still ~10s after weight locks)")

        now = dt.datetime.now()
        seq = [
            cfg(0xF7, 0, 0, 0),
            cfg(0xFA, 0, 0, 0),
            cfg(0xFB, SEX_MALE, AGE, HEIGHT_CM),
        ]
        yy, mm, dd = now.year - 2000, now.month, now.day
        hh, mi, ss = now.hour, now.minute, now.second
        seq += [cfg(0xFD, yy, mm, dd), cfg(0xFC, hh, mi, ss), cfg(0xFE, 6, UNIT_KG, 0)]

        for i, pkt in enumerate(seq):
            await client.write_gatt_char(CHR_CFG, pkt, response=False)
            line = f"[{ts()}] FFB1 WRITE       8B {pkt.hex(' ').upper()}"
            print(line)
            log.write(line + "\n")
            await asyncio.sleep(0.25)

        print(f">>> init done, capturing for {DURATION:.0f}s ...")
        await asyncio.sleep(DURATION)
        await client.stop_notify(CHR_DATA)
        await client.stop_notify(CHR_X)
        log.close()


if __name__ == "__main__":
    asyncio.run(main())
