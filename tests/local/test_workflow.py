"""Workflow regressions use actual stage execution in isolated Git repositories.

Only the model response is a fixture; stage bindings, commands, scope checks,
receipt archiving, recovery and completion decisions are the real runner.
"""
import datetime as dt
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
from unittest.mock import patch

from test_acceptance import FactoryCase, runner
from runtime.local.workflow import VERSION, validate_clarification, read_readiness, readiness_result
import runtime.local.workflow as workflow


class WorkflowTests(FactoryCase):
    def test_fresh_task_runs_every_new_stage_and_real_command_before_completion(self):
        self.request.write_text('src/allowed.ts 的 value 从 1 改为 2，其他源码保持原样。')
        self.write('.rules/skills/factory-routing.json', {'schema': 'factory-local/skill-routing/1',
                                                       'skills': {'fixture': {'always': True}, **{name: {'explicit_only': True} for name in self.config['workflow_skills'].values()}}})
        self.write('.rules/skills/fixture/SKILL.md', '---\nname: fixture\n---\nUse observable requirements.\n')
        self.argv = [runner.sys.executable, '-c',
                     "from pathlib import Path; assert Path('src/allowed.ts').read_text() == 'export const value = 2\\n'"]
        self.config['verification_commands'] = [{'id': 'behavior', 'argv': self.argv}]
        self.write('.factory/factory-local.json', self.config)
        calls = []
        prompts = {}
        def model(*args):
            stage = args[0]; calls.append(stage); prompts[stage] = args[1]
            if stage == 'requirement':
                (self.task / 'requirement.md').write_text('Expected value is explicitly 2')
            elif stage == 'clarification':
                self.clarify(self.task, self.request)
            elif stage == 'spec':
                inputs = json.loads((self.task / 'spec-input.json').read_text())
                self.contract = {**inputs, 'schema': 'factory-local/spec-contract/1', 'goal': 'value becomes 2',
                                 'scope': {'allowed_files': ['src/allowed.ts'], 'non_goals': ['No other source edits']},
                                 'acceptance': [{'id': 'AC-1', 'criterion': 'value is 2', 'clarification_ids': ['CL-1'],
                                                 'verification_ids': ['V-C']}],
                                 'verification': [{'id': 'V-C', 'kind': 'command', 'argv': self.argv,
                                                   'environment': 'local', 'required': True, 'status': 'NOT_RUN'}],
                                 'external_requirements': []}
                runner.write_json(self.task / 'spec-contract.json', self.contract)
                (self.task / 'spec.md').write_text(f"{inputs['task_id']} AC-1 src/allowed.ts")
            elif stage == 'implement':
                self.write('src/allowed.ts', 'export const value = 2\n')
            elif stage == 'review':
                return self.review_response(*args)
            else:
                self.fail('Unexpected model stage: ' + stage)
            return subprocess.CompletedProcess([], 0, '{}', '')
        with patch.object(runner, 'omp', side_effect=model):
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request)]), 0)
        self.assertEqual(calls, ['requirement', 'clarification', 'spec', 'implement', 'review'])
        self.assertEqual(self.result()['overall_status'], 'completed')
        for stage in ('clarification', 'review'):
            name = self.config['workflow_skills'][stage]
            self.assertIn((self.root / '.rules/skills' / name / 'SKILL.md').read_text(), prompts[stage])
        guide = json.loads((self.task / 'preflight-guide.json').read_text())
        self.assertEqual(guide['name'], self.config['workflow_skills']['preflight'])
        self.assertIn('# Factory 验收环境预检', guide['content'])
        events = [json.loads(line) for line in (self.task / 'state-events.jsonl').read_text().splitlines()]
        completed = [e['stage'] for e in events if e['status'] in {'completed', 'passed'}]
        for stage in ('clarification', 'preflight', 'review', 'verify'):
            self.assertIn(stage, completed)
        verification = json.loads((self.task / 'verification-result.json').read_text())
        self.assertEqual(verification['verification'][0]['exit_code'], 0)

    def test_old_receipt_without_shared_bindings_cannot_resume(self):
        self.initialize()
        path = self.task / 'stage-receipts.json'
        data = json.loads(path.read_text())
        data['clarification'].pop('workflow_capabilities')
        runner.write_json(path, data)
        with patch.object(runner, 'omp', side_effect=AssertionError('must block before model')):
            self.assertEqual(self.run_from('spec'), 2)
        self.assertIn('公共运行时或阶段 Skill 已升级', self.result()['reason'])

    def test_shared_code_drift_during_stage_is_recorded_as_blocked_not_uncaught(self):
        self.initialize()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            original_source = Path(self.config['upstream_path'])
            lock = json.loads((self.root / '.factory/runtime-lock.json').read_text())
            for name in lock['files']:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original_source / name, path)
            self.config['upstream_path'] = str(source)
            self.write('.factory/factory-local.json', self.config)
            value = json.loads((self.task / 'clarification-contract.json').read_text())
            model = self.clarity_model(value)
            def drift(*args):
                result = model(*args)
                path = source / 'runtime/local/docs_context.py'
                path.write_text(path.read_text() + '\n# concurrent change\n')
                return result
            with patch.object(runner, 'omp', side_effect=drift):
                self.assertEqual(self.run_from('clarification'), 2)
            self.assertEqual(self.result()['status'], 'failed')
            self.assertIn('公共运行时执行中已变化', self.result()['reason'])

    def run_from(self, stage, *extra):
        return runner.main(['--task', 'original', '--request-file', str(self.request),
                            '--from-stage', stage, *extra])

    def result(self):
        return json.loads((self.task / 'result.json').read_text())

    def clarity_model(self, value):
        def model(*args):
            self.assertEqual(args[0], 'clarification')
            self.assertNotIn('spec.log', str(args[2]))
            # A newly supplied operator answer changes only the answer binding.
            answers = self.task / 'clarification-answers.json'
            value['answers_sha256'] = runner.digest(answers) if answers.exists() else None
            runner.write_json(self.task / 'clarification-contract.json', value)
            return subprocess.CompletedProcess(['fixture model'], 0, '{}', '')
        return model

    def test_explicit_request_needs_no_extra_human_confirmation(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)) as model:
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 0)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.result()['resume_from'], 'spec')
        self.assertEqual(self.result()['status'], 'stage_completed')

    def test_blocking_question_stops_before_spec_and_is_recoverable(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['open_questions'] = [{'id': 'Q-1', 'question': 'Which business behavior?', 'blocking': True}]
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)) as model:
            self.assertEqual(self.run_from('clarification'), 3)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.result()['overall_status'], 'awaiting_clarification')
        self.assertEqual(self.result()['resume_from'], 'clarification')
        self.assertFalse((self.task / 'spec.md').exists())

    def test_unconfirmed_behavior_assumption_blocks_but_informational_assumption_does_not(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['assumptions'] = [{'id': 'AS-1', 'statement': 'Change business behavior', 'affects_behavior': False}]
        runner.write_json(self.task / 'clarification-contract.json', value)
        self.assertEqual(validate_clarification(self.task, self.request, runner.task_identity(self.task))['status'], 'passed')
        value['assumptions'][0]['affects_behavior'] = True
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 3)
        value['assumptions'][0]['affects_behavior'] = False
        runner.write_json(self.task / 'clarification-contract.json', value)
        self.assertEqual(validate_clarification(self.task, self.request, runner.task_identity(self.task))['status'], 'pending')

    def test_retry_cannot_silently_drop_a_previously_blocking_question(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['open_questions'] = [{'id': 'Q-1', 'question': 'Which behavior?', 'blocking': True}]
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 3)
        value['open_questions'] = []
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 3)
        self.assertEqual(self.result()['overall_status'], 'awaiting_clarification')
        self.assertIn('Q-1', self.result()['reason'])

    def test_model_cannot_create_a_fake_operator_confirmation_when_no_answer_exists(self):
        self.initialize()
        def model(*args):
            self.write('artifacts/factory-local/original/clarification-answers.json', {
                'schema': 'factory-local/clarification-answers/1', 'executor': 'invented human'})
        with patch.object(runner, 'omp', side_effect=model):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 2)
        self.assertIn('模型创建运行器输入', self.result()['reason'])
        self.assertFalse((self.task / 'clarification-answers.json').exists())

    def test_pending_question_resumes_only_with_matching_operator_answer(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['open_questions'] = [{'id': 'Q-1', 'question': 'Which behavior?', 'blocking': True}]
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 3)
        value['open_questions'] = []
        value['rules'][0]['source'] = {'kind': 'confirmation', 'answer_id': 'Q-1', 'quote': 'Confirmed behavior'}
        path = self.write('artifacts/answer-input.json', {
            'schema': 'factory-local/clarification-answers/1', 'workflow_version': VERSION,
            'task_id': runner.task_identity(self.task), 'request_sha256': runner.digest(self.request),
            'executor': 'human operator',
            'answers': [{'id': 'Q-1', 'question': 'Which behavior?', 'decision': 'Confirmed behavior'}]})
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request), '--resume',
                                          '--only-stage', 'clarification', '--clarification-answers', str(path)]), 0)
        self.assertEqual(self.result()['resume_from'], 'spec')

    def test_operator_answers_resolve_assumption_and_wrong_task_answers_are_rejected(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        answer = {'schema': 'factory-local/clarification-answers/1', 'workflow_version': VERSION,
                  'task_id': runner.task_identity(self.task), 'request_sha256': runner.digest(self.request),
                  'executor': 'human operator',
                  'answers': [{'id': 'Q-1', 'question': 'Which behavior?', 'decision': 'Explicit business decision'}]}
        path = self.write('artifacts/answer-input.json', answer)
        value['assumptions'] = [{'id': 'AS-1', 'statement': 'Business assumption', 'affects_behavior': True, 'confirmed_by': 'Q-1'}]
        value['rules'][0]['source'] = {'kind': 'confirmation', 'answer_id': 'Q-1', 'quote': 'Explicit business decision'}
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification',
                                           '--clarification-answers', str(path)), 0)
        answer['task_id'] = 'another task'; path.write_text(json.dumps(answer))
        with patch.object(runner, 'omp', side_effect=AssertionError('must not call model')):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification',
                                           '--clarification-answers', str(path)), 2)

    def test_source_code_cannot_be_the_authority_for_expected_behavior(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['rules'][0]['source'] = {'kind': 'source', 'quote': 'export const value = 1'}
        with patch.object(runner, 'omp', side_effect=self.clarity_model(value)):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 2)
        self.assertIn('不能从当前源码推导', self.result()['reason'])

    def test_model_cannot_rewrite_requirement_or_operator_answers(self):
        self.initialize()
        before = (self.task / 'requirement.md').read_bytes()
        def model(*_):
            (self.task / 'requirement.md').write_text('Self-approved changed business requirement')
        with patch.object(runner, 'omp', side_effect=model):
            self.assertEqual(self.run_from('clarification', '--only-stage', 'clarification'), 2)
        self.assertEqual((self.task / 'requirement.md').read_bytes(), before)

    def test_spec_cannot_omit_clarified_rule_or_use_unknown_mapping(self):
        self.initialize()
        for mode in ('omit', 'unknown'):
            with self.subTest(mode=mode):
                value = json.loads((self.task / 'clarification-contract.json').read_text())
                if mode == 'omit':
                    value['rules'].append({**value['rules'][0], 'id': 'CL-2'})
                runner.write_json(self.task / 'clarification-contract.json', value)
                binding = runner.spec_input(self.task, self.request)
                contract = {**self.contract, **{k: v for k, v in binding.items() if k != 'schema'}}
                if mode == 'unknown': contract['acceptance'][0]['clarification_ids'] = ['invented']
                runner.write_json(self.task / 'spec-input.json', binding)
                runner.write_json(self.task / 'spec-contract.json', contract)
                with self.assertRaises(ValueError):
                    runner.validate_spec(self.task, self.request, self.config)

    def test_missing_active_environment_stops_before_implementation(self):
        self.initialize()
        (self.task / 'environment-evidence.json').unlink()
        with patch.object(runner, 'omp', side_effect=AssertionError('implementation must not run')):
            self.assertEqual(self.run_from('preflight', '--only-stage', 'implement'), 3)
        self.assertEqual(self.result()['overall_status'], 'pending_environment')
        self.assertEqual(self.result()['resume_from'], 'preflight')
        self.assertFalse((self.task / 'implement.md').exists())

    def test_readiness_corruption_staleness_and_environment_substitution_block(self):
        self.initialize()
        path = self.task / 'environment-evidence.json'
        good = json.loads(path.read_text())
        for mode in ('expired', 'future', 'naive', 'task', 'spec', 'environment', 'hash', 'missing-artifact', 'duplicate', 'unknown'):
            with self.subTest(mode=mode):
                data = json.loads(json.dumps(good))
                if mode == 'expired': data['checked_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
                elif mode == 'future': data['checked_at'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
                elif mode == 'naive': data['checked_at'] = '2026-10-26T12:00:00'
                elif mode == 'task': data['task_id'] = 'foreign'
                elif mode == 'spec': data['spec_sha256'] = 'old spec'
                elif mode == 'environment': data['checks'][1]['environment'] = 'mock'
                elif mode == 'hash': data['checks'][0]['artifacts'][0]['sha256'] = 'wrong'
                elif mode == 'missing-artifact': data['checks'][0]['artifacts'] = []
                elif mode == 'duplicate': data['checks'].append(data['checks'][0])
                else: data['checks'][0]['id'] = 'unplanned resource'
                path.write_text(json.dumps(data))
                expected = runner.readiness_request(self.task, self.request, runner.task_identity(self.task),
                                                   runner.readiness_plan(self.task, self.contract, 'all'))
                checks, error = read_readiness(expected, path, self.task / ('bad-evidence-' + mode), max_age=86400)
                self.assertFalse(checks)
                self.assertTrue(error)

    def test_environment_receipt_uses_one_byte_snapshot_for_time_check_and_archiving(self):
        self.initialize()
        path = self.task / 'environment-evidence.json'; original_bytes = path.read_bytes()
        expected = runner.readiness_request(self.task, self.request, runner.task_identity(self.task),
                                           runner.readiness_plan(self.task, self.contract, 'all'))
        native = workflow.read_evidence
        def change_between_validation_and_archive(*args, **kwargs):
            data = json.loads(path.read_text())
            data['checked_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
            path.write_text(json.dumps(data))
            return native(*args, **kwargs)
        archive = self.task / 'frozen-environment'
        with patch('runtime.local.workflow.read_evidence', side_effect=change_between_validation_and_archive):
            rows, error = read_readiness(expected, path, archive, max_age=86400)
        self.assertIsNone(error)
        self.assertTrue(rows)
        self.assertEqual((archive / 'receipt.json').read_bytes(), original_bytes)

    def test_runtime_version_failure_blocks_before_implementation(self):
        self.initialize()
        override = {**self.config, 'preflight_commands': [{'id': 'python-too-old', 'argv': [runner.sys.executable, '--version'],
                                                         'minimum_version': [99, 0, 0]}]}
        with patch.object(runner, 'load_local_factory_config', return_value=override):
            self.assertEqual(self.run_from('preflight', '--only-stage', 'implement'), 3)
        self.assertEqual(self.result()['overall_status'], 'pending_environment')
        self.assertIn('python-too-old', self.result()['reason'])

    def test_node_v_prefix_is_parsed_and_required_patch_version_is_enforced(self):
        self.initialize()
        for version, expected_rc in [('v22.22.3', 0), ('v22.20.0', 3)]:
            override = {**self.config, 'preflight_commands': [{'id': 'node',
                'argv': [runner.sys.executable, '-c', f'print({version!r})'], 'minimum_version': [22, 22, 3]}]}
            with patch.object(runner, 'load_local_factory_config', return_value=override):
                self.assertEqual(self.run_from('preflight', '--only-stage', 'preflight'), expected_rc)

    def test_available_label_without_evidence_is_not_readiness(self):
        self.initialize()
        contract = {**self.contract, 'external_requirements': [
            {'id': 'permission', 'environment': 'mock', 'description': 'Menu permission', 'status': 'AVAILABLE'}]}
        plan = runner.readiness_plan(self.task, contract, 'all')
        result = readiness_result(plan, {}, [])
        self.assertEqual(result['status'], 'pending')
        self.assertIn('external:permission', result['missing'])

    def test_expired_environment_is_rechecked_before_final_quality(self):
        self.initialize()
        path = self.task / 'environment-evidence.json'; data = json.loads(path.read_text())
        data['checked_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
        path.write_text(json.dumps(data))
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 3)
        self.assertEqual(result['overall_status'], 'pending_environment')
        self.assertFalse(list((self.root / 'artifacts/factory-local/quality').glob('*')))

    def test_green_preflight_does_not_hide_later_runtime_version_drift(self):
        self.initialize()
        override = {**self.config, 'preflight_commands': [{'id': 'changed-runtime', 'argv': [runner.sys.executable, '--version'],
                                                         'minimum_version': [99, 0, 0]}]}
        with patch.object(runner, 'load_local_factory_config', return_value=override):
            rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 3)
        self.assertEqual(result['overall_status'], 'pending_environment')
        self.assertIn('changed-runtime', result['reason'])

    def test_environment_proof_can_be_supplied_and_resume_uses_real_preflight(self):
        self.initialize()
        proof = self.task / 'environment-evidence.json'
        saved = self.write('artifacts/environment-input.json', json.loads(proof.read_text()))
        self.write('artifacts/environment.txt', (self.task / 'environment.txt').read_text())
        proof.unlink()
        self.assertEqual(self.run_from('preflight', '--only-stage', 'preflight'), 3)
        self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request), '--resume',
                                      '--only-stage', 'preflight', '--readiness-evidence', str(saved)]), 0)
        self.assertEqual(self.result()['resume_from'], 'implement')

    def test_local_mock_scope_defers_real_environment_but_cannot_complete_real_acceptance(self):
        self.initialize()
        data = json.loads((self.task / 'environment-evidence.json').read_text())
        data['checks'] = [c for c in data['checks'] if c['environment'] == 'mock']
        runner.write_json(self.task / 'environment-evidence.json', data)
        proof = self.evidence()
        check = json.loads(proof.read_text())
        check['checks'] = [c for c in check['checks'] if c['environment'] == 'mock']
        proof.write_text(json.dumps(check))
        self.assertEqual(self.run_from('preflight', '--only-stage', 'preflight', '--acceptance-scope', 'local-mock'), 0)
        def model(*args):
            if args[0] == 'implement':
                (self.task / 'implement.md').write_text('No source edit needed')
                return subprocess.CompletedProcess([], 0, '{}', '')
            self.assertEqual(args[0], 'review')
            return self.review_response(*args)
        with patch.object(runner, 'omp', side_effect=model):
            rc = runner.main(['--task', 'original', '--request-file', str(self.request), '--resume',
                              '--verification-evidence', str(proof)])
        self.assertEqual(rc, 3)
        self.assertEqual(self.result()['overall_status'], 'pending_acceptance')
        self.assertEqual(self.result()['missing_required'], ['V-R'])
        self.assertEqual(self.result()['review_status'], 'passed')
        self.assertEqual(self.result()['integration_status'], 'not_run')
        self.assertEqual(self.result()['acceptance_scope'], 'local-mock')

    def test_high_review_issue_blocks_even_when_model_claims_passed_and_resolved(self):
        self.initialize()
        def model(*args):
            response = self.review_response(*args); data = json.loads(response.stdout)
            data.update(status='passed', blocking=False)
            data['findings'] = [{'id': 'R-1', 'severity': 'high', 'resolved': True, 'blocking': False,
                                 'description': 'Wrong business behavior',
                                 'evidence': [{'path': 'request.md', 'quote': self.request.read_text()}]}]
            response.stdout = json.dumps(data); return response
        with patch.object(runner, 'omp', side_effect=model):
            self.assertEqual(self.run_from('review'), 3)
        self.assertEqual(self.result()['overall_status'], 'pending_review')
        self.assertEqual(self.result()['resume_from'], 'review')
        self.assertFalse(list((self.root / 'artifacts/factory-local/quality').glob('*')))

    def test_review_gap_uncertainty_missing_ac_wrong_binding_or_invalid_json_cannot_pass(self):
        self.initialize()
        for mode in ('gap', 'uncertain', 'missing-ac', 'source', 'evidence', 'json'):
            with self.subTest(mode=mode):
                def model(*args):
                    response = self.review_response(*args); data = json.loads(response.stdout)
                    if mode in {'gap', 'uncertain'}: data['acceptance'][0]['verdict'] = mode
                    elif mode == 'missing-ac': data['acceptance'] = []
                    elif mode == 'source': data['source_sha256'] = 'wrong'
                    elif mode == 'evidence': data['acceptance'][0]['evidence'][0]['quote'] = 'invented quotation'
                    response.stdout = 'not JSON' if mode == 'json' else json.dumps(data)
                    return response
                with patch.object(runner, 'omp', side_effect=model):
                    rc = self.run_from('review', '--only-stage', 'review')
                self.assertEqual(rc, 3 if mode in {'gap', 'uncertain'} else 2)
                self.assertNotEqual(self.result()['overall_status'], 'completed')

    def test_review_is_session_free_read_only_and_has_no_implementation_plan(self):
        self.initialize()
        command = runner.build_omp_command('review', 'fixture prompt', 'fixture', self.config)
        self.assertIn('--no-session', command)
        self.assertEqual(command[command.index('--tools') + 1], 'read,grep')
        report = json.loads((self.task / 'review-result.json').read_text())
        context = Path(report['context'])
        for name in ('spec.md', 'implement.md', 'requirement.md'):
            self.assertFalse((context / name).exists())
        self.assertFalse((context / 'source/artifacts').exists())
        self.assertEqual((context / 'source/src/allowed.ts').read_text(), 'export const value = 1\n')

    def test_independent_reviewer_gets_full_original_operator_decisions(self):
        self.initialize()
        answer = {'schema': 'factory-local/clarification-answers/1', 'workflow_version': VERSION,
                  'task_id': runner.task_identity(self.task), 'request_sha256': runner.digest(self.request),
                  'executor': 'operator', 'answers': [{'id': 'Q-1', 'question': 'Which behavior?',
                                                     'decision': 'Confirmed behavior, with its original conditions and exceptions'}]}
        runner.write_json(self.task / 'clarification-answers.json', answer)
        clarity = json.loads((self.task / 'clarification-contract.json').read_text())
        clarity['answers_sha256'] = runner.digest(self.task / 'clarification-answers.json')
        runner.write_json(self.task / 'clarification-contract.json', clarity)
        binding = runner.spec_input(self.task, self.request)
        contract = {**self.contract, **{k: v for k, v in binding.items() if k != 'schema'}}
        runner.write_json(self.task / 'spec-input.json', binding)
        runner.write_json(self.task / 'spec-contract.json', contract)
        runner.validate_spec(self.task, self.request, self.config)
        attempt = runner.Attempt(self.task, self.request)
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            args = type('Args', (), {'model': 'fixture', 'stage_timeout': 10})()
            with patch.object(runner, 'omp', side_effect=self.review_response):
                report = runner.independent_review(args, self.task, self.request, contract, self.config)
            copied = Path(report['context']) / 'clarification-answers.json'
            self.assertEqual(copied.read_bytes(), (self.task / 'clarification-answers.json').read_bytes())
            self.assertIn('clarification-answers.json', report['manifest'])
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)

    def test_review_uses_task_entry_bytes_instead_of_attributing_preexisting_head_diff(self):
        self.write('src/allowed.ts', 'export const value = 41\n')
        self.initialize()
        def implement(*_):
            self.write('src/allowed.ts', 'export const value = 41\nexport const added = true\n')
            return subprocess.CompletedProcess([], 0, '{}', '')
        with patch.object(runner, 'omp', side_effect=implement):
            self.assertEqual(self.run_from('implement', '--only-stage', 'implement'), 0)
        with patch.object(runner, 'omp', side_effect=self.review_response):
            self.assertEqual(self.run_from('review', '--only-stage', 'review'), 0)
        context = Path(json.loads((self.task / 'review-result.json').read_text())['context'])
        self.assertEqual((context / 'before/src/allowed.ts').read_text(), 'export const value = 41\n')
        self.assertIn('+export const added = true', (context / 'task.diff').read_text())
        self.assertNotIn('-export const value = 1', (context / 'task.diff').read_text())
        self.assertIn('-export const value = 1', (context / 'head.diff').read_text())

    def test_review_cannot_write_source_and_preserves_evidence_of_violation(self):
        self.initialize()
        def model(*args):
            result = self.review_response(*args)
            self.write('src/allowed.ts', 'reviewer unexpectedly edited source')
            return result
        with patch.object(runner, 'omp', side_effect=model):
            self.assertEqual(self.run_from('review', '--only-stage', 'review'), 2)
        self.assertIn('分析阶段源码被改动', self.result()['reason'])
        self.assertEqual((self.root / 'src/allowed.ts').read_text(), 'reviewer unexpectedly edited source')

    def test_review_process_timeout_blocks_and_keeps_review_as_recovery_stage(self):
        self.initialize()
        def timeout(*args):
            return runner.run([runner.sys.executable, '-c', 'import time; time.sleep(10)'],
                              log=self.task / 'timeout.log', timeout=0.05)
        with patch.object(runner, 'omp', side_effect=timeout):
            self.assertEqual(self.run_from('review', '--only-stage', 'review'), 124)
        self.assertEqual(self.result()['overall_status'], 'failed')
        self.assertEqual(self.result()['resume_from'], 'review')
        self.assertEqual(self.result()['failure_code'], 'timeout')
        self.assertTrue(self.result()['retryable'])
        self.assertEqual((self.root / 'src/allowed.ts').read_text(), 'export const value = 1\n')

    def test_legacy_receipts_cannot_skip_new_workflow_checks(self):
        self.initialize()
        path = self.task / 'stage-receipts.json'; data = json.loads(path.read_text())
        data['classify'].pop('workflow_version')
        runner.write_json(path, data)
        with patch.object(runner, 'omp', side_effect=AssertionError('must not invoke model')):
            rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 2)
        self.assertIn('请从 classify', result['reason'])

    def test_green_quality_without_review_receipt_cannot_complete(self):
        self.initialize()
        path = self.task / 'stage-receipts.json'; data = json.loads(path.read_text()); data.pop('review')
        runner.write_json(path, data)
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 2)
        self.assertIn('review', result['reason'])

    def test_changed_review_context_blocks_even_if_source_and_root_receipt_are_unchanged(self):
        self.initialize()
        report = json.loads((self.task / 'review-result.json').read_text())
        (Path(report['context']) / 'source/src/allowed.ts').write_text('modified historical review context')
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 2)
        self.assertIn('审查上下文', result['reason'])

    def test_reviewer_covers_untracked_task_files_and_cannot_omit_actual_file(self):
        self.initialize()
        self.write('src/allowed.ts', 'export const value = 2\n')
        self.write('src/new.ts', 'export const created = true\n')
        self.assertEqual(self.git('ls-files', '--', 'src/new.ts'), b'')
        self.contract['scope']['allowed_files'].append('src/new.ts')
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        changes = json.loads((self.task / 'implement-changes.json').read_text())
        changes['content_before']['src/new.ts'] = {'kind': 'missing'}
        runner.write_json(self.task / 'implement-changes.json', changes)
        (self.task / 'implemented-files.txt').write_text('src/allowed.ts\nsrc/new.ts\n')
        # Record this as a new implementation, with its actual source binding.
        attempt = runner.Attempt(self.task, self.request)
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            args = type('Args', (), {'model': 'fixture', 'stage_timeout': 10})()
            def omit(*args):
                response = self.review_response(*args); data = json.loads(response.stdout)
                data['reviewed_files'] = []; response.stdout = json.dumps(data); return response
            with patch.object(runner, 'omp', side_effect=omit):
                with self.assertRaisesRegex(ValueError, '全部实际变更'):
                    runner.independent_review(args, self.task, self.request, self.contract, self.config)
            context = json.loads((self.task / 'review-input.json').read_text())['context']
            self.assertEqual((Path(context) / 'source/src/allowed.ts').read_text(), 'export const value = 2\n')
            self.assertEqual((Path(context) / 'source/src/new.ts').read_text(), 'export const created = true\n')
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
