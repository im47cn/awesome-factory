#!/usr/bin/env python3
"""Create a clean isolated task worktree; preserve the source checkout and its branch.

No commit, remote, dependency installation, pin refresh or business implementation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from runtime.local.execution import RepositoryLease


class WorkspaceCreationError(ValueError):
    def __init__(self, reason, project, destination):
        super().__init__(reason)
        self.receipt = {'schema': 'factory-local/isolated-workspace/1', 'status': 'failed',
                        'source': str(project), 'workspace': str(destination), 'reason': reason,
                        'recovery': '工作树可能已创建；先检查 git worktree list 和该目录，保留改动，再使用 git worktree remove 回收；不会自动删除目录'}


def create(project, destination):
    project, destination = project.resolve(), destination.resolve()
    with RepositoryLease(project, 'workspace-create'):
        if destination.exists() or destination.is_relative_to(project):
            raise ValueError('目标必须为仓库外尚不存在的目录')
        if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=project):
            raise ValueError('源仓有未提交内容；请先自行保存为已审核基线，工具不会提交、忽略或复制未知改动')
        config = json.loads((project / '.factory/factory-local.json').read_text())
        if not isinstance(config, dict) or not all(isinstance(config.get(key), str) and config[key] for key in ('upstream_path', 'rules_path')):
            raise ValueError('源仓公共运行时配置不完整')
        subprocess.run(['git', 'cat-file', '-e', 'HEAD:.factory/factory-local.json'], cwd=project, check=True, capture_output=True)
        source_installation = project / '.factory/installation.json'
        installation_value = json.loads(source_installation.read_text()) if source_installation.is_file() else None
        if installation_value is not None and (not isinstance(installation_value, dict) or not isinstance(installation_value.get('files'), dict)):
            raise ValueError('installation.json.files 必须为对象')
        try:
            subprocess.run(['git', 'worktree', 'add', '--detach', str(destination), 'HEAD'], cwd=project, check=True, capture_output=True, text=True)
            return configure(project, destination, config, installation_value)
        except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
            failure = WorkspaceCreationError(str(exc), project, destination)
            if isinstance(exc, subprocess.CalledProcessError):
                failure.receipt['git_exit_code'] = exc.returncode
                # Preserve diagnostics privately; never turn hook output into an instruction.
                import uuid
                metadata = Path(subprocess.check_output(['git', 'rev-parse', '--absolute-git-dir'], cwd=project, text=True).strip())
                log = metadata / ('factory-workspace-failure-' + uuid.uuid4().hex + '.log')
                log.write_text((exc.stderr or '') + (exc.stdout or ''))
                failure.receipt['git_log'] = str(log)
            raise failure from exc


def configure(project, destination, config, installation_value):
        config['upstream_path'] = str((project / config['upstream_path']).resolve())
        config['rules_path'] = str((project / config['rules_path']).resolve())
        path = destination / '.factory/factory-local.json'
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        installation = destination / '.factory/installation.json'
        if installation.is_file():
            value = installation_value
            if '.factory/factory-local.json' in value.get('files', {}):
                value['files']['.factory/factory-local.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
                installation.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        # Local ignores are per worktree; copy only the known generated-file policy.
        exclude = Path(subprocess.check_output(['git', 'rev-parse', '--git-path', 'info/exclude'], cwd=destination, text=True).strip())
        if not exclude.is_absolute():
            exclude = destination / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text() if exclude.exists() else ''
        patterns = ['/artifacts/factory-local/', '/.factory/**/__pycache__/', '/.factory/__pycache__/', '/.factory/*.pyc']
        exclude.write_text(existing + '\n' + '\n'.join(pattern for pattern in patterns if pattern not in existing.splitlines()) + '\n')
        return {'schema': 'factory-local/isolated-workspace/1', 'source': str(project), 'workspace': str(destination),
                'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=destination, text=True).strip(),
                'dependency_install': 'NOT_RUN', 'pins_refreshed': False, 'status': 'created',
                'next': '在 workspace 安装冻结依赖，检查 .factory/setup.py check，然后运行任务；业务改动保留在该目录供审核'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(create(args.project, args.destination), ensure_ascii=False, indent=2))
        return 0
    except WorkspaceCreationError as exc:
        print(json.dumps(exc.receipt, ensure_ascii=False, indent=2))
        return 2
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
