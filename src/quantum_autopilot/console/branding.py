"""The desktop's persistent Windows identity and packaged OrionQ icon."""
from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path
from quantum_autopilot.product import PRODUCT_NAME


# Keep this independent of the version and install directory so shortcuts and
# already-pinned taskbar items identify every release as the same application.
WINDOWS_APP_ID = "ORION-Q.Desktop"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "orion-q.ico"


def configure_windows_app_identity() -> bool:
    """Set the taskbar identity before QApplication creates any native windows."""

    if sys.platform != "win32":
        return False
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        setter = shell.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(WINDOWS_APP_ID)
    except (AttributeError, OSError) as error:
        logging.getLogger(__name__).warning("Cannot set %s taskbar identity: %s", PRODUCT_NAME, error)
        return False
    if result < 0:
        logging.getLogger(__name__).warning("Cannot set %s taskbar identity: HRESULT %#x", PRODUCT_NAME, result)
        return False
    return True


def app_icon():
    """Load all packaged icon resolutions after QApplication has been created."""

    from PySide6.QtGui import QIcon

    # PyInstaller preserves the package-relative __file__ location in _internal.
    # A source checkout and an installed wheel use precisely the same path.
    if not ICON_PATH.is_file():
        raise FileNotFoundError(f"Required {PRODUCT_NAME} desktop icon missing: {ICON_PATH}")
    icon = QIcon(str(ICON_PATH))
    if icon.isNull():
        raise RuntimeError(f"Cannot load {PRODUCT_NAME} desktop icon: {ICON_PATH}")
    return icon
