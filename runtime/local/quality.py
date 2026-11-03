#!/usr/bin/env python3
"""Run configured checks without fixing source files; retain every exit code."""
import datetime as dt
import json
import subprocess
import sys
import uuid
from pathlib import Path

from .worktree import GeneratedTransaction, snapshot, changed_files

ROOT = Path.cwd().resolve()


def main():
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
        transaction = GeneratedTransaction(ROOT, config.get('generated_files', []),
                                           directory / (item['id'] + '-generated'))
        restored = []
        integrity_error = None
        try:
            with log.open('w') as output:
                process = subprocess.run(item['argv'], cwd=ROOT, stdout=output,
                                         stderr=subprocess.STDOUT, timeout=900)
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
                  'status': 'PASSED' if rc == 0 else 'FAILED',
                  'started_at': started, 'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'log': str(log.relative_to(ROOT))}
        results.append(result)
        print(f"{item['id']}: {result['status']} (rc={rc})", flush=True)
        if integrity_error:
            # Leave unexpected changes visible, and do not run more commands on them.
            break
    receipt = directory / 'receipt.json'
    receipt.write_text(json.dumps({'schema': 'factory-local/quality/1', 'results': results}, indent=2) + '\n')
    print(f'receipt: {receipt}')
    return 0 if all(r['exit_code'] == 0 for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
