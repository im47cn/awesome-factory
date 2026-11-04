#!/usr/bin/env python3
"""Generate Rules' mechanical reference from executable Factory definitions."""
import argparse
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from runtime.local.protocol import reference_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rules', type=Path, default=SOURCE.parent / 'awesome-rules')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    expected = reference_fields()
    paths = [args.rules / 'skills' / skill / 'references/generated-contract.md'
             for skill in ('factory-spec-authoring', 'factory-spec-review')]
    for path in paths:
        if args.write:
            path.write_text(expected)
        elif not path.is_file() or path.read_text() != expected:
            print('Factory/Rules 机械协议漂移：' + str(path))
            return 1
    print('Factory/Rules mechanical protocol: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
