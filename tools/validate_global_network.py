"""Run the same importable global network acceptance used by the frozen console."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from quantum_autopilot.network_engine.global_validation import validate_global_network


def main():
    parser = argparse.ArgumentParser(description='Validate global demonstration through actual backend acquisitions and receipts.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=10)
    parser.add_argument('--quick', action='store_true', help='Run nominal, ISL obstruction/recovery and integrity checks; omit global degradation/isolation scenarios.')
    args = parser.parse_args()
    result = validate_global_network(args.output, repository_root=Path(__file__).resolve().parents[1], steps=args.steps, full=not args.quick)
    print(json.dumps({'passed': result['passed'], 'checks': len(result['checks']),
        'mode': result['mode'], 'full_campaign': result['full_campaign'],
        'failed_checks': result['failed_checks'], 'output': result['output'], 'elapsed_s': result['elapsed_s']}, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
