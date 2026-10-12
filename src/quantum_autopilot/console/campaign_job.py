"""Native campaign adapter: child process, bounded progress, verified readback.

The dialog only consumes presentation fields. Files/SQLite stay at this seam.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from quantum_autopilot.control_plane.temporal_campaign import read_temporal_campaign, list_temporal_campaigns, validate_history_prefix
from quantum_autopilot.control_plane.temporal_campaign_cli import load_manifest, parse_manifest
from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, list_attempts, export_attempt_snapshot, validate_attempt_status, validate_attempt_period
from quantum_autopilot.control_plane.attempt_file import verify_attempt_file
from quantum_autopilot.control_plane.attempt_batch import export_attempt_page
from quantum_autopilot.control_plane.attempt_archive import verify_attempt_archive
from quantum_autopilot.control_plane.attempt_receipt import read_attempt_receipt
from quantum_autopilot.control_plane.campaign_read_store import ReadOnlyCampaignStore
from quantum_autopilot.control_plane.campaign_attempts import RECEIPT_STATES
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, _sha256
from quantum_autopilot.worker_runtime import worker_command, worker_supported
from .theme import C
from .capabilities import SourceCapabilities


class CampaignJob(QObject):
    changed = Signal()
    idle = Signal()
    _read_done = Signal(object)
    _export_done = Signal(object)
    _history_done = Signal(object)
    _attempt_ready = Signal(object)
    _attempts_done = Signal(object)
    _attempt_export_done = Signal(object)
    _attempt_file_done = Signal(object)
    _attempt_page_done = Signal(object)
    _attempt_zip_done = Signal(object)
    _manifest_saved = Signal(object)

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.context = self._context()
        self._source_presentation = None
        self._presentation_capabilities = SourceCapabilities()
        self.request = self.receipt = self.process = None
        self.active = False
        self.review_text = self.provenance_text = ""
        self.result_rows, self.comparison_rows = [], []
        self.note = "Load a complete manifest. SIMULATION ONLY; no WDM inference."
        self.completed = self.total = 0
        self.cancel_requested = False
        self._buffer = b""
        self._worker_error = ""
        self._cancel_ack = False
        self._replayed = None
        self.history_entries = ()
        self.history_rows = []
        self.history_before = self.history_next = None
        self.history_back = ()
        self.history_prefix = ""
        self.history_note = "No history loaded. Index entries are unverified until opened."
        self.attempt_id = None
        self.attempts = ()
        self.attempt_before = self.attempt_next = None
        self.attempt_back = ()
        self.attempt_prefix = self.attempt_status = ""
        self.attempt_from = self.attempt_before_date = ""
        self.attempt_note = "Refresh to read operational attempts. OPEN is unresolved, not proof of a running process."
        self.attempt_file_text = "Choose an exported operational JSON. Read only; no journal import or scientific verification."
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="campaign-receipt")
        self._read_done.connect(self._on_read)
        self._export_done.connect(self._on_export)
        self._history_done.connect(self._on_history)
        self._attempt_ready.connect(self._on_attempt_ready)
        self._attempts_done.connect(self._on_attempts)
        self._attempt_export_done.connect(self._on_attempt_export)
        self._attempt_file_done.connect(self._on_attempt_file)
        self._attempt_page_done.connect(self._on_attempt_page)
        self._attempt_zip_done.connect(self._on_attempt_zip)
        self._manifest_saved.connect(self._on_manifest_saved)

    def _context(self):
        return (id(self.provider.active), self.provider._generation)

    @property
    def allowed(self):
        return self.provider.capabilities.allows("run_temporal_campaign") and worker_supported()

    @property
    def history_allowed(self):
        return self.provider.capabilities.allows("read_temporal_campaign_history")

    @property
    def presentation_capabilities(self):
        """Declarations from the committed frame, never authority for an action."""
        if self.context != self._context():
            return SourceCapabilities()
        return self._presentation_capabilities

    def invalidate_presentation_capabilities(self):
        """Withdraw UI affordances without issuing or cancelling an operation."""
        self._presentation_capabilities = SourceCapabilities()
        self._source_presentation = None
        self.changed.emit()

    def refresh_source(self, snapshot=None):
        context = self._context()
        if snapshot is None:
            capabilities = self.provider.capabilities
        else:
            # Presentation only: these declarations arrived in the source's
            # authenticated frame. Operational methods still recheck access.
            capabilities = snapshot.capabilities
        allowed = capabilities.allows("run_temporal_campaign") and worker_supported()
        history_allowed = capabilities.allows("read_temporal_campaign_history")
        self._presentation_capabilities = capabilities
        presentation = (context, capabilities)
        if presentation == self._source_presentation:
            return
        self._source_presentation = presentation
        if context != self.context or not (allowed or history_allowed):
            self.cancel()
            self.context = context
            self.request = self.receipt = None
            self.review_text = self.provenance_text = ""
            self.result_rows, self.comparison_rows = [], []
            self.completed = self.total = 0
            self.history_entries, self.history_rows = (), []
            self.history_before = self.history_next = None
            self.history_back = ()
            self.history_prefix = ""
            self.attempts = ()
            self.attempt_before = self.attempt_next = None
            self.attempt_back = ()
            self.attempt_prefix = self.attempt_status = ""
            self.attempt_from = self.attempt_before_date = ""
            self.attempt_note = "Source changed; operational history cleared."
            self.attempt_file_text = "Source changed; external file preview cleared."
            self.history_note = "Source changed; history cleared."
            self.note = "Source changed or unavailable; previous preview/results cleared. No request resubmitted."
        self.changed.emit()

    def load(self, path):
        if self.active or not self.allowed:
            raise ValueError("campaign unavailable or already active")
        # Invalidate the old confirmation/result even if the new file is invalid.
        self.request = self.receipt = None
        self.review_text = self.provenance_text = ""
        self.result_rows, self.comparison_rows = [], []
        self.completed = self.total = 0
        self.note = "No manifest loaded; validation required."
        try:
            request = load_manifest(path)
            review = self._manifest_review(request)
            self.request = request
            self.review_text, self.total, self.note = review
        except (ValueError, OSError) as exc:
            self.note = f"REFUSED · {exc}"
            raise
        finally:
            self.changed.emit()

    def _manifest_review(self, request):
        snap = self.provider.snapshot()
        if request.topology_id != snap.topology.id or request.expected_version != snap.topology.version:
            raise ValueError("manifest topology/version differs from current source; update the manifest explicitly")
        hops = ({request.source, request.repeater}, {request.repeater, request.destination})
        preview = {"adapter": snap.capabilities.adapter_id, "input_kind": snap.input_kind,
            "topology_id": snap.topology.id, "version": snap.topology.version,
            "selected_links": [{"id": link.id, "a": link.a, "b": link.b,
                "nominal_pair_fidelity": link.nominal_pair_fidelity, "distance_km": link.distance_km,
                "configured_active": link.configured_active}
                for link in snap.topology.links if {link.a, link.b} in hops]}
        text = ("SOURCE PREVIEW · frozen only when the worker starts\n" + json.dumps(preview, indent=2)
                + "\n\nCOMPLETE MANIFEST\n" + json.dumps(asdict(request), indent=2, ensure_ascii=False))
        total = len(request.regimes)*len(request.seeds)
        note = f"REVIEW · {total} trials · request SHA256 {_sha256(asdict(request))}\nParameters are assumed, not experimental measurements."
        return text, total, note

    def apply_manifest_draft(self, text, *, expected_request, expected_context):
        if self.active or not self.allowed or self.request is None:
            raise ValueError("manifest editing unavailable or another operation is active")
        if self._context() != expected_context or self.request is not expected_request:
            raise ValueError("source or loaded manifest changed; reopen the editor")
        if not isinstance(text, str) or len(text) > 65536:
            raise ValueError("draft must be UTF-8 text of at most 64 KiB")
        try:
            request = parse_manifest(text.encode('utf-8'))
        except UnicodeError as exc:
            raise ValueError("invalid Unicode in draft") from exc
        review = self._manifest_review(request)
        # Commit only after the complete draft and source preview validate.
        self.request, self.receipt = request, None
        self.result_rows, self.comparison_rows = [], []
        self.provenance_text = ""
        self.completed = 0
        self.review_text, self.total, self.note = review
        self.changed.emit()  # New identity invalidates the previous review checkbox.

    def save_manifest(self, path, *, expected_request, expected_context):
        """Save the applied request, never an unapplied editor draft or receipt."""
        if self.active or not self.allowed or self.request is None:
            raise ValueError("load a manifest on an idle campaign source first")
        if self._context() != expected_context or self.request is not expected_request:
            raise ValueError("source or loaded manifest changed while choosing a file; select again")
        request, context = self.request, self._context()
        raw = (json.dumps(asdict(request), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if parse_manifest(raw) != request:
            raise ValueError("manifest serialization differs from the applied request")
        destination = Path(path).absolute()
        self.active = True
        self.note = "SAVING MANIFEST · configuration only; no trials or journal writes."
        self.changed.emit()
        def write():
            try:
                # Exclusive creation also refuses files created after the chooser.
                with destination.open("xb") as stream:
                    stream.write(raw)
                return context, request, str(destination), ""
            except Exception as exc:
                return context, request, str(destination), str(exc)
        self._pool.submit(write).add_done_callback(lambda future: self._manifest_saved.emit(future.result()))

    def _on_manifest_saved(self, result):
        context, request, path, error = result
        self.active = False
        if context == self._context() and self.request is request:
            self.note = (f"MANIFEST SAVE FAILED · {error} · a failed write may leave an incomplete file; choose a new destination."
                         if error else f"MANIFEST SAVED · {path}\nConfiguration only, not a result or frozen topology. Request SHA256 {_sha256(asdict(request))}")
        self.changed.emit()
        self.idle.emit()

    def start(self):
        if self.active or self.request is None or not self.allowed or self.provider.busy:
            raise ValueError("load/review a manifest on an available idle service first")
        # Recheck authority and version at dispatch, not just when the form opened.
        snap = self.provider.snapshot()
        if snap.topology.id != self.request.topology_id or snap.topology.version != self.request.expected_version:
            raise ValueError("topology version conflict; reload the manifest")
        store_path = self.provider.active.temporal_campaign_store_path()
        self._running_request = self.request
        self._running_context = self._context()
        self._store_path = Path(store_path)
        self.active = True
        self.receipt = None
        self.result_rows, self.comparison_rows = [], []
        self.provenance_text = ""
        self.completed = 0
        self.cancel_requested = self._cancel_ack = False
        self._replayed = None
        self._buffer, self._worker_error = b"", ""
        self.note = "STARTING · isolated worker process; no physical action."
        process = QProcess(self)
        self.process = process
        program, arguments = worker_command(store_path)
        process.setProgram(program)
        process.setArguments(arguments)
        process.started.connect(lambda: process.write((json.dumps(asdict(self._running_request), allow_nan=False)+"\n" + ("CANCEL\n" if self.cancel_requested else "")).encode("utf-8")))
        process.readyReadStandardOutput.connect(self._progress)
        process.readyReadStandardError.connect(lambda: self._stderr(process))
        process.finished.connect(lambda code, status: self._finished(process, code))
        process.errorOccurred.connect(lambda error: self._process_error(process, error))
        self.attempt_id = None
        self.note = "REGISTERING ATTEMPT · worker starts only after the operational journal commits."
        request, path = self._running_request, self._store_path
        def prepare():
            try:
                return process, begin_attempt(SimulatedTopologyStore(path), request), ""
            except Exception as exc:
                return process, None, str(exc)
        self._pool.submit(prepare).add_done_callback(lambda future: self._attempt_ready.emit(future.result()))
        self.changed.emit()

    def _on_attempt_ready(self, result):
        process, attempt_id, error = result
        if process is not self.process:
            return
        if error:
            self.process = None
            process.deleteLater()
            self.active = False
            self.note = f"UNCONFIRMED · operational journal unavailable; no worker launched. {error}"
            self.changed.emit()
            self.idle.emit()
            return
        self.attempt_id = attempt_id
        if self.cancel_requested or self._running_context != self._context() or not self.allowed:
            self.cancel_requested = self._cancel_ack = True
            self._finished(process, 3)
        else:
            self.note = f"STARTING · attempt {attempt_id} · isolated worker; no physical action."
            process.start()
            self.changed.emit()

    def _stderr(self, process):
        self._worker_error = (self._worker_error + bytes(process.readAllStandardError()).decode("utf-8", "replace"))[-2000:]

    def _progress(self):
        if self.process is None:
            return
        self._buffer += bytes(self.process.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self._worker_error = "Worker output exceeded protocol limit"
            self.process.kill()
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    continue
                if value.get("kind") == "progress":
                    done, total = value.get("completed"), value.get("total")
                    if type(done) is int and type(total) is int and total == self.total and self.completed <= done <= total:
                        self.completed = done
                        if not self.cancel_requested:
                            self.note = f"RUNNING · {done}/{total} · {value.get('regime')} · seed {value.get('seed')}"
                elif value.get("kind") == "cancelled":
                    self._cancel_ack = True
                elif value.get("kind") == "committed":
                    self._replayed = value.get("replay")
                elif value.get("kind") == "failed":
                    self._worker_error = str(value.get("message"))[:1000]
            except (ValueError, TypeError):
                continue  # Non-protocol library output is never a receipt.
        self.changed.emit()

    def cancel(self):
        if not self.active or self.cancel_requested:
            return
        self.cancel_requested = True
        self.note = "CANCELLATION REQUESTED · waiting for current trial / commit readback."
        process = self.process
        if process is not None:
            if process.state() == QProcess.ProcessState.Running:
                process.write(b"CANCEL\n")
            # A stuck kernel must not trap the GUI or leave a child on exit.
            QTimer.singleShot(10000, self, lambda: process.kill() if self.process is process else None)
        self.changed.emit()

    def _process_error(self, process, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._worker_error = process.errorString()
            self._finished(process, -1)

    def _finished(self, process, code):
        if self.process is not process:
            return
        self._progress()
        self._stderr(process)
        self.process = None
        process.deleteLater()
        request, context, path = self._running_request, self._running_context, self._store_path
        self.note = "VERIFYING · reading the complete persisted receipt; progress alone is not confirmation."
        self.changed.emit()
        attempt_id = self.attempt_id
        cancelled, cancel_ack, replayed = self.cancel_requested, self._cancel_ack, self._replayed
        worker_error = self._worker_error
        def read():
            receipt, error, journal_error = None, "", ""
            try:
                receipt = read_temporal_campaign(ReadOnlyCampaignStore(path), request.campaign_id)
                if receipt and _sha256(receipt["record"]["request"]) != _sha256(asdict(request)):
                    raise ValueError("receipt belongs to a different request with this ID")
            except Exception as exc:
                receipt, error = None, str(exc)
            if receipt:
                status = "REPLAYED" if replayed is True else "CONFIRMED" if code == 0 and replayed is False else "RECOVERED"
            else:
                status = ("CANCELLED" if cancel_ack and code == 3 and not error else
                          "FAILED_TO_START" if code == -1 else "FAILED" if code == 2 else "UNCONFIRMED")
            try:
                finish_attempt(SimulatedTopologyStore(path), attempt_id, status=status, exit_code=code,
                    cancelled=cancelled, receipt_sha256=receipt["record_sha256"] if receipt else None,
                    detail=(error or worker_error or ("Matching receipt verified; no quantum metrics are stored in this attempt."
                            if receipt else "No completed receipt confirmed; this is not a quantum timeout."))[:2000])
            except Exception as exc:
                journal_error = f" Operational journal NOT finalized: {exc}. Attempt may remain OPEN."
            return context, receipt, error, code, journal_error
        self._pool.submit(read).add_done_callback(lambda future: self._read_done.emit(future.result()))

    def _on_read(self, result):
        context, receipt, error, code, journal_error = result
        self.active = False
        if context == self._context() and self.allowed and self.request is not None:
            if receipt:
                self.receipt = receipt
                self.completed = self.total
                self._present(receipt)
                self.note = "CONFIRMED · complete hash-verified simulated receipt." + (
                    " Cancellation arrived after a completed record (possibly an idempotent replay); no rollback claimed." if self.cancel_requested else "")
                if self._replayed is True:
                    self.note += " IDEMPOTENT REPLAY · no new trials."
                elif code != 0:
                    self.note += " Recovered an existing receipt; this worker did not confirm a new calculation."
            else:
                self.note = ("CANCELLED · no completed campaign receipt." if self._cancel_ack and code == 3 and not error
                             else f"UNCONFIRMED · {error or self._worker_error or 'worker stopped without a complete receipt'}")
        else:
            self.note = "Previous source job ended. Its result was not attached to the current input."
        self.note += journal_error
        self.changed.emit()
        self.idle.emit()

    def _present(self, receipt):
        record = receipt["record"]
        summaries = record["result"]["summaries"]
        self.result_rows = []
        for regime in record["request"]["regimes"]:
            name = regime["regime_id"]
            s = summaries[name]
            lo, hi = s["target_probability_wilson_95"]
            values = [name, f"{s['target_met']}/{s['trials']}", f"{100*s['target_probability']:.1f}% [{100*lo:.1f}, {100*hi:.1f}]",
                      str(s["timeouts"]), "—" if s["conditional_fidelity_mean"] is None else f"{s['conditional_fidelity_mean']:.5f} (n={s['swaps']})",
                      f"{s['generated_pairs_mean_all_trials']:.2f}"]
            self.result_rows.append([(value, C.amber if i == 1 else C.ink) for i, value in enumerate(values)])
        self.comparison_rows = []
        for s in record["result"]["comparisons"]:
            values = [s["regime_id"], f"{100*s['target_probability_difference']:+.2f} pp", f"{s['p_value_holm']:.4g}",
                      "YES (not proof of gain)" if s["reject_equal_target_probability_at_005"] else "NO (not equivalence)"]
            self.comparison_rows.append([(v, C.ink) for v in values])
        self.provenance_text = (f"SIMULATED · {record['request']['campaign_id']} · frozen version {record['request']['expected_version']}\n"
            f"Completed UTC {record['completed_at_utc']}\nSource SHA256 {record['source_topology_sha256']}\nRecord SHA256 {receipt['record_sha256']}")

    def export(self, path):
        if self.active or self.receipt is None or not (self.allowed or self.history_allowed) or not self.provider.capabilities.allows("export_record"):
            raise ValueError("a confirmed receipt on the current source is required")
        receipt, context = self.receipt, self._context()
        self.active = True
        self.note = "EXPORTING · complete receipt; existing files are never overwritten."
        self.changed.emit()
        def write():
            try:
                with Path(path).open("x", encoding="utf-8") as stream:
                    json.dump(receipt, stream, indent=2, ensure_ascii=False, allow_nan=False)
                    stream.write("\n")
                return context, str(Path(path).resolve()), ""
            except Exception as exc:
                return context, str(path), str(exc)
        self._pool.submit(write).add_done_callback(lambda future: self._export_done.emit(future.result()))

    def _on_export(self, result):
        context, path, error = result
        self.active = False
        if context == self._context():
            self.note = f"EXPORT FAILED · {error}" if error else f"EXPORTED · {path}"
        self.changed.emit()
        self.idle.emit()

    def browse_history(self, direction="refresh", *, campaign_prefix=None):
        if self.active or not self.history_allowed:
            raise ValueError("history unavailable or another operation is active")
        prefix = self.history_prefix if campaign_prefix is None else validate_history_prefix(campaign_prefix)
        if direction != "refresh" and prefix != self.history_prefix:
            raise ValueError("refresh history before changing the campaign prefix")
        if direction == "refresh":
            before, back = None, ()
        elif direction == "older" and self.history_next is not None:
            before, back = self.history_next, (*self.history_back, self.history_before)
        elif direction == "newer" and self.history_back:
            before, back = self.history_back[-1], self.history_back[:-1]
        else:
            raise ValueError("history page unavailable")
        path = self.provider.active.temporal_campaign_history_store_path()
        context = self._context()
        self.active, self.cancel_requested = True, False
        self.history_prefix = prefix
        self.history_before = self.history_next = None
        self.history_back = ()
        self.history_entries, self.history_rows = (), []
        self.history_note = "LOADING INDEX · metadata only, no experiments executed."
        self.changed.emit()
        def read():
            try:
                page = list_temporal_campaigns(ReadOnlyCampaignStore(path), before_sequence=before, campaign_prefix=prefix)
                return "page", context, page, "", (before, back)
            except Exception as exc:
                return "page", context, None, str(exc), (before, back)
        self._pool.submit(read).add_done_callback(lambda future: self._history_done.emit(future.result()))

    def open_history(self, index):
        if self.active or not self.history_allowed:
            raise ValueError("history unavailable or another operation is active")
        if type(index) is not int or not 0 <= index < len(self.history_entries):
            raise ValueError("select an entry from the current history page")
        entry = self.history_entries[index]
        path = self.provider.active.temporal_campaign_history_store_path()
        context = self._context()
        self.active, self.cancel_requested = True, False
        # Browsing an old result must never leave a runnable previous manifest.
        self.request = self.receipt = None
        self.review_text = self.provenance_text = ""
        self.result_rows, self.comparison_rows = [], []
        self.completed = self.total = 0
        self.note = "VERIFYING HISTORICAL RECEIPT · no worker launched."
        self.changed.emit()
        def read():
            try:
                receipt = read_temporal_campaign(ReadOnlyCampaignStore(path), entry.campaign_id)
                if receipt is None or receipt["record_sha256"] != entry.record_sha256:
                    raise ValueError("record changed or disappeared since listing; refresh history")
                return "receipt", context, receipt, "", None
            except Exception as exc:
                return "receipt", context, None, str(exc), None
        self._pool.submit(read).add_done_callback(lambda future: self._history_done.emit(future.result()))

    def open_attempt_receipt(self, index):
        if self.active or not self.history_allowed:
            raise ValueError("linked receipt unavailable or another operation is active")
        if type(index) is not int or not 0 <= index < len(self.attempts):
            raise ValueError("select an attempt from the current page")
        selected = self.attempts[index]
        if selected['record']['status'] not in RECEIPT_STATES:
            raise ValueError("attempt has no recorded receipt association")
        path = self.provider.active.temporal_campaign_history_store_path()
        context = self._context()
        self.active, self.cancel_requested = True, False
        self.request = self.receipt = None
        self.review_text = self.provenance_text = ""
        self.result_rows, self.comparison_rows = [], []
        self.completed = self.total = 0
        self.note = "VERIFYING LINKED RECEIPT · selected attempt + request + receipt hashes; no worker launched."
        self.changed.emit()
        def read():
            try:
                receipt = read_attempt_receipt(path,
                    selected['record']['attempt_id'], selected['record_sha256'])
                return "attempt_receipt", context, receipt, "", selected
            except Exception as exc:
                return "attempt_receipt", context, None, str(exc), selected
        self._pool.submit(read).add_done_callback(lambda future: self._history_done.emit(future.result()))

    def _on_history(self, result):
        kind, context, value, error, pagination = result
        self.active = False
        if context != self._context() or not self.history_allowed or self.cancel_requested:
            self.history_note = "History read discarded after source change, cancellation or capability loss."
            if kind == "attempt_receipt" and context == self._context():
                self.note = "LINKED RECEIPT DISCARDED · cancellation or capability loss; no result attached."
        elif kind == "page":
            if error:
                self.history_note = f"HISTORY UNAVAILABLE · {error}"
            else:
                self.history_entries = value.entries
                self.history_before, self.history_back = pagination
                self.history_next = value.next_before
                self.history_rows = [[(str(e.sequence), C.muted), (e.campaign_id, C.ink),
                    (e.record_sha256, C.muted), ("UNVERIFIED INDEX", C.amber)] for e in value.entries]
                self.history_note = (f"Page {len(self.history_back)+1} · {len(value.entries)} records · newest sequence first. Open a row to verify."
                                     if value.entries else "No completed campaigns on this page. Cancelled/failed runs without a receipt are not listed.")
                self.history_note += f" Prefix (case-sensitive): {self.history_prefix!r}." if self.history_prefix else " All campaign IDs."
        elif error:
            label = "LINKED" if kind == "attempt_receipt" else "HISTORICAL"
            self.note = f"{label} RECEIPT REFUSED · {error}"
        else:
            try:
                self._present(value)
                record = value["record"]
                self.review_text = "HISTORICAL MANIFEST · READ ONLY · not a new request\n" + json.dumps(record["request"], indent=2)
                self.total = self.completed = record["result"]["total_trials"]
                self.receipt = value
                self.note = (f"HISTORICAL · HASH VERIFIED · frozen version {record['request']['expected_version']}\n"
                             "No trials executed. Current workspace unchanged; load a new manifest to run another experiment.")
                if kind == "attempt_receipt":
                    attempt = pagination['record']
                    self.provenance_text += (f"\nRecorded attempt {attempt['attempt_id']} · {attempt['status']}"
                        f"\nAttempt SHA256 {pagination['record_sha256']}"
                        f"\nRequest SHA256 {attempt['request_sha256']}"
                        "\nVerified association, not proof this dispatch produced new trials. No attempt state changed.")
                    self.note = "LINKED RECEIPT · ASSOCIATION VERIFIED · historical simulation result. No trials executed or attempt state changed."
            except (KeyError, TypeError, ValueError) as exc:
                self.result_rows, self.comparison_rows = [], []
                self.provenance_text = self.review_text = ""
                self.note = f"HISTORICAL RECEIPT REFUSED · malformed result: {exc}"
        self.changed.emit()
        self.idle.emit()

    def shutdown(self):
        # Shell waits asynchronously for idle before invoking shutdown.
        if self.active:
            raise RuntimeError("cancel/wait for the campaign before destroying its owner")
        self._pool.shutdown(wait=False)

    def browse_attempts(self, direction="refresh", *, campaign_prefix=None, status=None, requested_from=None, requested_before=None):
        if self.active or not self.history_allowed:
            raise ValueError("operational history unavailable or an operation is active")
        prefix = self.attempt_prefix if campaign_prefix is None else validate_history_prefix(campaign_prefix)
        selected_status = self.attempt_status if status is None else validate_attempt_status(status)
        since, until = validate_attempt_period(
            self.attempt_from if requested_from is None else requested_from,
            self.attempt_before_date if requested_before is None else requested_before)
        if direction != "refresh" and (prefix, selected_status, since, until) != (
                self.attempt_prefix, self.attempt_status, self.attempt_from, self.attempt_before_date):
            raise ValueError("refresh operational history before changing filters")
        if direction == "refresh":
            before, back = None, ()
        elif direction == "older" and self.attempt_next is not None:
            before, back = self.attempt_next, (*self.attempt_back, self.attempt_before)
        elif direction == "newer" and self.attempt_back:
            before, back = self.attempt_back[-1], self.attempt_back[:-1]
        else:
            raise ValueError("attempt page unavailable")
        path, context = self.provider.active.temporal_campaign_history_store_path(), self._context()
        self.active, self.cancel_requested = True, False
        self.attempt_prefix, self.attempt_status = prefix, selected_status
        self.attempt_from, self.attempt_before_date = since, until
        self.attempts = ()
        self.attempt_before = self.attempt_next = None
        self.attempt_back = ()
        self.attempt_note = "Loading verified operational metadata; no scientific results are generated."
        self.changed.emit()
        def read():
            try:
                return context, list_attempts(ReadOnlyCampaignStore(path), before_sequence=before,
                    campaign_prefix=prefix, status=selected_status, requested_from=since, requested_before=until), "", (before, back)
            except Exception as exc:
                return context, None, str(exc), (before, back)
        self._pool.submit(read).add_done_callback(lambda future: self._attempts_done.emit(future.result()))

    def _on_attempts(self, result):
        context, page, error, pagination = result
        self.active = False
        if context != self._context() or not self.history_allowed or self.cancel_requested:
            self.attempt_note = "Operational history discarded after source change/cancellation."
        elif error:
            self.attempt_note = f"OPERATIONAL HISTORY UNAVAILABLE · {error}"
        else:
            self.attempts, self.attempt_next = page
            self.attempt_before, self.attempt_back = pagination
            self.attempt_note = f"Page {len(self.attempt_back)+1} · {len(self.attempts)} attempts. OPEN = no terminal observation, not proof of liveness."
            self.attempt_note += f" Applied: prefix {self.attempt_prefix!r}, status {self.attempt_status or 'ALL'}."
            if self.attempt_from or self.attempt_before_date:
                self.attempt_note += f" Requested UTC: [{self.attempt_from or 'unbounded'}, {self.attempt_before_date or 'unbounded'})."
        self.changed.emit()
        self.idle.emit()

    def export_attempt(self, index, destination):
        if self.active or not self.history_allowed or not self.provider.capabilities.allows("export_record"):
            raise ValueError("operational export unavailable or another operation is active")
        if type(index) is not int or not 0 <= index < len(self.attempts):
            raise ValueError("select an attempt from the current page")
        selected = self.attempts[index]
        path = self.provider.active.temporal_campaign_history_store_path()
        context = self._context()
        self.active, self.cancel_requested = True, False
        self.attempt_note = "EXPORTING ATTEMPT · rechecking the selected version; operational metadata only."
        self.changed.emit()
        def write():
            try:
                snapshot = export_attempt_snapshot(ReadOnlyCampaignStore(path), selected["record"]["attempt_id"],
                    selected["record_sha256"], destination)
                return context, str(Path(destination).absolute()), snapshot["record_sha256"], ""
            except Exception as exc:
                return context, str(destination), "", str(exc)
        self._pool.submit(write).add_done_callback(lambda future: self._attempt_export_done.emit(future.result()))

    def _on_attempt_export(self, result):
        context, path, digest, error = result
        self.active = False
        if context == self._context() and self.history_allowed:
            self.attempt_note = (f"ATTEMPT EXPORT FAILED · {error}" if error else
                                 f"ATTEMPT EXPORTED · {path} · record SHA256 {digest} · operational snapshot, not a scientific receipt.")
        self.changed.emit()
        self.idle.emit()

    def verify_attempt_file(self, path):
        if self.active or not self.history_allowed:
            raise ValueError("operational verification unavailable or another operation is active")
        context = self._context()
        self.active, self.cancel_requested = True, False
        self.attempt_file_text = "VERIFYING EXTERNAL FILE · read only · maximum 32 KiB."
        self.changed.emit()
        def read():
            try:
                return context, str(path), verify_attempt_file(path), ""
            except Exception as exc:
                return context, str(path), None, str(exc)
        self._pool.submit(read).add_done_callback(lambda future: self._attempt_file_done.emit(future.result()))

    def verify_attempt_archive(self, path):
        if self.active or not self.history_allowed:
            raise ValueError("operational verification unavailable or another operation is active")
        context = self._context()
        self.active, self.cancel_requested = True, False
        self.attempt_file_text = "VERIFYING PAGE ZIP · no extraction/import · maximum 2 MiB / 50 attempts."
        self.changed.emit()
        def read():
            try:
                return context, str(path), verify_attempt_archive(path), ""
            except Exception as exc:
                return context, str(path), None, str(exc)
        self._pool.submit(read).add_done_callback(lambda future: self._attempt_zip_done.emit(future.result()))

    def _on_attempt_zip(self, result):
        context, path, wrapper, error = result
        self.active = False
        if context == self._context() and self.history_allowed and not self.cancel_requested:
            self.attempt_file_text = (f"ZIP REFUSED · {error}" if error else
                f"PAGE ZIP VERIFIED · manifest + all {wrapper['manifest']['count']} operational records\n"
                "CRC / SCHEMA / HASH / FILTERS VERIFIED · read snapshot only\n"
                "NOT AUTHENTICATED · NOT SCIENTIFIC EVIDENCE · NOT IMPORTED\n"
                "Page only; completeness of the original search is not established.\n"
                "Not compared with the current journal. OPEN does not prove process liveness.\n"
                f"File selected: {path}\n\n" + json.dumps(wrapper, indent=2, ensure_ascii=False))
        elif context == self._context():
            self.attempt_file_text = "ZIP verification discarded after cancellation or capability loss."
        self.changed.emit()
        self.idle.emit()

    def export_attempt_page(self, destination):
        if self.active or not self.history_allowed or not self.provider.capabilities.allows("export_record"):
            raise ValueError("operational page export unavailable or another operation is active")
        if not self.attempts:
            raise ValueError("load a nonempty operational page first")
        selection = tuple({'attempt_id': item['record']['attempt_id'], 'record_sha256': item['record_sha256']}
                          for item in self.attempts)
        filters = dict(campaign_prefix=self.attempt_prefix, status=self.attempt_status,
                       requested_from=self.attempt_from, requested_before=self.attempt_before_date)
        path, context = self.provider.active.temporal_campaign_history_store_path(), self._context()
        self.active, self.cancel_requested = True, False
        self.attempt_note = f"EXPORTING PAGE · rechecking {len(selection)} operational snapshots; no scientific results."
        self.changed.emit()
        def write():
            try:
                manifest = export_attempt_page(ReadOnlyCampaignStore(path), selection, destination, **filters)
                return context, str(Path(destination).absolute()), manifest, ""
            except Exception as exc:
                return context, str(destination), None, str(exc)
        self._pool.submit(write).add_done_callback(lambda future: self._attempt_page_done.emit(future.result()))

    def _on_attempt_page(self, result):
        context, path, manifest, error = result
        self.active = False
        if context == self._context() and self.history_allowed:
            self.attempt_note = (f"PAGE EXPORT FAILED · {error}" if error else
                f"PAGE EXPORTED · {manifest['manifest']['count']} operational attempts · {path} · "
                f"manifest SHA256 {manifest['manifest_sha256']} · selected page only, not scientific evidence.")
        self.changed.emit()
        self.idle.emit()

    def _on_attempt_file(self, result):
        context, path, snapshot, error = result
        self.active = False
        if context == self._context() and self.history_allowed and not self.cancel_requested:
            self.attempt_file_text = (f"FILE REFUSED · {error}" if error else
                "SCHEMA + RECORD HASH VERIFIED · external read snapshot only\n"
                "NOT AUTHENTICATED · NOT A SCIENTIFIC RECEIPT · NOT IMPORTED\n"
                "Not compared with this source's journal. OPEN does not prove process liveness.\n"
                f"File selected: {path}\n\n" + json.dumps(snapshot, indent=2, ensure_ascii=False))
        elif context == self._context():
            self.attempt_file_text = "File verification discarded after cancellation or capability loss."
        self.changed.emit()
        self.idle.emit()
