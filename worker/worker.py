"""Job worker: one long-polling thread per job type, over the REST API.

    python3 worker/worker.py            # run until Ctrl-C

The same loop is reused by run_demo.py in the background.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from c8 import C8, C8Error  # noqa: E402
from handlers import HANDLERS  # noqa: E402

WORKER_NAME = os.environ.get("WORKER_NAME", "support-case-worker")


def _loop(client: C8, job_type: str, stop: threading.Event) -> None:
    handler = HANDLERS[job_type]
    while not stop.is_set():
        try:
            jobs = client.activate_jobs(job_type, WORKER_NAME, long_poll_ms=5_000)
        except Exception as e:  # cluster restarting, network blip: back off, keep going
            print(f"[worker] activate {job_type} failed: {e}", flush=True)
            time.sleep(3)
            continue
        for job in jobs:
            try:
                out = handler(job)
                client.complete_job(job["jobKey"], out)
            except C8Error as e:
                print(f"[worker] complete {job_type} failed: {e}", flush=True)
            except Exception as e:
                traceback.print_exc()
                client.fail_job(job["jobKey"], max(int(job.get("retries", 1)) - 1, 0),
                                f"{type(e).__name__}: {e}")


def start(client: C8 | None = None, types: list[str] | None = None) -> threading.Event:
    """Start one daemon thread per job type; return the stop event."""
    client = client or C8()
    stop = threading.Event()
    for t in types or HANDLERS:
        threading.Thread(target=_loop, args=(client, t, stop), name=f"worker-{t}", daemon=True).start()
    return stop


if __name__ == "__main__":
    c = C8()
    c.wait_ready()
    print(f"[worker] polling {len(HANDLERS)} job types at {c.base} as {WORKER_NAME}", flush=True)
    stop_event = start(c)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
