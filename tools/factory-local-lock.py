#!/usr/bin/env python3
"""Record reviewed shared inputs. Explicit --write only; normal checks never refresh pins."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

FILES = ['runtime/local/' + name for name in (
    '__init__.py', 'bootstrap.py', 'runner.py', 'workflow.py', 'acceptance.py',
    'worktree.py', 'revisions.py', 'business_sources.py', 'quality.py', 'skills.py', 'docs_context.py', 'figma.py')]
FILES += ['tools/factory-local-setup.py', 'tools/factory-local-lock.py']
DEFAULT_SKILLS = {
    'requirement': 'factory-requirement-analysis',
    'clarification': 'factory-clarification',
    'spec': 'factory-spec-authoring',
    'spec-review': 'factory-spec-review',
    'preflight': 'factory-environment-preflight',
    'review': 'factory-independent-review',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inputs(source):
    return [*(source / 'tests/local').glob('*.py'), *(source / 'templates/local').glob('*.py'), source / '.factory/guard.py', source / 'tools/evaluate-clarification.py']


def create(project, source=None):
    config = json.loads((project / '.factory/factory-local.json').read_text())
    source = source or (project / config['upstream_path']).resolve()
    rules = (project / config['rules_path']).resolve()
    skills = {}
    for name in config['workflow_skills'].values():
        for rel in [f'skills/{name}/SKILL.md', *sorted(p.relative_to(rules).as_posix() for p in (rules / 'skills' / name / 'references').glob('*.md'))]:
            path = rules / rel
            if not path.resolve().is_relative_to(rules) or path.is_symlink():
                raise ValueError('Skill path outside reviewed rules repository')
            skills[rel] = sha(path)
    def revision(repo):
        return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    return {'schema': 'factory-local/runtime-lock/1', 'api_version': 'factory-local/runtime/1',
            'workflow_version': 'factory-local/workflow/4',
            'provenance': {'factory_head': revision(source), 'rules_head': revision(rules),
                           'source_state': 'reviewed-working-tree-content',
                           'note': 'HEAD is provenance only; exact file bytes are pinned below.'},
            'files': {name: sha(source / name) for name in FILES}, 'skills': skills,
            'test_files': {p.relative_to(source).as_posix(): sha(p) for p in test_inputs(source)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--write', action='store_true', help='Explicitly accept reviewed shared file bytes')
    args = parser.parse_args()
    project = args.project.resolve()
    try:
        value = create(project)
        path = project / '.factory/runtime-lock.json'
        if args.write:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            print(path)
            return 0
        current = json.loads(path.read_text())
        # Git history can move without affecting the reviewed bytes.
        if any(current.get(key) != value[key] for key in ('schema', 'api_version', 'workflow_version', 'files', 'skills', 'test_files')):
            raise ValueError('Shared runtime/skills changed; review the diff before using --write')
        print('Shared runtime and Skill pins: PASS')
        return 0
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
