"""Sparse loading surface whose five bars follow reported completed work."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QWidget

from quantum_autopilot.product import product_version
from ..theme import C, DESIGN_H, DESIGN_W, MIN_CONTENT_H, MIN_CONTENT_W, mono, text
from ..widgets import Btn, Frame, L, Prose, hbox, vbox
from ..widgets.brand_mark import OrionQWordmark
from ..widgets.loading_state import LoadingStatus
from ..widgets.prims import scoped_style, scroll_panel


class SplashScreen(QWidget):
    finished = Signal()
    retry_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        scoped_style(self, f"background:{C.bg};")
        self.resize(DESIGN_W, DESIGN_H)
        self.setMinimumSize(MIN_CONTENT_W, MIN_CONTENT_H)
        outer = vbox(self, (0, 0, 0, 0), 0)
        body = QWidget()
        root = vbox(body, (48, 32, 48, 32), 0)
        self.scroll = scroll_panel(body)
        outer.addWidget(self.scroll, 1)
        root.addStretch(1)
        center = QWidget()
        center.setMinimumWidth(0)
        center.setMaximumWidth(640)
        center.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = vbox(center, (0, 0, 0, 0), 12)
        self.brand = OrionQWordmark(72)
        lay.addWidget(self.brand, 0, Qt.AlignmentFlag.AlignLeft)
        self.version_label = L(f"Version {product_version()}", mono(12), C.ink3)
        self.version_label.setAccessibleName("Distribution version")
        lay.addWidget(self.version_label)
        lay.addWidget(L("Integrated quantum network control.", text(18), C.ink2))
        lay.addSpacing(24)
        self.loading = LoadingStatus()
        self.loading.finished.connect(self.finished.emit)
        lay.addWidget(self.loading)
        self.recovery_actions = QWidget()
        recovery = hbox(self.recovery_actions, (0, 8, 0, 0), 12)
        self.retry_button = Btn("Retry loading", variant="solid", height=32)
        self.back_button = Btn("Back to sign in", height=32)
        self.retry_button.clicked.connect(self.retry_requested.emit)
        self.back_button.clicked.connect(self.back_requested.emit)
        recovery.addWidget(self.retry_button)
        recovery.addWidget(self.back_button)
        recovery.addStretch(1)
        self.recovery_actions.hide()
        lay.addWidget(self.recovery_actions)
        # Keep the existing entry API available to providers and callers.
        self.progress = self.loading.progress
        self.percent = self.loading.percent
        self.message = self.loading.message
        self.step_states = []
        root.addWidget(center, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addStretch(1)
        footer = Frame(border=C.soft, sides="t")
        footer_row = hbox(footer, (48, 16, 48, 16), 24)
        self.build_label = L(f"BUILD {product_version()}", mono(10.5), C.ink3)
        self.build_label.setAccessibleName("Application build")
        footer_row.addWidget(self.build_label)
        footer_row.addStretch(1)
        self.lock_label = Prose("N.A.K.A. · PHYSICAL ACTUATION LOCKED", 11, C.ink3)
        self.lock_label.setFont(mono(10.5))
        self.lock_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        footer_row.addWidget(self.lock_label, 1)
        outer.addWidget(footer)

    def start(self, message="Loading local service, evidence and model metadata…", *, total=5, completed=0):
        self.recovery_actions.hide()
        self.loading.start(message, total=total, completed=completed)

    def set_progress(self, completed, total, message="", *, indeterminate=True):
        self.loading.set_progress(completed, total, message, indeterminate=indeterminate)

    def note(self, message):
        self.loading.note(message)

    def complete(self, message="Selected data source is ready."):
        self.recovery_actions.hide()
        self.loading.complete(message)

    def abort(self, message="Unable to load selected data source."):
        self.loading.abort(message)
        self.recovery_actions.show()

    def dispose(self):
        self.loading.dispose()
