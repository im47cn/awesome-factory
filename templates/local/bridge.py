"""Thin project adapter. Verify the reviewed public bundle before executing it."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

API_VERSION = 'factory-local/runtime/1'


def resolve(project):
    config = json.loads((project / '.factory/factory-local.json').read_text())
    source = (project / config['upstream_path']).resolve()
    lock = json.loads((project / '.factory/runtime-lock.json').read_text())
    if lock.get('api_version') != API_VERSION:
        raise ValueError('Factory runtime API 不匹配；请审核公共仓升级后重新锁定')
    files = lock.get('files')
    required = {'runtime/local/' + name for name in (
        '__init__.py', 'bootstrap.py', 'runner.py', 'workflow.py', 'acceptance.py',
        'worktree.py', 'revisions.py', 'business_sources.py', 'quality.py', 'skills.py', 'docs_context.py', 'figma.py')}
    required.update({'tools/factory-local-setup.py', 'tools/factory-local-lock.py'})
    if not isinstance(files, dict) or set(files) != required:
        raise ValueError('Factory runtime lock 文件清单缺失或不兼容')
    for name, expected in files.items():
        path = source / name
        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('Factory 公共运行时缺失或摘要已改变：' + name + '；请核对 upstream_path 和已审核的 runtime-lock.json')
    return source


def main(component, argv=None):
    project = Path(__file__).resolve().parents[1]
    try:
        source = resolve(project)
        spec = importlib.util.spec_from_file_location('_factory_bootstrap', source / 'runtime/local/bootstrap.py')
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        return bootstrap.execute(component, project, sys.argv[1:] if argv is None else argv)
    except (OSError, KeyError, ValueError) as exc:
        print('Factory 接入失败：' + str(exc), file=sys.stderr)
        return 2
