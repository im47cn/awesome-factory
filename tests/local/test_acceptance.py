"""Exercise the actual runner in isolated Git repositories, without any model call."""
import hashlib
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SOURCE, runner, prepare
from runtime.local.acceptance import evidence_request, read_evidence, scope_result, summarize
from runtime.local.worktree import snapshot
from runtime.local.workflow import VERSION
FACTORY = SOURCE / 'runtime/local'


class FactoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.write('.gitignore', '/artifacts/\n__pycache__/\n')
        self.write('src/allowed.ts', 'export const value = 1\n')
        self.write('src/unrelated.ts', 'export const other = 1\n')
        self.write('.rules/skills/factory-routing.json', {'schema': 'fixture-policy'})
        self.write('MISSION.md', '## 周界（PERIMETER）\n- `.factory/`\n- `MISSION.md`\n')
        self.write('.factory/guard.py', (SOURCE / '.factory/guard.py').read_text())
        self.argv = [sys.executable, '-c', "print('actual verification ran')"]
        self.config = {
            'workflow_version': VERSION, 'local_stage_order': list(runner.STAGES),
            'execution_mode': 'local', 'remote_side_effects': False, 'auto_commit': False,
            'local_allowed_tools': ['read', 'write'], 'perimeter': ['.factory/', 'MISSION.md'],
            'intent_routing': {'remote_handoff': {'enabled': True, 'executor': 'external',
                                                'local_runner_may_execute_remote_actions': False}},
            'final_gate_cmd': f'{sys.executable} .factory/quality-gate.py', 'generated_files': [],
            'quality_commands': [{'id': 'quality', 'argv': [sys.executable, '-c', "print('quality')"]}],
            'verification_commands': [{'id': 'probe', 'argv': self.argv}],
            'preflight_commands': [{'id': 'runtime', 'argv': [sys.executable, '--version'], 'minimum_version': [3, 10, 0]}],
        }
        prepare(self.root, self.config)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Factory regression',
                 '-c', 'user.email=factory@example.invalid', 'commit', '-qm', 'baseline')
        self.addCleanup(patch.stopall)
        patch.object(runner, 'ROOT', self.root).start()
        patch.object(runner, 'AWESOME_RULES_ROOT', self.root / '.rules').start()
        # Default arguments are evaluated at import time; bind the fixture's policy explicitly.
        original = runner.load_local_factory_config
        patch.object(runner, 'load_local_factory_config',
                     lambda: original(self.root / '.factory/factory-local.json')).start()
        self.task = self.root / 'artifacts/factory-local/original'
        self.task.mkdir(parents=True)
        self.request = self.write('artifacts/factory-local/original/request.md', 'Verify behavior, no remote delivery')

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root)

    def initialize(self, *, dirty=False, command=None):
        if dirty:
            self.write('src/unrelated.ts', 'user preexisting edit\n')
        self.write('artifacts/factory-local/original/requirement.md', 'Observable behavior')
        self.clarify(self.task, self.request)
        binding = runner.spec_input(self.task, self.request)
        self.contract = {'schema': 'factory-local/spec-contract/1', **{k: v for k, v in binding.items() if k != 'schema'},
                         'goal': 'Verify behavior', 'scope': {'allowed_files': ['src/allowed.ts'], 'non_goals': ['No other edits']},
                         'verification': [
                             {'id': 'V-C', 'kind': 'command', 'argv': command or self.argv,
                              'environment': 'local', 'required': True, 'status': 'NOT_RUN'},
                             {'id': 'V-M', 'kind': 'manual', 'steps': ['Observe mock'],
                              'environment': 'mock', 'required': True, 'status': 'NOT_RUN'},
                             {'id': 'V-R', 'kind': 'manual', 'steps': ['Observe test environment'],
                              'environment': 'test-environment', 'required': True, 'status': 'NOT_RUN'}],
                         'acceptance': [{'id': 'AC-1', 'criterion': 'Observed result', 'clarification_ids': ['CL-1'],
                                         'verification_ids': ['V-C', 'V-M', 'V-R']}],
                         'external_requirements': []}
        runner.write_json(self.task / 'spec-input.json', binding)
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        (self.task / 'spec.md').write_text(f"{binding['task_id']} AC-1 src/allowed.ts")
        runner.validate_spec(self.task, self.request, self.config)
        runner.write_json(self.task / 'classification.json', {'schema': 'factory-local/classification/4',
                          'scope': 'frontend', 'has_design': False, 'needs_browser_acceptance': True})
        runner.write_json(self.task / 'skill-selection.json', {'schema': 'factory-local/skill-selection/2',
                          'status': 'passed', 'policy': {'sha256': runner.digest(self.root / '.rules/skills/factory-routing.json')}})
        before = snapshot(self.root)
        baseline = {'schema': 'factory-local/worktree-baseline/1', 'snapshot': before, 'legacy_scope_candidates': []}
        runner.write_json(self.task / 'worktree-baseline.json', baseline)
        (self.task / 'baseline-files.txt').write_text('')
        runner.write_json(self.task / 'implement-changes.json', {'files': [], 'stage_before': before, 'stage_after': before,
                                                               'baseline_sha256': runner.digest(self.task / 'worktree-baseline.json')})
        (self.task / 'implemented-files.txt').write_text('')
        (self.task / 'implement.md').write_text('No change required')
        auth = {'schema': 'factory-local/authorization/1', 'task_id': binding['task_id'],
                'request_sha256': binding['request_sha256'], 'authorized_end_stage': 'verify', 'grants': []}
        runner.write_json(self.task / 'authorization.json', auth)
        runner.write_json(self.task / 'execution-policy.json', {'workflow_version': VERSION,
                          'task_id': binding['task_id'], 'request_sha256': binding['request_sha256'], 'acceptance_scope': 'all'})
        attempt = runner.Attempt(self.task, self.request, auth)
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            changes = json.loads((self.task / 'implement-changes.json').read_text())
            changes['content_before'] = runner.capture_implementation_before(self.task, ['src/allowed.ts'], before)
            runner.write_json(self.task / 'implement-changes.json', changes)
            self.ready()
            for stage in ('classify', 'skill-discovery', 'requirement', 'clarification', 'spec'):
                runner.append_event(self.task / 'state-events.jsonl', stage, 'completed')
            runner.check_preflight(None, self.task, self.request, self.contract, self.config)
            runner.append_event(self.task / 'state-events.jsonl', 'preflight', 'completed')
            runner.append_event(self.task / 'state-events.jsonl', 'implement', 'completed')
            args = type('Args', (), {'model': 'fixture', 'stage_timeout': 10})()
            with patch.object(runner, 'omp', side_effect=self.review_response):
                runner.independent_review(args, self.task, self.request, self.contract, self.config)
            runner.append_event(self.task / 'state-events.jsonl', 'review', 'completed')
            attempt.finish('stage_completed', 0, resume_from='verify')
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)

    def clarify(self, task, request):
        runner.business_sources(task, request, self.config, freeze=True)
        runner.write_json(task / 'clarification-contract.json', {
            'schema': 'factory-local/clarification/2', 'workflow_version': VERSION,
            'task_id': runner.task_identity(task), 'request_sha256': runner.digest(request),
            'requirement_sha256': runner.digest(task / 'requirement.md'), 'answers_sha256': None,
            'business_sources_sha256': runner.digest(task / 'business-sources.snapshot.json'),
            'rules': [{'id': 'CL-1', 'given': 'Original request', 'when': 'User acts',
                       'then': 'Observed behavior', 'forbidden': 'No extra effects',
                       'source': {'kind': 'request', 'quote': request.read_text()}}],
            'open_questions': [], 'assumptions': [], 'source_conflicts': []})
        runner.write_json(task / 'clarification-result.json',
                          runner.validate_clarification(task, request, runner.task_identity(task)))

    def ready(self, scope='all'):
        plan = runner.readiness_plan(self.task, self.contract, scope)
        value = runner.readiness_request(self.task, self.request, runner.task_identity(self.task), plan)
        proof = self.task / 'environment.txt'; proof.write_text('Checked endpoint, contract revision, test identity permissions')
        value.update(executor='fixture operator', checked_at=dt.datetime.now(dt.timezone.utc).isoformat())
        for check in value['checks']:
            check.update(status='passed', artifacts=[{'path': proof.name, 'sha256': runner.digest(proof)}],
                         details={'url': 'http://127.0.0.1:6277', 'observation': 'fixture checked',
                                  'version': 'fixture-v1', 'source': 'fixture-contract',
                                  'alias': 'fixture-test-account', 'permissions': 'with/without dead-letter menu verified'})
        runner.write_json(self.task / 'environment-evidence.json', value)

    def review_response(self, *args):
        report = json.loads((self.task / 'review-input.json').read_text())
        context = Path(report.pop('context')); report.pop('manifest')
        files = report.pop('files')
        proof = [{'path': 'request.md', 'quote': self.request.read_text()}]
        proof.extend({'path': 'source/' + name, 'quote': (context / 'source' / name).read_text()}
                     for name in (files or ['src/allowed.ts']))
        report.update(reviewed_files=files, findings=[], acceptance=[{
            'id': 'AC-1', 'verdict': 'covered', 'reason': 'Independent fixture checks original requirement',
            'evidence': proof}])
        return subprocess.CompletedProcess(['fixture reviewer'], 0, json.dumps(report), '')

    def evidence(self, checks=None):
        expected = evidence_request(self.task, runner.task_identity(self.task), runner.digest(self.request),
                                    runner.source_digest(), self.contract)
        proof = self.task / 'proof.txt'
        proof.write_text('Observed behavior and environment')
        expected['executor'] = 'isolated regression executor'
        expected['checks'] = checks if checks is not None else [
            {**item, 'status': 'passed', 'artifacts': [{'path': 'proof.txt', 'sha256': runner.digest(proof)}]}
            for item in expected['checks']]
        runner.write_json(self.task / 'evidence.json', expected)
        return self.task / 'evidence.json'

    def run_verify(self, evidence=None):
        args = ['--task', 'original', '--request-file', str(self.request), '--resume']
        if evidence:
            args += ['--verification-evidence', str(evidence)]
        rc = runner.main(args)
        return rc, json.loads((self.task / 'result.json').read_text())


class AcceptanceTests(FactoryCase):
    def test_generic_browser_pass_cannot_replace_required_checks(self):
        self.initialize()
        path = self.evidence([{'id': 'browser-regression', 'status': 'passed', 'environment': 'mock', 'artifacts': []}])
        rc, result = self.run_verify(path)
        self.assertEqual(rc, 3)
        self.assertEqual(result['overall_status'], 'pending_acceptance')
        self.assertEqual(result['missing_required'], ['V-M', 'V-R'])

    def test_missing_real_environment_blocks_completion(self):
        self.initialize()
        path = self.evidence()
        receipt = json.loads(path.read_text()); receipt['checks'].pop()
        runner.write_json(path, receipt)
        rc, result = self.run_verify(path)
        self.assertEqual(rc, 3)
        self.assertEqual(result['missing_required'], ['V-R'])
        self.assertEqual(result['integration_status'], 'not_run')

    def test_legacy_generic_browser_receipt_does_not_complete_task(self):
        self.initialize()
        proof = self.task / 'legacy-proof.txt'; proof.write_text('Observed mock only')
        path = self.task / 'legacy-evidence.json'
        runner.write_json(path, {'schema': 'factory-local/browser-acceptance/1',
            'task_id': runner.task_identity(self.task), 'request_sha256': runner.digest(self.request),
            'source_sha256': runner.source_digest(), 'status': 'passed',
            'checks': [{'id': 'browser-regression', 'status': 'passed',
                        'artifacts': [{'path': proof.name, 'sha256': runner.digest(proof)}]}]})
        rc = runner.main(['--task', 'original', '--request-file', str(self.request), '--resume',
                          '--browser-evidence', str(path)])
        self.assertEqual(rc, 3)
        result = json.loads((self.task / 'result.json').read_text())
        self.assertEqual(result['overall_status'], 'pending_acceptance')

    def test_all_required_checks_really_run_then_complete(self):
        self.initialize(dirty=True)
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 0)
        self.assertEqual(result['overall_status'], 'completed')
        verification = json.loads((self.task / 'verification-result.json').read_text())
        command = verification['verification'][0]
        self.assertEqual(command['exit_code'], 0)
        self.assertIn('actual verification ran', Path(command['artifacts'][0]['path']).read_text())
        manual = verification['verification'][1]
        archive = Path(manual['evidence_archive'])
        self.assertEqual(runner.digest(archive / 'artifact-map.json'), manual['artifact_map_sha256'])
        for item in json.loads((archive / 'artifact-map.json').read_text())['files']:
            self.assertEqual(runner.digest(archive / item['archived_path']), item['sha256'])
        self.assertEqual((self.root / 'src/unrelated.ts').read_text(), 'user preexisting edit\n')

    def test_failed_trusted_command_blocks_completion(self):
        command = [sys.executable, '-c', 'raise SystemExit(7)']
        self.config['verification_commands'].append({'id': 'failure', 'argv': command})
        self.write('.factory/factory-local.json', self.config)
        self.initialize(command=command)
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 1)
        self.assertEqual(result['overall_status'], 'failed')

    def test_model_command_is_not_executed_without_registry(self):
        marker = self.root / 'MUST_NOT_EXIST'
        self.initialize(command=[sys.executable, '-c', f"open({str(marker)!r}, 'w').write('bad')"])
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 3)
        self.assertIn('V-C', result['missing_required'])
        self.assertFalse(marker.exists())

    def test_evidence_environment_staleness_duplicates_and_artifacts_fail_closed(self):
        self.initialize()
        for mutation in ('environment', 'spec', 'duplicate', 'missing', 'hash', 'escape'):
            with self.subTest(mutation=mutation):
                path = self.evidence(); receipt = json.loads(path.read_text())
                if mutation == 'environment': receipt['checks'][1]['environment'] = 'mock'
                elif mutation == 'spec': receipt['spec_sha256'] = 'stale'
                elif mutation == 'duplicate': receipt['checks'].append(receipt['checks'][0])
                elif mutation == 'missing': receipt['checks'][0]['artifacts'] = []
                elif mutation == 'hash': receipt['checks'][0]['artifacts'][0]['sha256'] = 'wrong'
                else: receipt['checks'][0]['artifacts'][0]['path'] = '../proof.txt'
                runner.write_json(path, receipt)
                expected = evidence_request(self.task, runner.task_identity(self.task), runner.digest(self.request),
                                            runner.source_digest(), self.contract)
                rows, error = read_evidence(expected, path, self.task / 'audit-copies')
                self.assertEqual(rows, {})
                self.assertTrue(error)

    def test_receipt_cannot_override_spec_required_or_environment(self):
        self.initialize()
        rows = {'V-C': {'status': 'passed'}, 'V-M': {'status': 'passed'},
                'V-R': {'status': 'NOT_RUN', 'required': False, 'environment': 'mock'}}
        result = summarize(self.contract, rows, quality_passed=True, visual_status='not_required', needs_browser=True)
        self.assertEqual(result['missing_required'], ['V-R'])

    def test_artifact_changed_during_copy_is_rejected(self):
        self.initialize()
        path = self.evidence()
        expected = evidence_request(self.task, runner.task_identity(self.task), runner.digest(self.request),
                                    runner.source_digest(), self.contract)
        copy = shutil.copy2
        def tamper(source, target):
            copy(source, target)
            Path(target).write_text('changed during copy')
        with patch('runtime.local.acceptance.shutil.copy2', side_effect=tamper):
            rows, error = read_evidence(expected, path, self.task / 'racy-archive')
        self.assertEqual(rows, {})
        self.assertIn('归档期间', error)

    def test_implementation_blocks_out_of_scope_edit_before_quality(self):
        self.initialize(dirty=True)
        with patch.object(runner, 'run_model_stage', side_effect=lambda *args: self.write('src/unrelated.ts', 'unexpected task edit')):
            rc = runner.main(['--task', 'original', '--request-file', str(self.request),
                              '--from-stage', 'implement', '--only-stage', 'implement'])
        self.assertEqual(rc, 2)
        report = json.loads((self.task / 'scope-result.json').read_text())
        self.assertEqual(report['unexpected_files'], ['src/unrelated.ts'])
        self.assertEqual((self.root / 'src/unrelated.ts').read_text(), 'unexpected task edit')
        self.assertFalse(list((self.root / 'artifacts/factory-local/quality').glob('*')))

    def test_implementation_allows_scoped_edit_and_preserves_unrelated_dirty_file(self):
        self.initialize(dirty=True)
        with patch.object(runner, 'run_model_stage', side_effect=lambda *args: self.write('src/allowed.ts', 'task change')):
            rc = runner.main(['--task', 'original', '--request-file', str(self.request),
                              '--from-stage', 'implement', '--only-stage', 'implement'])
        self.assertEqual(rc, 0)
        self.assertEqual((self.task / 'implemented-files.txt').read_text(), 'src/allowed.ts\n')
        self.assertEqual((self.root / 'src/unrelated.ts').read_text(), 'user preexisting edit\n')

    def test_scope_includes_both_rename_ends_deletions_and_rejects_symlinks(self):
        report = scope_result(self.root, ['src/allowed.ts'], ['src/allowed.ts', 'src/renamed.ts'])
        self.assertEqual(report['unexpected_files'], ['src/renamed.ts'])
        (self.root / 'src/allowed.ts').unlink()
        self.assertEqual(scope_result(self.root, ['src/allowed.ts'], ['src/allowed.ts'])['status'], 'passed')
        (self.root / 'src/allowed.ts').symlink_to(self.root.parent / 'outside')
        with self.assertRaises(ValueError): scope_result(self.root, ['src/allowed.ts'], ['src/allowed.ts'])

    def test_stale_or_tampered_implemented_list_is_not_trusted(self):
        self.initialize()
        (self.task / 'implemented-files.txt').write_text('src/allowed.ts\n')
        rc, result = self.run_verify()
        self.assertEqual(rc, 2)
        self.assertEqual(result['overall_status'], 'failed')

    def test_model_cannot_expand_spec_scope_during_implement(self):
        self.initialize()
        original = (self.task / 'spec-contract.json').read_bytes()
        attempt = runner.Attempt(self.task, self.request, json.loads((self.task / 'authorization.json').read_text()))
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            with patch.object(runner, 'omp', side_effect=lambda *args: (self.task / 'spec-contract.json').write_text('{}')):
                with self.assertRaisesRegex(ValueError, '模型改写运行器输入'):
                    runner.run_model_stage('implement', 'fixture', self.task / 'model.log', 'unused', 1, self.config)
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
        self.assertEqual((self.task / 'spec-contract.json').read_bytes(), original)

    def test_staged_only_out_of_scope_change_cannot_hide_behind_unchanged_worktree(self):
        self.initialize()
        def change(*_args):
            self.write('src/unrelated.ts', 'unexpected staged content')
            self.git('add', 'src/unrelated.ts')
            self.write('src/unrelated.ts', 'export const other = 1\n')
        with patch.object(runner, 'run_model_stage', side_effect=change):
            rc = runner.main(['--task', 'original', '--request-file', str(self.request),
                              '--from-stage', 'implement', '--only-stage', 'implement'])
        self.assertEqual(rc, 2)
        report = json.loads((self.task / 'scope-result.json').read_text())
        self.assertEqual(report['unexpected_files'], ['src/unrelated.ts'])
        self.assertEqual(self.git('show', ':src/unrelated.ts'), b'unexpected staged content')

    def test_out_of_scope_delete_and_rename_are_detected_by_actual_runner(self):
        self.initialize()
        with patch.object(runner, 'run_model_stage', side_effect=lambda *_: (self.root / 'src/unrelated.ts').unlink()):
            rc = runner.main(['--task', 'original', '--request-file', str(self.request),
                              '--from-stage', 'implement', '--only-stage', 'implement'])
        self.assertEqual(rc, 2)
        self.assertIn('src/unrelated.ts', json.loads((self.task / 'scope-result.json').read_text())['unexpected_files'])

    def test_index_only_change_to_already_implemented_file_invalidates_prior_receipt(self):
        self.initialize()
        with patch.object(runner, 'run_model_stage', side_effect=lambda *_: self.write('src/allowed.ts', 'approved implementation')):
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request),
                                          '--from-stage', 'implement', '--only-stage', 'implement']), 0)
        prior_hash = runner.source_digest()
        self.write('src/allowed.ts', 'unverified staged content')
        self.git('add', 'src/allowed.ts')
        self.write('src/allowed.ts', 'approved implementation')
        self.assertNotEqual(prior_hash, runner.source_digest())
        rc, result = self.run_verify()
        self.assertEqual(rc, 2)
        self.assertEqual(result['overall_status'], 'failed')
        self.assertIn('编码后源码已改变', result['reason'])


if __name__ == '__main__':
    unittest.main()
