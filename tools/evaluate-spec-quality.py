#!/usr/bin/env python3
"""Prepare oracle-blind Spec-review cases, then score independently supplied verdicts.

This checks evidence and curated oracle agreement, not arbitrary business semantics.
No model/network/commit/push is invoked by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def case_pack(pack):
    value = json.loads(pack.read_text())
    cases = value.get('cases')
    if value.get('schema') != 'factory-spec-quality/cases/1' or not isinstance(cases, list) or not cases:
        raise ValueError('Unknown or empty Spec case pack')
    seen = set()
    for case in cases:
        cid = case.get('id')
        checks = case.get('checks')
        if not isinstance(cid, str) or not cid.replace('-', '').isalnum() or cid in seen:
            raise ValueError('Invalid or repeated case ID')
        seen.add(cid)
        if (not isinstance(checks, list) or not checks or len({row['id'] for row in checks}) != len(checks)
                or any(row.get('expected') not in {'covered', 'gap', 'uncertain'} for row in checks)):
            raise ValueError('Empty or invalid case checks')
    return value


def prepare(pack, output):
    value = case_pack(pack)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    seen = set()
    skill = pack.parent.parent
    skill_files = [skill / 'SKILL.md', *sorted((skill / 'references').glob('*.md'))]
    for case in value['cases']:
        cid = case['id']
        if not isinstance(cid, str) or not cid.replace('-', '').isalnum() or cid in seen:
            raise ValueError('Invalid case ID')
        seen.add(cid)
        directory = output / cid
        directory.mkdir()
        write(directory / 'input.json', {key: case[key] for key in ('id', 'request', 'spec')})
        write(directory / 'checks.json', [{'id': row['id'], 'question': row['question']} for row in case['checks']])
        for path in skill_files:
            (directory / path.name).write_bytes(path.read_bytes())
        write(directory / 'binding.json', {'case_id': cid, 'input_sha256': sha(directory / 'input.json')})
        files = {path.name: sha(path) for path in directory.iterdir()}
        cases.append({'id': cid, 'files': files})
    write(output / 'run.json', {'schema': 'factory-spec-quality/run/1', 'pack': str(pack.resolve()),
                                'pack_sha256': sha(pack), 'oracle_status': value['oracle_status'],
                                'skill_files': {str(path): sha(path) for path in skill_files}, 'cases': cases})


def score(output):
    run = json.loads((output / 'run.json').read_text())
    pack = Path(run['pack'])
    value = case_pack(pack)
    skill = pack.parent.parent
    skill_files = [skill / 'SKILL.md', *sorted((skill / 'references').glob('*.md'))]
    expected_skills = {str(path): sha(path) for path in skill_files}
    if (run.get('schema') != 'factory-spec-quality/run/1' or sha(pack) != run['pack_sha256']
            or run.get('skill_files') != expected_skills):
        raise ValueError('Case pack or Skill version drifted')
    oracles = {case['id']: case for case in value['cases']}
    recorded = run.get('cases')
    if not isinstance(recorded, list) or len(recorded) != len(oracles) or {case['id'] for case in recorded} != set(oracles):
        raise ValueError('Missing, empty or repeated evaluation cases')
    results = []
    for case in run['cases']:
        directory = output / case['id']
        expected_names = {'input.json', 'checks.json', 'binding.json', *(path.name for path in skill_files)}
        if set(case.get('files', {})) != expected_names:
            raise ValueError('Frozen evaluation file manifest incomplete')
        if any(sha(directory / name) != expected for name, expected in case['files'].items()):
            raise ValueError('Frozen evaluation inputs drifted')
        original = oracles[case['id']]
        if (json.loads((directory / 'input.json').read_text()) != {key: original[key] for key in ('id', 'request', 'spec')}
                or json.loads((directory / 'checks.json').read_text()) != [{'id': row['id'], 'question': row['question']} for row in original['checks']]
                or any(sha(directory / path.name) != sha(path) for path in skill_files)):
            raise ValueError('Frozen case or Skill content differs from pack')
        response = json.loads((directory / 'response.json').read_text())
        if response.get('case_id') != case['id'] or response.get('input_sha256') != sha(directory / 'input.json'):
            raise ValueError('Evaluation response binding mismatch')
        expected = {row['id']: row['expected'] for row in oracles[case['id']]['checks']}
        rows = response.get('checks', [])
        if len(rows) != len(expected) or {row['id'] for row in rows} != set(expected):
            raise ValueError('Incomplete or repeated evaluation checks')
        source = json.loads((directory / 'input.json').read_text())
        for row in rows:
            evidence = row.get('evidence', [])
            if not evidence or not any(item.get('path') == 'request' for item in evidence):
                raise ValueError('Each judgement needs original request evidence')
            for item in evidence:
                if item.get('path') not in {'request', 'spec'} or not item.get('quote') or item['quote'] not in source[item['path']]:
                    raise ValueError('Non-contiguous or unsupported evaluation quote')
            results.append({'case_id': case['id'], 'check_id': row['id'], 'expected': expected[row['id']],
                            'actual': row.get('verdict'), 'passed': row.get('verdict') == expected[row['id']]})
    result = {'schema': 'factory-spec-quality/result/1', 'status': 'passed' if all(row['passed'] for row in results) else 'failed',
              'oracle_status': run['oracle_status'], 'scope': 'curated-proxy-evaluation', 'checks': results}
    write(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'score'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--pack', type=Path, default=Path(__file__).resolve().parents[2] / 'awesome-rules/skills/factory-spec-review/evals/behavior-cases.json')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args.pack, args.output)
            print(args.output)
            return 0
        result = score(args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'passed' else 1
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
