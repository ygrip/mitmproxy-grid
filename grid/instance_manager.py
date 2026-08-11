import subprocess
import socket
import time
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from config import INSTANCE_TTL, PORT_END, PORT_START

log = logging.getLogger("grid.instance_manager")

BASE_DATA = Path("/data")
BASE_CA = Path("/ca")
BASE_LOG = Path("/data/logs")
STARTUP_TIMEOUT = 30


def _wait_until_listening(port: int, timeout: int = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


class Instance:
    def __init__(self, instance_id: str, port: int, process: subprocess.Popen, log_path: Path, ttl: int):
        self.instance_id = instance_id
        self.port = port
        self.process = process
        self.log_path = log_path
        self.ttl = ttl
        self.created_at = datetime.now(timezone.utc)
        self.expires_at = self.created_at + timedelta(seconds=ttl)

    @property
    def status(self) -> str:
        if self.process.poll() is None:
            return "running"
        if self.process.returncode == 0:
            return "stopped"
        return "error"

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, (self.expires_at - datetime.now(timezone.utc)).total_seconds())

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def renew(self, ttl: int | None = None):
        self.ttl = ttl if ttl is not None else self.ttl
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl)

    def to_dict(self) -> dict:
        return {
            "instanceId": self.instance_id,
            "port": self.port,
            "status": self.status,
            "createdAt": self.created_at.isoformat(),
            "uptimeSeconds": round(self.uptime_seconds, 1),
            "ttl": self.ttl,
            "remainingSeconds": round(self.remaining_seconds, 1),
        }


class InstanceManager:
    def __init__(self):
        self.instances: dict[str, Instance] = {}
        self.used_ports: set[int] = set()
        BASE_LOG.mkdir(parents=True, exist_ok=True)

    def _allocate_port(self) -> int:
        for port in range(PORT_START, PORT_END + 1):
            if port not in self.used_ports:
                self.used_ports.add(port)
                return port
        raise RuntimeError("No free ports available")

    def create(self, ttl: int | None = None) -> tuple[str, int, int, str]:
        effective_ttl = ttl if ttl is not None else INSTANCE_TTL
        instance_id = str(uuid.uuid4())
        port = self._allocate_port()

        rule_file = BASE_DATA / f"{instance_id}.json"
        ca_dir = BASE_CA / instance_id
        log_file = BASE_LOG / f"{instance_id}.log"

        rule_file.parent.mkdir(parents=True, exist_ok=True)
        ca_dir.mkdir(parents=True, exist_ok=True)
        rule_file.write_text("[]")

        cmd = [
            "mitmdump",
            "--listen-host", "0.0.0.0",
            "--listen-port", str(port),
            "--set", f"confdir={ca_dir}",
            "-s", "/app/interceptor_template.py",
            "--set", f"rule_file={rule_file}",
        ]

        try:
            stderr_fh = open(log_file, "w")
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_fh)
        except Exception as exc:
            self.used_ports.discard(port)
            raise RuntimeError(f"Failed to start mitmdump: {exc}") from exc

        log.info("Starting mitmdump on port %d (pid %d) …", port, process.pid)

        if not _wait_until_listening(port):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            self.used_ports.discard(port)

            stderr_output = ""
            try:
                stderr_fh.close()
                stderr_output = log_file.read_text()[-500:]
            except Exception:
                pass
            detail = f"Instance failed to start on port {port}"
            if stderr_output:
                detail += f": {stderr_output}"
            raise RuntimeError(detail)

        log.info("Instance %s listening on port %d", instance_id[:8], port)
        instance = Instance(instance_id, port, process, log_file, effective_ttl)
        self.instances[instance_id] = instance
        return instance_id, port, effective_ttl, instance.expires_at.isoformat()

    def destroy(self, instance_id: str, cleanup: bool = False) -> bool:
        instance = self.instances.pop(instance_id, None)
        if not instance:
            return False

        instance.process.terminate()
        try:
            instance.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            instance.process.kill()
            try:
                instance.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

        self.used_ports.discard(instance.port)

        if cleanup:
            import shutil

            rule_file = BASE_DATA / f"{instance_id}.json"
            if rule_file.exists():
                rule_file.unlink()
            ca_dir = BASE_CA / instance_id
            if ca_dir.exists():
                shutil.rmtree(ca_dir, ignore_errors=True)
            if instance.log_path.exists():
                instance.log_path.unlink(missing_ok=True)

        return True

    def renew(self, instance_id: str, ttl: int | None = None) -> Instance | None:
        instance = self.instances.get(instance_id)
        if not instance:
            return None
        instance.renew(ttl)
        return instance

    def get(self, instance_id: str):
        return self.instances.get(instance_id)

    def list_instances(self) -> list[dict]:
        return [inst.to_dict() for inst in self.instances.values()]

    def reap_expired(self) -> list[str]:
        expired = [iid for iid, inst in self.instances.items() if inst.is_expired]
        for iid in expired:
            log.info("Reaping expired instance %s", iid[:8])
            self.destroy(iid, cleanup=True)
        return expired

    @property
    def available_slots(self) -> int:
        return (PORT_END - PORT_START + 1) - len(self.used_ports)


manager = InstanceManager()
