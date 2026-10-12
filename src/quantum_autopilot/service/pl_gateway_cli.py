"""Linux accelerator broker, independent of desktop login and the core API.

The available CLI backends execute on a CPU. Physical driver composition is a
trusted Python integration point, never a module name received from JSON/API.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import stat
from threading import Event

from quantum_autopilot.lab import pl_accelerator, pl_transport, processing_node, pspl
from quantum_autopilot.lab.pl_transport import BackendIdentity, PLBackendServer, UnixPLCorrelationWorker
from quantum_autopilot.security.http_boundary import strict_object


def software_image_digest() -> dict:
    components = {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                  for module in (pl_accelerator, pl_transport, processing_node, pspl)}
    digest = hashlib.sha256(json.dumps(components, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"image_sha256": digest, "components": components}


@dataclass(frozen=True)
class PLGatewayConfig:
    socket_path: Path
    state_directory: Path
    backend: BackendIdentity
    allowed_uid: int
    request_timeout_s: float

    @classmethod
    def parse(cls, value):
        fields = {"schema", "socket_path", "state_directory", "backend", "allowed_uid", "request_timeout_s"}
        if not isinstance(value, dict) or set(value) != fields or value["schema"] != "orion.pl-gateway.v1":
            raise ValueError("invalid PL gateway schema or fields")
        paths = []
        for name in ("socket_path", "state_directory"):
            raw_path = value[name]
            if not isinstance(raw_path, str) or not raw_path or any(c in raw_path for c in "\x00\r\n"):
                raise ValueError("invalid gateway path")
            path = Path(raw_path)
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("gateway paths must be absolute without traversal")
            paths.append(path)
        backend = BackendIdentity.from_dict(value["backend"])
        uid, timeout = value["allowed_uid"], value["request_timeout_s"]
        if type(uid) is not int or not 0 <= uid < 2**32:
            raise ValueError("invalid gateway peer UID")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.01 <= timeout <= 60:
            raise ValueError("invalid gateway request deadline")
        return cls(*paths, backend, uid, float(timeout))

    def check(self):
        if platform.system() != "Linux":
            raise ValueError("gateway execution requires Linux with SO_PEERCRED")
        if self.allowed_uid != os.geteuid() or self.allowed_uid == 0:
            raise ValueError("initial gateway profile requires the same non-root service UID")
        if (self.state_directory == self.socket_path.parent
                or Path("/run") in self.state_directory.parents or self.state_directory == Path("/run")
                or Path("/dev/shm") in self.state_directory.parents):
            raise ValueError("gateway replay state requires a separate persistent directory")
        if self.backend.mode == "linux-pl-driver":
            raise ValueError("physical mode requires a qualified board-specific driver composition")
        if self.backend.image_sha256 != software_image_digest()["image_sha256"]:
            raise ValueError("configured software image does not match the installed worker")
        if len(os.fsencode(self.socket_path)) >= 108:
            raise ValueError("gateway socket exceeds the Linux address limit")
        # Read-only preflight, identical to the broker's private-path policy.
        # Provisioning belongs to systemd/administrator, never an input event.
        pl_transport._private_directory(self.socket_path.parent, self.allowed_uid)
        pl_transport._private_directory(self.state_directory, self.allowed_uid)
        return {"schema": "orion.pl-gateway-check.v1", "status": "PASS", "backend": asdict(self.backend),
                "physical_driver_available": False, "hardware_validated": False,
                "physical_actuation_locked": True, "hard_realtime_qualified": False}


def load_config(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("gateway config path must be absolute")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 16_384:
        raise ValueError("invalid gateway configuration file")
    if os.name == "posix" and (info.st_mode & 0o022 or info.st_uid not in {0, os.geteuid()}):
        raise ValueError("gateway config must be owned by root/service and protected from modification")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode, opened.st_uid) != (
                info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid):
            raise ValueError("gateway config changed during opening")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = strict_object(stream.read(16_385))
        return PLGatewayConfig.parse(value)
    finally:
        os.close(fd)


def registry_for_gateway(config: PLGatewayConfig, *, worker_id="external-pl"):
    """Trusted service composition; detector origin never selects a worker."""
    config.check()
    registry = processing_node.ProcessingWorkerRegistry()
    def factory():
        worker = UnixPLCorrelationWorker(config.socket_path, config.backend,
                                        peer_uid=config.allowed_uid, timeout_s=config.request_timeout_s)
        worker.probe()
        return worker
    registry.register(worker_id, factory,
        processing_node.ProcessingWorkerCapabilities(config.backend.mode, max_events_per_block=config.backend.max_events))
    return registry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("command", choices=("describe", "check", "probe", "serve"))
    args = parser.parse_args(argv)
    stop = Event()
    previous = {}
    try:
        if args.command == "describe":
            result = {"schema": "orion.pl-gateway-software.v1", **software_image_digest(),
                      "platform": platform.system(), "architecture": platform.machine(),
                      "uid": os.geteuid() if hasattr(os, "geteuid") else None,
                      "available_modes": ["cpu-external", "emulated-pl-external"],
                      "hardware_validated": False, "physical_actuation_locked": True}
        else:
            if args.config is None:
                parser.error("--config is required for this command")
            config = load_config(args.config)
            result = config.check()
            if args.command == "probe":
                worker = UnixPLCorrelationWorker(config.socket_path, config.backend,
                    peer_uid=config.allowed_uid, timeout_s=config.request_timeout_s)
                try:
                    result = worker.probe()
                finally:
                    worker.close()
            elif args.command == "serve":
                worker = (processing_node.CPUReferenceWorker() if config.backend.mode == "cpu-external"
                          else processing_node.EmulatedPLCorrelationWorker())
                server = PLBackendServer(config.socket_path, config.backend, worker,
                    allowed_uid=config.allowed_uid, timeout_s=config.request_timeout_s,
                    state_directory=config.state_directory)
                for number in (signal.SIGINT, signal.SIGTERM):
                    previous[number] = signal.signal(number, lambda *_: stop.set())
                print(json.dumps({"status": "starting", "backend": asdict(config.backend),
                                  "physical_actuation_locked": True}), flush=True)
                server.serve(stop)
                result = {"status": "stopped", "physical_action_performed": False}
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        # Neither request bodies nor arbitrary driver exception text enter logs.
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "physical_actuation_locked": True}), flush=True)
        return 2
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
