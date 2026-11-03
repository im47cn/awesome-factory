"""Required workflow methods live in awesome-rules, separately from intent routing."""
import hashlib
import json
from pathlib import Path

STAGES = {'clarification', 'preflight', 'review'}


def load(root: Path, rules: Path, config: dict) -> dict:
    mapping = config.get('workflow_skills')
    if not isinstance(mapping, dict) or set(mapping) != STAGES:
        raise ValueError('workflow_skills 必须配置 clarification/preflight/review 三项公共能力')
    lock = json.loads((root / '.factory/runtime-lock.json').read_text())
    pinned = lock.get('skills')
    if not isinstance(pinned, dict) or not pinned:
        raise ValueError('runtime-lock 缺少公共 Skill 摘要')
    expected_files, result = set(), {}
    for stage, name in mapping.items():
        if not isinstance(name, str) or not name.replace('-', '').isalnum():
            raise ValueError('公共 Skill 名称无效')
        folder = rules / 'skills' / name
        names = [f'skills/{name}/SKILL.md', *sorted(p.relative_to(rules).as_posix() for p in (folder / 'references').glob('*.md'))]
        required = {'SKILL.md', 'references/protocol.md'}
        if stage == 'clarification':
            required.update({'references/business-rules.md', 'references/scenarios.md'})
        if not all((folder / item).is_file() for item in required):
            raise ValueError('公共 Skill 缺少阶段必需的参考文件：' + name)
        content, manifest = [], {}
        for rel in names:
            path = rules / rel
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(rules.resolve()):
                raise ValueError('公共 Skill 缺失或越界：' + rel)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if not data.strip() or pinned.get(rel) != digest:
                raise ValueError('公共 Skill 已变化，审核后重新锁定：' + rel)
            manifest[rel] = digest
            if path.relative_to(folder).as_posix() in required:
                content.append(data.decode())
        expected_files.update(names)
        result[stage] = {'name': name, 'files': manifest, 'content': '\n\n'.join(content)}
    if set(pinned) != expected_files:
        raise ValueError('公共 Skill lock 与阶段能力配置不一致')
    return result


def binding(capabilities: dict) -> dict:
    return {stage: {k: v for k, v in row.items() if k != 'content'} for stage, row in capabilities.items()}


def verify_code(root, config):
    lock = json.loads((root / '.factory/runtime-lock.json').read_text())
    source = (root / config['upstream_path']).resolve()
    for name, expected in lock['files'].items():
        path = source / name
        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('公共运行时执行中已变化：' + name)
