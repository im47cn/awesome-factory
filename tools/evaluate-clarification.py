#!/usr/bin/env python3
"""Prepare isolated, oracle-blind inputs; validate and score independent judgements.

No model, network, business command, commit or push is invoked. Semantic verdicts
come from a separately supplied reviewer; they are not inferred from keywords.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from runtime.local.business_sources import capture, CLARIFICATION, source_file
from runtime.local.workflow import VERSION, validate_clarification


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Expected JSON object: ' + str(path))
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def safe_id(value):
    if not isinstance(value, str) or not value or not value.replace('-', '').isalnum():
        raise ValueError('Invalid case ID')
    return value


def prepare(pack, output, selected=None):
    pack, output = pack.resolve(), output.resolve()
    manifest = read(pack / 'manifest.json')
    if manifest.get('schema') != 'factory-clarification/case-pack/1':
        raise ValueError('Unknown case pack')
    ids = manifest.get('cases')
    if not isinstance(ids, list) or len(set(ids)) != len(ids) or not ids:
        raise ValueError('Invalid case list')
    ids = [safe_id(i) for i in ids]
    if selected and (not set(selected).issubset(ids) or len(set(selected)) != len(selected)):
        raise ValueError('Unknown or repeated selected case')
    ids = selected or ids
    output.mkdir(parents=True, exist_ok=False)
    run = {'schema': 'factory-local/clarification-eval-run/1', 'workflow_version': VERSION,
           'pack': str(pack), 'pack_version': manifest['version'], 'pack_manifest_sha256': digest(pack / 'manifest.json'),
           'oracle_status': manifest['oracle_status'], 'cases': []}
    skill = pack.parent
    for cid in ids:
        case = pack / 'cases' / cid
        meta, oracle = read(case / 'case.json'), read(case / 'oracle.json')
        if oracle.get('schema') != 'factory-clarification/oracle/1':
            raise ValueError('Unknown oracle schema')
        for field in ('requirements', 'questions'):
            rows = oracle.get(field)
            if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows) or len({r['id'] for r in rows}) != len(rows):
                raise ValueError('Invalid oracle rows')
        if any(type(q.get('required')) is not bool for q in oracle['questions']):
            raise ValueError('Oracle question required must be boolean')
        if (not oracle['requirements'] and not any(q['required'] for q in oracle['questions'])
                and oracle.get('require_source_conflict') is not True):
            raise ValueError('Oracle must declare an observable rule, required question or conflict')
        if type(oracle.get('require_source_conflict', False)) is not bool:
            raise ValueError('Oracle source conflict flag must be boolean')
        if any(not isinstance(r.get('criterion'), str) or not r['criterion'].strip()
               for field in ('requirements', 'questions') for r in oracle[field]):
            raise ValueError('Oracle criteria must be explicit nonempty text')
        if meta.get('id') != cid or oracle.get('case_id') != cid:
            raise ValueError('Case/oracle identity mismatch')
        work = output / cid / 'work'
        work.mkdir(parents=True)
        for path in (case / 'input').rglob('*'):
            if path.is_symlink(): raise ValueError('Case inputs cannot be symlinks')
            if path.is_file():
                destination = work / path.relative_to(case / 'input')
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        task = work / 'task'
        task.mkdir()
        request = work / 'request.md'
        (task / 'requirement.md').write_text((work / 'context.md').read_text())
        binding = {'workflow_version': VERSION, 'task_id': 'local-' + dt.datetime.now().strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:8],
                   'request_sha256': digest(request)}
        sources = capture(work, read(work / 'policy.json'), binding)
        write(task / 'business-sources.snapshot.json', sources)
        expected = {'schema': CLARIFICATION, **binding, 'requirement_sha256': digest(task / 'requirement.md'),
                    'answers_sha256': None, 'business_sources_sha256': digest(task / 'business-sources.snapshot.json')}
        write(work / 'bindings.json', expected)
        # Only the node's relevant method is given to the generator; no oracle.
        for rel in ('SKILL.md', 'references/protocol.md', 'references/business-rules.md', 'references/scenarios.md'):
            target = work / 'skill' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file(skill, rel), target)
        write(work / 'case-context.json', meta)
        inputs = {p.relative_to(work).as_posix(): digest(p) for p in work.rglob('*') if p.is_file()}
        run['cases'].append({'id': cid, 'workspace': str(work), 'inputs': inputs,
                             'oracle_sha256': digest(case / 'oracle.json'), 'case_sha256': digest(case / 'case.json')})
    write(output / 'run.json', run)
    return run


def exact_rows(rows, expected, label):
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows)
            or len({r['id'] for r in rows}) != len(rows) or {r['id'] for r in rows} != set(expected)):
        raise ValueError('Incomplete/unknown/duplicate judgement rows: ' + label)
    if any(not isinstance(r.get('reason'), str) or not r['reason'].strip() for r in rows):
        raise ValueError('Judgement needs reasons: ' + label)
    return {r['id']: r for r in rows}


def judge(candidate, oracle, judgement, expected):
    if (judgement.get('schema') != 'factory-local/clarification-judgement/1'
            or any(judgement.get(k) != v for k, v in expected.items())
            or not isinstance(judgement.get('reviewer'), str) or not judgement['reviewer'].strip()):
        raise ValueError('Missing reviewer or stale judgement binding')
    rules = {r['id']: r for r in candidate['rules']}
    questions = {q['id']: q for q in candidate['open_questions']}
    wanted = {r['id']: r for r in oracle['requirements']}
    gold_questions = {q['id']: q for q in oracle['questions']}
    requirements = exact_rows(judgement.get('requirements'), wanted, 'requirements')
    decisions = exact_rows(judgement.get('decisions'), rules, 'decisions')
    qjudged = exact_rows(judgement.get('questions'), questions, 'questions')
    assumptions = {a['id']: a for a in candidate['assumptions']}
    ajudged = exact_rows(judgement.get('assumptions'), assumptions, 'assumptions')
    issues, covered, matched, unnecessary, invented, uncertain = [], 0, set(), 0, 0, 0
    for rid, row in requirements.items():
        if row.get('verdict') not in {'covered', 'missed', 'uncertain'}: raise ValueError('Invalid requirement verdict')
        refs = row.get('rule_ids')
        if not isinstance(refs, list) or not set(refs).issubset(rules): raise ValueError('Unknown candidate rule')
        if row['verdict'] == 'covered':
            if not refs or not any(rules[i]['source']['kind'] == wanted[rid]['source_kind'] for i in refs):
                raise ValueError('Covered verdict lacks a candidate rule with the expected source category')
            covered += 1
        else: issues.append('requirement:' + rid)
        uncertain += row['verdict'] == 'uncertain'
    for rid, row in decisions.items():
        if row.get('verdict') not in {'supported', 'invented', 'uncertain'}: raise ValueError('Invalid decision verdict')
        invented += row['verdict'] == 'invented'
        uncertain += row['verdict'] == 'uncertain'
        if row['verdict'] != 'supported': issues.append('decision:' + rid)
    for qid, row in qjudged.items():
        if row.get('verdict') not in {'needed', 'redundant', 'engineering', 'uncertain'}: raise ValueError('Invalid question verdict')
        refs = row.get('gold_ids')
        if not isinstance(refs, list) or not set(refs).issubset(gold_questions): raise ValueError('Unknown gold question')
        if row['verdict'] == 'needed':
            if not refs or questions[qid]['blocking'] is not True: raise ValueError('Needed business question must bind an oracle issue and block')
            matched.update(refs)
        else:
            unnecessary += row['verdict'] in {'redundant', 'engineering'}
            uncertain += row['verdict'] == 'uncertain'
            issues.append('question:' + qid)
    for aid, row in ajudged.items():
        if row.get('verdict') not in {'engineering', 'invented', 'uncertain'}: raise ValueError('Invalid assumption verdict')
        invented += row['verdict'] == 'invented'
        uncertain += row['verdict'] == 'uncertain'
        if row['verdict'] != 'engineering' or assumptions[aid]['affects_behavior'] is not False:
            issues.append('assumption:' + aid)
    missing = sorted(qid for qid, row in gold_questions.items() if row['required'] and qid not in matched)
    issues.extend('missing-question:' + qid for qid in missing)
    if oracle.get('require_source_conflict') and not candidate.get('source_conflicts'):
        issues.append('unreported-source-conflict')
    return {'status': 'proxy-passed' if not issues else 'failed', 'reviewer': judgement['reviewer'],
            'covered_requirements': covered, 'total_requirements': len(wanted), 'missing_questions': missing,
            'unnecessary_questions': unnecessary, 'invented_decisions': invented, 'uncertain': uncertain, 'issues': issues}


def score(output):
    output = output.resolve()
    run = read(output / 'run.json')
    if run.get('schema') != 'factory-local/clarification-eval-run/1' or run.get('workflow_version') != VERSION:
        raise ValueError('Unsupported evaluation run')
    cases = run.get('cases')
    if (not isinstance(cases, list) or not cases
            or any(not isinstance(c, dict) for c in cases)
            or len({safe_id(c.get('id')) for c in cases}) != len(cases)):
        raise ValueError('Missing, duplicate or invalid evaluation cases')
    pack = Path(run['pack'])
    if digest(pack / 'manifest.json') != run['pack_manifest_sha256']: raise ValueError('Case pack manifest changed')
    results = []
    for item in cases:
        cid, work = safe_id(item['id']), Path(item['workspace'])
        result = {'case_id': cid, 'status': 'failed'}
        try:
            if (work.resolve() != (output / cid / 'work').resolve() or work.is_symlink()
                    or not work.resolve().is_relative_to(output)): raise ValueError('Workspace escape')
            for name, expected in item['inputs'].items():
                if digest(source_file(work, name)) != expected: raise ValueError('Generator changed protected input: ' + name)
            extra = {p.relative_to(work).as_posix() for p in work.rglob('*') if p.is_file() or p.is_symlink()} - set(item['inputs']) - {'task/clarification-contract.json'}
            if extra: raise ValueError('Unauthorized generated files: ' + ','.join(sorted(extra)))
            case = pack / 'cases' / cid
            if digest(case / 'oracle.json') != item['oracle_sha256'] or digest(case / 'case.json') != item['case_sha256']:
                raise ValueError('Oracle/case changed after preparation')
            candidate_path = work / 'task/clarification-contract.json'
            if candidate_path.is_symlink(): raise ValueError('Candidate cannot be symlink')
            if not candidate_path.is_file():
                result['status'] = 'pending-generation'; results.append(result); continue
            checked = validate_clarification(work / 'task', work / 'request.md', read(work / 'bindings.json')['task_id'])
            result['protocol_status'] = checked['status']
            candidate, oracle = read(candidate_path), read(case / 'oracle.json')
            expected = {'case_id': cid, 'candidate_sha256': digest(candidate_path), 'oracle_sha256': item['oracle_sha256']}
            judgement_path = output / cid / 'judgement.json'
            if not judgement_path.is_file():
                result.update(status='pending-independent-judgement', judgement_binding=expected)
            else:
                result.update(judge(candidate, oracle, read(source_file(output, cid + '/judgement.json')), expected))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(status='failed', error=str(exc))
        results.append(result)
    status = ('failed' if any(r['status'] == 'failed' for r in results) else
              'incomplete' if any(r['status'] != 'proxy-passed' for r in results) else 'proxy-passed')
    report = {'schema': 'factory-local/clarification-eval-result/1', 'status': status,
              'scope': 'curated-proxy-evaluation', 'human_oracle_review': 'NOT_RUN',
              'comparison_with_superpowers': 'NOT_RUN', 'cases': results}
    write(output / 'result.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'score'])
    parser.add_argument('--pack', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            if not args.pack: parser.error('prepare requires --pack')
            run = prepare(args.pack, args.output, args.case)
            print(json.dumps({'status': 'prepared', 'output': str(args.output), 'cases': [c['id'] for c in run['cases']]}))
            return 0
        result = score(args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'proxy-passed' else 3 if result['status'] == 'incomplete' else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
