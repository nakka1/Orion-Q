"""Explicit local package identity check, not installation or model activation."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .catalog import model_options


@dataclass(frozen=True)
class ArchiveVerification:
    model_id: str
    archive_kind: str
    path: str
    sha256: str
    checked_at_utc: str
    member_count: int
    expanded_bytes: int
    blocked_pickle_members: tuple[str, ...]
    adapter_status: str

    def describe(self) -> str:
        blocked = ("\n".join(self.blocked_pickle_members) or "None in this package")
        return (
            "PACKAGE IDENTITY VERIFIED · SNAPSHOT ONLY\n"
            f"Model inspected: {self.model_id}\n"
            f"Package: {self.archive_kind.upper()}\n"
            f"File: {self.path}\n"
            f"Checked at (UTC): {self.checked_at_utc}\n"
            f"SHA-256: {self.sha256}\n"
            f"Inventory: {self.member_count} files / {self.expanded_bytes} expanded bytes\n"
            f"Adapter admission remains: {self.adapter_status}\n"
            f"Pickle members (deserialization blocked):\n{blocked}\n\n"
            "Matches the pinned reviewed export; member bytes and CRC checked without extraction. "
            "No model was constructed, no weights deserialized, no scripts executed. "
            "NOT INSTALLED; NOT ACTIVE. This is not a scientific result, signature or license. "
            "It does not verify the current telemetry's compatibility. The file may change after "
            "this check; any future use must verify its bytes again."
        )


def verify_model_archive(model_id: str, path: str | Path) -> ArchiveVerification:
    """Use the existing pinned reader; do not accept arbitrary exports or update pins."""
    option = next((item for item in model_options() if item.model_id == model_id), None)
    if option is None:
        raise ValueError("unknown optional model")
    # A local file chooser must not turn this read-only feature into a network client.
    # Reject UNC/device paths before resolve/stat/open, including Windows slash variants.
    spelling = str(path)
    if not spelling or spelling.replace("\\", "/").startswith("//") or "://" in spelling:
        raise ValueError("choose a regular local ZIP file; network/device paths are not supported")
    source = Path(path).absolute()
    if not source.is_file():
        raise ValueError("choose an existing regular local ZIP file")
    resolved = source.resolve(strict=True)
    if str(resolved).replace("\\", "/").startswith("//"):
        raise ValueError("network targets are not supported")
    # Lazy import: simply opening the catalog never imports the data reader.
    from .artifacts import ReviewedArchive

    archive = ReviewedArchive(resolved, option.archive_kind)
    members = archive.inventory()
    return ArchiveVerification(
        model_id=option.model_id, archive_kind=option.archive_kind, path=str(resolved),
        sha256=archive.sha256, checked_at_utc=datetime.now(timezone.utc).isoformat(),
        member_count=len(members), expanded_bytes=sum(item["bytes"] for item in members),
        blocked_pickle_members=tuple(item["name"] for item in members if item["deserialization_blocked"]),
        adapter_status=option.status,
    )
