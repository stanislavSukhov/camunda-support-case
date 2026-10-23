"""Tiny client for the Camunda 8 Orchestration Cluster REST API (v2).

Standard library only, on purpose: the point of this project is the process model,
not a dependency tree. Covers exactly what the demo needs: deploy, start, message,
job worker loop, user tasks, searches. For anything bigger use the official SDKs
(pyzeebe / @camunda8/sdk / camunda-client-java).

Docs: https://docs.camunda.io/docs/apis-tools/orchestration-cluster-api-rest/
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_BASE = os.environ.get("CAMUNDA_REST", "http://localhost:8080/v2")


class C8Error(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"HTTP {status} on {path}: {body[:500]}")
        self.status, self.body, self.path = status, body, path


class C8:
    def __init__(self, base: str = DEFAULT_BASE, basic_auth: tuple[str, str] | None = None):
        self.base = base.rstrip("/")
        self.auth = basic_auth  # None = unprotected API (the local compose default)

    # -- transport -----------------------------------------------------------
    def _req(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None,
             content_type: str = "application/json", timeout: float = 70) -> Any:
        url = self.base + path
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", content_type)
        if self.auth:
            import base64
            token = base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                if resp.status == 204 or not payload:
                    return None
                return json.loads(payload)
        except urllib.error.HTTPError as e:
            raise C8Error(e.code, e.read().decode(errors="replace"), path) from None

    # -- cluster -------------------------------------------------------------
    def topology(self) -> dict:
        return self._req("GET", "/topology")

    def wait_ready(self, timeout_s: float = 180) -> dict:
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            try:
                t = self.topology()
                if t.get("brokers"):
                    return t
            except (C8Error, urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last = e
            time.sleep(2)
        raise TimeoutError(f"Camunda not ready after {timeout_s}s: {last}")

    # -- deployments ---------------------------------------------------------
    def deploy(self, paths: list[str | Path]) -> dict:
        boundary = "----c8-" + uuid.uuid4().hex
        parts = []
        for p in map(Path, paths):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"resources\"; filename=\"{p.name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n".encode() + p.read_bytes() + b"\r\n"
            )
        raw = b"".join(parts) + f"--{boundary}--\r\n".encode()
        return self._req("POST", "/deployments", raw=raw, content_type=f"multipart/form-data; boundary={boundary}")

    # -- process instances ---------------------------------------------------
    def create_instance(self, process_id: str, variables: dict | None = None) -> dict:
        return self._req("POST", "/process-instances",
                         {"processDefinitionId": process_id, "variables": variables or {}})

    def search_instances(self, flt: dict, limit: int = 50) -> list[dict]:
        r = self._req("POST", "/process-instances/search", {"filter": flt, "page": {"limit": limit}})
        return r.get("items", [])

    def search_incidents(self, flt: dict, limit: int = 50) -> list[dict]:
        r = self._req("POST", "/incidents/search", {"filter": flt, "page": {"limit": limit}})
        return r.get("items", [])

    # -- messages ------------------------------------------------------------
    def publish_message(self, name: str, correlation_key: str, variables: dict | None = None,
                        ttl_ms: int = 60_000) -> dict:
        """Buffered publish: if nobody is waiting yet, the message lives for ttl_ms."""
        return self._req("POST", "/messages/publication", {
            "name": name, "correlationKey": correlation_key,
            "variables": variables or {}, "timeToLive": ttl_ms,
        })

    # -- jobs ----------------------------------------------------------------
    def activate_jobs(self, job_type: str, worker: str, *, max_jobs: int = 8,
                      lock_ms: int = 30_000, long_poll_ms: int = 10_000) -> list[dict]:
        r = self._req("POST", "/jobs/activation", {
            "type": job_type, "worker": worker, "maxJobsToActivate": max_jobs,
            "timeout": lock_ms, "requestTimeout": long_poll_ms,
        }, timeout=long_poll_ms / 1000 + 15)
        return (r or {}).get("jobs", [])

    def complete_job(self, job_key: int | str, variables: dict | None = None) -> None:
        self._req("POST", f"/jobs/{job_key}/completion", {"variables": variables or {}})

    def fail_job(self, job_key: int | str, retries: int, message: str, backoff_ms: int = 5_000) -> None:
        self._req("POST", f"/jobs/{job_key}/failure",
                  {"retries": retries, "errorMessage": message[:1000], "retryBackOff": backoff_ms})

    # -- user tasks ----------------------------------------------------------
    def search_user_tasks(self, flt: dict, limit: int = 50) -> list[dict]:
        r = self._req("POST", "/user-tasks/search", {"filter": flt, "page": {"limit": limit}})
        return r.get("items", [])

    def complete_user_task(self, key: int | str, variables: dict | None = None) -> None:
        self._req("POST", f"/user-tasks/{key}/completion", {"variables": variables or {}})
