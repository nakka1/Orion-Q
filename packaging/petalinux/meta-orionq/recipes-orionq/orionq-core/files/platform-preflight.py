"""Read-only embedded deployment checks; never programs or opens a PL device.

A manifest and hashes identify prerequisites, not hardware qualification. The
Linux PL driver profile stays blocked pending trusted driver composition and
unit-level qualification; software workers may run with physical actuation locked.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys


MAX_RING_BYTES = 64 * 1024 * 1024
MODES = {"cpu-external", "emulated-pl-external", "linux-pl-driver"}


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError("invalid embedded deployment fields")
    return value


def _regular_stream(file, maximum):
    info = file.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
        raise ValueError("deployment file must be a bounded regular file")
    # A FIFO substituted between lstat and open must not block service startup.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(file, flags)
    try:
        observed = os.fstat(descriptor)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_size > maximum
                or (info.st_dev, info.st_ino) != (observed.st_dev, observed.st_ino)):
            raise ValueError("deployment file changed before reading")
        return os.fdopen(descriptor, "rb"), info
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular(path, maximum):
    stream, info = _regular_stream(Path(path), maximum)
    with stream:
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if len(data) > maximum or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("deployment file changed while reading")
    return data


def _json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate deployment field")
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError("non-finite deployment number")
    return json.loads(_read_regular(path, 65536), object_pairs_hook=pairs, parse_constant=reject_constant)


def _version(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value) is None:
        raise ValueError("a release version is required for qualification")
    return tuple(int(item) for item in value.split("."))


def _satisfies(actual, operator, expected):
    a, b = _version(actual), _version(expected)
    size = max(len(a), len(b))
    a, b = a + (0,) * (size - len(a)), b + (0,) * (size - len(b))
    return {">=": a >= b, "<=": a <= b, "==": a == b, ">": a > b, "<": a < b}[operator]


def observe_runtime(requirements):
    """Observe only local version metadata; no module imports, network or secrets."""
    distributions = {}
    for requirement in ["quantum-twin"] + requirements:
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.-]*", requirement)
        if match is None:
            raise ValueError("invalid runtime requirement")
        name = match.group(0)
        try:
            distributions[name.lower()] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name.lower()] = None
    systemd_version = None
    try:
        result = subprocess.run(["systemd", "--version"], capture_output=True, text=True, timeout=2,
                                check=False)
        found = re.match(r"systemd ([0-9]+)", result.stdout)
        if result.returncode == 0 and found:
            systemd_version = int(found.group(1))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"system": platform.system(), "architecture": platform.machine(),
            "kernel_release": platform.release(), "python": platform.python_version(),
            "systemd": systemd_version, "distributions": distributions}


def _artifact(reference, label):
    _object(reference, {"path", "sha256"})
    if not isinstance(reference["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None:
        raise ValueError("artifact requires a SHA256 reference")
    file = Path(reference["path"])
    if not file.is_absolute():
        raise ValueError("artifact paths must be absolute")
    stream, info = _regular_stream(file, 16 * 1024**3)
    digest = hashlib.sha256()
    with stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("artifact changed while checking")
    if digest.hexdigest() != reference["sha256"]:
        raise ValueError("artifact fingerprint mismatch")
    return {"component": label, "sha256": digest.hexdigest(), "bytes": info.st_size}


def check(configuration, artifact, *, observed=None):
    _object(configuration, {"schema", "backend_mode", "architecture", "limits"}, {"hardware"})
    if configuration["schema"] != "orion.petalinux.platform.v1" or configuration["backend_mode"] not in MODES:
        raise ValueError("unsupported embedded profile")
    if configuration["architecture"] not in {"aarch64", "armv7l", "x86_64"}:
        raise ValueError("unsupported target architecture")
    limits = _object(configuration["limits"], {"slots", "ring_budget_bytes", "max_events", "abi_major", "abi_minor"})
    bounds = {"slots": (2, 1024), "ring_budget_bytes": (1, MAX_RING_BYTES), "max_events": (1, 65536)}
    for key, (minimum, maximum) in bounds.items():
        if type(limits[key]) is not int or not minimum <= limits[key] <= maximum:
            raise ValueError("embedded resource limit out of range")
    if type(limits["abi_major"]) is not int or type(limits["abi_minor"]) is not int or (
            limits["abi_major"], limits["abi_minor"]) != (1, 0):
        raise ValueError("unsupported PS/PL transport ABI")
    # Existing TimestampBlock wire envelope; native driver's buffers require a
    # separate negotiated capacity and must not treat this as an AMD descriptor.
    if limits["slots"] * (64 + 4096 + 16 * limits["max_events"] + 32) > limits["ring_budget_bytes"]:
        raise ValueError("declared ring cannot hold its configured wire slots")
    if (not isinstance(artifact, dict) or artifact.get("schema") != "orion.core.artifact.v1"
            or artifact.get("profile") != "headless-service" or artifact.get("distribution") != "quantum-twin"
            or not isinstance(artifact.get("dependencies"), list)):
        raise ValueError("reviewed headless artifact required")
    actual = observed if observed is not None else observe_runtime(artifact["dependencies"])
    errors, checks = [], []
    def require(name, condition):
        checks.append({"component": name, "passed": bool(condition)})
        if not condition:
            errors.append(name)
    require("linux-operating-system", actual.get("system") == "Linux")
    require("target-architecture", actual.get("architecture") == configuration["architecture"])
    require("python-minimum", _satisfies(actual.get("python", "0"), ">=", "3.10"))
    require("systemd-credential-support", type(actual.get("systemd")) is int and actual["systemd"] >= 255)
    versions = actual.get("distributions", {})
    require("installed-core-version", versions.get("quantum-twin") == artifact.get("version"))
    for requirement in artifact["dependencies"]:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)([<>=0-9.,]+)", requirement)
        if match is None:
            raise ValueError("unsupported runtime requirement")
        name, constraint = match.groups()
        bounds = re.findall(r"(>=|<=|==|>|<)([0-9]+(?:\.[0-9]+)*)", constraint)
        if not bounds or ",".join(op + ver for op, ver in bounds) != constraint:
            raise ValueError("unsupported runtime version bounds")
        version = versions.get(name.lower())
        try:
            compatible = version is not None and all(_satisfies(version, op, expected) for op, expected in bounds)
        except ValueError:
            compatible = False
        require("dependency:" + name.lower(), compatible)
    fingerprints = []
    if configuration["backend_mode"] == "linux-pl-driver":
        hardware = _object(configuration.get("hardware"), {"bsp", "hardware_export", "bitstream", "kernel", "driver"})
        for name in ("bsp", "hardware_export", "bitstream"):
            fingerprints.append(_artifact(hardware[name], name))
        kernel = _object(hardware["kernel"], {"release", "configuration", "required_options"})
        fingerprints.append(_artifact(kernel["configuration"], "kernel-configuration"))
        require("kernel-release", actual.get("kernel_release") == kernel["release"])
        if not isinstance(kernel["required_options"], list) or not kernel["required_options"]:
            raise ValueError("kernel dependencies must be explicit")
        data = _read_regular(kernel["configuration"]["path"], 2 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != kernel["configuration"]["sha256"]:
            raise ValueError("kernel configuration changed after verification")
        text = data.decode("utf-8")
        for option in kernel["required_options"]:
            if not isinstance(option, str) or re.fullmatch(r"CONFIG_[A-Z0-9_]+=[ym]", option) is None:
                raise ValueError("invalid kernel dependency")
            require("kernel-option:" + option.split("=")[0], option in text.splitlines())
        driver = _object(hardware["driver"], {"device", "module", "abi_major", "abi_minor", "design_id"})
        if not isinstance(driver["module"], str) or re.fullmatch(r"[A-Za-z0-9_]{1,64}", driver["module"]) is None:
            raise ValueError("invalid driver module")
        if not isinstance(driver["design_id"], str) or not 1 <= len(driver["design_id"]) <= 128:
            raise ValueError("driver design identity required")
        device = Path(driver["device"])
        if not device.is_absolute() or str(device) == "/dev/mem":
            raise ValueError("a dedicated driver device is required")
        require("driver-abi", type(driver["abi_major"]) is int and type(driver["abi_minor"]) is int and (
                driver["abi_major"], driver["abi_minor"]) == (1, 0))
        require("driver-module-present", (Path("/sys/module") / driver["module"]).is_dir())
        require("driver-character-device", device.exists() and stat.S_ISCHR(device.lstat().st_mode))
        # No register write, bitstream programming, driver ioctl or capture is
        # performed. Metadata cannot substitute actual driver/device readback.
        require("trusted-physical-driver-composition-and-qualification", False)
    elif "hardware" in configuration:
        raise ValueError("software profile must not pretend to activate hardware")
    return {"schema": "orion.petalinux.preflight.v1",
            "status": "blocked" if errors else "ready-for-software",
            "backend_mode": configuration["backend_mode"], "checks": checks, "errors": errors,
            "artifact_fingerprints": fingerprints,
            "runtime_observation_source": "injected-test-fixture" if observed is not None else "local-host-metadata",
            "physical_actuation_locked": True, "hardware_validated": False, "arm_image_built": False,
            "driver_device_opened": False, "bitstream_programmed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = check(_json(args.config), _json(args.artifact))
    except Exception:
        # Configuration/SDK paths and arbitrary exception text are not logs.
        print(json.dumps({"schema": "orion.petalinux.preflight.v1", "status": "blocked",
                          "reason": "invalid_or_unavailable_deployment_prerequisites", "hardware_validated": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "ready-for-software" else 2


if __name__ == "__main__":
    sys.exit(main())
