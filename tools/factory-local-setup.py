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
_lease_spec = importlib.util.spec_from_file_location('_factory_setup_lease', SOURCE / 'runtime/local/execution.py')
_lease_module = importlib.util.module_from_spec(_lease_spec)
_lease_spec.loader.exec_module(_lease_module)
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
        reason = f' — {details["reason"]}' if details.get('reason') else ''
        print(f'{node}: {status}{reason}', flush=True)

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
    if 'coverage:gate' not in scripts:
        raise ValueError('缺少 package.json scripts.coverage:gate；先完成 frontend-unit-test-setup，不能仅依赖 Git Hook')
    contract_names = ['contract:check', 'contract:fields', 'contract:stale-v1']
    if 'quality:check' in scripts:
        names = ['quality:check', 'coverage:gate', *contract_names]
    else:
        names = ['format:check', 'lint:ci', 'stylelint:ci', 'typecheck']
        test_name = next((name for name in ('test:ci', 'test:unit') if name in scripts), None)
        if test_name:
            names.append(test_name)
        names.append('coverage:gate')
        names.extend(contract_names)
        build_name = next((name for name in ('build:dev', 'build') if name in scripts), None)
        if build_name:
            names.append(build_name)
    return [{'id': name.replace(':', '-'), 'argv': ['pnpm', 'run', name]}
            for name in names if name in scripts]


def context_module(name):
    spec = importlib.util.spec_from_file_location('onboarding_' + name, SOURCE / 'runtime/local' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_context(repo, values):
    """Validate operator-selected documents; never infer their normative authority."""
    if not isinstance(values, dict) or set(values) - {'docs_context', 'business_sources'}:
        raise ValueError('上下文输入只能包含 docs_context 和 business_sources')
    docs = values.get('docs_context')
    if docs is not None:
        module = context_module('docs_context')
        _, entries = module.validate_policy(repo, docs)
        for entry in entries:
            path = module.source_path(repo, entry['path'])
            if not path.read_bytes().strip():
                raise ValueError('登记文档为空：' + entry['path'])
    sources = values.get('business_sources', {'schema': 'factory-local/business-source-policy/1', 'sources': []})
    context_module('business_sources').capture(repo, sources, {})
    return {'business_sources': sources, **({'docs_context': docs} if docs is not None else {})}


def read_context_input(repo, path):
    if path is None:
        return validate_context(repo, {})
    path = path.expanduser().resolve()
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('schema') != 'factory-local/context-input/1':
        raise ValueError('上下文输入 schema 必须为 factory-local/context-input/1')
    return validate_context(repo, {key: value[key] for key in value if key != 'schema'})


def install(repo, audit, context_file=None):
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
    # Validate the project's current-worktree quality contract before installing
    # any governance files. A missing coverage gate is an incomplete engineering
    # baseline, not a partially installed Factory repository.
    commands = quality_commands(repo)
    context = read_context_input(repo, context_file)
    audit.record('context-input-validation', 'PASSED',
                 docs_context_enabled='docs_context' in context,
                 normative_sources=len(context['business_sources']['sources']),
                 note='未配置 docs_context 时不启用文档自动选择和恢复漂移检查')
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
        workflow_version='factory-local/workflow/4',
        business_sources={'schema':'factory-local/business-source-policy/1', 'sources':[]},
        local_stage_order=['classify','skill-discovery','design-analysis','requirement','clarification','spec','spec-review','preflight','implement','review','verify'],
        rules_path=os.path.relpath(Path(os.environ.get('AWESOME_RULES_ROOT', str(repo.parent / 'awesome-rules'))).resolve(), repo),
        workflow_skills={
            'requirement':'factory-requirement-analysis',
            'clarification':'factory-clarification',
            'spec':'factory-spec-authoring',
            'spec-review':'factory-spec-review',
            'preflight':'factory-environment-preflight',
            'review':'factory-independent-review',
        },
        preflight_commands=[{'id':'python','argv':[sys.executable,'--version'],'minimum_version':[3,10,0]},
                            {'id':'node','argv':['node','--version'],'minimum_version':[24,19,0]},
                            {'id':'pnpm','argv':['pnpm','--version'],'minimum_version':[10,28,1]}],
        readiness_max_age_seconds=86400,
        review_basis='仓库 README.md、模块规范和任务 spec；按规则库技能执行',
        upstream_path=os.path.relpath(SOURCE, repo),
        intent_routing={'remote_handoff': {'enabled': True, 'executor': 'awesome-factory',
            'local_runner_may_execute_remote_actions': False}},
        quality_commands=commands)
    cfg.update(context)
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
    patterns = [line for line in IGNORE.splitlines() if line.startswith('/')]
    missing = [pattern for pattern in patterns if pattern not in content.splitlines()]
    if missing:
        exclude.write_text(content + '\n# Factory local generated files\n' + '\n'.join(missing) + '\n')
    for probe in ('artifacts/factory-local/probe.json', '.factory/__pycache__/probe.pyc', '.factory/tests/__pycache__/probe.pyc'):
        if subprocess.run(['git', 'check-ignore', '-q', '--', probe], cwd=repo).returncode != 0:
            raise ValueError('Factory 产物排除规则未生效：' + probe)


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
    validate_context(repo, {key: cfg[key] for key in ('docs_context', 'business_sources') if key in cfg})
    audit.record('project-context', 'PASSED', docs_context_enabled=cfg.get('docs_context') is not None,
                 note='仅显式登记的文档和现行规范可形成上下文证据')
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
    parser.add_argument('--context-file', type=Path,
                        help='仅 init：维护者审核的 docs_context/business_sources JSON，在安装和锁定前验证')
    args = parser.parse_args(argv)
    if args.command != 'init' and args.context_file is not None:
        parser.error('--context-file 仅用于 init；check 不修改配置或刷新锁')
    repo = args.repo.expanduser().resolve()
    try:
        if Path(git(repo, 'rev-parse','--show-toplevel')).resolve() != repo:
            raise ValueError('请指定 Git 仓库根目录')
        common = Path(git(repo, 'rev-parse','--path-format=absolute','--git-common-dir'))
        # Single writer for onboarding/verification; normal source work is not modified.
        with _lease_module.RepositoryLease(repo, 'setup'), (common / 'factory-onboarding.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('同一仓库正在初始化或检查，请等待结束')
            ensure_excludes(repo)
            audit = Audit(repo)
            head = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD'],
                                  capture_output=True, text=True)
            if head.returncode:
                audit.record('workspace-baseline', 'BLOCKED',
                             reason='缺少 Git 基线提交；先按接入指南完成工程基线的本地提交，再运行 init/check')
                return audit.finish()
            audit.record('workspace-baseline', 'PASSED', branch=git(repo,'branch','--show-current'),
                         head=head.stdout.strip(), changed=git(repo,'status','--porcelain'))
            try:
                if args.command == 'init':
                    install(repo, audit, args.context_file)
                check(repo, audit)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                audit.record('setup-error', 'BLOCKED', reason=str(exc))
            return audit.finish()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
