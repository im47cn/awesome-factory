"""Spec-driven checks. Evidence is an executor assertion, never proof of pixels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scope_result(root: Path, allowed: list[str], actual: list[str]) -> dict:
    """Check task-owned deltas only, including both ends of a rename/deletion."""
    def canonical(name):
        path = Path(name)
        if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
                or not (root / path).resolve().is_relative_to(root.resolve())):
            raise ValueError(f'Invalid scope path: {name!r}')
        # Keep lexical names as well: a symlink must not grant an alias to another file.
        return path.as_posix()

    permitted = {canonical(name) for name in allowed}
    files = sorted({canonical(name) for name in actual})
    unexpected = sorted(set(files) - permitted)
    return {'schema': 'factory-local/scope-result/1',
            'status': 'failed' if unexpected else 'passed',
            'allowed_files': sorted(permitted), 'files': files, 'unexpected_files': unexpected}


def evidence_request(task: Path, task_id: str, request_hash: str, source_hash: str, contract: dict) -> dict:
    return {'schema': 'factory-local/verification-evidence/1', 'task_id': task_id,
            'request_sha256': request_hash, 'source_sha256': source_hash,
            'spec_sha256': sha(task / 'spec.md'), 'contract_sha256': sha(task / 'spec-contract.json'),
            'executor': '', 'checks': [
                {'id': item['id'], 'environment': item['environment'], 'status': 'NOT_RUN',
                 'artifacts': []} for item in contract['verification'] if item['kind'] == 'manual']}


def read_evidence(expected: dict, evidence: Path | None, archive: Path,
                  *, receipt_bytes: bytes | None = None) -> tuple[dict, str | None]:
    """Reject unknown/duplicate IDs, environment substitution, stale or missing artifacts."""
    if evidence is None:
        return {}, '缺少按 Spec 编号填写的验收收据'
    try:
        receipt_bytes = evidence.read_bytes() if receipt_bytes is None else receipt_bytes
        receipt = json.loads(receipt_bytes)
        keys = ('schema', 'task_id', 'request_sha256', 'source_sha256', 'spec_sha256', 'contract_sha256')
        if not isinstance(receipt, dict) or any(receipt.get(k) != expected[k] for k in keys):
            raise ValueError('验收收据与任务、Spec 或当前源码不匹配')
        if not isinstance(receipt.get('executor'), str) or not receipt['executor'].strip():
            raise ValueError('缺少验收执行者')
        plan = {item['id']: item for item in expected['checks']}
        checks = receipt.get('checks')
        if not isinstance(checks, list):
            raise ValueError('验收 checks 必须为列表')
        results = {}
        copies = []
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError('验收检查项结构无效')
            cid = check.get('id')
            if cid not in plan or cid in results:
                raise ValueError(f'未知或重复的验证编号: {cid}')
            if check.get('environment') != plan[cid]['environment']:
                raise ValueError(f'{cid}: 验收环境与 Spec 不符，Mock 不能替代测试环境')
            status = check.get('status')
            if status not in {'passed', 'failed', 'NOT_RUN', 'pending', 'blocked'}:
                raise ValueError(f'{cid}: 验收状态无效')
            artifacts = check.get('artifacts', [])
            if not isinstance(artifacts, list) or (status in {'passed', 'failed'} and not artifacts):
                raise ValueError(f'{cid}: 已执行检查缺少实际证据')
            for item in artifacts:
                if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                    raise ValueError(f'{cid}: 证据路径无效')
                path = (evidence.parent / item['path']).resolve()
                if (Path(item['path']).is_absolute() or not path.is_relative_to(evidence.parent.resolve())
                        or not path.is_file() or not path.stat().st_size or sha(path) != item.get('sha256')):
                    raise ValueError(f'{cid}: 证据越界、缺失、为空或摘要不匹配')
                copies.append((path, f'{len(copies)}-{path.name}', item['sha256']))
            results[cid] = {**check, 'executor': receipt['executor'],
                            'receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest()}
        # Archive only after the complete receipt has validated; original snapshots remain immutable.
        archive.mkdir(parents=True, exist_ok=True)
        for path, name, expected_hash in copies:
            saved = archive / name
            shutil.copy2(path, saved)
            if sha(saved) != expected_hash:
                raise ValueError('证据在归档期间发生变化，拒绝使用不一致的证据')
        (archive / 'receipt.json').write_bytes(receipt_bytes)
        mapping = {'schema': 'factory-local/evidence-archive/1',
                   'receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
                   'files': [{'original_path': str(path.relative_to(evidence.parent.resolve())),
                              'archived_path': name, 'sha256': expected_hash}
                             for path, name, expected_hash in copies]}
        (archive / 'artifact-map.json').write_text(json.dumps(mapping, indent=2) + '\n')
        for row in results.values():
            row.update(evidence_archive=str(archive), artifact_map_sha256=sha(archive / 'artifact-map.json'))
        return results, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {}, str(exc)


def trusted_commands(config: dict) -> dict[tuple, dict]:
    commands = {}
    for item in [*config.get('quality_commands', []), *config.get('verification_commands', [])]:
        argv = item.get('argv') if isinstance(item, dict) else None
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError('可信验证命令配置无效')
        commands[tuple(argv)] = item
    return commands


def summarize(contract: dict, results: dict, *, quality_passed: bool, visual_status: str,
              needs_browser: bool) -> dict:
    plan = contract['verification']
    rows = [{**results.get(item['id'], {}), **item,
             'status': results.get(item['id'], {}).get('status', 'NOT_RUN')} for item in plan]
    required = [item for item in rows if item['required']]
    missing = [item['id'] for item in required if item['status'] not in {'passed', 'failed'}]
    failed = [item['id'] for item in required if item['status'] == 'failed']
    acs = []
    for ac in contract['acceptance']:
        refs = [item for item in required if item['id'] in ac['verification_ids']]
        passed = bool(refs) and all(item['status'] == 'passed' for item in refs)
        acs.append({'id': ac['id'], 'status': ('failed' if any(item['status'] == 'failed' for item in refs)
                                           else 'passed' if passed else 'pending'),
                    'verification_ids': ac['verification_ids']})
    browser = [item for item in required if item['kind'] == 'manual' and item.get('evidence_type') == 'browser'
               and item['environment'] in {'local', 'mock', 'test-environment'}]
    browser_status = ('failed' if any(item['status'] == 'failed' for item in browser)
                      else 'passed' if browser and all(item['status'] == 'passed' for item in browser)
                      else 'pending') if needs_browser else 'not_required'
    real = [item for item in required if item['environment'] == 'test-environment']
    integration = ('failed' if any(item['status'] == 'failed' for item in real)
                   else 'passed' if all(item['status'] == 'passed' for item in real) else 'not_run') if real else 'not_required'
    if not quality_passed or failed:
        status = 'failed'
    elif visual_status in {'blocked', 'failed'}:
        status = 'blocked'
    elif missing or any(ac['status'] != 'passed' for ac in acs) or browser_status == 'pending':
        status = 'pending_acceptance'
    else:
        status = 'completed'
    return {'schema': 'factory-local/verification-result/1', 'verification': rows, 'acceptance': acs,
            'missing_required': missing, 'failed_required': failed, 'browser_status': browser_status,
            'integration_status': integration, 'overall_status': status}
