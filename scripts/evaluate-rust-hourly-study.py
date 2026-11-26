"""Reproduce a bounded offline native Rust study; no market or broker requests.

This script only declares, invokes and measures the native CLI. Models, feature
calculation, calibration and portfolio simulation run in Rust. Repeating an
exposed development experiment never creates independent confirmation evidence.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--feed', choices=['iex', 'sip'], required=True)
    parser.add_argument('--dataset-directory', type=Path, required=True)
    parser.add_argument('--quote-dataset', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to((ROOT / '.local').resolve())
    output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    binary_sha = digest(binary)
    datasets = sorted(args.dataset_directory.resolve().glob('dataset-*.json'))
    if not datasets:
        raise ValueError('Existing content-addressed datasets required; nothing is downloaded')
    quotes = [p.resolve() for p in args.quote_dataset]
    declaration = subprocess.run(
        [str(binary), 'hourly-declare', '--feed', args.feed, '--output', str(output)],
        capture_output=True, text=True, check=True,
    )
    protocol = Path(json.loads(declaration.stdout)['protocol'])
    command = [str(binary), 'hourly-evaluate', '--protocol', str(protocol),
               '--dataset', *map(str, datasets)]
    if quotes:
        command.extend(['--quote-dataset', *map(str, quotes)])
    command.extend(['--output', str(output)])
    started = {
        'started_at': datetime.now(UTC).isoformat(), 'binary_sha256': binary_sha,
        'protocol': str(protocol), 'protocol_sha256': digest(protocol),
        'inputs': {str(p): digest(p) for p in datasets + quotes},
        'command': command, 'native_training_and_inference': True,
        'execution_authority': False, 'independent_confirmation': False,
        'orchestrator_sha256': digest(Path(__file__)),
    }
    (output / 'started.json').write_text(json.dumps(started, indent=2), encoding='utf-8')
    spec = importlib.util.spec_from_file_location('measurement', ROOT / 'scripts/benchmark-rust-numeric.py')
    measurement = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measurement)
    try:
        result = measurement.measured(command, output / 'native-result.json')
        if digest(binary) != binary_sha:
            raise ValueError('Native executable changed during measurement')
        (output / 'measurement.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    except Exception as error:
        (output / 'failed.json').write_text(json.dumps({'at': datetime.now(UTC).isoformat(),
                                                       'error': str(error)}), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
