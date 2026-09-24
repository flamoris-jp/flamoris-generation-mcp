"""Probe the HTTP process locally without contacting any generation provider."""

import os
import sys
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def main() -> None:
    port = os.environ.get("FLAMORIS_HTTP_PORT", "8765")
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{int(port)}/healthz", timeout=2) as response:
            if response.status == 200:
                return
    except (OSError, URLError, ValueError):
        pass
    sys.exit(1)


if __name__ == "__main__":
    main()
