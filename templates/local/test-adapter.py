"""Project adapter smoke test; the shared workflow battery lives in awesome-factory."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class AdapterTests(unittest.TestCase):
    def test_pinned_runtime_and_skills_load(self):
        result = subprocess.run([sys.executable, str(ROOT / '.factory/run.py'), '--check-runtime'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
