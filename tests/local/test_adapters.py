"""Actual shared adapters in independent Git projects; no model/network/business edits."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from support import SOURCE, prepare
from runtime.local.bootstrap import load
from runtime.local.runner import STAGES, WORKFLOW_VERSION


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def project(self, name='repo'):
        root = self.root / name
        root.mkdir()
        (root / '.gitignore').write_text('artifacts/\n__pycache__/\n')
        (root / 'source.txt').write_text('unchanged user input\n')
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        config = {'workflow_version': WORKFLOW_VERSION, 'local_stage_order': STAGES,
                  'execution_mode': 'local', 'remote_side_effects': False, 'auto_commit': False,
                  'local_allowed_tools': ['read', 'write'],
                  'final_gate_cmd': 'python3 .factory/quality-gate.py',
                  'intent_routing': {'remote_handoff': {'enabled': True, 'executor': 'external',
                                        'local_runner_may_execute_remote_actions': False}},
                  'quality_commands': [{'id': 'probe', 'argv': [sys.executable, '-c', 'print("ok")']}]}
        prepare(root, config)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null',
                        '-c', 'user.name=Adapter Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        return root, config

    def invoke(self, root, entry='run.py', *args):
        return subprocess.run([sys.executable, str(root / '.factory' / entry), *args], cwd=root,
                              capture_output=True, text=True, timeout=30)

    def test_two_projects_load_independent_namespaces_and_commands(self):
        first, _ = self.project('first')
        second, cfg = self.project('second')
        cfg['quality_commands'][0]['argv'] = [sys.executable, '-c', 'raise SystemExit(7)']
        (second / '.factory/factory-local.json').write_text(json.dumps(cfg))
        a, b = load(first), load(second)
        self.assertNotEqual(a.__package__, b.__package__)
        self.assertEqual(a.ROOT, first)
        self.assertEqual(b.ROOT, second)
        self.assertEqual(self.invoke(first, 'quality-gate.py').returncode, 0)
        failed = self.invoke(second, 'quality-gate.py')
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertEqual((first / 'source.txt').read_text(), 'unchanged user input\n')

    def test_missing_runtime_or_bad_pin_refuses_before_task_state(self):
        root, cfg = self.project()
        for damage in ('missing', 'pin', 'api', 'list'):
            with self.subTest(damage=damage):
                path = root / '.factory/runtime-lock.json'
                original = path.read_bytes()
                lock = json.loads(original)
                if damage == 'missing':
                    cfg['upstream_path'] = 'absent-runtime'
                elif damage == 'pin':
                    lock['files']['runtime/local/bootstrap.py'] = '0' * 64
                elif damage == 'api':
                    lock['api_version'] = 'incompatible'
                else:
                    lock['files'].pop('runtime/local/workflow.py')
                (root / '.factory/factory-local.json').write_text(json.dumps(cfg))
                path.write_text(json.dumps(lock))
                result = self.invoke(root, 'run.py', '--task', 'should-not-exist', '--request-file', 'source.txt')
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((root / 'artifacts/factory-local/should-not-exist').exists())
                self.assertEqual((root / 'source.txt').read_text(), 'unchanged user input\n')
                path.write_bytes(original)
                cfg['upstream_path'] = str(SOURCE)

    def test_missing_changed_or_unconfigured_skill_is_blocked_without_state(self):
        root, cfg = self.project()
        path = root / '.rules/skills/factory-clarification/SKILL.md'
        original = path.read_bytes()
        for damage in ('missing', 'changed', 'unconfigured'):
            with self.subTest(damage=damage):
                if damage == 'missing':
                    path.unlink()
                elif damage == 'changed':
                    path.write_bytes(original + b'\nchanged method\n')
                else:
                    cfg['workflow_skills'].pop('review')
                    (root / '.factory/factory-local.json').write_text(json.dumps(cfg))
                result = self.invoke(root, 'run.py', '--check-runtime')
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((root / 'artifacts').exists())
                path.write_bytes(original)

    def test_pinned_test_helper_tamper_is_rejected_before_suite_execution(self):
        root, cfg = self.project()
        source = self.root / 'custom-runtime'
        for name in ('runtime/local', 'tools', 'tests/local', 'templates/local'):
            shutil.copytree(SOURCE / name, source / name, ignore=shutil.ignore_patterns('__pycache__'))
        (source / '.factory').mkdir()
        shutil.copy2(SOURCE / '.factory/guard.py', source / '.factory/guard.py')
        cfg['upstream_path'] = str(source)
        (root / '.factory/factory-local.json').write_text(json.dumps(cfg))
        shutil.copy2(SOURCE / 'templates/local/test.py', root / '.factory/test.py')
        for name in ('tests/local/support.py', 'tools/evaluate-spec-quality.py', 'tools/factory-local-workspace.py'):
            path = source / name; original = path.read_bytes()
            path.write_text('raise RuntimeError("unreviewed test dependency")\n')
            result = self.invoke(root, 'test.py')
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('公共回归输入已变化', result.stderr)
            self.assertNotIn('Ran ', result.stdout + result.stderr)
            path.write_bytes(original)

    def test_rule_case_drift_is_rejected_before_test_suite(self):
        root, _ = self.project()
        shutil.copy2(SOURCE / 'templates/local/test.py', root / '.factory/test.py')
        path = root / '.rules/skills/factory-spec-review/evals/behavior-cases.json'
        path.write_text('{}')
        result = self.invoke(root, 'test.py')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('Rules 回归夹具已变化', result.stderr)
        self.assertNotIn('Ran ', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
