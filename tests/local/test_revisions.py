"""Actual revision registration, restart and history checks in temporary repositories."""
import fcntl
import hashlib
import json
from unittest.mock import patch

from test_acceptance import FactoryCase, runner


class RevisionTests(FactoryCase):
    def register(self, *extra, grant=True):
        request = self.write('artifacts/factory-local/followup-input.md', 'Extend the Mock pagination verification')
        args = ['--task', 'followup', '--request-file', str(request), '--revision-of', 'original',
                '--revision-reason', 'User expanded the scope after native Mock failed']
        if grant:
            args += ['--authorize-through', 'verify']
        return runner.main([*args, *extra])

    def test_revision_preserves_attempts_and_links_current_pointer_without_inheriting_passes(self):
        self.initialize()
        history = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.task / 'attempts').rglob('*') if p.is_file()}
        original_state = (self.task / 'run-state.json').read_bytes()
        self.assertEqual(self.register('--revision-evidence', str(self.task / 'result.json')), 0)
        child = self.root / 'artifacts/factory-local/followup'
        self.assertEqual((child / 'parent-snapshot/run-state.json').read_bytes(), original_state)
        self.assertEqual(json.loads((child / 'revision.json').read_text())['inherited_passes'], [])
        self.assertFalse((child / 'stage-receipts.json').exists())
        state = json.loads((self.task / 'run-state.json').read_text())
        self.assertEqual(state['status'], 'superseded')
        self.assertIn('--task followup', state['recovery_command'])
        for path, digest in history.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_child_recovery_starts_from_classify_with_fresh_request_and_no_model_call(self):
        self.initialize()
        self.assertEqual(self.register(), 0)
        child = self.root / 'artifacts/factory-local/followup'
        with patch.object(runner, 'omp', side_effect=AssertionError('must not invoke model')):
            rc = runner.main(['--task', 'followup', '--request-file', str(child / 'request.md'),
                              '--resume', '--only-stage', 'classify'])
        self.assertEqual(rc, 0)
        self.assertTrue((child / 'classification.json').is_file())
        self.assertEqual(json.loads((child / 'run-state.json').read_text())['resume_from'], 'skill-discovery')

    def test_parent_resume_redirects_without_rewriting_history_or_state(self):
        self.initialize()
        self.assertEqual(self.register(), 0)
        before = (self.task / 'run-state.json').read_bytes()
        rc = runner.main(['--task', 'original', '--request-file', str(self.request), '--resume'])
        self.assertEqual(rc, 2)
        self.assertEqual((self.task / 'run-state.json').read_bytes(), before)

    def test_explicit_new_authorization_is_required(self):
        self.initialize()
        before = (self.task / 'run-state.json').read_bytes()
        self.assertEqual(self.register(grant=False), 2)
        self.assertEqual((self.task / 'run-state.json').read_bytes(), before)

    def test_running_parent_and_duplicate_child_are_rejected(self):
        self.initialize()
        with (self.task / '.runner.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.register(), 2)
        self.assertEqual(self.register(), 0)
        self.assertEqual(self.register(), 2)

    def test_tampered_revision_request_or_snapshot_cannot_resume(self):
        self.initialize()
        self.assertEqual(self.register(), 0)
        child = self.root / 'artifacts/factory-local/followup'
        request = child / 'request.md'
        original = request.read_bytes()
        request.write_text('unapproved different scope')
        args = ['--task', 'followup', '--request-file', str(request), '--resume', '--only-stage', 'classify']
        self.assertEqual(runner.main(args), 2)
        request.write_bytes(original)
        (child / 'parent-snapshot/spec.md').write_text('tampered history')
        self.assertEqual(runner.main(args), 2)

    def test_missing_or_outside_parent_is_rejected_without_marking_complete(self):
        self.initialize()
        self.assertEqual(self.register('--revision-of', '../outside'), 2)
        self.assertNotEqual(json.loads((self.task / 'run-state.json').read_text())['status'], 'completed')

    def test_child_spec_must_preserve_parent_required_checks_and_environment(self):
        self.initialize()
        self.assertEqual(self.register(), 0)
        child = self.root / 'artifacts/factory-local/followup'
        runner.write_json(child / 'classification.json', {'schema': 'factory-local/classification/4',
                          'scope': 'frontend', 'has_design': False, 'needs_browser_acceptance': True})
        runner.write_json(child / 'skill-selection.json', {'schema': 'factory-local/skill-selection/2',
                          'status': 'passed', 'policy': {'sha256': runner.digest(self.root / '.rules/skills/factory-routing.json')},
                          'selected': []})
        self.requirement(child, child / 'request.md')
        self.clarify(child, child / 'request.md')
        binding = runner.spec_input(child, child / 'request.md')
        contract = {**self.contract, **{k: v for k, v in binding.items() if k != 'schema'}}
        runner.write_json(child / 'spec-input.json', binding)
        (child / 'spec.md').write_text(f"{binding['task_id']} AC-1 src/allowed.ts")
        original = json.loads(json.dumps(contract))
        for mode in ('drop', 'optional', 'mock'):
            contract = json.loads(json.dumps(original))
            if mode == 'drop': contract['verification'].pop()
            elif mode == 'optional': contract['verification'][-1]['required'] = False
            else: contract['verification'][-1]['environment'] = 'mock'
            runner.write_json(child / 'spec-contract.json', contract)
            with self.assertRaisesRegex(ValueError, '修订不能丢弃父任务必验项'):
                runner.validate_spec(child, child / 'request.md', self.config)
        runner.write_json(child / 'spec-contract.json', original)
        self.assertEqual(runner.validate_spec(child, child / 'request.md', self.config)['status'], 'passed')
