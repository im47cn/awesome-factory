"""Project-owned normative sources. Configuration grants eligibility, never the model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

POLICY = 'factory-local/business-source-policy/1'
SNAPSHOT = 'factory-local/business-sources/1'
CLARIFICATION = 'factory-local/clarification/2'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value
            or PurePosixPath(value).is_absolute()
            or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise ValueError('业务来源/适用范围必须为规范项目相对路径')
    return value


def validate_scopes(scopes):
    if (not isinstance(scopes, list) or not scopes
            or any(not isinstance(scope, str) for scope in scopes)
            or len(set(scopes)) != len(scopes)):
        raise ValueError('业务规范适用范围必须为非空且不重复的路径列表')
    for scope in scopes:
        relative(scope[:-1] if scope.endswith('/') else scope)
        if any(symbol in scope for symbol in '*?['):
            raise ValueError('业务规范适用范围仅支持精确路径或目录前缀')


def source_file(root: Path, name: str) -> Path:
    path = root / relative(name)
    if (not path.resolve().is_relative_to(root.resolve())
            or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root))
            or not path.is_file()):
        raise ValueError('业务来源缺失、越界或为符号链接：' + name)
    return path


def capture(root: Path, policy: dict | None, binding: dict) -> dict:
    policy = policy if policy is not None else {'schema': POLICY, 'sources': []}
    if not isinstance(policy, dict) or policy.get('schema') != POLICY or not isinstance(policy.get('sources'), list):
        raise ValueError('business_sources 配置协议无效')
    sources, excluded, seen, total = [], [], set(), 0
    for item in policy['sources']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in seen:
            raise ValueError('业务来源 ID 缺失或重复')
        seen.add(item['id'])
        name = relative(item.get('path'))
        if item.get('kind') not in {'normative', 'historical', 'record', 'guidance'} or item.get('status') not in {'active', 'reference', 'superseded'}:
            raise ValueError('业务来源类别/有效状态无效')
        if item['kind'] != 'normative' or item['status'] != 'active':
            excluded.append({'id': item['id'], 'path': name, 'reason': 'not-active-normative'})
            continue
        if item.get('authority') != 'project-maintained':
            raise ValueError('业务规范必须由项目维护配置登记，不能由模型自报批准')
        scopes = item.get('scope_paths')
        if not isinstance(scopes, list) or not scopes:
            raise ValueError('业务规范缺少适用范围')
        validate_scopes(scopes)
        data = source_file(root, name).read_bytes()
        digest = sha(data)
        if not data.strip() or digest != item.get('reviewed_sha256'):
            raise ValueError('业务规范缺失或版本漂移，需维护者核对并更新配置：' + name)
        total += len(data)
        if total > 131072:
            raise ValueError('业务规范上下文超过 128 KiB，请收窄登记范围；不截断规范')
        sources.append({'id': item['id'], 'path': name, 'sha256': digest,
                        'version': 'sha256:' + digest, 'scope_paths': scopes,
                        'authority': item['authority'], 'content': data.decode('utf-8')})
    return {'schema': SNAPSHOT, **binding,
            'policy_sha256': sha(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()),
            'sources': sources, 'excluded': excluded}


def validate_snapshot(data: dict, binding: dict) -> dict:
    if (not isinstance(data, dict) or data.get('schema') != SNAPSHOT
            or any(data.get(k) != v for k, v in binding.items())):
        raise ValueError('业务规则来源快照与任务/请求不匹配')
    values, seen = data.get('sources'), set()
    if not isinstance(values, list):
        raise ValueError('业务来源快照结构无效')
    for row in values:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in seen
                or not isinstance(row.get('content'), str)
                or sha(row['content'].encode()) != row.get('sha256')
                or row.get('version') != 'sha256:' + row['sha256']):
            raise ValueError('业务来源快照内容/版本损坏')
        relative(row.get('path'))
        validate_scopes(row.get('scope_paths'))
        if row.get('authority') != 'project-maintained':
            raise ValueError('业务来源快照登记权限无效')
        seen.add(row['id'])
    return {row['id']: row for row in values}


def cited_rule(source: dict, rule: dict, available: dict) -> str:
    row = available.get(source.get('source_id'))
    if (not row or source.get('source_sha256') != row['sha256']
            or source.get('source_version') != row['version']):
        raise ValueError('业务规则引用未登记、过期或被排除的来源')
    if rule.get('intent') != 'preserve':
        raise ValueError('既有业务规范只能作为保留约束，不能授权本次新增/变更行为')
    targets = rule.get('targets')
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(t, str) for t in targets) or len(set(targets)) != len(targets)):
        raise ValueError('业务约束必须声明实际适用文件')
    for target in targets:
        relative(target)
        if not any(target.startswith(scope) if scope.endswith('/') else target == scope for scope in row['scope_paths']):
            raise ValueError('业务规则超出登记的适用范围：' + target)
    return row['content']
