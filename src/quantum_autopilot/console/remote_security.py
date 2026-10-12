"""Remote desktop composition: peer identity is reported by the mTLS core."""
from __future__ import annotations

import ssl

from PySide6.QtWidgets import QLabel

from .screens.boot import SourceOption
from .shell import ConsoleWindow


def core_tls_context(*, ca_file, certificate_file, key_file, key_password_reader=None):
    if not ca_file or not certificate_file or not key_file:
        raise ValueError("remote mode requires CA, client certificate and private-key files")
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_file))
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    def password():
        # An explicit empty callback rejects encrypted keys without a supplied
        # institutional reader; OpenSSL must never prompt on the GUI thread.
        if key_password_reader is None:
            return b""
        try:
            value = key_password_reader()
            if not isinstance(value, bytes) or not 1 <= len(value) <= 4096:
                raise ValueError("protected client-key password is unavailable")
            return value
        except Exception:
            raise ValueError("protected client-key password is unavailable") from None
    context.load_cert_chain(str(certificate_file), str(key_file), password=password)
    return context


class RemoteConsoleWindow(ConsoleWindow):
    """Existing screens with remote-only source selection and certificate login."""

    def __init__(self, provider, **kwargs):
        if getattr(provider, "is_remote", False) is not True:
            raise ValueError("remote console requires a remote provider")
        self._remote_endpoint = provider.endpoint
        kwargs.pop("security", None)
        kwargs.pop("service_factory", None)
        kwargs.pop("demo_factory", None)
        super().__init__(provider, security=None, service_factory=None, demo_factory=None, **kwargs)

    def source_options(self):
        return (SourceOption("service", "Remote OrionQ core", self._remote_endpoint, "Remote core"),)

    def source_short(self):
        return "Remote core · mutual TLS"

    def source_factory(self, key):
        raise ValueError("This session is bound to the configured remote core; local sources are unavailable.")

    def open_demo_source(self):
        raise ValueError("Local simulation is unavailable in a remote desktop session.")

    def _build_ui(self, zoom=1.0):
        super()._build_ui(zoom=zoom)
        for label in self.boot.findChildren(QLabel):
            if label.text().startswith("Use your Windows account"):
                label.setText("The core authenticates your provisioned client certificate. "
                              "Its identity, roles and resource permissions apply on every request.")
            elif label.text() == "Operator ID / Windows account":
                label.setText("Authenticated backend peer")
            elif label.text() == "Local journal · no physical actuation":
                label.setText("Remote journal · physical actuation locked")
        self._configure_remote_login()

    def _configure_remote_login(self):
        active = self.provider.active
        ready = getattr(active, "ready", False)
        identity = (self._snap.security or {}) if self._snap is not None else {}
        self.boot._login_verified = ready
        self.boot._login_available = False
        self.boot._fixture_entry = False
        self.boot.operator_id.setText(identity.get("subject", "Waiting for backend peer identity"))
        self.boot.operator_id.setReadOnly(True)
        self.boot.operator_id.setAccessibleName("Authenticated backend peer identity")
        self.boot.password.clear()
        self.boot.password.setEnabled(False)
        self.boot.password.setPlaceholderText("Client certificate · no Windows password")
        self.boot.password.setAccessibleName("Client-certificate authentication; password entry disabled")
        self.boot.show_password.setEnabled(False)
        self.boot.os_session_button.hide()
        self.boot.select_source("service")
        self.boot.open_button.setText("Open remote workspace →")
        self.boot.login_feedback.setText(
            "Backend confirmed peer " + identity.get("subject", "") + ". Permissions are enforced by the core on every request."
            if ready else "Connecting to " + self._remote_endpoint + "; server verification and backend peer authorization are required.")
        self.boot._update_login_actions()
        self.boot.open_button.setToolTip("Open the workspace authenticated by mutual TLS on the remote core.")

    def _on_snapshot(self, snap):
        super()._on_snapshot(snap)
        self._configure_remote_login()

    def open_workspace(self, key=None):
        if not getattr(self.provider.active, "ready", False):
            self.boot.login_failed("The remote core has not admitted an authenticated snapshot.")
            return False
        return super().open_workspace("service")

    def _login_requested(self, username, password):
        self.boot.password.clear()
        self.boot.login_failed("Remote authentication uses the provisioned TLS certificate; no Windows login is accepted.")
