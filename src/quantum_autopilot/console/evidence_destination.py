"""Validate the shared evidence-export destination contract without writing files."""
import json
from pathlib import Path


def export_destination(target: str, default: Path) -> Path:
    if not isinstance(target, str) or len(target) > 32768:
        raise ValueError("bounded evidence export request required")
    if not target.lstrip().startswith("{"):
        return default
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate export request field")
            result[key] = value
        return result
    request = json.loads(target, object_pairs_hook=unique)
    if set(request) != {"scope", "destination"}:
        raise ValueError("export requires scope and destination")
    scope, destination = request["scope"], request["destination"]
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 128:
        raise ValueError("evidence export scope required")
    if not isinstance(destination, str) or not destination.strip() or len(destination) > 32000:
        raise ValueError("export destination must be a nonempty file path")
    path = Path(destination).expanduser().resolve()
    if path.suffix.lower() != ".json" or not path.parent.is_dir():
        raise ValueError("export requires a .json file in an existing directory")
    return path
