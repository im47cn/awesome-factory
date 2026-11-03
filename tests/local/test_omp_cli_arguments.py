"""Exercise the installed OMP argument parser without invoking a model/provider."""
import shutil
import subprocess
import unittest

from support import runner


@unittest.skipUnless(shutil.which('omp'), 'Optional installed OMP parser probe; no model request')
class OmpArgumentTests(unittest.TestCase):
    def test_skill_frontmatter_is_prompt_text_instead_of_a_cli_option(self):
        # An intentionally nonexistent model must fail after successful argument
        # parsing; no authentication or provider request is needed for this probe.
        model = '__factory_probe_no_such_model__'
        prompt = '---\nname: factory-clarification\n---\n消费组必填'
        command = runner.build_omp_command('clarification', prompt, model,
                                           {'local_allowed_tools': ['read']})
        result = subprocess.run(command, capture_output=True, text=True, timeout=25)
        output = result.stdout + result.stderr
        self.assertNotIn('unknown flag', output.lower())
        self.assertIn('Model "' + model + '" not found', output)
        self.assertEqual(result.returncode, 1)


if __name__ == '__main__':
    unittest.main()
