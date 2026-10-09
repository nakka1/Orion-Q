"""Process-exclusive acquisition authority; not a certified physical interlock.

An OS-owned lock remains held for the connected session. SQLite provides a
durable fencing generation, identity binding and conservative prerequisites.
Expiry blocks the holder but never lets another live process steal its device.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
import uuid


class EquipmentDenied(RuntimeError):
    """A device permit is missing, expired, uncertain or owned elsewhere."""


@dataclass(frozen=True)
class PhysicalIdentity:
    serial: str
    endpoint: str
    namespace: str = "swabian"

    def validate(self):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 512
               or any(ord(c) < 32 for c in v) for v in (self.serial, self.endpoint, self.namespace)):
            raise EquipmentDenied("physical serial and endpoint are required")
        parsed = urlsplit(self.endpoint)
        if parsed.username is not None or parsed.password is not None:
            raise EquipmentDenied("device endpoints cannot contain credentials")

    @property
    def key(self):
        self.validate()
        # The serial locks all endpoints of the SAME instrument. Endpoint is
        # separately bound to the lease: USB and TCP cannot control it twice.
        return hashlib.sha256((self.namespace + "\0" + self.serial).encode()).hexdigest()


class EquipmentAuthority:
    def __init__(self, root: Path | str, *, now=time.time):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "equipment.sqlite"
        self.now = now
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS permits (
                device_key TEXT PRIMARY KEY, serial TEXT NOT NULL, endpoint TEXT NOT NULL,
                owner TEXT NOT NULL, session TEXT NOT NULL, token TEXT NOT NULL,
                fence INTEGER NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
                configuration TEXT, calibrated_until REAL, synchronized INTEGER NOT NULL DEFAULT 0)""")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def acquire(self, identity: PhysicalIdentity, *, owner: str, session_id: str, ttl_s=30.):
        identity.validate()
        if not isinstance(owner, str) or not owner or not isinstance(session_id, str) or not session_id:
            raise EquipmentDenied("owner identity and authenticated session required")
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, (float, int)) or not math.isfinite(ttl_s) or not 0 < ttl_s <= 3600:
            raise EquipmentDenied("lease TTL must be in (0,3600] seconds")
        handle = (self.root / (identity.key + ".lock")).open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise EquipmentDenied("physical instrument already controlled by another session") from error
        try:
            token = uuid.uuid4().hex
            with self._db() as db:
                old = db.execute("SELECT fence FROM permits WHERE device_key=?", (identity.key,)).fetchone()
                fence = old[0] + 1 if old else 1
                db.execute("INSERT OR REPLACE INTO permits VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (identity.key, identity.serial, identity.endpoint, owner, session_id, token,
                            fence, self.now() + ttl_s, "connected", None, None, 0))
            return EquipmentLease(self, identity, owner, session_id, token, fence, ttl_s, handle)
        except BaseException:
            handle.close()
            raise


class EquipmentLease:
    def __init__(self, authority, identity, owner, session_id, token, fence, ttl_s, handle):
        self.authority, self.identity, self.owner, self.session_id = authority, identity, owner, session_id
        self.token, self.fence, self.ttl_s, self._handle = token, fence, ttl_s, handle

    def _row(self, db):
        if self._handle is None:
            raise EquipmentDenied("device session revoked")
        row = db.execute("SELECT owner,session,token,fence,expires,state,configuration,calibrated_until,synchronized,endpoint FROM permits WHERE device_key=?",
                         (self.identity.key,)).fetchone()
        if row is None or row[:4] != (self.owner, self.session_id, self.token, self.fence) or row[9] != self.identity.endpoint:
            raise EquipmentDenied("stale device authority fencing generation")
        if row[4] <= self.authority.now() or row[5] == "revoked":
            raise EquipmentDenied("device authority expired or revoked")
        return row

    def renew(self):
        with self.authority._db() as db:
            self._row(db)
            db.execute("UPDATE permits SET expires=? WHERE device_key=?", (self.authority.now() + self.ttl_s, self.identity.key))

    def configuration_verified(self, configuration_id: str):
        if not isinstance(configuration_id, str) or len(configuration_id) != 64:
            raise EquipmentDenied("verified configuration hash required")
        with self.authority._db() as db:
            self._row(db)
            db.execute("UPDATE permits SET configuration=?,state='configured',calibrated_until=NULL,synchronized=0 WHERE device_key=?",
                       (configuration_id, self.identity.key))

    def uncertain(self):
        with self.authority._db() as db:
            self._row(db)
            db.execute("UPDATE permits SET state='uncertain',configuration=NULL,calibrated_until=NULL,synchronized=0 WHERE device_key=?", (self.identity.key,))

    def calibrated(self, *, valid_for_s: float, synchronized: bool):
        if isinstance(valid_for_s, bool) or not isinstance(valid_for_s, (int, float)) or not math.isfinite(valid_for_s) or not 0 < valid_for_s <= 86400 or type(synchronized) is not bool:
            raise EquipmentDenied("calibration validity and explicit clock evidence required")
        with self.authority._db() as db:
            row = self._row(db)
            if row[5] != "configured" or not row[6]:
                raise EquipmentDenied("configuration readback required before calibration")
            db.execute("UPDATE permits SET state='ready',calibrated_until=?,synchronized=? WHERE device_key=?",
                       (self.authority.now() + valid_for_s, int(synchronized), self.identity.key))

    def require(self, *, owner: str, session_id: str, configuration_id: str | None = None, acquisition=False):
        with self.authority._db() as db:
            row = self._row(db)
            if owner != self.owner or session_id != self.session_id:
                raise EquipmentDenied("identity/session changed; reconnect required")
            if acquisition and (row[5] != "ready" or row[6] != configuration_id
                                or row[7] is None or row[7] <= self.authority.now() or not row[8]):
                raise EquipmentDenied("acquisition requires verified configuration, valid calibration and synchronization")
        return {"owner": self.owner, "session_id": self.session_id, "fence": self.fence,
                "physical_serial": self.identity.serial, "endpoint": self.identity.endpoint}

    def revoke(self):
        if self._handle is None:
            return
        try:
            with self.authority._db() as db:
                db.execute("UPDATE permits SET state='revoked',configuration=NULL,calibrated_until=NULL,synchronized=0 WHERE device_key=? AND token=? AND fence=?",
                           (self.identity.key, self.token, self.fence))
        finally:
            # Closing the OS-owned handle releases authority on all platforms.
            self._handle.close()
            self._handle = None

    def invalidate(self):
        """Revoke permission while retaining the lock until native stop finishes."""
        if self._handle is not None:
            with self.authority._db() as db:
                db.execute("UPDATE permits SET state='revoked',configuration=NULL,calibrated_until=NULL,synchronized=0 WHERE device_key=? AND token=? AND fence=?",
                           (self.identity.key, self.token, self.fence))


class AuthorizedPhotonAdapter:
    """Common policy wrapper; underlying SDK contracts remain unchanged."""
    def __init__(self, adapter, runtime, principal=None, *, authority=None, calibration_validity_s=300.):
        self._adapter, self.runtime, self.principal = adapter, runtime, principal
        self.authority, self.calibration_validity_s, self.lease = authority, calibration_validity_s, None
        self._authority_revoked = False
        identity = adapter.identify()
        self.physical = identity.get("mode") == "hardware"
        self.resource = "device:" + str(getattr(adapter, "serial", None) or identity.get("device_id") or adapter.adapter_id)
        if hasattr(adapter, "_security_guard"):
            adapter._security_guard = self._native_guard

    def __getattr__(self, name):
        # Deliberately do not expose raw SDK methods or native tagger handles.
        if name in {"adapter_id", "state", "configuration", "record"}:
            self._authorize("adapters.read")
            return getattr(self._adapter, name)
        raise AttributeError(name)

    def _authorize(self, action):
        try:
            identity = self.runtime.authorize(action, self.resource, self.principal)
            if identity.session_id is None and identity.source == "os":
                identity = self.runtime.authorize(action, self.resource, self.runtime.device_session_identity())
            if self.lease is not None:
                self.lease.require(owner=identity.subject, session_id=identity.session_id)
                self.lease.renew()
        except BaseException:
            if self.lease is not None:
                # Remove permission immediately; keep exclusivity until the
                # native acquisition/setting call has actually stopped.
                self.lease.invalidate()
                self._authority_revoked = True
                native_lock = getattr(self._adapter, "_operation_lock", None)
                busy = self._adapter.state == "acquiring" or (native_lock is not None and native_lock.locked())
                if not busy:
                    self._release_revoked_authority()
            raise
        return identity

    def _release_revoked_authority(self):
        # If native teardown fails, retain the lock rather than handing an
        # instrument with an uncertain active transport to another process.
        self._adapter.disconnect()
        if self.lease:
            self.lease.revoke()
            self.lease = None

    def _native_guard(self, *, acquisition=False):
        principal = self._authorize("adapters.acquire" if acquisition else "adapters.configure")
        if not self.physical:
            return
        if self.lease is None:
            raise EquipmentDenied("physical device has no connected session authority")
        cfg = self._adapter.configuration
        self.lease.require(owner=principal.subject, session_id=principal.session_id,
                           configuration_id=cfg.configuration_id if cfg else None, acquisition=acquisition)

    def _uncertain(self):
        if self.lease is not None:
            try:
                self.lease.uncertain()
            except EquipmentDenied:
                self.lease.invalidate()
                self._authority_revoked = True
                self._release_revoked_authority()

    def identify(self):
        self._authorize("adapters.read")
        result = self._adapter.identify()
        result["security_enforced"] = True
        result["physical_interlock_certified"] = False
        return result

    def availability(self):
        self._authorize("adapters.read")
        return self._adapter.availability()

    def capabilities(self):
        self._authorize("adapters.read")
        return self._adapter.capabilities()

    def connect(self):
        principal = self._authorize("adapters.configure")
        self._authority_revoked = False
        if self.physical:
            serial = getattr(self._adapter, "serial", "")
            address = getattr(self._adapter, "address", None)
            identity = PhysicalIdentity(serial, str(address or "usb://" + serial))
            if self.authority is None:
                raise EquipmentDenied("shared machine equipment authority required")
            self.lease = self.authority.acquire(identity, owner=principal.subject, session_id=principal.session_id)
        try:
            return self._adapter.connect()
        except BaseException:
            if self.lease:
                self.lease.revoke()
                self.lease = None
            raise

    def configure(self, configuration):
        self._authorize("adapters.configure")
        from quantum_autopilot.lab.adapter_registry import validate_adapter_configuration
        validate_adapter_configuration(self._adapter, configuration)
        try:
            result = self._adapter.configure(configuration)
            if self.lease:
                if not result.get("readback_verified"):
                    raise EquipmentDenied("physical configuration lacks verified readback")
                self.lease.configuration_verified(result["configuration_id"])
            return result
        except BaseException:
            self._uncertain()
            raise

    def reverify_configuration(self, configuration=None):
        self._authorize("adapters.configure")
        result = self._adapter.reverify_configuration(configuration)
        if self.lease:
            self.lease.configuration_verified(result["configuration_id"])
        return result

    def calibrate(self):
        self._authorize("adapters.configure")
        try:
            result = self._adapter.calibrate()
            if self.lease:
                clock = self._adapter.tagger.getReferenceClockState()
                # Internal free-running clocks are not evidence of external
                # experiment synchronization. A locked reference is mandatory.
                synchronized = bool(clock.enabled) and bool(clock.is_locked)
                self.lease.calibrated(valid_for_s=self.calibration_validity_s, synchronized=synchronized)
            return result
        except BaseException:
            self._uncertain()
            raise

    def acquire(self, experiment_id, *, fault=None):
        principal = self._authorize("adapters.acquire")
        if self.lease:
            cfg = self._adapter.configuration
            self.lease.require(owner=principal.subject, session_id=principal.session_id,
                               configuration_id=cfg.configuration_id if cfg else None, acquisition=True)
        try:
            result = self._adapter.acquire(experiment_id, fault=fault)
        except BaseException:
            self._uncertain()
            raise
        if self.lease:
            self._native_guard(acquisition=True)
        if self.lease and self._adapter.state in {"disconnected", "fault", "configuration_unverified"}:
            self.lease.revoke()
            self.lease = None
        self.runtime.audit.record(principal, "adapters.acquire", self.resource, "completed",
                                  {"experiment_id": experiment_id, "origin": result.origin,
                                   "fence": self.lease.fence if self.lease else None})
        return result

    def acquire_continuous(self, experiment_id, journal_path, *, cancel=None, drain_interval_s=.01, on_block=None):
        principal = self._authorize("adapters.acquire")
        cfg = self._adapter.configuration
        if self.lease:
            self.lease.require(owner=principal.subject, session_id=principal.session_id,
                               configuration_id=cfg.configuration_id if cfg else None, acquisition=True)
        def checkpoint(batch, receipt):
            self._authorize("adapters.acquire")
            if self.lease:
                self.lease.require(owner=principal.subject, session_id=principal.session_id,
                                   configuration_id=cfg.configuration_id, acquisition=True)
            if on_block:
                on_block(batch, receipt)
        try:
            return self._adapter.acquire_continuous(experiment_id, journal_path, cancel=cancel,
                            drain_interval_s=drain_interval_s, on_block=checkpoint)
        except BaseException:
            self._uncertain()
            raise

    def disconnect(self):
        self._authorize("adapters.configure")
        result = self._adapter.disconnect()
        # No release when an in-progress operation or teardown error prevented
        # disconnect; acquisition cancellation must finish first.
        if self.lease:
            self.lease.revoke()
            self.lease = None
        return result
