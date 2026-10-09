"""Export declared Q05 compatibility; never opens a runtime, model or database."""

import argparse
import json
from pathlib import Path
from quantum_autopilot.model_intake.policy_compatibility import (
    inspect_policy_compatibility,
    policy_compatibility_catalog,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", help="Known controller/model ID; omit for the complete catalog")
    parser.add_argument("--output", type=Path, help="New JSON file; existing paths are never overwritten")
    args = parser.parse_args(argv)
    try:
        result = (
            inspect_policy_compatibility(args.candidate)
            if args.candidate is not None
            else policy_compatibility_catalog()
        )
        text = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output is not None:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
        else:
            print(text, end="")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
