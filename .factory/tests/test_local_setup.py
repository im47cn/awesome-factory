"""Real CLI in temporary repos: installation, repeat checks, no overwrites."""
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[2]
TOOL = SOURCE / 'tools/factory-local-setup.py'


def fixture_repo(tmp_path):
    repo = tmp_path / 'frontend'
    repo.mkdir()
    (repo/'docs').mkdir()
    (repo/'docs/spec.md').write_text('# Project spec\n')
    (repo/'README.md').write_text('# Real frontend project\n')
    (repo/'.gitignore').write_text('node_modules/\n/artifacts/\n__pycache__/\n')
    (repo/'package.json').write_text(json.dumps({'scripts': {
        'typecheck': 'echo typecheck',
        'test:ci': 'echo tests',
        'coverage:gate': 'echo coverage gate',
    }}))
    (repo/'node_modules').mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', 'README.md', '.gitignore', 'package.json'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Setup Test', '-c', 'user.email=setup@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture'], check=True)
    (tmp_path / 'awesome-rules').symlink_to(SOURCE.parent / 'awesome-rules', target_is_directory=True)
    return repo


def invoke(repo, *args):
    return subprocess.run([sys.executable, str(TOOL), *args, str(repo)], capture_output=True, text=True,
                          timeout=120)


def test_install_check_and_existing_install_no_clobber(tmp_path):
    repo=fixture_repo(tmp_path)
    result=invoke(repo, 'init')
    assert result.returncode == 0, result.stdout+result.stderr
    cfg=json.loads((repo/'.factory/factory-local.json').read_text())
    assert cfg['local_runner']=='.factory/run.py'
    assert cfg['remote_side_effects'] is False
    assert cfg['quality_commands'] == [
        {'id': 'typecheck', 'argv': ['pnpm', 'run', 'typecheck']},
        {'id': 'test-ci', 'argv': ['pnpm', 'run', 'test:ci']},
        {'id': 'coverage-gate', 'argv': ['pnpm', 'run', 'coverage:gate']},
    ]
    assert (repo/'.factory/bridge.py').is_file()
    assert (repo/'.factory/runtime-lock.json').is_file()
    assert not (repo/'.factory/workflow.py').exists()
    assert not (repo/'.factory/dispatch.sh').exists()
    before=(repo/'.factory/run.py').read_bytes()
    again=invoke(repo, 'init')
    assert again.returncode == 1
    assert before==(repo/'.factory/run.py').read_bytes()
    result=subprocess.run([sys.executable, str(repo/'.factory/setup.py'), 'check', str(repo)],
                          capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout+result.stderr
    # No full upstream or runtime interpreter dependencies copied into the project.
    assert (repo/'.factory/tests/test_adapter.py').is_file()
    assert all('summary.json' in [p.name for p in attempt.iterdir()]
               for attempt in (repo/'artifacts/factory-local/onboarding').iterdir())


def test_existing_foreign_factory_is_preserved(tmp_path):
    repo=fixture_repo(tmp_path)
    (repo/'.factory').mkdir()
    file=repo/'.factory/owner.txt';file.write_text('user content')
    assert invoke(repo, 'init').returncode == 1
    assert file.read_text()=='user content'
    assert not (repo/'MISSION.md').exists()


def test_bootstrap_aggregate_quality_command_covers_unit_and_build(tmp_path):
    repo = fixture_repo(tmp_path)
    package = json.loads((repo/'package.json').read_text())
    package['scripts'].update({
        'quality:check': 'pnpm run test:unit && pnpm run build',
        'test:unit': 'echo unit',
        'build': 'echo build',
    })
    (repo/'package.json').write_text(json.dumps(package))
    result = invoke(repo, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = json.loads((repo/'.factory/factory-local.json').read_text())
    assert cfg['quality_commands'] == [
        {'id': 'quality-check', 'argv': ['pnpm', 'run', 'quality:check']},
        {'id': 'coverage-gate', 'argv': ['pnpm', 'run', 'coverage:gate']},
    ]


def test_init_rejects_missing_worktree_coverage_gate(tmp_path):
    repo = fixture_repo(tmp_path)
    package = json.loads((repo/'package.json').read_text())
    package['scripts'].pop('coverage:gate')
    (repo/'package.json').write_text(json.dumps(package))
    result = invoke(repo, 'init')
    assert result.returncode == 1
    assert 'frontend-unit-test-setup' in result.stdout + result.stderr
    assert not (repo/'.factory').exists()


def test_factory_quality_gate_propagates_coverage_gate_failure(tmp_path):
    repo = fixture_repo(tmp_path)
    package = json.loads((repo/'package.json').read_text())
    package['scripts']['coverage:gate'] = (
        f'{sys.executable} -c "raise SystemExit(9)"'
    )
    (repo/'package.json').write_text(json.dumps(package))
    result = invoke(repo, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    gate = subprocess.run(
        [sys.executable, str(repo/'.factory/quality-gate.py')],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    assert gate.returncode == 1, gate.stdout + gate.stderr
    receipt = max((repo/'artifacts/factory-local/quality').glob('*/receipt.json'))
    rows = json.loads(receipt.read_text())['results']
    coverage = next(row for row in rows if row['id'] == 'coverage-gate')
    assert coverage['exit_code'] == 9
    assert coverage['status'] == 'FAILED'


def test_check_rejects_config_drift_without_overwriting(tmp_path):
    repo=fixture_repo(tmp_path)
    assert invoke(repo, 'init').returncode == 0
    file=repo/'.factory/factory-local.json'
    file.write_text(file.read_text()+'\n')
    before=file.read_bytes()
    result=invoke(repo, 'check')
    assert result.returncode == 1
    assert before==file.read_bytes()


def test_quality_gate_preserves_failure_and_records_all_commands(tmp_path):
    repo=fixture_repo(tmp_path)
    factory=repo/'.factory';factory.mkdir()
    (factory/'factory-local.json').write_text(json.dumps({'quality_commands': [
        {'id':'fail', 'argv':[sys.executable,'-c','raise SystemExit(5)']},
        {'id':'pass', 'argv':[sys.executable,'-c','print("ok")']}]}))
    sys.path.insert(0, str(SOURCE / 'tests/local'))
    from support import prepare
    prepare(repo, json.loads((factory / 'factory-local.json').read_text()))
    result=subprocess.run([sys.executable,str(factory/'quality-gate.py')],capture_output=True,text=True,timeout=20)
    assert result.returncode == 1
    receipts=list((repo/'artifacts/factory-local/quality').glob('*/receipt.json'))
    rows=json.loads(receipts[0].read_text())['results']
    assert [r['exit_code'] for r in rows]==[5,0]
    assert [r['status'] for r in rows]==['FAILED','PASSED']
