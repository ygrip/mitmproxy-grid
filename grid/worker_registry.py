from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable


class RemoteGridError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Remote grid returned HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body

    @property
    def detail(self):
        try:
            payload = json.loads(self.body)
            return payload.get("detail", self.body)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return self.body


class RemoteGridClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def request_json(self, base_url: str, method: str, path: str, body=None):
        text = self.request_text(base_url, method, path, body)
        if not text:
            return None
        return json.loads(text)

    def request_text(self, base_url: str, method: str, path: str, body=None) -> str:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RemoteGridError(exc.code, body_text) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unable to reach remote grid {url}: {exc.reason}") from exc


@dataclass
class WorkerState:
    worker_id: str
    api_url: str
    proxy_host: str
    available_slots: int
    instances: int
    grid_version: str
    api_version: str
    last_seen: float

    def to_dict(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        return {
            "workerId": self.worker_id,
            "apiUrl": self.api_url,
            "proxyHost": self.proxy_host,
            "availableSlots": self.available_slots,
            "instances": self.instances,
            "gridVersion": self.grid_version,
            "apiVersion": self.api_version,
            "lastSeenSeconds": round(max(0.0, now - self.last_seen), 3),
        }


class WorkerRegistry:
    def __init__(self, stale_after: float = 20.0, clock: Callable[[], float] = time.monotonic):
        self.stale_after = stale_after
        self.clock = clock
        self._workers: dict[str, WorkerState] = {}
        self._instance_owners: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, payload: dict) -> WorkerState:
        now = self.clock()
        state = WorkerState(
            worker_id=payload["workerId"],
            api_url=payload["apiUrl"].rstrip("/"),
            proxy_host=payload["proxyHost"],
            available_slots=int(payload["availableSlots"]),
            instances=int(payload["instances"]),
            grid_version=payload["gridVersion"],
            api_version=payload["apiVersion"],
            last_seen=now,
        )
        with self._lock:
            self._workers[state.worker_id] = state
        return state

    def prune(self) -> list[str]:
        now = self.clock()
        with self._lock:
            stale = [
                worker_id
                for worker_id, worker in self._workers.items()
                if now - worker.last_seen > self.stale_after
            ]
            for worker_id in stale:
                self._workers.pop(worker_id, None)
                for instance_id, owner_id in list(self._instance_owners.items()):
                    if owner_id == worker_id:
                        self._instance_owners.pop(instance_id, None)
            return stale

    def workers(self) -> list[WorkerState]:
        self.prune()
        with self._lock:
            return list(self._workers.values())

    def worker(self, worker_id: str) -> WorkerState | None:
        self.prune()
        with self._lock:
            return self._workers.get(worker_id)

    def select_worker(self) -> WorkerState | None:
        workers = [worker for worker in self.workers() if worker.available_slots > 0]
        if not workers:
            return None
        return max(workers, key=lambda worker: (worker.available_slots, -worker.instances, worker.worker_id))

    def remember_instance(self, instance_id: str, worker_id: str, adjust_capacity: bool = True) -> None:
        with self._lock:
            self._instance_owners[instance_id] = worker_id
            worker = self._workers.get(worker_id)
            if adjust_capacity and worker is not None:
                worker.available_slots = max(0, worker.available_slots - 1)
                worker.instances += 1

    def forget_instance(self, instance_id: str) -> None:
        with self._lock:
            worker_id = self._instance_owners.pop(instance_id, None)
            worker = self._workers.get(worker_id) if worker_id else None
            if worker is not None:
                worker.available_slots += 1
                worker.instances = max(0, worker.instances - 1)

    def owner(self, instance_id: str) -> WorkerState | None:
        with self._lock:
            worker_id = self._instance_owners.get(instance_id)
        return self.worker(worker_id) if worker_id else None

    def discover_owner(
        self,
        instance_id: str,
        fetch_instances: Callable[[WorkerState], Iterable[dict]],
    ) -> WorkerState | None:
        owner = self.owner(instance_id)
        if owner is not None:
            return owner
        for worker in self.workers():
            try:
                instances = fetch_instances(worker)
            except Exception:
                continue
            if any(instance.get("instanceId") == instance_id for instance in instances):
                with self._lock:
                    self._instance_owners[instance_id] = worker.worker_id
                return worker
        return None
