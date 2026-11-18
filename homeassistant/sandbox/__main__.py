"""Entry point of a sandbox process: python -m homeassistant.sandbox --fd N.

Core starts this with a clean environment and one end of a socket pair. The
first message on the socket is ``init``. The process confines itself before it
starts the event loop, and only then imports any integration code.
"""

import argparse
import asyncio
import logging
import socket
import sys

from . import confine
from .protocol import encode, read_frame_blocking


def main() -> int:
    """Run the sandbox."""
    parser = argparse.ArgumentParser(prog="python -m homeassistant.sandbox")
    parser.add_argument("--fd", type=int, required=True)
    args = parser.parse_args()

    sock = socket.socket(fileno=args.fd)
    init = read_frame_blocking(sock)
    if init.get("method") != "init" or not isinstance(init.get("id"), int):
        return 2
    params = init["params"]

    logging.basicConfig(
        level=params.get("log_level", logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        confinement = confine.apply(
            confine.ConfineRequest(
                config_dir=params["config_dir"],
                data_dir=params["data_dir"],
                integration_dirs=params["integration_dirs"],
                uid=params["uid"],
                gid=params["gid"],
                file_isolation=params["file_isolation"],
            )
        )
    except Exception as err:  # noqa: BLE001
        sock.sendall(
            encode(
                {
                    "id": init["id"],
                    "error": {"type": "confine_failed", "message": str(err)},
                }
            )
        )
        return 1

    # Imported only now: this pulls in Home Assistant itself, still no
    # integration code.
    from .runtime import async_run  # noqa: PLC0415

    sock.setblocking(False)
    asyncio.run(async_run(sock, init["id"], params, confinement))
    return 0


if __name__ == "__main__":
    sys.exit(main())
