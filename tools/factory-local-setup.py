#!/usr/bin/env python3
"""One entry for local onboarding: init a new repo, check an existing clone.

No dependency installation, model invocation, Git checkout/commit/push or network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

SOURCE = Path(__file__).resolve().parents[1]
IGNORE = '\n# Factory local runtime (shared config/programs remain tracked)\n/artifacts/factory-local/\n/.factory/**/__pycache__/\n/.factory/__pycache__/\n/.factory/*.pyc\n'


class Audit:
    def __init__(self, repo):
        self.repo = repo
        self.directory = repo / 'artifacts/factory-local/onboarding' / (
            dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        self.directory.mkdir(parents=True)
        self.nodes = []

    def record(self, node, status, **details):
        row = dict(node=node, status=status, time=dt.datetime.now(dt.timezone.utc).isoformat(), **details)
        self.nodes.append(row)
        with (self.directory / 'nodes.jsonl').open('a') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'{node}: {status}', flush=True)

    def run(self, node, argv, expected=(0,)):
        try:
            proc = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, timeout=180)
            rc, output = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            rc, output = 124, 'timeout'
        except OSError as exc:
            rc, output = 127, str(exc)
        log = self.directory / (node + '.log')
        log.write_text(output)
        self.record(node, 'PASSED' if rc in expected else 'BLOCKED', argv=argv,
                    exit_code=rc, log=str(log.relative_to(self.repo)))
        return rc in expected

    def finish(self):
        success = all(n['status'] == 'PASSED' for n in self.nodes)
        (self.directory / 'summary.json').write_text(json.dumps({
            'schema': 'factory-local/onboarding/1', 'status': 'PASSED' if success else 'BLOCKED',
            'nodes': self.nodes, 'scope': 'installation-and-tool-presence-only',
            'model_auth': 'NOT_RUN', 'business_acceptance': 'NOT_RUN',
        }, ensure_ascii=False, indent=2) + '\n')
        print(f'初始化记录: {self.directory}')
        return 0 if success else 1


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def quality_commands(repo):
    package = json.loads((repo / 'package.json').read_text())
    scripts = package.get('scripts', {})
    names = ['format:check', 'lint:ci', 'stylelint:ci', 'typecheck', 'test:ci',
             'contract:check', 'contract:fields', 'contract:stale-v1', 'build:dev']
    return [{'id': name.replace(':', '-'), 'argv': ['pnpm', 'run', name]}
            for name in names if name in scripts]


def install(repo, audit):
    tool = SOURCE / 'tools/factory-init.py'
    if not tool.is_file():
        raise ValueError('新仓接入请使用 awesome-factory/tools/factory-local-setup.py init <项目>；clone 后使用 check')
    # Existing installs are check-only: never overwrite runtime/config/user changes.
    if (repo / '.factory').exists() or (repo / 'MISSION.md').exists():
        raise ValueError('检测到现有 .factory 或 MISSION.md；拒绝覆盖。已接入项目请使用 check')
    if not (repo / 'package.json').is_file():
        raise ValueError('当前一站式方案支持 package.json 前端仓；后端请使用 factory-init --project-type backend')
    if not (repo / 'README.md').is_file():
        raise ValueError('缺少 README.md 良性负例载体；请先补真实项目文档')
    # Keep README outside perimeter for B-01; protect actual existing governance files.
    perimeter = ['.gitignore']
    perimeter += [p for p in ['scripts/', 'docs/', '.aliyun/', '.github/', '.lefthook/', 'lefthook.yml',
        'package.json', 'pnpm-lock.yaml', 'tsconfig.app.json', 'tsconfig.node.json',
        'vite.config.ts'] if (repo / p).exists()]
    # Untracked ignored artifacts, including this audit, must stay outside business diffs.
    argv = [sys.executable, str(tool), 'init', str(repo), '--project-type', 'frontend',
            '--mode', 'local', '--perimeter', ','.join(perimeter), '--upstream', str(SOURCE),
            '--final-gate-cmd', 'python3 .factory/quality-gate.py', '--no-register', '--no-labels']
    if not audit.run('governance-install-and-mutations', argv):
        raise ValueError('治理初始化失败；查看原始日志，不重写现有目录。修复后人工重跑 factory-init')
    factory = repo / '.factory'
    for name in ('run.py', 'bridge.py', 'setup.py', 'test.py', 'quality-gate.py', 'figma-capture.py', 'README.md'):
        shutil.copy2(SOURCE / 'templates/local' / name, factory / name)
    (factory / 'tests').mkdir()
    shutil.copy2(SOURCE / 'templates/local/test-adapter.py', factory / 'tests/test_adapter.py')
    cfg_path = factory / 'factory-local.json'
    cfg = json.loads(cfg_path.read_text())
    cfg.update(local_runner='.factory/run.py', local_allowed_tools=['read','write','edit','grep','lsp'],
        workflow_version='factory-local/workflow/3',
        business_sources={'schema':'factory-local/business-source-policy/1', 'sources':[]},
        local_stage_order=['classify','skill-discovery','design-analysis','requirement','clarification','spec','preflight','implement','review','verify'],
        rules_path=os.path.relpath(Path(os.environ.get('AWESOME_RULES_ROOT', str(repo.parent / 'awesome-rules'))).resolve(), repo),
        workflow_skills={'clarification':'factory-clarification','preflight':'factory-environment-preflight','review':'factory-independent-review'},
        preflight_commands=[{'id':'python','argv':[sys.executable,'--version'],'minimum_version':[3,10,0]},
                            {'id':'node','argv':['node','--version'],'minimum_version':[22,22,3]},
                            {'id':'pnpm','argv':['pnpm','--version'],'minimum_version':[10,28,1]}],
        readiness_max_age_seconds=86400,
        review_basis='仓库 README.md、模块规范和任务 spec；按规则库技能执行',
        upstream_path=os.path.relpath(SOURCE, repo),
        intent_routing={'remote_handoff': {'enabled': True, 'executor': 'awesome-factory',
            'local_runner_may_execute_remote_actions': False}},
        quality_commands=quality_commands(repo))
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
    mission = repo / 'MISSION.md'
    text = mission.read_text()
    text = text.replace('issue → triage → 实现 → 确定性门 → PR → 独立验证（holdout）→ 人工合并',
        '本地 request → 分类 → 技能发现 → 设计分析 → requirement → 澄清 → spec → 环境预检 → 实现 → 独立审查 → 验证')
    text = text.replace('人类只保留两件事：**写 issue、合并 PR**。',
        '当前为本地模式：不自动提交、推送、创建 MR 或发布。需求确认、设计/接口证据及浏览器验收由对应执行者提供。')
    mission.write_text(text)
    lock_spec = importlib.util.spec_from_file_location('factory_lock', SOURCE / 'tools/factory-local-lock.py')
    locker = importlib.util.module_from_spec(lock_spec)
    lock_spec.loader.exec_module(locker)
    (factory / 'runtime-lock.json').write_text(json.dumps(locker.create(repo, SOURCE), ensure_ascii=False, indent=2) + '\n')
    paths = ['MISSION.md', '.factory/factory-local.json', '.factory/setup.py',
             '.factory/run.py', '.factory/test.py', '.factory/bridge.py', '.factory/runtime-lock.json', '.factory/quality-gate.py', '.factory/figma-capture.py',
             '.factory/README.md', '.factory/tests/test_adapter.py', '.factory/guard.py', '.factory/factory_lib.py',
             '.factory/mutations/run.py', '.factory/mutations/defects.json']
    hashes = {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in paths}
    (factory / 'installation.json').write_text(json.dumps({'schema':'factory-local/installation/1',
        'upstream_commit': git(SOURCE, 'rev-parse','HEAD'), 'source_state': 'working-tree-snapshot',
        'files':hashes}, indent=2) + '\n')
    audit.record('local-runtime-config-and-provenance', 'PASSED', files=paths,
                 note='snapshot hash recorded; no remote/model calls')


def ensure_excludes(repo):
    exclude = Path(git(repo, 'rev-parse', '--git-path', 'info/exclude'))
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    content = exclude.read_text() if exclude.exists() else ''
    if '/artifacts/factory-local/' not in content:
        exclude.write_text(content + IGNORE)


def check(repo, audit):
    ensure_excludes(repo)
    factory = repo / '.factory'
    install_info = json.loads((factory / 'installation.json').read_text())
    drift = [p for p,h in install_info['files'].items() if not (repo / p).is_file()
             or hashlib.sha256((repo / p).read_bytes()).hexdigest() != h]
    audit.record('installed-file-integrity', 'BLOCKED' if drift else 'PASSED', drift=drift,
                 recovery='由维护者审核配置或升级差异后重新生成 installation.json；不自动覆盖')
    if drift:
        return
    cfg = json.loads((factory / 'factory-local.json').read_text())
    safe = cfg.get('execution_mode') == 'local' and cfg.get('remote_side_effects') is False and cfg.get('auto_commit') is False
    audit.record('local-policy', 'PASSED' if safe else 'BLOCKED')
    audit.run('python', [sys.executable, '--version'])
    audit.run('node', ['node', '--version'])
    audit.run('pnpm', ['pnpm', '--version'])
    # Probe presence only, never print auth files or invoke a paid model request.
    omp = shutil.which('omp')
    audit.record('model-tool', 'PASSED' if omp else 'BLOCKED',
                 next_action='如未安装，安装 OMP 并在交互终端登录；模型实际调用留待试点需求验证')
    rules = (repo / cfg['rules_path']).resolve()
    audit.record('rules-source', 'PASSED' if (rules / 'skills').is_dir() else 'BLOCKED',
                 next_action='准备 awesome-rules 并修正 factory-local.json 的 rules_path；审核差异后更新锁与 installation.json')
    audit.record('project-dependencies', 'PASSED' if (repo / 'node_modules').is_dir() else 'BLOCKED',
                 next_action='按 package.json engines 安装 Node/pnpm，运行 pnpm install --frozen-lockfile')
    audit.run('shared-runtime-and-skills', [sys.executable, str(factory / 'run.py'), '--check-runtime'])
    audit.run('runner-help', [sys.executable, str(factory / 'run.py'), '--help'])
    audit.run('guard-self-check', [sys.executable, '-c',
        "import sys; sys.path.insert(0,'.factory'); import guard; guard.self_check()"])
    audit.run('mutations', [sys.executable, str(factory / 'mutations/run.py')])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'check'])
    parser.add_argument('repo', type=Path, nargs='?', default=Path.cwd())
    args = parser.parse_args(argv)
    repo = args.repo.expanduser().resolve()
    try:
        if Path(git(repo, 'rev-parse','--show-toplevel')).resolve() != repo:
            raise ValueError('请指定 Git 仓库根目录')
        common = Path(git(repo, 'rev-parse','--path-format=absolute','--git-common-dir'))
        # Single writer for onboarding/verification; normal source work is not modified.
        with (common / 'factory-onboarding.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('同一仓库正在初始化或检查，请等待结束')
            ensure_excludes(repo)
            audit = Audit(repo)
            audit.record('workspace-baseline', 'PASSED', branch=git(repo,'branch','--show-current'),
                         head=git(repo,'rev-parse','HEAD'), changed=git(repo,'status','--porcelain'))
            try:
                if args.command == 'init':
                    install(repo, audit)
                check(repo, audit)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                audit.record('setup-error', 'BLOCKED', reason=str(exc))
            return audit.finish()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
