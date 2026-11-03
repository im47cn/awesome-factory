"""Pre-code observations survive legitimate coding, never stale or forged evidence."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_acceptance import FactoryCase
from support import runner


class RequirementHistoryTests(FactoryCase):
    def requirement(self, task, request):
        super().requirement(task, request)
        path = task/'requirement-contract.json'
        contract = json.loads(path.read_text())
        contract['observations'].append({'id': 'OBS-SOURCE', 'kind': 'source',
            'statement': 'Baseline exports value 1', 'limitation': 'Pre-code only',
            'source': {'path': 'src/allowed.ts', 'quote': 'export const value = 1'}})
        runner.write_json(path, contract)
        runner.validate_requirement(task, request, freeze_sources=True)

    def implement(self):
        self.initialize()
        with patch.object(runner, 'run_model_stage', side_effect=lambda *_:
                self.write('src/allowed.ts', 'export const value = 2\n')):
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request),
                '--from-stage', 'implement', '--only-stage', 'implement']), 0)

    def test_real_review_resume_accepts_old_quote_after_scoped_implementation(self):
        self.implement()
        frozen = runner._requirement_source(self.task, self.request, './src/allowed.ts', historical=True)
        self.assertEqual(frozen.read_text(), 'export const value = 1\n')
        with patch.object(runner, 'omp', side_effect=self.review_response):
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request),
                '--resume', '--only-stage', 'review']), 0)
        self.assertEqual((self.root/'src/allowed.ts').read_text(), 'export const value = 2\n')
        rc, result = self.run_verify(self.evidence())
        self.assertEqual(rc, 0)
        self.assertEqual(result['overall_status'], 'completed')
        with self.assertRaisesRegex(ValueError, '源码|source_sha256|未命中'):
            runner.validate_requirement(self.task, self.request, check_source=True)

    def test_implementation_retry_preserves_analysis_history_and_scope(self):
        self.implement()
        with patch.object(runner, 'run_model_stage', side_effect=lambda *_:
                self.write('src/allowed.ts', 'export const value = 3\n')):
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request),
                '--from-stage', 'implement', '--only-stage', 'implement']), 0)
        self.assertEqual(runner._requirement_source(self.task, self.request, 'src/allowed.ts', historical=True).read_text(),
                         'export const value = 1\n')
        self.write('src/unrelated.ts', 'unauthorized change\n')
        with patch.object(runner, 'run_model_stage') as model:
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request),
                '--from-stage', 'implement', '--only-stage', 'implement']), 2)
            model.assert_not_called()
        self.write('src/unrelated.ts', 'export const other = 1\n')
        self.git('add', 'src/allowed.ts')
        self.git('-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Regression',
            '-c', 'user.email=regression@example.invalid', 'commit', '-qm', 'changed HEAD')
        with patch.object(runner, 'run_model_stage') as model:
            self.assertEqual(runner.main(['--task', 'original', '--request-file', str(self.request),
                '--from-stage', 'implement', '--only-stage', 'implement']), 2)
            model.assert_not_called()

    def test_history_requires_intact_archived_manifest_and_content(self):
        self.implement()
        manifest = json.loads((self.task/'requirement-source-snapshot.json').read_text())
        saved = self.task/manifest['files']['src/allowed.ts']['path']
        original = saved.read_bytes()
        saved.write_text('forged historical body')
        with self.assertRaisesRegex(ValueError, '历史内容'):
            runner.validate_requirement(self.task, self.request, check_source=False)
        saved.write_bytes(original)
        archived = Path(json.loads((self.task/'stage-receipts.json').read_text())['requirement']['artifact_dir'])/'requirement-source-snapshot.json'
        archived.write_text('{}')
        with self.assertRaisesRegex(ValueError, '历史快照'):
            runner.validate_requirement(self.task, self.request, check_source=False)

    def test_historical_lookup_cannot_escape_or_substitute_analysis_baseline(self):
        self.implement()
        path = self.task/'requirement-source-snapshot.json'
        receipts = json.loads((self.task/'stage-receipts.json').read_text())
        archive = Path(receipts['requirement']['artifact_dir'])/'requirement-source-snapshot.json'
        original = json.loads(path.read_text())
        outside = self.write('outside.txt', 'export const value = 1\n')
        for mode in ('outside', 'baseline'):
            changes = json.loads(json.dumps(original))
            if mode == 'outside':
                changes['files']['src/allowed.ts']['path'] = str(outside)
            else:
                changes['source_sha256'] = 'wrong-baseline'
            runner.write_json(path, changes)
            runner.write_json(archive, changes)
            receipts['requirement']['outputs']['requirement-source-snapshot.json'] = runner.digest(path)
            runner.write_json(self.task/'stage-receipts.json', receipts)
            with self.assertRaisesRegex(ValueError, '历史内容|历史快照'):
                runner.validate_requirement(self.task, self.request, check_source=False)

    def test_coordinated_manifest_forgery_cannot_replace_original_file_bytes(self):
        self.implement()
        path = self.task/'requirement-source-snapshot.json'
        data = json.loads(path.read_text())
        saved = self.task/'requirement-sources/forged'
        saved.write_text('FORGED export const value = 1\n')
        data['files']['src/allowed.ts'] = {'path': saved.relative_to(self.task).as_posix(), 'sha256': runner.digest(saved)}
        receipts = json.loads((self.task/'stage-receipts.json').read_text())
        archive = Path(receipts['requirement']['artifact_dir'])/path.name
        runner.write_json(path, data); runner.write_json(archive, data)
        receipts['requirement']['outputs'][path.name] = runner.digest(path)
        runner.write_json(self.task/'stage-receipts.json', receipts)
        with self.assertRaisesRegex(ValueError, '分析文件基线'):
            runner.validate_requirement(self.task, self.request, check_source=False)

    def test_ignored_credentials_and_governance_are_not_persisted_as_sources(self):
        self.initialize()
        self.write('.git/info/exclude', '.env\n')
        self.write('.env', 'FAKE_TOKEN=never-copy\n')
        binding = runner.requirement_input(self.task, self.request)
        for name in ('.env', '.git/config', '.factory/guard.py', 'MISSION.md', 'artifacts/factory-local/original/request.md'):
            contract = {'observations': [{'kind': 'source', 'source': {'path': name}}]}
            with self.subTest(path=name), self.assertRaisesRegex(ValueError, '未纳入基线、治理或凭据'):
                runner.freeze_requirement_sources(self.task, self.request, contract, binding)
        self.assertFalse(any('never-copy' in p.read_text(errors='replace') for p in (self.task/'requirement-sources').rglob('*') if p.is_file()))

    def test_symlinked_archived_manifest_is_rejected(self):
        self.implement()
        receipts = json.loads((self.task/'stage-receipts.json').read_text())
        manifest = self.task/'requirement-source-snapshot.json'
        archive = Path(receipts['requirement']['artifact_dir'])/manifest.name
        archive.unlink(); archive.symlink_to(manifest)
        with self.assertRaisesRegex(ValueError, '分析收据'):
            runner.validate_requirement(self.task, self.request, check_source=False)

    def test_model_cannot_rewrite_archived_manifest_or_source_body(self):
        self.initialize()
        receipts = json.loads((self.task/'stage-receipts.json').read_text())
        archived = Path(receipts['requirement']['artifact_dir'])/'requirement-source-snapshot.json'
        manifest = json.loads(archived.read_text())
        saved = self.task/manifest['files']['src/allowed.ts']['path']
        original = {p: p.read_bytes() for p in (archived, saved)}
        attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'verify'})
        token = runner.ACTIVE_ATTEMPT.set(attempt)
        def mutate(*_):
            archived.write_text('{}'); saved.write_text('FORGED')
            return subprocess.CompletedProcess([], 0, '{}', '')
        try:
            with patch.object(runner, 'omp', side_effect=mutate):
                with self.assertRaisesRegex(ValueError, '阶段输入完整性失败'):
                    runner.run_model_stage('implement', 'fixture', self.task/'rewrite.log', 'fixture', 10, self.config)
        finally:
            runner.ACTIVE_ATTEMPT.reset(token)
        for path, body in original.items():
            self.assertEqual(path.read_bytes(), body)

    def test_validation_rejects_before_quote_read_and_archives_only_quarantined_outputs(self):
        self.initialize()
        original_contract = json.loads((self.task/'requirement-contract.json').read_text())
        self.write('.git/info/exclude', '.env\n')
        self.write('.env', 'FAKE_NEVER_ARCHIVE_TOKEN\n')
        for name in ('.env', '.git/config', '.factory/guard.py', 'MISSION.md', 'id_rsa', '.aws/credentials', '.docker/config.json'):
            contract = json.loads(json.dumps(original_contract))
            contract['observations'].append({'id': 'OBS-UNSAFE', 'kind': 'source',
                'statement': 'Unsafe candidate', 'limitation': 'Must be rejected',
                'source': {'path': name, 'quote': 'FAKE_NEVER_ARCHIVE_TOKEN'}})
            runner.write_json(self.task/'requirement-contract.json', contract)
            (self.task/'requirement.md').write_text('FAKE_NEVER_ARCHIVE_TOKEN')
            read_text = Path.read_text
            def guarded_read(path, *args, **kwargs):
                if path == self.root/name:
                    raise AssertionError('Forbidden quote path was read')
                return read_text(path, *args, **kwargs)
            with self.subTest(path=name), patch.object(Path, 'read_text', guarded_read):
                with self.assertRaisesRegex(ValueError, '受限|隔离'):
                    runner.validate_requirement(self.task, self.request)
            attempt = runner.Attempt(self.task, self.request, {'authorized_end_stage': 'verify'})
            attempt.stage = 'requirement'
            attempt.finish('failed', 2)
            for path in (self.task/'requirement-contract.json', self.task/'requirement.md', *attempt.directory.rglob('requirement*')):
                if path.is_file():
                    self.assertNotIn('FAKE_NEVER_ARCHIVE_TOKEN', path.read_text())
