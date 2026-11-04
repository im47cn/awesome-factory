"""Driver protocol negative controls using a fake browser, not live browser proof."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from support import SOURCE


@unittest.skipUnless(shutil.which('node'), 'Node required for driver protocol tests')
class BrowserDriverTests(unittest.TestCase):
    def run_driver(self, destination='/', redirect=False, no_assertion=False, interaction_navigation=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'node_modules/playwright'
            module.mkdir(parents=True)
            (root / 'package.json').write_text('{}')
            (module / 'index.js').write_text('''
const fs = require('node:fs');
let changed = false;
exports.chromium = { launch: async () => ({
 version: () => 'fake-protocol-browser', close: async () => {},
 newContext: async () => ({ close: async () => {}, newPage: async () => ({
  setDefaultTimeout: () => {}, on: () => {},
  goto: async url => { fs.writeFileSync(process.env.FACTORY_BROWSER_PROJECT + '/navigated', url); return {ok:()=>true}; },
  url: () => changed || process.env.TEST_REDIRECT === '1' ? 'https://external.invalid/' : 'http://127.0.0.1:6192/',
  getByRole: () => ({click: async () => { changed = true; }}),
  evaluate: async () => true,
  screenshot: async ({path}) => fs.writeFileSync(path, 'fixture screenshot')
 }) })
}) };
''')
            request = {'schema': 'factory-local/verification-evidence/1', 'task_id': 'test', 'request_sha256': 'r',
                       'source_sha256': 's', 'spec_sha256': 'p', 'contract_sha256': 'c', 'executor': '',
                       'base_url': 'http://127.0.0.1:6192/', 'checks': [{'id': 'V-BROWSER', 'environment': 'local'}]}
            plan = {'checks': [{'id': 'V-BROWSER', 'environment': 'local', 'path': destination,
                                'viewports': [{'width': 375, 'height': 900}],
                                'steps': [] if no_assertion else ([{'action': 'click', 'role': 'button', 'name': 'leave'}] if interaction_navigation else []) + [{'action': 'assertNoOverflow'}]}]}
            (root / 'request.json').write_text(json.dumps(request))
            (root / 'plan.json').write_text(json.dumps(plan))
            import os
            result = subprocess.run(['node', str(SOURCE / 'runtime/local/browser-driver.mjs'),
                                     str(root / 'request.json'), str(root / 'plan.json'), str(root)],
                                    env={**os.environ, 'FACTORY_BROWSER_PROJECT': str(root), 'TEST_REDIRECT': '1' if redirect else '0'},
                                    capture_output=True, text=True, timeout=20)
            receipt = json.loads((root / 'receipt.json').read_text())
            return result.returncode, receipt, (root / 'navigated').exists()

    def test_cross_origin_paths_fail_before_navigation(self):
        for destination in ('https://external.invalid/', '//external.invalid/'):
            with self.subTest(destination=destination):
                rc, receipt, navigated = self.run_driver(destination)
                self.assertEqual(rc, 1)
                self.assertEqual(receipt['checks'][0]['status'], 'failed')
                self.assertFalse(navigated)

    def test_redirect_cannot_be_reported_as_local_pass(self):
        rc, receipt, _ = self.run_driver(redirect=True)
        self.assertEqual(rc, 1)
        self.assertEqual(receipt['checks'][0]['status'], 'failed')

    def test_interaction_navigation_cannot_be_reported_as_local_pass(self):
        rc, receipt, _ = self.run_driver(interaction_navigation=True)
        self.assertEqual(rc, 1)
        self.assertEqual(receipt['checks'][0]['status'], 'failed')

    def test_empty_assertions_cannot_pass(self):
        rc, receipt, navigated = self.run_driver(no_assertion=True)
        self.assertEqual(rc, 1)
        self.assertEqual(receipt['checks'][0]['status'], 'failed')
        self.assertFalse(navigated)

    def test_positive_protocol_contains_per_check_evidence(self):
        rc, receipt, navigated = self.run_driver()
        self.assertEqual(rc, 0)
        self.assertTrue(navigated)
        self.assertEqual(receipt['checks'][0]['status'], 'passed')
        self.assertEqual(len(receipt['checks'][0]['artifacts']), 2)


if __name__ == '__main__':
    unittest.main()
