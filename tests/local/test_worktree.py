"""Real temporary Git repositories; no edits to business repositories."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from support import SOURCE, prepare
from runtime.local.worktree import GeneratedTransaction, changed_files, legacy_candidates, snapshot


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.write('.gitignore', '/artifacts/\n__pycache__/\n')
        self.write('source.ts', 'original\n')
        self.write('types/components.d.ts', 'original declarations\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Factory Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'initial')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_already_dirty_file_is_not_dropped(self):
        self.write('source.ts', 'preexisting edit\n')
        before = snapshot(self.root)
        self.write('source.ts', 'task edit\n')
        self.assertEqual(changed_files(before, snapshot(self.root)), ['source.ts'])

    def test_unchanged_dirty_file_is_not_owned(self):
        self.write('source.ts', 'preexisting edit\n')
        before = snapshot(self.root)
        self.assertEqual(changed_files(before, snapshot(self.root)), [])

    def test_staged_only_changes_are_included(self):
        before = snapshot(self.root)
        self.write('source.ts', 'staged\n')
        self.git('add', 'source.ts')
        self.write('source.ts', 'original\n')
        self.assertEqual(changed_files(before, snapshot(self.root)), ['source.ts'])

    def test_added_deleted_renamed_and_spaces(self):
        before = snapshot(self.root)
        self.git('mv', 'source.ts', 'renamed source.ts')
        self.write('new file.ts', 'new\n')
        (self.root / 'types/components.d.ts').unlink()
        self.assertEqual(changed_files(before, snapshot(self.root)),
                         ['new file.ts', 'renamed source.ts', 'source.ts', 'types/components.d.ts'])

    def test_legacy_scope_is_conservative_without_claiming_other_dirty_files(self):
        self.write('source.ts', 'already implemented\n')
        self.write('unrelated.ts', 'keep me\n')
        self.assertEqual(legacy_candidates(self.root, ['source.ts']), ['source.ts'])

    def test_head_change_is_blocked(self):
        before = snapshot(self.root)
        self.git('-c', 'user.name=Factory Test', '-c', 'user.email=test@example.invalid',
                 'commit', '--allow-empty', '-qm', 'unexpected')
        with self.assertRaisesRegex(ValueError, 'HEAD changed'):
            changed_files(before, snapshot(self.root))

    def test_generated_restore_preserves_existing_dirty_bytes_mode_and_index(self):
        self.write('types/components.d.ts', 'user staged edit\n')
        self.git('add', 'types/components.d.ts')
        self.write('types/components.d.ts', 'user unstaged edit\n')
        path = self.root / 'types/components.d.ts'
        path.chmod(0o600)
        before = snapshot(self.root)
        tx = GeneratedTransaction(self.root, ['types/components.d.ts'], self.root / 'artifacts/generator')
        self.write('types/components.d.ts', 'generated\n')
        path.chmod(0o644)
        records = tx.restore()
        self.assertEqual(changed_files(before, snapshot(self.root)), [])
        self.assertTrue(records[0]['restored'])
        self.assertEqual((self.root / 'artifacts/generator/generated/types/components.d.ts').read_text(), 'generated\n')

    def test_new_generated_file_is_removed_and_source_edit_still_detected(self):
        before = snapshot(self.root)
        tx = GeneratedTransaction(self.root, ['types/auto-imports.d.ts'], self.root / 'artifacts/generator')
        self.write('types/auto-imports.d.ts', 'generated\n')
        self.write('source.ts', 'unexpected business edit\n')
        tx.restore()
        self.assertFalse((self.root / 'types/auto-imports.d.ts').exists())
        self.assertEqual(changed_files(before, snapshot(self.root)), ['source.ts'])

    def test_generation_cannot_hide_index_edits(self):
        before = snapshot(self.root)
        tx = GeneratedTransaction(self.root, ['types/components.d.ts'], self.root / 'artifacts/generator')
        self.write('types/components.d.ts', 'generated\n')
        self.git('add', 'types/components.d.ts')
        tx.restore()
        self.assertEqual(changed_files(before, snapshot(self.root)), ['types/components.d.ts'])

    def test_unsupported_or_symlink_output_is_rejected(self):
        with self.assertRaises(ValueError):
            GeneratedTransaction(self.root, ['source.ts'], self.root / 'artifacts/bad')
        (self.root / 'types/components.d.ts').unlink()
        (self.root / 'types/components.d.ts').symlink_to(self.root / 'source.ts')
        with self.assertRaises(ValueError):
            GeneratedTransaction(self.root, ['types/components.d.ts'], self.root / 'artifacts/link')

    def run_gate(self, code, exit_code=0):
        prepare(self.root, {
            'generated_files': ['types/components.d.ts'],
            'quality_commands': [{'id': 'probe', 'argv': [sys.executable, '-c', code + f'\nraise SystemExit({exit_code})']}],
        })
        result = subprocess.run([sys.executable, '.factory/quality-gate.py'], cwd=self.root,
                                capture_output=True, text=True)
        receipts = list((self.root / 'artifacts/factory-local/quality').glob('*/receipt.json'))
        self.assertEqual(len(receipts), 1, result.stderr)
        return result, json.loads(receipts[0].read_text())['results'][0]

    def test_real_gate_restores_generator_on_success(self):
        result, receipt = self.run_gate("from pathlib import Path\nPath('types/components.d.ts').write_text('generated')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt['exit_code'], 0)
        self.assertTrue(receipt['generated_outputs'][0]['restored'])
        self.assertEqual((self.root / 'types/components.d.ts').read_text(), 'original declarations\n')

    def test_real_gate_restores_generator_but_keeps_command_failure(self):
        result, receipt = self.run_gate("from pathlib import Path\nPath('types/components.d.ts').write_text('generated')", 7)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['exit_code'], 7)
        self.assertEqual((self.root / 'types/components.d.ts').read_text(), 'original declarations\n')

    def test_real_gate_blocks_source_changes_even_when_command_passes(self):
        result, receipt = self.run_gate("from pathlib import Path\nPath('source.ts').write_text('unexpected')")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['command_exit_code'], 0)
        self.assertEqual(receipt['exit_code'], 2)
        self.assertIn('source.ts', receipt['integrity_error'])
        self.assertEqual((self.root / 'source.ts').read_text(), 'unexpected')


if __name__ == '__main__':
    unittest.main()
