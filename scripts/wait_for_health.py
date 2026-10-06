"""Poll the API health endpoint until it reports healthy, or time out."""

import os
import sys
import time
import urllib.request

base_url = os.getenv("API_BASE_URL", "http://api:8000")
health_path = os.getenv("HEALTH_PATH", "/health")
url = base_url.rstrip("/") + health_path
deadline = time.monotonic() + float(os.getenv("HEALTH_TIMEOUT", "60"))

while True:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status == 200:
                print(f"health check passed: {url}")
                sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready yet"
        last = exc
    if time.monotonic() > deadline:
        print(f"health check failed after timeout: {url} ({last})", file=sys.stderr)
        sys.exit(1)
    time.sleep(1)
