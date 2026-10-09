"""Minimal read seam for existing campaign queries; never initializes a journal."""
from pathlib import Path
from threading import RLock
import sqlite3


class ReadOnlyCampaignStore:
    """Supply the query interface without inheriting schema setup or writers.

    mode=ro rejects writes and missing databases; query_only is defense in depth.
    No immutable=1: readers must observe committed changes on later reads and
    respect concurrent SQLite/WAL transactions. This is not an untrusted DB loader.
    """

    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file():
            raise ValueError('campaign journal must be an existing file')
        self._lock = RLock()

    def _connect(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
        except Exception:
            connection.close()
            raise
        return connection
