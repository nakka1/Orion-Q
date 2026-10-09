"""Export read-only Q04 inventory evidence; requires an explicitly selected receipt."""
import argparse
import json
import sqlite3

from quantum_autopilot.control_plane.command_reconciliation import export_reconciliation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', required=True, help='Existing local control SQLite journal')
    parser.add_argument('--command-id', required=True, help='UUID selected in COMMAND CENTER')
    parser.add_argument('--command-sha256', required=True, help='Exact hash of the selected receipt')
    parser.add_argument('--output', required=True, help='New JSON file; existing files are refused')
    args = parser.parse_args(argv)
    try:
        report = export_reconciliation(args.journal, args.command_id, args.command_sha256, args.output)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f'Reconciliation refused: {exc}\n')
    record = report['record']
    print(json.dumps(dict(output=args.output, record_sha256=report['record_sha256'],
                         original_state=record['original_state'], comparison=record['comparison'],
                         physical_action_performed=False), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
