"""Load a separate runtime namespace per project; no global ROOT shared across projects."""
import importlib
import importlib.util
import json
import os
import hashlib
import sys
import uuid
from pathlib import Path

API_VERSION = 'factory-local/runtime/1'


def load(project: Path):
    project = project.resolve()
    name = '_factory_local_' + uuid.uuid4().hex
    execution_spec = importlib.util.spec_from_file_location('_factory_execution_' + uuid.uuid4().hex,
                                                          Path(__file__).with_name('execution.py'))
    execution = importlib.util.module_from_spec(execution_spec)
    execution_spec.loader.exec_module(execution)
    config = json.loads((project / '.factory/factory-local.json').read_text())
    source = (project / config['upstream_path']).resolve()
    lock = json.loads((project / '.factory/runtime-lock.json').read_text())
    directory = execution.freeze_runtime(project, source, lock) / 'runtime/local'
    spec = importlib.util.spec_from_file_location(name, directory / '__init__.py',
                                                submodule_search_locations=[str(directory)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    runner = importlib.import_module(name + '.runner')
    runner.ROOT = project
    runner.FACTORY_LOCAL_CONFIG = project / '.factory/factory-local.json'
    config = json.loads(runner.FACTORY_LOCAL_CONFIG.read_text())
    rules = config.get('rules_path', '../awesome-rules')
    runner.AWESOME_RULES_ROOT = (project / rules).resolve()
    return runner


def execute(component: str, project: Path, argv: list[str]) -> int:
    if component == 'setup' and (not argv or argv[0] != 'check'):
        raise ValueError('项目 setup 适配仅支持 check；新仓 init 请使用公共 tools/factory-local-setup.py')
    runner = load(project)
    runner.load_workflow_skills(project, runner.AWESOME_RULES_ROOT, json.loads(runner.FACTORY_LOCAL_CONFIG.read_text()))
    if component == 'run':
        if argv == ['--check-runtime']:
            runner.load_local_factory_config()
            print('Shared Factory runtime and workflow Skills: PASS')
            return 0
        return runner.main(argv)
    module = importlib.import_module(runner.__package__ + '.' + component) if component not in {'setup', 'tests'} else None
    if component == 'tests':
        import subprocess
        source = Path(__file__).resolve().parents[2]
        config = json.loads(runner.FACTORY_LOCAL_CONFIG.read_text())
        runner.verify_code(project, config)
        lock = json.loads((project / '.factory/runtime-lock.json').read_text())
        pinned = lock.get('test_files', {})
        actual = {p.relative_to(source).as_posix() for directory in ('tests/local', 'templates/local') for p in (source / directory).glob('*.py')} | {
            '.factory/guard.py', 'tools/evaluate-clarification.py', 'tools/evaluate-spec-quality.py', 'tools/factory-local-workspace.py'}
        if not pinned or set(pinned) != actual:
            raise ValueError('公共回归清单缺失或漂移')
        for name, expected in pinned.items():
            path = source / name
            if path.is_symlink() or not path.resolve().is_relative_to(source) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('公共回归输入已变化：' + name)
        rule_files = lock.get('test_rules_files', {})
        rules = runner.AWESOME_RULES_ROOT
        rule_actual = {p.relative_to(rules).as_posix() for skill in ('factory-clarification', 'factory-spec-review')
                       for p in (rules / 'skills' / skill / 'evals').rglob('*') if p.is_file() and not p.is_symlink()}
        if not rule_files or set(rule_files) != rule_actual:
            raise ValueError('Rules 回归夹具清单缺失或漂移')
        for name, expected in rule_files.items():
            path = rules / name
            if path.is_symlink() or not path.resolve().is_relative_to(rules) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Rules 回归夹具已变化：' + name)
        commands = [[sys.executable, '-m', 'unittest', 'discover', '-s', str(source / 'tests/local'), '-v'],
                    [sys.executable, '-m', 'unittest', 'discover', '-s', str(project / '.factory/tests'), '-v']]
        for command in commands:
            result = subprocess.run(command, cwd=project, env={**os.environ, 'FACTORY_TEST_RULES_ROOT': str(runner.AWESOME_RULES_ROOT)})
            if result.returncode:
                return result.returncode
        return 0
    if component == 'quality':
        module.ROOT = project.resolve()
        return module.main()
    if component == 'figma':
        return module.main(argv)
    if component == 'setup':
        source = Path(__file__).resolve().parents[2] / 'tools/factory-local-setup.py'
        spec = importlib.util.spec_from_file_location('_factory_setup_' + uuid.uuid4().hex, source)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        return tool.main(argv)
    raise ValueError('Unknown Factory adapter: ' + component)
