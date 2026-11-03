"""Explicitly link a new request to an immutable task history. Never inherit passes."""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
from pathlib import Path
import shlex
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required_verification(task: Path) -> list[dict]:
    path = task / 'spec-contract.json'
    if not path.exists():
        return []
    contract = json.loads(path.read_text())
    result = []
    for item in contract['verification']:
        if item.get('required') is not True:
            continue
        if (not isinstance(item.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', item['id'])
                or item.get('kind') not in {'command', 'manual'}
                or item.get('environment') not in {'local', 'mock', 'test-environment'}
                or (item.get('kind') == 'command' and 'evidence_type' in item)
                or item.get('evidence_type') not in {None, 'browser', 'integrity', 'other'}):
            raise ValueError('修订必验项 ID、类型或环境无效')
        result.append({key: item[key] for key in ('id', 'kind', 'environment', 'evidence_type') if key in item})
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('修订必验 ID 重复')
    return result


def validate_revision(root: Path, task: Path) -> None:
    path = task / 'revision.json'
    if not path.exists():
        return
    revision = json.loads(path.read_text())
    base = (root / 'artifacts/factory-local').resolve()
    parent = (base / revision['parent_task']).resolve()
    if parent == task or not parent.is_relative_to(base) or parent == base:
        raise ValueError('修订父任务路径无效')
    state = json.loads((parent / 'run-state.json').read_text())
    identity = json.loads((task / 'task-identity.json').read_text())
    if (revision.get('schema') != 'factory-local/revision/1'
            or revision['task_id'] != identity['task_id']
            or state.get('task_id') != revision['parent_task_id']
            or state.get('superseded_by', {}).get('task_id') != revision['task_id']
            or state['superseded_by'].get('revision_sha256') != sha(path)
            or sha(task / 'request.md') != revision['request_sha256']
            or sha(task / 'revision-source.json') != revision['source_snapshot_sha256']
            or revision['required_verification'] != required_verification(task / 'parent-snapshot')):
        raise ValueError('修订身份、请求或父子任务链接已改变')
    for name, expected in revision['parent_snapshot'].items():
        if sha(task / 'parent-snapshot' / name) != expected:
            raise ValueError('修订的历史输入快照已改变')


def create_revision(root, task, request, parent_name, reason, ceiling, evidence,
                    *, stages, identify, source_digest, snapshot, write_json, now, python, runner):
    if not reason or not reason.strip() or ceiling not in stages:
        raise ValueError('创建修订需明确 --revision-reason 和 --authorize-through，不继承授权')
    base = (root / 'artifacts/factory-local').resolve()
    parent = (base / parent_name).resolve()
    if parent == task or not parent.is_relative_to(base) or parent == base or not parent.is_dir():
        raise ValueError('修订父任务不存在或路径无效')
    if any((task / name).exists() for name in ('run-state.json', 'revision.json', 'stage-receipts.json', 'authorization.json')):
        raise ValueError('修订必须使用新的任务目录，拒绝覆盖既有任务')
    # Validate all referenced evidence before publishing any state transition.
    references = []
    for path in evidence:
        path = path.resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('修订引用必须为任务目录中的现有证据文件')
        references.append({'path': str(path.relative_to(base)), 'sha256': sha(path),
                           'role': 'historical-only; must be revalidated against new Spec/source'})
    with (parent / '.runner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('父任务仍在运行，拒绝追加修订') from exc
        previous = json.loads((parent / 'run-state.json').read_text())
        if previous.get('status') in {'running', 'superseded'} or previous.get('active_command'):
            raise ValueError('父任务正在运行或已有后续修订，拒绝覆盖当前状态')
        if identify(parent) != previous.get('task_id'):
            raise ValueError('父任务身份与状态不一致')
        if request.resolve() != (task / 'request.md').resolve():
            shutil.copy2(request, task / 'request.md')
        request = task / 'request.md'
        task_id = identify(task)
        archive = task / 'parent-snapshot'
        archive.mkdir()
        snapshots = {}
        for name in ('run-state.json', 'result.json', 'authorization.json', 'stage-receipts.json',
                     'spec.md', 'spec-contract.json'):
            if (parent / name).is_file():
                shutil.copy2(parent / name, archive / name)
                snapshots[name] = sha(archive / name)
        write_json(task / 'revision-source.json', snapshot(root))
        revision = {'schema': 'factory-local/revision/1', 'task_id': task_id,
                    'parent_task': str(parent.relative_to(base)), 'parent_task_id': previous['task_id'],
                    'created_at': now(), 'reason': reason, 'request_sha256': sha(request),
                    'source_sha256': source_digest(), 'source_snapshot_sha256': sha(task / 'revision-source.json'),
                    'parent_snapshot': snapshots,
                    'required_verification': required_verification(archive),
                    'historical_evidence': references, 'inherited_passes': [], 'restart_from': 'classify'}
        write_json(task / 'revision.json', revision)
        write_json(task / 'authorization.json', {
            'schema': 'factory-local/authorization/1', 'task_id': task_id,
            'request_sha256': sha(request), 'authorized_end_stage': ceiling,
            'grants': [{'time': now(), 'through': ceiling, 'source': 'explicit-revision-cli'}]})
        command = shlex.join([python, str(runner), '--task', str(task.relative_to(base)),
                              '--request-file', str(request), '--resume'])
        state = {'schema': 'factory-local/run-state/1', 'task_id': task_id, 'attempt_id': None,
                 'status': 'revision_ready', 'stage': 'classify', 'resume_from': 'classify',
                 'request_sha256': sha(request), 'authorized_end_stage': ceiling,
                 'parent_task_id': previous['task_id'], 'recovery_command': command,
                 'updated_at': now(), 'active_command': None}
        write_json(task / 'run-state.json', state)
        write_json(task / 'result.json', {**state, 'overall_status': 'revision_ready'})
        updated = {**previous, 'status': 'superseded', 'resume_from': None,
                   'previous_status': previous['status'], 'updated_at': now(),
                   'recovery_command': command,
                   'superseded_by': {'task_id': task_id, 'task': str(task.relative_to(base)),
                                     'revision_sha256': sha(task / 'revision.json')}}
        # The append-only event is also a recovery record if the final pointer write is interrupted.
        with (parent / 'state-events.jsonl').open('a') as output:
            output.write(json.dumps({'time': now(), 'stage': 'revision', 'status': 'superseded',
                                     'task_id': previous['task_id'], 'next': updated['superseded_by'],
                                     'recovery_command': command}, ensure_ascii=False) + '\n')
        write_json(parent / 'run-state.json', updated)
        old_result = json.loads((archive / 'result.json').read_text()) if (archive / 'result.json').exists() else {}
        write_json(parent / 'result.json', {**old_result, **updated, 'overall_status': 'superseded'})
        validate_revision(root, task)
        return state
