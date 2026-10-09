"""Public product identity, independent of protocols and persistent storage IDs."""
from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import sys


PRODUCT_NAME = "OrionQ"
EXECUTABLE_NAME = PRODUCT_NAME + ".exe"
PACKAGE_DIRECTORY = PRODUCT_NAME
DISTRIBUTION_NAME = "quantum-twin"


@lru_cache(maxsize=1)
def product_version() -> str:
    """Read the release version without duplicating it in interface labels.

    A checkout uses its own pyproject, even if editable-install metadata is
    stale. A wheel or frozen executable uses the bundled distribution metadata,
    never a working-directory file or a telemetry source's build description.
    """
    if not getattr(sys, "frozen", False):
        project = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if project.is_file():
            text = project.read_text(encoding="utf-8")
            try:
                import tomllib
            except ModuleNotFoundError:  # Supported Python 3.10 source installs.
                section = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
                if section:
                    declared = re.search(
                        r'''^\s*version\s*=\s*(["'])([^"'\r\n]+)\1\s*(?:\#.*)?$''', section[1], re.M)
                    if declared:
                        return declared[2]
            else:
                declared = tomllib.loads(text).get("project", {}).get("version")
                if isinstance(declared, str) and declared:
                    return declared
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError as error:
        if getattr(sys, "frozen", False):
            raise RuntimeError("OrionQ release version metadata is missing") from error
        return "development"
