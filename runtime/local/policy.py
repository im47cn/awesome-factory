"""Canonical source boundaries shared by configuration and evidence readers."""
from pathlib import Path
import re

# File role, not business meaning. Credentials/governance programs never enter this list.
PUBLIC_CONFIGURATION = frozenset({'package.json', 'pnpm-lock.yaml', 'tsconfig.json', 'tsconfig.app.json',
                                  'tsconfig.node.json', 'vite.config.ts', 'vite.config.js', 'lefthook.yml',
                                  'eslint.config.js', 'eslint.config.mjs', 'eslint.config.ts'})


def relative_path(name: str, *, directory=False) -> str:
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('路径必须为规范的项目相对路径')
    value = name[:-1] if directory and name.endswith('/') else name
    if (not value or Path(value).is_absolute() or any(p in {'', '.', '..'} for p in value.split('/'))
            or re.match(r'^[A-Za-z]:', value)):
        raise ValueError('路径必须为规范的项目相对路径：' + name)
    return value


def perimeter(config: dict) -> list[str]:
    values = config.get('perimeter', [])
    if not isinstance(values, list):
        raise ValueError('perimeter 必须是相对路径数组')
    normalized = [relative_path(name, directory=True) for name in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError('perimeter 存在重复路径')
    return normalized


def sensitive_path(name: str) -> bool:
    parts = name.lower().split('/')
    return bool(re.search(r'(^|/)(?:\.env(?:\..*)?|\.envrc|\.npmrc|\.netrc|credentials(?:\.json)?|'
                          r'id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|.*\.(?:pem|key|p12|pfx|keystore))$', name, re.I)
                or any(parts[i:i + 2] in [['.aws', 'credentials'], ['.docker', 'config.json'],
                                         ['.codex', 'auth.json']] for i in range(len(parts) - 1)))
