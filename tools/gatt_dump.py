"""Connect to the scale and dump the full GATT hierarchy (descriptors listed, not read)."""

import asyncio
import sys
from bleak import BleakClient


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected: {client.is_connected}, mtu={client.mtu_size}")
        for service in client.services:
            print(f"\nSERVICE {service.uuid}  ({service.description or '?'})")
            for char in service.characteristics:
                props = ",".join(sorted(char.properties))
                print(f"  CHAR {char.uuid}  [{props}]  handle={char.handle}")
                for desc in char.descriptors:
                    print(f"    DESC {desc.uuid}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: gatt_dump.py <BLE-address>")
    addr = sys.argv[1]
    asyncio.run(main(addr))
