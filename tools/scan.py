"""Scan for BLE devices, highlighting anything that looks like the FitTrack scale."""

import asyncio
import sys
from bleak import BleakScanner


async def main(timeout: float) -> None:
    print(f"Scanning for {timeout:.0f}s ...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    hits, others = [], []
    for addr, (dev, adv) in devices.items():
        name = dev.name or adv.local_name or ""
        entry = (addr, name, adv.rssi, adv)
        if "fit" in name.lower() or any("ffb0" in str(u).lower() for u in adv.service_uuids):
            hits.append(entry)
        else:
            others.append(entry)

    print(f"\n=== {len(devices)} devices seen ===\n")
    if hits:
        print(">>> FITTRACK CANDIDATES:")
        for addr, name, rssi, adv in sorted(hits, key=lambda e: -e[2]):
            print(f"  {addr}  {name!r}  RSSI {rssi}")
            print(f"    service_uuids: {adv.service_uuids}")
            print(f"    manufacturer_data: {dict(adv.manufacturer_data)}")
            print(f"    service_data: {dict(adv.service_data)}")
            print(f"    tx_power: {adv.tx_power}  platform_data: {adv.platform_data}")
    else:
        print("No FitTrack-looking device found. Wake it up (step on briefly) and retry.")

    print("\n--- all devices ---")
    for addr, name, rssi, _ in sorted(others, key=lambda e: -e[2]):
        print(f"  {addr}  {rssi:4d}  {name!r}")


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 15.0))
