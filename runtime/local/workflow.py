"""Fail-closed workflow controls; source citations do not prove business semantics.

The operator supplies business decisions/readiness evidence. The runner owns stage
bindings and invokes review in a separate read-only, session-free process.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil

from .acceptance import sha, read_evidence
from .business_sources import CLARIFICATION, validate_snapshot, cited_rule

VERSION = 'factory-local/workflow/4'


class WorkflowPending(ValueError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status


def object_file(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'{path.name}: expected object')
    return value


def rows(value, name: str, *, nonempty=False) -> list[dict]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f'{name}: expected list')
    seen = set()
    for row in value:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not row['id'].strip() or row['id'] in seen):
            raise ValueError(f'{name}: missing/duplicate id')
        seen.add(row['id'])
    return value


def text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name}: nonempty text required')
    return value


def binding(task: Path, request: Path, task_id: str) -> dict:
    return {'workflow_version': VERSION, 'task_id': task_id, 'request_sha256': sha(request)}


def answers(task: Path, request: Path, task_id: str) -> dict:
    path = task / 'clarification-answers.json'
    if not path.exists():
        return {}
    data = object_file(path)
    expected = {'schema': 'factory-local/clarification-answers/1', **binding(task, request, task_id)}
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError('澄清答复与任务/需求不匹配')
    text(data.get('executor'), 'clarification executor')
    result = {}
    for row in rows(data.get('answers'), 'answers', nonempty=True):
        text(row.get('question'), 'question')
        result[row['id']] = text(row.get('decision'), 'decision')
    return result


def validate_clarification(task: Path, request: Path, task_id: str) -> dict:
    data = object_file(task / 'clarification-contract.json')
    expected = {'schema': CLARIFICATION, **binding(task, request, task_id),
                'requirement_sha256': sha(task / 'requirement.md'),
                'requirement_contract_sha256': sha(task / 'requirement-contract.json'),
                'business_sources_sha256': sha(task / 'business-sources.snapshot.json')}
    available = validate_snapshot(object_file(task / 'business-sources.snapshot.json'), binding(task, request, task_id))
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError('澄清产物与任务、需求或需求分析不匹配')
    confirmed = answers(task, request, task_id)
    wanted_answers_hash = sha(task / 'clarification-answers.json') if confirmed else None
    if data.get('answers_sha256') != wanted_answers_hash:
        raise ValueError('澄清答复已改变，必须重新澄清')

    def cited(source, rule):
        if not isinstance(source, dict):
            raise ValueError('规则缺少需求来源')
        quote = text(source.get('quote'), 'source.quote')
        if source.get('kind') == 'request':
            original = request.read_text()
        elif source.get('kind') == 'confirmation':
            original = confirmed.get(source.get('answer_id'), '')
        elif source.get('kind') == 'project-rule':
            original = cited_rule(source, rule, available)
        else:
            raise ValueError('业务预期只允许原始需求、显式答复或已登记的既有规范约束，不能从当前源码推导')
        if quote not in original:
            raise ValueError('规则引用未命中原始需求/确认答复')

    rules = rows(data.get('rules'), 'rules')
    for rule in rules:
        for key in ('given', 'when', 'then', 'forbidden'):
            text(rule.get(key), f'{rule["id"]}.{key}')
        cited(rule.get('source'), rule)
    blockers = [] if rules else ['no-observable-rules']
    obligations = []
    questions = rows(data.get('open_questions'), 'open_questions')
    requirement = object_file(task / 'requirement-contract.json')
    candidates = {row['id']: row for row in rows(requirement.get('behavior_candidates'), 'behavior candidates', nonempty=True)}
    coverage = rows(data.get('requirement_coverage'), 'requirement coverage', nonempty=True)
    if len(coverage) != len(candidates) or {row.get('candidate_id') for row in coverage} != set(candidates):
        raise ValueError('澄清必须逐项覆盖需求分析中的行为候选')
    rule_ids = {row['id'] for row in rules}
    question_ids = {row['id'] for row in questions}
    for row in coverage:
        candidate = candidates[row['candidate_id']]
        if row.get('resolution') == 'rule':
            rule_id = row.get('rule_id')
            rule = next((item for item in rules if item['id'] == rule_id), None)
            if not rule or row.get('question_id') is not None:
                raise ValueError('requirement_coverage 的 rule 解析必须引用唯一澄清规则')
            if (candidate.get('status') == 'needs-clarification'
                    and rule.get('source', {}).get('kind') != 'confirmation'):
                raise ValueError('待澄清行为没有真实答复时不能直接升级为确定规则')
        elif row.get('resolution') == 'question':
            question = next((item for item in questions if item['id'] == row.get('question_id')), None)
            if not question or question.get('blocking') is not True or row.get('rule_id') is not None:
                raise ValueError('requirement_coverage 的 question 解析必须引用有效问题')
        else:
            raise ValueError('requirement_coverage.resolution 无效')
    if not rule_ids.issuperset({row.get('rule_id') for row in coverage if row.get('resolution') == 'rule'}):
        raise ValueError('需求候选引用了未知澄清规则')
    conflicts = rows(data.get('source_conflicts'), 'source_conflicts')
    for conflict in conflicts:
        text(conflict.get('description'), 'source conflict description')
        sources = conflict.get('source_ids')
        question = next((q for q in questions if q['id'] == conflict.get('question_id')), None)
        if (not isinstance(sources, list) or not sources
                or not all(isinstance(s, str) and s in available for s in sources)
                or len(set(sources)) != len(sources)
                or not question or question.get('blocking') is not True):
            raise ValueError('已声明的来源冲突必须关联有效规范和阻塞问题，不能自报解决')
    for question in questions:
        text(question.get('question'), 'open question')
        if type(question.get('blocking')) is not bool:
            raise ValueError('澄清问题必须明确 blocking')
        if question['blocking']:
            blockers.append(question['id'])
            obligations.append({'id': question['id'], 'question': question['question']})
    for assumption in rows(data.get('assumptions'), 'assumptions'):
        text(assumption.get('statement'), 'assumption')
        if type(assumption.get('affects_behavior')) is not bool:
            raise ValueError('假设必须明确是否影响业务行为')
        confirmation = assumption.get('confirmed_by')
        if confirmation is not None and (not isinstance(confirmation, str) or confirmation not in confirmed):
            raise ValueError('假设的 confirmed_by 必须为空或真实操作者答复 ID，不能伪造确认来源')
        if assumption['affects_behavior']:
            if assumption.get('confirmed_by') not in confirmed:
                blockers.append(assumption['id'])
                obligations.append({'id': assumption.get('confirmed_by') or assumption['id'],
                                    'question': assumption['statement']})
            else:
                # The confirmed decision must be represented by an observable rule.
                if not any(r['source'].get('kind') == 'confirmation'
                           and r['source'].get('answer_id') == assumption['confirmed_by'] for r in rules):
                    blockers.append(assumption['id'])
    history_path = task / 'clarification-obligations.json'
    history = object_file(history_path) if history_path.exists() else None
    if history:
        if any(history.get(k) != v for k, v in binding(task, request, task_id).items()):
            raise ValueError('未解决澄清问题记录与任务不一致')
        answer_rows = object_file(task / 'clarification-answers.json')['answers'] if confirmed else []
        for old in rows(history.get('questions'), 'prior clarification questions'):
            answered = next((a for a in answer_rows if a['id'] == old['id']), None)
            cited_answer = any(r['source'].get('kind') == 'confirmation'
                               and r['source'].get('answer_id') == old['id'] for r in rules)
            if not answered or answered['question'] != old['question'] or not cited_answer:
                blockers.append(old['id'])
                if old['id'] not in {q['id'] for q in obligations}:
                    obligations.append(old)
    return {**expected, 'schema': 'factory-local/clarification-result/1',
            'status': 'pending' if blockers else 'passed', 'blockers': blockers,
            'obligations': obligations,
            'rules': [r['id'] for r in rules], 'contract_sha256': sha(task / 'clarification-contract.json')}


def validate_ac_mapping(task: Path, contract: dict) -> None:
    clarity = object_file(task / 'clarification-contract.json')
    ids = {r['id'] for r in clarity['rules']}
    allowed = set(contract.get('scope', {}).get('allowed_files', []))
    for rule in clarity['rules']:
        if rule.get('source', {}).get('kind') == 'project-rule' and not set(rule['targets']).issubset(allowed):
            raise ValueError('业务规范约束的适用文件不在 Spec 范围内')
    mapped = set()
    for ac in contract['acceptance']:
        refs = ac.get('clarification_ids')
        if not isinstance(refs, list) or not refs or not set(refs).issubset(ids):
            raise ValueError('每条验收条件必须关联冻结的澄清规则')
        mapped.update(refs)
    if mapped != ids:
        raise ValueError('Spec 遗漏已澄清的业务规则: ' + ', '.join(sorted(ids - mapped)))


def readiness_plan(task: Path, contract: dict, scope: str) -> list[dict]:
    if scope not in {'all', 'local-mock'}:
        raise ValueError('验收准备范围无效')
    environments = {row['environment'] for row in contract['verification'] if row['required']}
    plan = []
    for environment, kinds in [('mock', ['endpoint']),
                               ('test-environment', ['endpoint', 'contract', 'identity'])]:
        if environment in environments:
            plan.extend({'id': f'{environment}:{kind}', 'environment': environment,
                         'description': kind, 'deferred': scope == 'local-mock' and environment == 'test-environment'}
                        for kind in kinds)
    for item in contract['external_requirements']:
        if (not isinstance(item.get('id'), str) or not item['id'].strip()
                or item.get('environment') not in {'local', 'mock', 'test-environment'}):
            raise ValueError('外部前置项必须声明唯一 id 和 environment，AVAILABLE 文字不作为证明')
        plan.append({'id': 'external:' + item['id'], 'environment': item['environment'],
                     'description': item['description'],
                     'deferred': scope == 'local-mock' and item['environment'] == 'test-environment'})
    rows(plan, 'readiness resources')
    return plan


def readiness_request(task: Path, request: Path, task_id: str, plan: list[dict]) -> dict:
    return {'schema': 'factory-local/readiness-evidence/1', **binding(task, request, task_id),
            'spec_sha256': sha(task / 'spec.md'), 'contract_sha256': sha(task / 'spec-contract.json'),
            'checked_at': '', 'executor': '',
            'checks': [{'id': p['id'], 'environment': p['environment'], 'status': 'NOT_RUN',
                        'artifacts': []} for p in plan]}


def import_environment_evidence(source: Path, task: Path) -> None:
    """Keep original relative paths/proofs when the operator supplies an external receipt."""
    original = source.read_bytes()
    data = json.loads(original)
    if not isinstance(data, dict):
        raise ValueError('环境证据必须是对象')
    directory = task / 'environment-inputs' / hashlib.sha256(original).hexdigest()
    # Validate every proof before publishing a replacement working receipt.
    copies = []
    for row in rows(data.get('checks'), 'readiness checks'):
        artifacts = row.get('artifacts', [])
        if not isinstance(artifacts, list):
            raise ValueError('环境证据 artifacts 无效')
        for item in artifacts:
            name = text(item.get('path'), 'environment proof path')
            path = (source.parent / name).resolve()
            if (Path(name).is_absolute() or not path.is_relative_to(source.parent.resolve())
                    or not path.is_file() or not path.stat().st_size or sha(path) != item.get('sha256')):
                raise ValueError('外部环境证据文件越界、缺失或摘要不匹配')
            copies.append((path, directory / 'proofs' / f'{len(copies)}-{path.name}', item))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'original-receipt.json').write_bytes(original)
    for path, target, item in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if path != target:
            shutil.copy2(path, target)
        if sha(target) != item['sha256']:
            raise ValueError('环境证据归档期间发生变化')
        item['path'] = target.relative_to(task).as_posix()
    (task / 'environment-evidence.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def read_readiness(expected: dict, path: Path | None, archive: Path, *, max_age: int,
                   now: dt.datetime | None = None) -> tuple[dict, str | None]:
    if path is None:
        return {}, '尚未提供验收环境可用性证据'
    try:
        original = path.read_bytes()
        receipt = json.loads(original)
        if not isinstance(receipt, dict):
            raise ValueError('环境证据必须是对象')
        if receipt.get('workflow_version') != VERSION:
            raise ValueError('环境预检协议已过期')
        checked = dt.datetime.fromisoformat(receipt.get('checked_at', '').replace('Z', '+00:00'))
        current = now or dt.datetime.now(dt.timezone.utc)
        if checked.tzinfo is None or not -300 <= (current - checked).total_seconds() <= max_age:
            raise ValueError('环境可用性证据已过期或时间无效，请重新检查环境')
        # Reuse the strict ID/environment/artifact archiver. No source binding:
        # implementation changes source by design; this receipt binds frozen Spec.
        request = {**expected, 'source_sha256': None}
        if receipt.get('source_sha256') is not None:
            raise ValueError('环境预检不接受源码版本替代 Spec 绑定')
        results, error = read_evidence(request, path, archive, receipt_bytes=original)
        if error:
            return results, error
        for row in results.values():
            if row['status'] != 'passed':
                continue
            details = row.get('details')
            if not isinstance(details, dict):
                raise ValueError('环境可用项缺少可核对的地址/版本/测试身份信息')
            kind = row['id'].rsplit(':', 1)[-1] if not row['id'].startswith('external:') else 'external'
            required = {'endpoint': ['url', 'observation'], 'contract': ['version', 'source'],
                        'identity': ['alias', 'permissions'], 'external': ['observation']}[kind]
            for key in required:
                text(details.get(key), 'readiness ' + key)
        return results, None
    except (ValueError, TypeError, OSError) as exc:
        return {}, str(exc)


def readiness_result(plan: list[dict], results: dict, command_results: list[dict]) -> dict:
    missing = [p['id'] for p in plan if not p['deferred'] and results.get(p['id'], {}).get('status') != 'passed']
    failed_commands = [r['id'] for r in command_results if r['exit_code'] != 0]
    return {'schema': 'factory-local/preflight-result/1', 'status': 'pending' if missing or failed_commands else 'passed',
            'missing': missing, 'failed_commands': failed_commands,
            'deferred': [p['id'] for p in plan if p['deferred']],
            'resources': [{**p, 'status': results.get(p['id'], {}).get('status', 'NOT_RUN')} for p in plan],
            'commands': command_results}


def review_input(task: Path, request: Path, task_id: str, source_hash: str,
                 context_hash: str, run_id: str) -> dict:
    return {'schema': 'factory-local/review-input/1', **binding(task, request, task_id),
            'source_sha256': source_hash, 'spec_sha256': sha(task / 'spec.md'),
            'contract_sha256': sha(task / 'spec-contract.json'),
            'clarification_sha256': sha(task / 'clarification-contract.json'),
            'context_sha256': context_hash, 'review_run_id': run_id}


def validate_review(expected: dict, report: dict, context: Path, ac_ids: list[str], files: list[str]) -> dict:
    if not isinstance(report, dict) or any(report.get(k) != v for k, v in expected.items()):
        raise ValueError('独立审查缺失、协议过期或与当前输入版本不匹配')
    reviewed = report.get('reviewed_files')
    if not isinstance(reviewed, list) or len(set(reviewed)) != len(reviewed) or set(reviewed) != set(files):
        raise ValueError('独立审查未覆盖全部实际变更文件')
    checks = rows(report.get('acceptance'), 'review acceptance', nonempty=True)
    if {c['id'] for c in checks} != set(ac_ids):
        raise ValueError('独立审查遗漏或添加未知验收条件')

    cited_files = set()
    def evidence(items):
        if not isinstance(items, list) or not items:
            raise ValueError('审查结论缺少可定位的源码/需求证据')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('审查证据结构无效')
            name = text(item.get('path'), 'review evidence path')
            path = (context / name).resolve()
            if (Path(name).is_absolute() or not path.is_relative_to(context.resolve()) or not path.is_file()
                    or text(item.get('quote'), 'review quote') not in path.read_text(errors='replace')):
                raise ValueError('审查证据路径越界、缺失或引用不匹配')
            if name.startswith('source/'):
                cited_files.add(name[len('source/'):])
            elif name == 'task.diff':
                cited_files.update(f for f in files if f in item['quote'])
        return any(item['path'].startswith('source/') or item['path'] == 'task.diff' for item in items)
    blockers = []
    for check in checks:
        if check.get('verdict') not in {'covered', 'gap', 'uncertain'}:
            raise ValueError('审查验收结论无效')
        text(check.get('reason'), 'review reason')
        source_cited = evidence(check.get('evidence'))
        if check['verdict'] == 'covered' and not source_cited:
            raise ValueError('covered 结论必须引用当前源码/差异，不能只重复需求')
        if check['verdict'] != 'covered':
            blockers.append(check['id'])
    for finding in rows(report.get('findings'), 'review findings'):
        if finding.get('severity') not in {'critical', 'high', 'medium', 'low'}:
            raise ValueError('审查问题严重程度无效')
        text(finding.get('description'), 'review finding')
        evidence(finding.get('evidence'))
        if finding['severity'] in {'critical', 'high'}:
            blockers.append(finding['id'])
    if not blockers and set(files) - cited_files:
        raise ValueError('审查声称覆盖但未提供全部变更文件的定位证据')
    return {**report, 'status': 'pending' if blockers else 'passed', 'blockers': blockers,
            'independence': 'fresh-process-read-only-no-implementation-plan',
            'boundary': '独立模型审查及引用校验，不是人工批准，也不替代运行验收'}
