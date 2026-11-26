"""Read-only verification of a frozen local implementation/research checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    differences = []
    for group in ['files', 'outputs']:
        for name, expected in manifest[group].items():
            path = ROOT / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                differences.append(name)
    with zipfile.ZipFile(ROOT / manifest['source_archive']) as archive:
        if set(archive.namelist()) != set(manifest['files']):
            differences.append('source ZIP member set')
        for name, expected in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                differences.append('ZIP:' + name)
    for group in manifest['preservation']:
        for name, expected in group['files'].items():
            path = ROOT / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                differences.append(group['name'] + ':' + name)
    print(json.dumps({'passed': not differences, 'differences': differences,
                      'source_files': len(manifest['files']), 'evidence_files': len(manifest['outputs']),
                      'preservation_counts': {g['name']: len(g['files']) for g in manifest['preservation']},
                      'deployment_allowed': False, 'profitable_advantage_validated': False}, indent=2))
    if differences:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
