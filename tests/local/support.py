import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

# Test fixtures must never inherit a hook's real repository/index binding.
for key in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES','GIT_COMMON_DIR','GIT_NAMESPACE'):
    os.environ.pop(key, None)

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from runtime.local import runner


def prepare(root, config):
    config['upstream_path'] = str(SOURCE)
    config['rules_path'] = '.rules'
    config['workflow_skills'] = {'clarification': 'factory-clarification',
        'preflight': 'factory-environment-preflight', 'review': 'factory-independent-review'}
    rules = Path(os.environ.get('FACTORY_TEST_RULES_ROOT', str(SOURCE.parent / 'awesome-rules')))
    for name in config['workflow_skills'].values():
        target = root / '.rules/skills' / name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rules / 'skills' / name / 'SKILL.md', target / 'SKILL.md')
        shutil.copytree(rules / 'skills' / name / 'references', target / 'references', dirs_exist_ok=True)
    (root / '.factory').mkdir(exist_ok=True)
    for name in ('run.py', 'quality-gate.py', 'bridge.py', 'setup.py'):
        shutil.copy2(SOURCE / 'templates/local' / name, root / '.factory' / name)
    (root / '.factory/factory-local.json').write_text(json.dumps(config))
    spec = importlib.util.spec_from_file_location('fixture_lock', SOURCE / 'tools/factory-local-lock.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # project .rules may be outside a Git tree before fixture initialization.
    files = {name: module.sha(SOURCE / name) for name in module.FILES}
    skills = {p.relative_to(root / '.rules').as_posix(): module.sha(p)
              for name in config['workflow_skills'].values()
              for p in (root / '.rules/skills' / name).rglob('*.md')}
    (root / '.factory/runtime-lock.json').write_text(json.dumps({
        'api_version': 'factory-local/runtime/1', 'workflow_version':'factory-local/workflow/3',
        'files': files, 'skills': skills,
        'test_files': {p.relative_to(SOURCE).as_posix(): module.sha(p) for p in module.test_inputs(SOURCE)}}))
