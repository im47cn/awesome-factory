"""Repair feedback uses validator-accepted reports and immutable source bindings."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from test_acceptance import FactoryCase
from support import runner


class SpecFeedbackTests(FactoryCase):
    def validated_report(self, gap=True, extra=False):
        # Real builder + validator, fixture OMP; preserve frozen context/outputs.
        self.initialize()
        report = json.loads((self.task/'spec-review-result.json').read_text())
        context = Path(report['context'])
        expected = json.loads((context.parent/'outputs/spec-review-input.json').read_text())
        expected.pop('context'); expected.pop('manifest')
        if gap:
            report['acceptance'][0].update(verdict='gap', reason='Missing protected-file evidence')
            report['findings'] = [{'id': 'F-1', 'severity': 'high',
                'description': 'Add verifiable scope checks',
                'evidence': [{'path': 'request.md', 'quote': self.request.read_text()}]}]
        if extra:
            report['acceptance'][0]['approved'] = True
            report['acceptance'][0]['evidence'][0]['status'] = 'passed'
            report['findings'][0]['status'] = 'approved'
        checked = runner.validate_spec_review(expected, report, context, ['RQ-1'], ['CL-1'], ['AC-1'])
        runner.write_json(self.task/'spec-review-result.json', checked)
        archived = context.parent/'outputs/spec-review-result.json'
        runner.write_json(archived, checked)
        return checked, archived

    def current_attempt(self, source):
        attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'spec-review'})
        target = attempt.directory/'superseded/spec-review-result.json'
        target.parent.mkdir()
        shutil.copy2(source, target)
        return attempt, target

    def revision_attempt(self, report, source):
        task = self.root/'artifacts/factory-local/child'
        task.mkdir()
        directory = task/'attempts/repair'
        directory.mkdir(parents=True)
        attempt = SimpleNamespace(task=task, directory=directory, id='repair',
            task_id=runner.task_identity(task), request_sha256=runner.digest(self.request))
        runner.write_json(task/'revision.json', {'parent_task_id': report['task_id'],
            'historical_evidence': [{'path': source.relative_to(self.root/'artifacts/factory-local').as_posix(),
                                     'sha256': runner.digest(source)}]})
        return attempt

    def test_spec_retry_receives_only_validated_diagnostic_fields(self):
        _, source = self.validated_report(extra=True)
        attempt, _ = self.current_attempt(source)
        runner.write_json(self.task/'revision.json', {'required_verification': [
            {'id': 'V-LEGACY-REQUIRED', 'kind': 'manual', 'environment': 'test-environment'}]})
        prompts = []
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            with patch.object(runner, 'omp', side_effect=lambda stage, prompt, *args: (
                    prompts.append(prompt) or subprocess.CompletedProcess([], 0, '{}', ''))):
                runner.run_model_stage('spec', 'Write current Spec', self.task/'spec.log', 'fixture', 10, self.config)
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
        feedback = attempt.directory/'spec-review-feedback.json'
        payload = json.loads(feedback.read_text())
        self.assertIn(str(feedback), prompts[0])
        self.assertIn('V-LEGACY-REQUIRED', prompts[0])
        self.assertIn('NOT_RUN', prompts[0])
        self.assertEqual(payload['reports'][0]['gaps']['requirement_coverage'], [])
        self.assertEqual(payload['reports'][0]['gaps']['acceptance'][0]['id'], 'AC-1')
        self.assertNotIn('"approved"', json.dumps(payload))
        self.assertNotIn('"status"', json.dumps(payload))
        self.assertIn('not business authority', payload['role'])

    def test_model_cannot_rewrite_feedback_source_or_frozen_context(self):
        report, archived = self.validated_report()
        attempt, source = self.current_attempt(archived)
        original = source.read_bytes()
        context_file = Path(report['context'])/'request.md'
        context_original = context_file.read_bytes()
        def mutate(*args):
            source.write_text('{}')
            context_file.write_text('fake request')
            (attempt.directory/'spec-review-feedback.json').write_text('{}')
            return subprocess.CompletedProcess([], 0, '{}', '')
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            with patch.object(runner, 'omp', side_effect=mutate):
                with self.assertRaisesRegex(ValueError, '阶段输入完整性失败'):
                    runner.run_model_stage('spec', 'Write', self.task/'spec.log', 'fixture', 10, self.config)
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(context_file.read_bytes(), context_original)

    def test_revision_feedback_revalidates_full_report_and_hash(self):
        report, source = self.validated_report()
        attempt = self.revision_attempt(report, source)
        self.assertEqual(runner.spec_review_feedback(attempt)['reports'][0]['originating_task_id'], report['task_id'])
        source.write_text('{}')
        with self.assertRaisesRegex(ValueError, '摘要已改变'):
            runner.spec_review_feedback(attempt)

    def test_historical_feedback_from_another_request_is_not_reused(self):
        report, source = self.validated_report()
        attempt = self.revision_attempt(report, source)
        attempt.request_sha256 = '0'*64
        self.assertIsNone(runner.spec_review_feedback(attempt))

    def test_wrong_parent_is_rejected(self):
        report, source = self.validated_report()
        attempt = self.revision_attempt(report, source)
        revision = json.loads((attempt.task/'revision.json').read_text())
        revision['parent_task_id'] = 'wrong-parent'
        runner.write_json(attempt.task/'revision.json', revision)
        with self.assertRaisesRegex(ValueError, '父任务绑定'):
            runner.spec_review_feedback(attempt)

    def test_historical_reference_cannot_escape_task_root(self):
        report, source = self.validated_report()
        attempt = self.revision_attempt(report, source)
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory)/'spec-review-result.json'
            outside.write_text('{}')
            runner.write_json(attempt.task/'revision.json', {'parent_task_id': report['task_id'],
                'historical_evidence': [{'path': str(outside), 'sha256': runner.digest(outside)}]})
            with self.assertRaisesRegex(ValueError, '越界'):
                runner.spec_review_feedback(attempt)

    def test_incomplete_current_report_cannot_suppress_valid_history(self):
        report, source = self.validated_report()
        attempt = self.revision_attempt(report, source)
        target = attempt.directory/'superseded/spec-review-result.json'
        target.parent.mkdir()
        runner.write_json(target, {'schema': 'factory-local/spec-review-result/1',
            'task_id': attempt.task_id, 'request_sha256': attempt.request_sha256})
        with self.assertRaisesRegex(ValueError, '只读上下文'):
            runner.spec_review_feedback(attempt)

    def test_current_report_requires_archived_bytes_and_complete_evidence(self):
        report, archived = self.validated_report()
        attempt, source = self.current_attempt(archived)
        report['acceptance'][0].pop('evidence')
        runner.write_json(source, report)
        with self.assertRaisesRegex(ValueError, '原始归档输出'):
            runner.spec_review_feedback(attempt)
        shutil.copy2(source, archived)
        with self.assertRaisesRegex(ValueError, '缺少证据'):
            runner.spec_review_feedback(attempt)

    def test_current_review_supersedes_historical_gaps(self):
        old_report, old_source = self.validated_report()
        self.task = self.root/'artifacts/factory-local/new-current'
        self.task.mkdir()
        self.request = self.write('artifacts/factory-local/new-current/request.md', self.request.read_text())
        _, current = self.validated_report(gap=False)
        attempt, _ = self.current_attempt(current)
        runner.write_json(self.task/'revision.json', {'parent_task_id': old_report['task_id'],
            'historical_evidence': [{'path': old_source.relative_to(self.root/'artifacts/factory-local').as_posix(),
                                    'sha256': runner.digest(old_source)}]})
        self.assertIsNone(runner.spec_review_feedback(attempt))
