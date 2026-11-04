"""Regression invariants from the first-user rehearsal; no live model or network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SOURCE, runner
from test_acceptance import FactoryCase
from runtime.local.execution import RepositoryLease, freeze_runtime, summarize_attempt
from runtime.local.protocol import complete, ContractFormatError, reference_fields
from runtime.local.policy import relative_path, sensitive_path
from runtime.local.quality import command_context


class ProtocolTests(unittest.TestCase):
    def test_public_config_role_normalization_preserves_business_values(self):
        value = {'observations': [
            {'id': 'O-1', 'kind': 'source', 'statement': 'Tool versions only', 'source': {'path': 'package.json', 'quote': 'unchanged'}},
            {'id': 'O-2', 'kind': 'source', 'source': {'path': '.factory/factory-local.json', 'quote': 'reject'}}],
            'behavior_candidates': [{'id': 'RQ-1', 'then': 'original business result'}],
            'open_questions': [], 'skill_usage': [], 'acceptance_candidates': []}
        result, fields = complete('requirement', value, {})
        self.assertEqual(result['observations'][0]['kind'], 'docs')
        self.assertEqual(result['observations'][0]['source'], value['observations'][0]['source'])
        self.assertEqual(result['behavior_candidates'], value['behavior_candidates'])
        self.assertEqual(result['observations'][1]['kind'], 'source')
        self.assertEqual(value['observations'][0]['kind'], 'source')
        self.assertIn('observations.0.kind=docs(public-configuration)', fields)

    def spec(self):
        return {'acceptance': [], 'external_requirements': [], 'verification': [{'id': 'V-1'}]}

    def test_mechanical_completion_does_not_mutate_or_invent_business_content(self):
        value = self.spec()
        result, fields = complete('spec', value, {'task_id': 'local-1', 'source_sha256': 'trusted'})
        self.assertEqual(result['verification'][0]['status'], 'NOT_RUN')
        self.assertEqual(result['source_sha256'], 'trusted')
        self.assertNotIn('task_id', value)
        self.assertEqual(result['acceptance'], [])
        self.assertIn('verification.V-1.status', fields)

    def test_conflicting_bindings_and_self_reported_pass_never_get_repaired(self):
        for change in ({'task_id': 'other'}, {'verification': [{'id': 'V-1', 'status': 'passed'}]}):
            with self.subTest(change=change), self.assertRaises(ValueError) as context:
                complete('spec', {**self.spec(), **change}, {'task_id': 'local-1'})
            self.assertNotIsInstance(context.exception, ContractFormatError)

    def test_reference_completion_binds_selected_file_without_replacing_conflicts(self):
        value = {**self.spec(), 'references': [{'path': 'src/allowed.ts'}]}
        result, fields = complete('spec', value, {}, reference_hashes={'src/allowed.ts': 'file-digest'})
        self.assertEqual(result['references'], [{'path': 'src/allowed.ts', 'sha256': 'file-digest'}])
        self.assertNotIn('sha256', value['references'][0])
        self.assertIn('references.0.sha256', fields)
        value['references'][0]['sha256'] = 'whole-tree-digest'
        with self.assertRaises(ValueError) as context:
            complete('spec', value, {}, reference_hashes={'src/allowed.ts': 'file-digest'})
        self.assertNotIsInstance(context.exception, ContractFormatError)

    def test_missing_semantic_arrays_are_not_silently_empty(self):
        with self.assertRaises(ContractFormatError):
            complete('spec', {'verification': []}, {})

    def test_canonical_paths_and_nested_credentials(self):
        for name in ('/', '.', '../src', 'src/../docs', './docs', 'a//b', 'C:/secret', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                relative_path(name, directory=True)
        self.assertEqual(relative_path('.factory/', directory=True), '.factory')
        for name in ('nested/.aws/credentials', 'nested/.docker/config.json', 'nested/credentials.json', '.codex/auth.json'):
            self.assertTrue(sensitive_path(name), name)
        self.assertFalse(sensitive_path('src/credentials-form.vue'))

    def test_generated_rules_reference_matches_executable_definition(self):
        rules = Path(os.environ.get('FACTORY_TEST_RULES_ROOT', SOURCE.parent / 'awesome-rules'))
        for skill in ('factory-spec-authoring', 'factory-spec-review'):
            self.assertEqual((rules / 'skills' / skill / 'references/generated-contract.md').read_text(), reference_fields())


class IsolationTests(unittest.TestCase):
    def test_worktree_hook_failure_returns_recovery_receipt(self):
        spec = importlib.util.spec_from_file_location('workspace_hook_tool', SOURCE / 'tools/factory-local-workspace.py')
        tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / 'project'; project.mkdir()
            (project / '.factory').mkdir()
            (project / '.factory/factory-local.json').write_text(json.dumps({
                'upstream_path': str(SOURCE), 'rules_path': str(SOURCE.parent / 'awesome-rules')}))
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            subprocess.run(['git', 'add', '.'], cwd=project, check=True)
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline'], cwd=project, check=True)
            hook = project / '.git/hooks/post-checkout'
            hook.write_text('#!/bin/sh\necho WORKSPACE_HOOK_BLOCKED >&2\nexit 1\n'); hook.chmod(0o755)
            with self.assertRaises(tool.WorkspaceCreationError) as context:
                tool.create(project, root / 'isolated')
            receipt = context.exception.receipt
            self.assertEqual(receipt['status'], 'failed')
            self.assertEqual(receipt['workspace'], str((root / 'isolated').resolve()))
            self.assertIn('WORKSPACE_HOOK_BLOCKED', Path(receipt['git_log']).read_text())
            self.assertIn('worktree remove', receipt['recovery'])

    def test_isolated_workspace_preserves_source_and_has_independent_lease(self):
        spec = importlib.util.spec_from_file_location('workspace_tool', SOURCE / 'tools/factory-local-workspace.py')
        tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'; project.mkdir()
            (project / '.factory').mkdir()
            config = {'upstream_path': str(SOURCE), 'rules_path': str(SOURCE.parent / 'awesome-rules')}
            path = project / '.factory/factory-local.json'; path.write_text(json.dumps(config))
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            subprocess.run(['git', 'add', '.'], cwd=project, check=True)
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline'], cwd=project, check=True)
            original = path.read_bytes()
            result = tool.create(project, root / 'isolated')
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(subprocess.check_output(['git', 'status', '--porcelain'], cwd=project))
            self.assertFalse(result['pins_refreshed'])
            with RepositoryLease(project), RepositoryLease(root / 'isolated'):
                pass
            (project / 'dirty.txt').write_text('preserve me')
            with self.assertRaisesRegex(ValueError, '未提交内容'):
                tool.create(project, root / 'another')
            self.assertFalse((root / 'another').exists())
            (project / 'dirty.txt').unlink()
            (project / '.factory/installation.json').write_text('{bad JSON')
            subprocess.run(['git', 'add', '.'], cwd=project, check=True)
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'bad manifest fixture'], cwd=project, check=True)
            with self.assertRaises(ValueError):
                tool.create(project, root / 'bad-manifest')
            self.assertFalse((root / 'bad-manifest').exists())

    def test_lease_blocks_other_tasks_in_same_worktree_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            with RepositoryLease(root, 'one'):
                with self.assertRaisesRegex(ValueError, '独立 Git worktree'):
                    with RepositoryLease(root, 'two'):
                        self.fail('Concurrent writer entered')
            with RepositoryLease(root, 'two'):
                pass
            self.assertFalse((root / 'artifacts').exists())

    def test_quality_can_join_parent_but_an_independent_check_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            with RepositoryLease(root):
                with RepositoryLease(root, 'quality'):
                    pass
                with patch.dict(os.environ, {'FACTORY_RUN_LEASE_TOKEN': 'not-owner'}):
                    with self.assertRaises(ValueError):
                        with RepositoryLease(root, 'quality'):
                            self.fail('Independent quality entered')

    def test_runtime_copy_is_verified_and_original_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, source = root / 'project', root / 'source'
            project.mkdir(); source.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            (source / 'probe.py').write_text('VALUE = 1\n')
            lock = {'files': {'probe.py': runner.digest(source / 'probe.py')}}
            saved = freeze_runtime(project, source, lock)
            self.assertEqual((saved / 'probe.py').read_text(), 'VALUE = 1\n')
            (source / 'probe.py').write_text('VALUE = 2\n')
            with self.assertRaisesRegex(ValueError, '冻结运行时'):
                freeze_runtime(project, source, lock)
            self.assertEqual((saved / 'probe.py').read_text(), 'VALUE = 1\n')
            (source / 'probe.py').write_text('VALUE = 1\n')
            (saved / 'probe.py').write_text('corrupt')
            with self.assertRaises(ValueError):
                freeze_runtime(project, source, lock)

    def test_pending_is_reported_as_pending_not_command_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in [
                {'stage': 'spec-review', 'status': 'started', 'time': '2026-11-04T00:00:00+00:00'},
                {'stage': 'spec-review', 'status': 'pending_spec_review', 'time': '2026-11-04T00:00:02+00:00', 'reason': 'gap'}]))
            report = summarize_attempt(root, {'status': 'pending_spec_review', 'rc': 3}, [])
            self.assertEqual(report['stages'][0]['status'], 'pending_spec_review')
            self.assertEqual(report['stages'][0]['duration_seconds'], 2)
            self.assertEqual(report['counts']['failed_commands'], 0)


class ReliabilityRunnerTests(FactoryCase):
    def test_quarantine_replaces_output_links_without_overwriting_business_source(self):
        source = self.root / 'src/allowed.ts'; original = source.read_bytes()
        (self.task / 'requirement.md').symlink_to(source)
        (self.task / 'requirement-contract.json.tmp').symlink_to(source)
        runner.quarantine_requirement(self.task)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse((self.task / 'requirement.md').is_symlink())
        self.assertIn('已隔离', (self.task / 'requirement.md').read_text())
        parent = self.task / 'linked'; parent.symlink_to(source.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '父目录'):
            runner.atomic_write(parent / 'allowed.ts', b'must not write')
        self.assertEqual(source.read_bytes(), original)

    def test_looping_forbidden_source_is_quarantined_before_resolve(self):
        path = self.root / '.factory/loop'; path.symlink_to(path)
        runner.write_json(self.task / 'requirement-contract.json', {'observations': [
            {'kind': 'source', 'source': {'path': '.factory/loop', 'quote': 'FAKE_LOOP_NEVER_ARCHIVE'}}]})
        (self.task / 'requirement.md').write_text('FAKE_LOOP_NEVER_ARCHIVE')
        self.assertTrue(runner.quarantine_disallowed_requirement(self.task, self.request))
        self.assertNotIn('FAKE_LOOP_NEVER_ARCHIVE', (self.task / 'requirement-contract.json').read_text())

    def test_rejected_requirement_never_gets_an_unquarantined_raw_output_copy(self):
        self.initialize()
        binding = runner.requirement_input(self.task, self.request)
        contract = json.loads((self.task / 'requirement-contract.json').read_text())
        contract['observations'].append({'id': 'OBS-UNSAFE', 'kind': 'source',
            'statement': 'must be rejected', 'limitation': 'not business evidence',
            'source': {'path': '.factory/guard.py', 'quote': 'FAKE_RAW_NEVER_ARCHIVE'}})
        for mode in ('normal', 'binding-conflict', 'failed-before-completion'):
            attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'verify'})
            attempt.stage = 'requirement'
            token = runner.ACTIVE_ATTEMPT.set(attempt)
            args = type('Args', (), {'model': 'fixture', 'stage_timeout': 1})()
            def output(*_):
                value = {**contract, **({'task_id': 'conflict'} if mode == 'binding-conflict' else {})}
                runner.write_json(self.task / 'requirement-contract.json', value)
                (self.task / 'requirement.md').write_text('FAKE_RAW_NEVER_ARCHIVE')
            try:
                if mode == 'failed-before-completion':
                    output()
                else:
                    with patch.object(runner, 'run_model_stage', side_effect=output), self.assertRaises(ValueError):
                        runner.generate_contract_stage('requirement', 'fixture', self.task / 'requirement.log', args, self.config, binding)
                runner.append_event(self.task / 'state-events.jsonl', 'requirement', 'failed')
                attempt.finish('failed', 2)
            finally:
                runner.ACTIVE_ATTEMPT.reset(token)
            self.assertFalse(list(attempt.directory.glob('requirement-model-output-*.json')))
            for path in attempt.directory.rglob('*'):
                if path.is_file():
                    self.assertNotIn('FAKE_RAW_NEVER_ARCHIVE', path.read_text(errors='replace'))

    def test_extra_integrity_checks_use_separate_bound_machine_evidence(self):
        self.initialize()
        item = {'id': 'V-I', 'kind': 'manual', 'evidence_type': 'integrity', 'environment': 'local',
                'executor': 'source-integrity', 'required': True, 'steps': ['Compare scope and protected files']}
        contract = {**self.contract, 'verification': [*self.contract['verification'], item]}
        attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'verify'})
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            expected = runner.evidence_request(self.task, attempt.task_id, attempt.request_sha256, runner.source_digest(), contract)
            results = runner.automatic_manual_evidence(self.task, contract, expected, self.config, {})
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
        self.assertEqual(set(results), {'V-I'})
        self.assertEqual(results['V-I']['status'], 'passed')
        self.assertNotIn('V-M', results)
        proof = json.loads((attempt.directory / 'integrity/source-integrity.json').read_text())
        self.assertEqual(proof['scope']['files'], [])


    def test_ignored_dependency_change_invalidates_command_fingerprint(self):
        self.write('.git/info/exclude', 'node_modules/\n')
        path = self.write('node_modules/runner/index.js', 'before')
        before = command_context(self.root, self.argv)
        path.write_text('after')
        self.assertNotEqual(command_context(self.root, self.argv), before)
        with patch.dict(os.environ, {'FACTORY_CONTEXT_PROBE': 'changed'}):
            self.assertNotEqual(command_context(self.root, self.argv), before)

    def test_browser_plan_is_bound_and_never_an_implementation_target(self):
        self.write('tests/browser-plan.json', {'schema': 'factory-local/browser-plan/1', 'checks': []})
        self.config['browser_executor'] = {'provider': 'playwright', 'plan': 'tests/browser-plan.json'}
        self.write('.factory/factory-local.json', self.config)
        self.initialize()
        self.contract['scope']['allowed_files'].append('tests/browser-plan.json')
        (self.task / 'spec.md').write_text((self.task / 'spec.md').read_text() + ' tests/browser-plan.json')
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        with self.assertRaisesRegex(ValueError, '只读输入'):
            runner.validate_spec(self.task, self.request, self.config)
        self.write('tests/browser-plan.json', {'schema': 'factory-local/browser-plan/1', 'checks': [], 'weakened': True})
        with self.assertRaisesRegex(ValueError, 'browser_plan_sha256'):
            runner.validate_spec(self.task, self.request, self.config, check_source=False)

    def test_browser_obligation_is_blocked_at_spec_not_final_verify(self):
        self.initialize()
        self.contract['verification'] = self.contract['verification'][:1]
        self.contract['acceptance'][0]['verification_ids'] = ['V-C']
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        with self.assertRaisesRegex(ValueError, 'required browser'):
            runner.validate_spec(self.task, self.request, self.config)

    def test_optional_file_references_reject_whole_tree_hash(self):
        self.initialize()
        runner.freeze_requirement_sources(self.task, self.request,
            runner.object_file(self.task / 'requirement-contract.json'), runner.requirement_input(self.task, self.request))
        self.contract['references'] = [{'path': 'src/allowed.ts', 'sha256': self.contract['source_sha256']}]
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        with self.assertRaisesRegex(ValueError, '单文件版本'):
            runner.validate_spec(self.task, self.request, self.config)
        self.contract['references'][0]['sha256'] = runner.digest(self.root / 'src/allowed.ts')
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        self.assertEqual(runner.validate_spec(self.task, self.request, self.config)['status'], 'passed')

    def test_reference_hashes_use_historical_source_and_reject_foreign_skills(self):
        self.initialize()
        with self.assertRaisesRegex(ValueError, '缺少需求源码历史快照'):
            runner.spec_reference_hashes(self.task, self.request, [{'path': 'src/allowed.ts'}])
        self.assertEqual(runner.spec_reference_hashes(self.task, self.request, [{'path': 'request'}]),
                         {'request': runner.digest(self.request)})
        runner.freeze_requirement_sources(self.task, self.request,
            runner.object_file(self.task / 'requirement-contract.json'), runner.requirement_input(self.task, self.request))
        original = runner.digest(self.root / 'src/allowed.ts')
        (self.root / 'src/allowed.ts').write_text('modified implementation')
        selected = [{'path': 'src/allowed.ts'}, {'path': 'request'}]
        hashes = runner.spec_reference_hashes(self.task, self.request, selected)
        self.assertEqual(hashes, {'src/allowed.ts': original, 'request': runner.digest(self.request)})
        for name in ('skills/factory-acceptance/SKILL.md', '.factory/factory-local.json', '../other/docs.md'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Spec reference 不在可信业务来源'):
                runner.spec_reference_hashes(self.task, self.request, [{'path': name}])

    def test_command_cannot_impersonate_browser_in_full_spec(self):
        self.initialize()
        self.contract['verification'][0]['evidence_type'] = 'browser'
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        with self.assertRaisesRegex(ValueError, 'command 不接受'):
            runner.validate_spec(self.task, self.request, self.config)

    def test_same_byte_input_symlink_is_detected_and_restored(self):
        self.initialize()
        original = (self.task / 'spec-contract.json').read_bytes()
        clone = self.task / 'same-byte-copy.json'; clone.write_bytes(original)
        attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'verify'})
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        def mutate(*_):
            path = self.task / 'spec-contract.json'; path.unlink(); path.symlink_to(clone)
            return subprocess.CompletedProcess([], 0, '', '')
        try:
            with patch.object(runner, 'omp', side_effect=mutate), self.assertRaisesRegex(ValueError, '阶段输入完整性失败'):
                runner.run_model_stage('implement', 'fixture', self.task / 'model.log', 'fixture', 5, self.config)
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
        self.assertFalse((self.task / 'spec-contract.json').is_symlink())
        self.assertEqual((self.task / 'spec-contract.json').read_bytes(), original)

    def test_quality_and_spec_share_one_exact_command_execution(self):
        counter = self.root / 'artifacts/command-count.txt'
        argv = [sys.executable, '-c', f'from pathlib import Path; p=Path({str(counter)!r}); p.write_text(str(int(p.read_text())+1) if p.exists() else "1")']
        self.config['quality_commands'] = [{'id': 'same', 'argv': argv}]
        self.config['verification_commands'] = []
        self.write('.factory/factory-local.json', self.config)
        self.argv = argv
        self.initialize()
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 0, result)
        self.assertEqual(counter.read_text(), '1')
        verification = json.loads((self.task / 'verification-result.json').read_text())
        row = verification['verification'][0]
        self.assertFalse(row['executed'])
        self.assertEqual(row['reused_from']['attempt_id'], verification['attempt_id'])
        summary = json.loads((self.task / 'execution-summary.json').read_text())
        self.assertEqual(summary['status'], 'completed')
        self.assertTrue(summary['stages'])

    def trace_command(self):
        target = self.root / 'artifacts/trace.json'
        return target, [sys.executable, '-c',
            'import os,json; from pathlib import Path; '
            'v={k:os.environ.get(k) for k in ["FACTORY_QUALITY_TASK_ID","FACTORY_QUALITY_ATTEMPT_ID","FACTORY_QUALITY_STAGE"]}; '
            'assert all(v.values()) and v["FACTORY_QUALITY_STAGE"]=="verify"; '
            f'Path({str(target)!r}).write_text(json.dumps(v))']

    def test_direct_spec_command_receives_the_same_trace_binding_as_quality(self):
        target, argv = self.trace_command()
        self.config['verification_commands'] = [{'id': 'trace', 'argv': argv}]
        self.write('.factory/factory-local.json', self.config)
        self.argv = argv
        self.initialize()
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 0, result)
        observed = json.loads(target.read_text())
        self.assertEqual(observed['FACTORY_QUALITY_TASK_ID'], result['task_id'])
        self.assertEqual(observed['FACTORY_QUALITY_ATTEMPT_ID'], result['attempt_id'])
        verification = json.loads((self.task / 'verification-result.json').read_text())
        self.assertTrue(verification['verification'][0]['executed'])

    def test_reused_trace_command_and_stale_binding_fail_closed(self):
        target, argv = self.trace_command()
        self.config['quality_commands'] = [{'id': 'trace', 'argv': argv}]
        self.config['verification_commands'] = []
        self.write('.factory/factory-local.json', self.config)
        self.argv = argv
        self.initialize()
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 0, result)
        self.assertEqual(json.loads(target.read_text())['FACTORY_QUALITY_ATTEMPT_ID'], result['attempt_id'])
        verification = json.loads((self.task / 'verification-result.json').read_text())
        self.assertFalse(verification['verification'][0]['executed'])
        from types import SimpleNamespace
        attempt = SimpleNamespace(task_id=result['task_id'], id=result['attempt_id'],
                                  request_sha256=runner.digest(self.request),
                                  directory=self.task / 'attempts' / result['attempt_id'])
        path = self.task / 'quality-gate-receipt.json'
        original = json.loads(path.read_text())
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        try:
            self.assertIsNotNone(runner.reuse_quality_command(self.task, {'argv': argv}))
            for key in ('task_id', 'attempt_id', 'stage'):
                runner.write_json(path, {**original, key: 'stale'})
                self.assertIsNone(runner.reuse_quality_command(self.task, {'argv': argv}))
        finally:
            runner.write_json(path, original)
            runner.ACTIVE_ATTEMPT.reset(token)

    def test_format_repair_is_bounded_and_does_not_fix_binding_conflicts(self):
        self.initialize()
        binding = runner.spec_input(self.task, self.request)
        target = self.task / 'spec-contract.json'
        attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'spec-review'})
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        args = type('Args', (), {'model': 'fixture', 'stage_timeout': 1})()
        calls = []
        def output(*_):
            calls.append(1)
            target.write_text('{broken' if len(calls) == 1 else json.dumps({
                'acceptance': [], 'verification': [{'id': 'V-1'}], 'external_requirements': []}))
        try:
            with patch.object(runner, 'run_model_stage', side_effect=output):
                runner.generate_contract_stage('spec', 'fixture', self.task / 'spec.log', args, self.config, binding)
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(target.read_text())['task_id'], binding['task_id'])
            with patch.object(runner, 'run_model_stage', side_effect=lambda *_: target.write_text(json.dumps({
                    'task_id': 'wrong', 'acceptance': [], 'verification': [], 'external_requirements': []}))) as model:
                with self.assertRaisesRegex(ValueError, '冲突'):
                    runner.generate_contract_stage('spec', 'fixture', self.task / 'spec.log', args, self.config, binding)
                self.assertEqual(model.call_count, 1)
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)


class EvaluationTests(unittest.TestCase):
    def test_zero_cases_and_missing_input_manifests_never_pass(self):
        spec = importlib.util.spec_from_file_location('spec_eval_tool', SOURCE / 'tools/evaluate-spec-quality.py')
        tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
        rules = Path(os.environ.get('FACTORY_TEST_RULES_ROOT', SOURCE.parent / 'awesome-rules'))
        pack = rules / 'skills/factory-spec-review/evals/behavior-cases.json'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'eval'
            tool.prepare(pack, output)
            path = output / 'run.json'
            original = json.loads(path.read_text())
            path.write_text(json.dumps({**original, 'cases': []}))
            with self.assertRaisesRegex(ValueError, 'evaluation cases'):
                tool.score(output)
            value = json.loads(json.dumps(original)); value['cases'][0]['files'] = {}
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'manifest incomplete'):
                tool.score(output)


if __name__ == '__main__':
    unittest.main()
