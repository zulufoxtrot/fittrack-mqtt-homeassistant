"""Entry point: python -m fittrack"""

import asyncio
import logging

from .config import Config
from .driver import Driver


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg = Config.from_env()
    asyncio.run(Driver(cfg).run())


if __name__ == "__main__":
    main()
