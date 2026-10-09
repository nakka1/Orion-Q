"""Lightweight windowless stdio; import before the scientific application."""
import io
import os
import sys


WORKER_FLAG = "--temporal-campaign-worker"


def worker_supported():
    return not getattr(sys, "frozen", False) or (
        os.name == "nt" and getattr(sys, "_orion_temporal_worker_v1", False) is True)


def worker_command(store_path):
    if not worker_supported():
        raise ValueError("this executable does not declare a temporal worker entry point")
    prefix = [WORKER_FLAG] if getattr(sys, "frozen", False) else [
        "-m", "quantum_autopilot.control_plane.temporal_campaign_worker"]
    return sys.executable, [*prefix, "--store", str(store_path)]


def restore_worker_stdio():
    """Reattach QProcess-inherited pipes, never allocate a Windows console.

    PyInstaller's windowless bootloader sets sys.std* to None, but QProcess
    supplies valid Win32 standard handles. Own duplicate handles, not Qt's.
    """
    if all(getattr(sys, name) is not None for name in ("stdin", "stdout", "stderr")):
        return True
    if os.name != "nt":
        return False
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    opened = []
    try:
        for name, number, reading in (("stdin", -10, True), ("stdout", -11, False), ("stderr", -12, False)):
            if getattr(sys, name) is not None:
                continue
            handle = kernel.GetStdHandle(number & 0xffffffff)
            if handle in (None, 0, ctypes.c_void_p(-1).value):
                raise OSError("worker requires inherited standard pipes")
            duplicate = wintypes.HANDLE()
            process = kernel.GetCurrentProcess()
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_RDONLY if reading else os.O_WRONLY))
            except OSError:
                kernel.CloseHandle(duplicate)
                raise
            raw = os.fdopen(fd, "rb" if reading else "wb", buffering=0)
            stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", line_buffering=True)
            opened.append((name, stream))
        for name, stream in opened:
            setattr(sys, name, stream)
        return True
    except OSError:
        for _, stream in opened:
            stream.close()
        return False
