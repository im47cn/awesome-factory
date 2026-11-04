#!/usr/bin/env python3
"""Run configured checks without fixing source files; retain every exit code."""
import datetime as dt
import json
import os
import subprocess
import sys
import uuid
import hashlib
import shutil
from pathlib import Path

from .worktree import GeneratedTransaction, snapshot, changed_files
from .policy import relative_path
from .execution import RepositoryLease

ROOT = Path.cwd().resolve()


def dependency_fingerprint(root):
    config = json.loads((root / '.factory/factory-local.json').read_text())
    names = config.get('verification_dependency_paths', ['node_modules'])
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError('verification_dependency_paths 必须是相对路径数组')
    hasher = hashlib.sha256()
    for name in names:
        relative_path(name)
        base = root / name
        if any(path.is_symlink() for path in (base, *base.parents) if path.is_relative_to(root)):
            return None
        if not base.exists():
            hasher.update((name + ':missing').encode())
            continue
        paths = [base, *sorted(base.rglob('*'))] if base.is_dir() else [base]
        for path in paths:
            hasher.update(path.relative_to(root).as_posix().encode())
            hasher.update(str(path.lstat().st_mode).encode())
            if path.is_symlink():
                if not path.resolve().is_relative_to(base.resolve()) or not path.exists():
                    return None
                hasher.update(('symlink:' + os.readlink(path)).encode())
            elif path.is_file():
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        hasher.update(chunk)
            elif not path.is_dir():
                return None
    return hasher.hexdigest()


def command_context(root, argv):
    executable = shutil.which(argv[0])
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    environment = {k: v for k, v in environment.items() if not k.startswith('FACTORY_QUALITY_') and k != 'FACTORY_RUN_LEASE_TOKEN'}
    payload = {'source': snapshot(root),
               'config_sha256': hashlib.sha256((root / '.factory/factory-local.json').read_bytes()).hexdigest(),
               'runtime_lock_sha256': hashlib.sha256((root / '.factory/runtime-lock.json').read_bytes()).hexdigest(),
               'argv': argv, 'environment': environment,
               'dependencies': dependency_fingerprint(root),
               'executable_sha256': hashlib.sha256(Path(executable).read_bytes()).hexdigest() if executable else None}
    if payload['dependencies'] is None:
        return None
    # Only the digest is saved; environment variables (including credentials) are never serialized.
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    try:
        with RepositoryLease(ROOT, 'quality'):
            return execute_checks()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Factory 质量检查被阻断：' + str(exc), file=sys.stderr)
        return 2


def execute_checks():
    config = json.loads((ROOT / '.factory/factory-local.json').read_text())
    commands = config.get('quality_commands')
    if not isinstance(commands, list) or not commands:
        print('quality_commands missing', file=sys.stderr)
        return 2
    # Validate all configuration before executing any command.
    for item in commands:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not item['id'].replace('-', '').isalnum()
                or not isinstance(item.get('argv'), list) or not item['argv']
                or not all(isinstance(x, str) and x for x in item['argv'])):
            print('quality_commands invalid', file=sys.stderr)
            return 2
    directory = ROOT / 'artifacts/factory-local/quality' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    results = []
    for item in commands:
        log = directory / (item['id'] + '.log')
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        before = snapshot(ROOT)
        context = command_context(ROOT, item['argv'])
        transaction = GeneratedTransaction(ROOT, config.get('generated_files', []),
                                           directory / (item['id'] + '-generated'))
        restored = []
        integrity_error = None
        try:
            with log.open('w') as output:
                process = subprocess.run(item['argv'], cwd=ROOT, stdout=output,
                                         stderr=subprocess.STDOUT, timeout=900,
                                         env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            rc = process.returncode
        except subprocess.TimeoutExpired:
            rc = 124
        except OSError as exc:
            log.write_text(str(exc))
            rc = 127
        finally:
            try:
                restored = transaction.restore()
                unexpected = changed_files(before, snapshot(ROOT))
                if unexpected:
                    integrity_error = 'Unexpected source/index changes: ' + ', '.join(unexpected)
            except Exception as exc:
                integrity_error = f'Generated output restoration/integrity failed: {exc}'
        command_rc = rc
        if integrity_error:
            rc = 2
        result = {'id': item['id'], 'argv': item['argv'], 'exit_code': rc,
                  'command_exit_code': command_rc, 'generated_outputs': restored,
                  'integrity_error': integrity_error,
                  'input_fingerprint': context,
                  'output_fingerprint': command_context(ROOT, item['argv']),
                  'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
                  'status': 'PASSED' if rc == 0 else 'FAILED',
                  'started_at': started, 'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'log': str(log.relative_to(ROOT))}
        results.append(result)
        print(f"{item['id']}: {result['status']} (rc={rc})", flush=True)
        if integrity_error:
            # Leave unexpected changes visible, and do not run more commands on them.
            break
    binding = {
        'task_id': os.environ.get('FACTORY_QUALITY_TASK_ID'),
        'attempt_id': os.environ.get('FACTORY_QUALITY_ATTEMPT_ID'),
        'stage': os.environ.get('FACTORY_QUALITY_STAGE'),
    }
    supplied = [value is not None for value in binding.values()]
    if any(supplied) and not all(supplied):
        print('quality receipt binding incomplete', file=sys.stderr)
        return 2
    payload = {
        'schema': 'factory-local/quality/2',
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'results': results,
    }
    if all(supplied):
        payload.update(binding)
    receipt = directory / 'receipt.json'
    receipt.write_text(json.dumps(payload, indent=2) + '\n')
    print(f'receipt: {receipt}')
    return 0 if all(r['exit_code'] == 0 for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
