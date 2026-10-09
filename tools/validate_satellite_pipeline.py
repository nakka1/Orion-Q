"""Validate OGS contracts, physical sensitivities and actual loopback ingress."""
import argparse
from pathlib import Path
from quantum_autopilot.quantum_protocols.satellite_validation import validate_satellite_pipeline

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=42)
    args=parser.parse_args()
    report=validate_satellite_pipeline(args.output_dir,args.seed)
    print(args.output_dir/'report.json')
    print(f"{'APROVADO' if report['passed'] else 'REPROVADO'}: {sum(report['checks'].values())}/{report['check_count']} software checks; hardware not qualified")
    raise SystemExit(0 if report['passed'] else 1)
