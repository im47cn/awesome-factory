#!/usr/bin/env python3
"""Run the repository-local Factory chain without issue tracker or PR side effects.

Stages: classify -> skills -> design -> requirement -> clarification -> spec ->
preflight -> implement -> independent review -> verify.
Each model stage gets a fresh omp process; deterministic checks remain runner-owned.
Stages write events and logs, and fail closed
when its required artifact is missing. Existing working-tree changes are recorded as
the task baseline; only changes introduced during implement are passed to guard.

For frontend work with Figma, design-analysis is an orchestration hand-off. Figma
facts and visual acceptance remain owned by awesome-rules; this runner never infers
pixels from a URL or turns page-load evidence into a visual pass.

Recovery: rerun the same --task/--request-file with --resume. A task lock excludes
concurrent writers. run-state.json is the current pointer; attempts/<id>/ holds
immutable logs, prerequisite/output snapshots and a SHA-256 manifest. Root stage
files are working copies, not historical evidence. Legacy tasks without stage
receipts must restart from classify. Changed prerequisites fail closed.

Every required Spec verification controls completion. Commands must match the
maintainer's registry; manual results use --verification-evidence and bind each
Spec ID/environment to task/request/source/spec hashes and actual artifacts.
Missing checks produce pending_acceptance (exit 3), failed required commands exit 1.
Receipt validation does not establish pixel semantics. Explicit linked revisions
preserve old attempts and restart from classify without inheriting passes.
No automatic retry/remote delivery is performed.
"""

from __future__ import annotations

import argparse
import contextvars
import datetime as dt
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from .skills import load as load_workflow_skills, binding as skill_binding, verify_code
from . import docs_context
from .business_sources import capture as capture_business_sources, CLARIFICATION
from .worktree import snapshot as worktree_snapshot, changed_files, legacy_candidates, GeneratedTransaction
from .acceptance import evidence_request, read_evidence, scope_result, summarize, trusted_commands
from .revisions import create_revision, validate_revision
from .workflow import (VERSION as WORKFLOW_VERSION, WorkflowPending, object_file, rows, text,
                      validate_clarification, validate_ac_mapping, readiness_plan,
                      readiness_request, read_readiness, readiness_result,
                      review_input, validate_review, import_environment_evidence,
                      answers as clarification_answers)


ROOT = Path.cwd().resolve()
FACTORY_LOCAL_CONFIG = ROOT / ".factory" / "factory-local.json"
AWESOME_RULES_ROOT = Path(
    os.environ.get("AWESOME_RULES_ROOT", str(ROOT.parent / "awesome-rules"))
).expanduser().resolve()
SAFE_LOCAL_TOOLS = frozenset({"read", "write", "edit", "grep", "lsp"})
STAGES = ["classify", "skill-discovery", "design-analysis", "requirement", "clarification",
          "spec", "spec-review", "preflight", "implement", "review", "verify"]
STAGE_OUTPUTS = {
    "classify": ["classification.json", "remote-handoff.json"],
    "skill-discovery": ["skill-selection.json", "skill-routing.snapshot.json"],
    "design-analysis": ["design-analysis.md", "design-orchestration.json", "design-contract.json", "visual-contract.json"],
    "requirement": ["requirement.md", "requirement-input.json", "requirement-contract.json", "requirement-source-snapshot.json",
                    "requirement-validation.json", "stage-integrity.json"],
    "clarification": ["business-sources.snapshot.json", "clarification-contract.json", "clarification-result.json", "stage-integrity.json"],
    "spec": ["spec.md", "spec-input.json", "spec-contract.json", "spec-validation.json", "stage-integrity.json"],
    "spec-review": ["spec-review-input.json", "spec-review-result.json", "stage-integrity.json"],
    "preflight": ["preflight-request.json", "preflight-result.json", "preflight-guide.json"],
    "implement": ["implement.md", "implemented-files.txt", "implement-changes.json", "scope-result.json", "stage-integrity.json"],
    "review": ["review-input.json", "review-result.json", "stage-integrity.json"],
    "verify": ["result.json", "verification.request.json", "verification-result.json",
               "verify-scope-result.json", "verify-preflight-result.json",
               "browser-acceptance.request.json", "visual-guard-result.json",
               "quality-gate-receipt.json"],
}
ACTIVE_ATTEMPT = contextvars.ContextVar("factory_attempt", default=None)
DOCS_STAGES = {"requirement", "clarification", "spec", "implement", "verify"}
for _docs_stage in DOCS_STAGES:
    STAGE_OUTPUTS[_docs_stage].append(f"{_docs_stage}-docs-context.json")


def docs_module():
    return docs_context


def stage_skills(config):
    # Method Skills are explicitly configured, independent of keyword capability routing.
    return load_workflow_skills(ROOT, (ROOT / config['rules_path']).resolve(), config)


def affirmed_doc_reference(text: str, term: str) -> bool:
    # A Markdown path's dot is not a sentence boundary. Preserve the established
    # negation parser for skills/remote routing; mask only dots inside this literal.
    protected = term.replace(".", "\ue000")
    visible = re.sub(re.escape(term), lambda match: match[0].replace(".", "\ue000"), text, flags=re.I)
    return bool(_affirmed_intents(visible, _literal_pattern(protected)))


def check_docs_context(task: Path, receipt: dict, config: dict) -> None:
    """Check immutable supplied sources before invalidating working stage outputs."""
    stage = receipt["stage"]
    name = f"{stage}-docs-context.json"
    expected = receipt.get("outputs", {}).get(name)
    policy = config.get("docs_context")
    if not expected:
        if policy and stage in DOCS_STAGES:
            raise ValueError(f"旧阶段 {stage} 缺少 docs 引用收据；请从 requirement 重跑")
        return
    archived = Path(receipt["artifact_dir"]) / name
    if not archived.is_file() or digest(archived) != expected:
        raise ValueError(f"docs 引用收据已损坏：{stage}；请从 requirement 重跑")
    record = json.loads(archived.read_text(encoding="utf-8"))
    binding = {key: receipt[key] for key in ("task_id", "stage", "attempt_id", "request_sha256")}
    changes = docs_module().check(ROOT, record, policy, binding)
    attempt = ACTIVE_ATTEMPT.get()
    if attempt:
        write_json(attempt.directory / f"{stage}-docs-resume-check.json", {
            "schema": "factory-local/docs-resume-check/1", "task_id": attempt.task_id,
            "attempt_id": attempt.id, "stage": stage, "previous_attempt_id": receipt["attempt_id"],
            "status": "blocked" if changes else "passed", "changes": changes,
        })
    if changes:
        raise ValueError("docs 引用资料已变化：" + json.dumps(changes, ensure_ascii=False)
                         + "；请检查差异，再用 --from-stage requirement --only-stage spec-review 重新分析，不自动沿用旧 Spec")


def capture_docs_context(stage: str, config: dict) -> Path | None:
    policy = config.get("docs_context")
    if policy is None:
        return None
    attempt = ACTIVE_ATTEMPT.get()
    # Recheck earlier supplied inputs even when continuing within the same invocation.
    for receipt in attempt.receipts.values():
        if STAGES.index(receipt["stage"]) < STAGES.index(stage):
            check_docs_context(attempt.task, receipt, config)
    request_text = attempt.request.read_text(encoding="utf-8")
    classification = json.loads((attempt.task / "classification.json").read_text())
    binding = {"task_id": attempt.task_id, "attempt_id": attempt.id,
               "stage": stage, "request_sha256": attempt.request_sha256,
               "consumer": "runner-validation" if stage == "verify" else "model-prompt"}
    module = docs_module()
    record = module.capture(ROOT, policy, binding, classification,
                            lambda term: affirmed_doc_reference(request_text, term))
    path = attempt.task / f"{stage}-docs-context.json"
    write_json(path, record)
    attempt.snapshot(path.name, "inputs")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execution_mode_for(text: str) -> str:
    """Execution intent is orthogonal to the technical domain."""
    return "analysis" if re.search(
        r"(?:只|仅|仅仅|只做|仅做)\s*(?:需求|代码|当前)?\s*分析|"
        r"(?:先)?(?:不|不要|无需|不用|禁止)\s*(?:改|修改|实现|写)(?:\s*(?:任何)?\s*(?:业务)?(?:代码|文件))?",
        text, re.I,
    ) else "development"


def resolve_authorization(task: Path, request: Path, args: argparse.Namespace, previous: dict) -> dict:
    """Only an explicit CLI grant may widen the persisted stage ceiling."""
    path = task / "authorization.json"
    task_id = task_identity(task)
    request_hash = digest(request)
    requested = args.authorize_through
    mode = execution_mode_for(request.read_text(encoding="utf-8"))
    if path.exists():
        auth = json.loads(path.read_text())
        if (not isinstance(auth, dict) or auth.get("schema") != "factory-local/authorization/1"
                or auth.get("task_id") != task_id or auth.get("request_sha256") != request_hash
                or auth.get("authorized_end_stage") not in STAGES):
            raise ValueError("授权记录无效或需求已变化；请使用新任务目录，不自动沿用授权")
    else:
        if previous and requested is None:
            raise ValueError("旧任务缺少阶段授权记录；须显式指定 --authorize-through spec-review 并从 classify 重跑，不能直接恢复编码")
        ceiling = args.only_stage or ("spec-review" if mode == "analysis" else "verify")
        if mode == "analysis" and STAGES.index(ceiling) > STAGES.index("spec-review"):
            ceiling = "spec-review"
        auth = {"schema": "factory-local/authorization/1", "task_id": task_id,
                "request_sha256": request_hash, "authorized_end_stage": ceiling, "grants": []}
    if requested is not None:
        auth = {**auth, "authorized_end_stage": requested, "grants": [*auth.get("grants", []), {
            "time": now(), "through": requested, "source": "explicit-cli",
        }]}
    ceiling = auth["authorized_end_stage"]
    start = previous.get("resume_from") if args.resume else (args.from_stage or "classify")
    if start not in STAGES:
        raise ValueError("恢复节点无效；请显式指定 --from-stage")
    if STAGES.index(start) > STAGES.index(ceiling):
        raise ValueError(f"恢复被授权上限 {ceiling} 阻断；进入 {start} 需要新的显式授权 --authorize-through <阶段>")
    if args.only_stage and STAGES.index(args.only_stage) > STAGES.index(ceiling):
        raise ValueError(f"--only-stage 超出授权上限 {ceiling}；不能用运行选项扩大授权")
    write_json(path, auth)
    return auth


def business_sources(task: Path, request: Path, config: dict, *, freeze=False) -> dict:
    current = capture_business_sources(ROOT, config.get('business_sources'), {
        'workflow_version': WORKFLOW_VERSION, 'task_id': task_identity(task), 'request_sha256': digest(request)})
    path = task / 'business-sources.snapshot.json'
    if freeze:
        write_json(path, current)
    elif not path.is_file() or object_file(path) != current:
        raise ValueError('业务规则来源或版本已变化，请核对后从 clarification 重跑')
    return current


def requirement_input(task: Path, request: Path) -> dict:
    return {
        "schema": "factory-local/requirement-input/1",
        "workflow_version": WORKFLOW_VERSION,
        "task_id": task_identity(task),
        "request_sha256": digest(request),
        "source_sha256": source_digest(),
        "classification_sha256": digest(task / "classification.json"),
        "skill_selection_sha256": digest(task / "skill-selection.json"),
        "design_contract_sha256": digest(task / "design-contract.json")
        if (task / "design-contract.json").is_file() else None,
    }


class RejectedRequirementSource(ValueError):
    """An explicitly disallowed observation must not become archived evidence."""


def guard_requirement_source(name: str, baseline: dict, *, business_only: bool) -> None:
    policy = load_local_factory_config()
    forbidden = ['.git', '.factory', 'MISSION.md', 'artifacts/factory-local']
    if business_only:
        forbidden.extend(Path(item).as_posix().rstrip('/') for item in policy.get('perimeter', []))
    sensitive = re.search(r'(^|/)(?:\.env(?:\..*)?|\.envrc|\.npmrc|\.netrc|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|.*\.(?:pem|key|p12|pfx|keystore))$', name, re.I)
    private_config = name in {'.aws/credentials', '.docker/config.json'}
    if (baseline.get('files', {}).get(name, {}).get('kind') != 'file' or sensitive or private_config
            or any(name == item or name.startswith(item + '/') for item in forbidden)):
        raise RejectedRequirementSource('需求引用触及未纳入基线、治理或凭据路径；已隔离该需求产物')


def _requirement_source(task: Path, request: Path, name: str, *, historical: bool = False, business_only: bool = False) -> Path:
    task_sources = {
        "request": request,
        "classification.json": task / "classification.json",
        "skill-selection.json": task / "skill-selection.json",
        "design-analysis.md": task / "design-analysis.md",
        "design-contract.json": task / "design-contract.json",
        "visual-contract.json": task / "visual-contract.json",
        "requirement-docs-context.json": task / "requirement-docs-context.json",
    }
    if name in task_sources:
        path = task_sources[name]
    else:
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
            raise ValueError("需求证据路径非法")
        lexical = ROOT / name
        if any(p.is_symlink() for p in [lexical, *lexical.parents] if p.is_relative_to(ROOT)):
            raise ValueError('需求证据不能经过符号链接')
        path = lexical.resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError("需求证据路径越界")
        if historical:
            manifest = task/'requirement-source-snapshot.json'
            receipt = object_file(task/'stage-receipts.json').get('requirement', {})
            archive = Path(receipt.get('artifact_dir', ''))
            expected = receipt.get('outputs', {}).get(manifest.name)
            if (not expected or any(p.is_symlink() for p in [manifest, archive/manifest.name, archive, *archive.parents] if p.is_relative_to(task))
                    or not archive.resolve().is_relative_to((task/'attempts').resolve())
                    or digest(manifest) != expected or digest(archive/manifest.name) != expected):
                raise ValueError('需求源码历史快照缺失或与分析收据不一致')
            frozen = object_file(manifest)
            inputs = object_file(task/'requirement-input.json')
            if (frozen.get('schema') != 'factory-local/requirement-source-snapshot/1'
                    or any(frozen.get(key) != inputs[key] for key in ('task_id', 'request_sha256', 'source_sha256'))
                    or hashlib.sha256(json.dumps(frozen.get('snapshot'), sort_keys=True, separators=(',', ':')).encode()).hexdigest() != inputs['source_sha256']):
                raise ValueError('需求源码历史快照与分析基线不一致')
            record = frozen.get('files', {}).get(path.relative_to(ROOT.resolve()).as_posix())
            if not isinstance(record, dict):
                raise ValueError('需求源码历史内容缺失')
            original = frozen['snapshot']['files'].get(path.relative_to(ROOT.resolve()).as_posix(), {})
            guard_requirement_source(path.relative_to(ROOT.resolve()).as_posix(), frozen['snapshot'], business_only=True)
            if original.get('kind') != 'file' or original.get('sha256') != record.get('sha256'):
                raise ValueError('需求源码历史内容与分析文件基线不一致')
            relative = Path(record.get('path', ''))
            saved = task/relative
            base = task/'requirement-sources'
            if (relative.is_absolute() or '..' in relative.parts
                    or any(p.is_symlink() for p in [saved, *saved.parents] if p.is_relative_to(task))
                    or not saved.resolve().is_relative_to(base.resolve())
                    or not base.resolve().is_relative_to(task.resolve())
                    or not saved.is_file() or digest(saved) != record.get('sha256')):
                raise ValueError('需求源码历史内容缺失、越界或摘要不匹配')
            path = saved
        elif path not in task_sources.values():
            guard_requirement_source(path.relative_to(ROOT.resolve()).as_posix(), worktree_snapshot(ROOT), business_only=business_only)
    if not path.is_file() or path.is_symlink():
        raise ValueError("需求证据缺失或为符号链接：" + name)
    return path


def freeze_requirement_sources(task: Path, request: Path, contract: dict, inputs: dict) -> None:
    """Capture actual source bytes at analysis time, independently of later retries."""
    baseline = worktree_snapshot(ROOT)
    base = task/'requirement-sources'
    if any(p.is_symlink() for p in [base, *base.parents] if p.is_relative_to(task)):
        raise ValueError('需求源码快照目录不能经过符号链接')
    directory = task/'requirement-sources'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    files = {}
    for row in contract['observations']:
        if row['kind'] != 'source':
            continue
        source = _requirement_source(task, request, row['source']['path'], business_only=True)
        name = source.relative_to(ROOT.resolve()).as_posix()
        entry = baseline['files'].get(name, {})
        guard_requirement_source(name, baseline, business_only=True)
        if name in files:
            continue
        saved = directory/str(len(files))
        saved.write_bytes(source.read_bytes())
        if digest(saved) != entry['sha256']:
            raise ValueError('需求源码快照与分析文件基线不一致')
        files[name] = {'path': saved.relative_to(task).as_posix(), 'sha256': digest(saved)}
    if (source_digest() != inputs['source_sha256']
            or hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != inputs['source_sha256']):
        raise ValueError('保存需求源码快照期间源码变化')
    write_json(task/'requirement-source-snapshot.json', {
        'schema': 'factory-local/requirement-source-snapshot/1',
        **{key: inputs[key] for key in ('task_id', 'request_sha256', 'source_sha256')}, 'snapshot': baseline, 'files': files})


def validate_requirement(task: Path, request: Path, *, check_source: bool = True, freeze_sources: bool = False) -> dict:
    errors, hashes = [], {}
    try:
        document = task / "requirement.md"
        contract_path = task / "requirement-contract.json"
        required(document)
        inputs = object_file(task / "requirement-input.json")
        contract = object_file(contract_path)
        current = requirement_input(task, request)
        if inputs.get("schema") != "factory-local/requirement-input/1":
            raise ValueError("requirement-input.json schema 无效")
        if contract.get("schema") != "factory-local/requirement-contract/1":
            raise ValueError("requirement-contract.json schema 无效")
        for key, value in current.items():
            contract_value = contract.get(key) if key != "schema" else "factory-local/requirement-input/1"
            if inputs.get(key) != value or contract_value != value:
                errors.append(f"{key} 与当前需求分析输入不一致")
        if not check_source:
            errors = [item for item in errors if not item.startswith("source_sha256 ")]
        text(contract.get("goal"), "requirement goal")
        scope = contract.get("scope")
        if not isinstance(scope, dict):
            raise ValueError("requirement scope 必须是对象")
        for key in ("in_scope", "non_goals"):
            value = scope.get(key)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
                errors.append(f"scope.{key} 必须是非空字符串数组")
        observations = rows(contract.get("observations"), "requirement observations", nonempty=True)
        observation_ids = {row["id"] for row in observations}
        for row in observations:
            if row.get("kind") not in {"request", "source", "docs", "design", "api", "unknown"}:
                errors.append(f"{row['id']}: observation.kind 无效")
            text(row.get("statement"), f"{row['id']}.statement")
            text(row.get("limitation"), f"{row['id']}.limitation")
            source = row.get("source")
            if not isinstance(source, dict):
                errors.append(f"{row['id']}: source 必须是对象")
                continue
            name = text(source.get("path"), f"{row['id']}.source.path")
            quote = text(source.get("quote"), f"{row['id']}.source.quote")
            try:
                evidence_source = _requirement_source(task, request, name,
                    historical=not check_source and row['kind'] == 'source', business_only=row['kind'] == 'source')
            except RejectedRequirementSource:
                # Preserve a diagnostic shell, never archive a rejected quote/body.
                write_json(contract_path, {'schema': 'factory-local/rejected-requirement/1',
                    'task_id': task_identity(task), 'reason': 'disallowed-source; re-run requirement'})
                document.write_text('# 需求产物已隔离\n\n引用了受限来源，请按允许范围重新分析。\n')
                raise
            if quote not in evidence_source.read_text(errors="replace"):
                errors.append(f"{row['id']}: 证据引用未命中原文")
        candidates = rows(contract.get("behavior_candidates"), "behavior candidates", nonempty=True)
        candidate_ids = {row["id"] for row in candidates}
        needs_clarification = set()
        for row in candidates:
            for key in ("given", "when", "then", "forbidden"):
                text(row.get(key), f"{row['id']}.{key}")
            refs = row.get("source_ids")
            if not isinstance(refs, list) or not refs or not set(refs).issubset(observation_ids):
                errors.append(f"{row['id']}: source_ids 未映射有效 observations")
            if row.get("status") not in {"explicit", "needs-clarification"}:
                errors.append(f"{row['id']}: status 无效")
            elif row["status"] == "needs-clarification":
                needs_clarification.add(row["id"])
        questions = rows(contract.get("open_questions"), "requirement open questions")
        covered_questions = set()
        for row in questions:
            text(row.get("question"), f"{row['id']}.question")
            if type(row.get("affects_behavior")) is not bool:
                errors.append(f"{row['id']}: affects_behavior 必须为布尔值")
            refs = row.get("candidate_ids")
            if not isinstance(refs, list) or not set(refs).issubset(candidate_ids):
                errors.append(f"{row['id']}: candidate_ids 无效")
            if row.get("affects_behavior"):
                covered_questions.update(refs)
        if needs_clarification - covered_questions:
            errors.append("needs-clarification 行为候选缺少影响行为的开放问题")
        selection = validate_selected_skills(task)
        selected = {row["name"]: row["path"] for row in selection.get("selected", [])}
        usages = rows(contract.get("skill_usage"), "skill usage")
        if len(usages) != len(selected) or {row.get("name") for row in usages} != set(selected):
            errors.append("skill_usage 必须覆盖且仅覆盖已选择 Skill")
        for row in usages:
            if row.get("path") != selected.get(row.get("name")):
                errors.append(f"{row.get('id')}: Skill 路径与选择记录不一致")
            text(row.get("reason"), f"{row.get('id')}.reason")
            if (not isinstance(row.get("applied_rules"), list) or not row["applied_rules"]
                    or not all(isinstance(item, str) and item.strip() for item in row["applied_rules"])):
                errors.append(f"{row.get('id')}: applied_rules 必须为非空字符串数组")
        acceptance = rows(contract.get("acceptance_candidates"), "acceptance candidates", nonempty=True)
        accepted_behavior_ids = set()
        for row in acceptance:
            text(row.get("criterion"), f"{row['id']}.criterion")
            text(row.get("verification_hint"), f"{row['id']}.verification_hint")
            refs = row.get("behavior_ids")
            if not isinstance(refs, list) or not refs or not set(refs).issubset(candidate_ids):
                errors.append(f"{row['id']}: behavior_ids 无效")
            else:
                accepted_behavior_ids.update(refs)
            if row.get("environment") not in {"local", "mock", "test-environment"}:
                errors.append(f"{row['id']}: environment 无效")
            if row.get("status") != "NOT_RUN":
                errors.append(f"{row['id']}: 需求分析不能自报验收通过")
        if accepted_behavior_ids != candidate_ids:
            errors.append("acceptance_candidates 未覆盖全部行为候选")
        body = document.read_text(encoding="utf-8")
        if current["task_id"] not in body or not all(row["id"] in body for row in candidates):
            errors.append("requirement.md 缺少 task_id 或行为候选 ID")
        hashes = {"requirement_sha256": digest(document), "contract_sha256": digest(contract_path)}
        if not errors and freeze_sources:
            freeze_requirement_sources(task, request, contract, inputs)
    except (OSError, ValueError, TypeError, KeyError, SystemExit) as exc:
        errors.append(str(exc))
    result = {"schema": "factory-local/requirement-validation/1", "task_id": task_identity(task),
              "status": "blocked" if errors else "passed", "errors": errors, **hashes,
              "boundary": "结构、来源和输入绑定校验；不代表业务候选已确认"}
    write_json(task / "requirement-validation.json", result)
    if errors:
        raise ValueError("需求分析内容校验失败: " + "; ".join(errors))
    return result


def spec_input(task: Path, request: Path) -> dict:
    return {"schema": "factory-local/spec-input/2", "task_id": task_identity(task),
            "request_sha256": digest(request), "requirement_sha256": digest(task / "requirement.md"),
            "requirement_contract_sha256": digest(task / "requirement-contract.json"),
            "workflow_version": WORKFLOW_VERSION,
            "clarification_sha256": digest(task / 'clarification-contract.json'),
            "business_sources_sha256": digest(task / 'business-sources.snapshot.json'),
            "skill_selection_sha256": digest(task / "skill-selection.json"),
            "source_sha256": source_digest()}


def validate_spec(task: Path, request: Path, config: dict, *, check_source: bool = True) -> dict:
    """Check structure and bindings; never execute model-proposed verification commands."""
    errors = []
    hashes = {}
    try:
        business_sources(task, request, config)
        clarity = validate_clarification(task, request, task_identity(task))
        if clarity['status'] != 'passed':
            raise ValueError('需求澄清尚未完成，不能生成或执行 Spec')
        document = task / "spec.md"
        required(document)
        contract_path = task / "spec-contract.json"
        contract = json.loads(contract_path.read_text())
        inputs = json.loads((task / "spec-input.json").read_text())
        if not isinstance(inputs, dict) or inputs.get("schema") != "factory-local/spec-input/2":
            raise ValueError("spec-input.json 必须是有效输入对象")
        hashes = {"spec_sha256": digest(document), "contract_sha256": digest(contract_path)}
        if not isinstance(contract, dict) or contract.get("schema") != "factory-local/spec-contract/2":
            raise ValueError("Spec 结构化清单 schema 无效")
        current = spec_input(task, request)
        for key in ("workflow_version", "task_id", "request_sha256", "requirement_sha256",
                    "requirement_contract_sha256", "clarification_sha256", "business_sources_sha256",
                    "skill_selection_sha256"):
            if contract.get(key) != current[key] or inputs.get(key) != current[key]:
                errors.append(f"{key} 与当前任务/输入不一致")
        if contract.get("source_sha256") != inputs.get("source_sha256"):
            errors.append("Spec 清单源码基线与运行器输入不一致")
        if check_source and inputs.get("source_sha256") != current["source_sha256"]:
            errors.append("源码基线已变化，须重跑 Spec")
        if not isinstance(contract.get("goal"), str) or not contract["goal"].strip():
            errors.append("缺少 goal")
        scope = contract.get("scope", {})
        files = scope.get("allowed_files") if isinstance(scope, dict) else None
        non_goals = scope.get("non_goals") if isinstance(scope, dict) else None
        if not isinstance(files, list) or not files:
            errors.append("scope.allowed_files 必须声明最小文件范围")
            files = []
        for name in files:
            if (not isinstance(name, str) or not name or Path(name).is_absolute()
                    or ".." in Path(name).parts or "\\" in name
                    or not (ROOT / name).resolve().is_relative_to(ROOT.resolve())):
                errors.append(f"修改文件路径非法: {name!r}")
                continue
            resolved = (ROOT / name).resolve()
            canonical = resolved.relative_to(ROOT.resolve()).as_posix()
            if canonical == "." or resolved.is_dir() or name.endswith("/"):
                errors.append(f"修改范围必须是文件，不能是根目录或目录: {name}")
            for protected in [".factory/", "MISSION.md", *config.get("perimeter", [])]:
                boundary = (ROOT / protected).resolve()
                if resolved == boundary or (protected.endswith("/") and resolved.is_relative_to(boundary)):
                    errors.append(f"修改范围触及治理周界: {name}")
                    break
        if not isinstance(non_goals, list) or not non_goals or not all(isinstance(x, str) and x.strip() for x in non_goals):
            errors.append("scope.non_goals 必须明确非目标")
        verification = contract.get("verification")
        if not isinstance(verification, list) or not verification:
            errors.append("缺少 verification 验证计划")
            verification = []
        verification_ids = set()
        required_verification_ids = set()
        for item in verification:
            if not isinstance(item, dict):
                errors.append("verification 条目须为对象")
                continue
            vid = item.get("id")
            if not isinstance(vid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', vid) or vid in verification_ids:
                errors.append("verification.id 缺失或重复")
                continue
            verification_ids.add(vid)
            if item.get("required") is True:
                required_verification_ids.add(vid)
            if item.get("status") != "NOT_RUN":
                errors.append(f"{vid}: Spec 计划状态必须为 NOT_RUN，不能自报测试通过")
            if item.get("environment") not in {"local", "mock", "test-environment"}:
                errors.append(f"{vid}: environment 无效")
            if type(item.get("required")) is not bool:
                errors.append(f"{vid}: 必须明确 required")
            if item.get("kind") == "command":
                if 'evidence_type' in item:
                    errors.append(f"{vid}: command 不接受人工证据类型 evidence_type")
                argv = item.get("argv")
                if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x.strip() for x in argv):
                    errors.append(f"{vid}: 缺少有效 argv")
            elif item.get("kind") == "manual":
                if item.get('evidence_type') not in {None, 'browser', 'integrity', 'other'}:
                    errors.append(f"{vid}: evidence_type 无效")
                steps = item.get("steps")
                if not isinstance(steps, list) or not steps or not all(isinstance(x, str) and x.strip() for x in steps):
                    errors.append(f"{vid}: 缺少人工验证步骤")
            else:
                errors.append(f"{vid}: kind 无效")
        acceptance = contract.get("acceptance")
        requirement_contract = object_file(task / "requirement-contract.json")
        requirement_ids = {row["id"] for row in rows(
            requirement_contract.get("behavior_candidates"), "behavior candidates", nonempty=True)}
        if (task / 'revision.json').is_file():
            validate_revision(ROOT, task)
            prior_required = json.loads((task / 'revision.json').read_text())['required_verification']
            current_plan = {item['id']: item for item in verification if isinstance(item, dict) and isinstance(item.get('id'), str)}
            for item in prior_required:
                current_item = current_plan.get(item['id'], {})
                if current_item.get('required') is not True or any(current_item.get(key) != item[key] for key in item if key != 'id'):
                    errors.append(f"修订不能丢弃父任务必验项或降低环境要求: {item['id']}")
        if not isinstance(acceptance, list) or not acceptance:
            errors.append("缺少 acceptance 验收条件")
            acceptance = []
        ids = set()
        text = document.read_text(encoding="utf-8")
        for item in acceptance:
            if not isinstance(item, dict):
                errors.append("acceptance 条目须为对象")
                continue
            cid = item.get("id")
            if not isinstance(cid, str) or not re.fullmatch(r"AC-[A-Za-z0-9_-]+", cid) or cid in ids:
                errors.append("AC ID 无效或重复")
                continue
            ids.add(cid)
            if cid not in text:
                errors.append(f"Spec 正文缺少 {cid}")
            if not isinstance(item.get("criterion"), str) or not item["criterion"].strip():
                errors.append(f"{cid}: criterion 缺失")
            refs = item.get("verification_ids")
            if (not isinstance(refs, list) or not refs or not all(isinstance(x, str) for x in refs)
                    or not set(refs).issubset(verification_ids)):
                errors.append(f"{cid}: 验收条件未映射有效验证方法")
            elif not set(refs) & required_verification_ids:
                errors.append(f"{cid}: 至少需要一种必需验证方法，不能全部选做")
            requirement_refs = item.get("requirement_ids")
            if (not isinstance(requirement_refs, list) or not requirement_refs
                    or not set(requirement_refs).issubset(requirement_ids)):
                errors.append(f"{cid}: 未映射有效需求行为候选")
        external = contract.get("external_requirements")
        if not isinstance(external, list):
            errors.append("必须显式声明 external_requirements，可为空列表")
        else:
            external_ids = set()
            for item in external:
                if (not isinstance(item, dict) or not isinstance(item.get("description"), str)
                        or not item["description"].strip()
                        or item.get("status") not in {"PENDING", "AVAILABLE"}):
                    errors.append("外部待补项缺描述或状态")
                    continue
                external_id = item.get("id")
                if (not isinstance(external_id, str) or not external_id.strip()
                        or external_id in external_ids):
                    errors.append("external_requirements.id 缺失或重复")
                else:
                    external_ids.add(external_id)
                if item.get("environment") not in {"local", "mock", "test-environment"}:
                    errors.append(f"{external_id}: external requirement environment 无效")
        if not errors:
            validate_ac_mapping(task, contract)
            mapped_requirement_ids = {
                requirement_id
                for item in acceptance
                for requirement_id in item.get("requirement_ids", [])
            }
            if mapped_requirement_ids != requirement_ids:
                errors.append("Spec 遗漏需求行为候选: " + ", ".join(sorted(requirement_ids - mapped_requirement_ids)))
            readiness_plan(task, contract, 'all')
        if current["task_id"] not in text or not all(isinstance(f, str) and f in text for f in files):
            errors.append("Spec 正文缺少任务身份或修改文件范围")
    except (OSError, ValueError, TypeError, KeyError, SystemExit) as exc:
        errors.append(str(exc))
    result = {"schema": "factory-local/spec-validation/1", "task_id": task_identity(task),
              "status": "blocked" if errors else "passed", "errors": errors, **hashes,
              "boundary": "结构、输入绑定和声明完整性校验；不代表业务语义、测试或人工审批通过"}
    write_json(task / "spec-validation.json", result)
    if errors:
        raise ValueError("Spec 内容校验失败: " + "; ".join(errors))
    return result


def spec_review_input(task: Path, request: Path, context_sha256: str, run_id: str) -> dict:
    return {
        "schema": "factory-local/spec-review-input/1",
        "workflow_version": WORKFLOW_VERSION,
        "task_id": task_identity(task),
        "request_sha256": digest(request),
        "requirement_sha256": digest(task / "requirement.md"),
        "requirement_contract_sha256": digest(task / "requirement-contract.json"),
        "clarification_sha256": digest(task / "clarification-contract.json"),
        "spec_sha256": digest(task / "spec.md"),
        "contract_sha256": digest(task / "spec-contract.json"),
        "source_sha256": object_file(task / "spec-input.json")["source_sha256"],
        "skill_selection_sha256": digest(task / "skill-selection.json"),
        "context_sha256": context_sha256,
        "review_run_id": run_id,
    }


def validate_spec_review(expected: dict, report: dict, context: Path,
                         requirement_ids: list[str], clarification_ids: list[str],
                         acceptance_ids: list[str], *, context_contents: dict | None = None) -> dict:
    if not isinstance(report, dict) or report.get("schema") != "factory-local/spec-review-result/1":
        raise ValueError("Spec 独立审查报告 schema 无效")
    if any(report.get(key) != value for key, value in expected.items() if key != "schema"):
        raise ValueError("Spec 独立审查报告与当前输入版本不匹配")

    def evidence(items, *, upstream=False):
        if not isinstance(items, list) or not items:
            raise ValueError("Spec 审查结论缺少证据")
        upstream_found = False
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Spec 审查证据结构无效")
            name = text(item.get("path"), "spec review evidence path")
            quote = text(item.get("quote"), "spec review evidence quote")
            path = (context / name).resolve()
            if (Path(name).is_absolute() or not path.is_relative_to(context.resolve())
                    or not path.is_file()):
                raise ValueError("Spec 审查证据路径越界、缺失或引用不匹配")
            body = (context_contents.get(path.relative_to(context.resolve()).as_posix(), b'').decode(errors='replace')
                    if context_contents is not None else path.read_text(errors='replace'))
            if quote not in body:
                raise ValueError("Spec 审查证据路径越界、缺失或引用不匹配")
            if name in {"request.md", "requirement-contract.json", "clarification-contract.json",
                        "business-sources.snapshot.json", "design-contract.json", "visual-contract.json"}:
                upstream_found = True
        if upstream and not upstream_found:
            raise ValueError("covered 结论必须引用 Spec 上游真值，不能由 Spec 自证")

    blockers = []
    for field, expected_ids in (("requirement_coverage", requirement_ids),
                                ("clarification_coverage", clarification_ids),
                                ("acceptance", acceptance_ids)):
        checks = rows(report.get(field), field, nonempty=True)
        if {row["id"] for row in checks} != set(expected_ids):
            raise ValueError(f"Spec 审查 {field} 未完整覆盖输入 ID")
        for row in checks:
            if row.get("verdict") not in {"covered", "gap", "uncertain"}:
                raise ValueError("Spec 审查 verdict 无效")
            text(row.get("reason"), "spec review reason")
            evidence(row.get("evidence"), upstream=row.get("verdict") == "covered")
            if row["verdict"] != "covered":
                blockers.append(row["id"])
    for finding in rows(report.get("findings"), "spec review findings"):
        if finding.get("severity") not in {"critical", "high", "medium", "low"}:
            raise ValueError("Spec 审查问题严重程度无效")
        text(finding.get("description"), "spec review finding")
        evidence(finding.get("evidence"))
        if finding["severity"] in {"critical", "high"}:
            blockers.append(finding["id"])
    return {**report, "status": "pending" if blockers else "passed", "blockers": blockers,
            "independence": "fresh-process-read-only-before-implementation",
            "boundary": "独立静态 Spec 语义审查，不是人工批准或运行验收"}


def independent_spec_review(args, task: Path, request: Path, contract: dict, config: dict) -> dict:
    attempt = ACTIVE_ATTEMPT.get()
    initial_source = source_digest()
    context = attempt.directory / "spec-review-context"
    context.mkdir()
    sources = {
        "request.md": request,
        "requirement.md": task / "requirement.md",
        "requirement-contract.json": task / "requirement-contract.json",
        "clarification-contract.json": task / "clarification-contract.json",
        "business-sources.snapshot.json": task / "business-sources.snapshot.json",
        "spec.md": task / "spec.md",
        "spec-contract.json": task / "spec-contract.json",
        "skill-selection.json": task / "skill-selection.json",
    }
    for optional in ("design-contract.json", "visual-contract.json"):
        if (task / optional).is_file():
            sources[optional] = task / optional
    for optional in ("requirement-docs-context.json", "spec-docs-context.json"):
        if (task / optional).is_file():
            sources[optional] = task / optional
    for name, source in sources.items():
        shutil.copy2(source, context / name)
    selection = validate_selected_skills(task)
    for row in selection.get("selected", []):
        for relative, expected_hash in row["files"].items():
            source = AWESOME_RULES_ROOT / relative
            if digest(source) != expected_hash:
                raise ValueError("创建 Spec 审查快照时领域 Skill 已变化：" + row["name"])
            destination = context / "domain-skills" / Path(relative).relative_to("skills")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for name, entry in worktree_snapshot(ROOT)["files"].items():
        source = ROOT / name
        if entry.get("kind") == "file" and source.is_file() and not source.is_symlink():
            destination = context / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    manifest = {path.relative_to(context).as_posix(): digest(path)
                for path in context.rglob("*") if path.is_file()}
    context_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if source_digest() != initial_source:
        raise ValueError("创建 Spec 审查快照期间源码发生变化")
    expected = spec_review_input(task, request, context_hash, uuid.uuid4().hex)
    write_json(task / "spec-review-input.json", {**expected, "context": str(context), "manifest": manifest})
    requirement_ids = [row["id"] for row in object_file(task / "requirement-contract.json")["behavior_candidates"]]
    clarification_ids = [row["id"] for row in object_file(task / "clarification-contract.json")["rules"]]
    acceptance_ids = [row["id"] for row in contract["acceptance"]]
    prompt = f"""执行已加载的 factory-spec-review，只读且全新无会话。
只读上下文目录：{context}。只输出一个 JSON，不修改任何文件。
原样复制绑定字段：{json.dumps(expected, ensure_ascii=False)}。
requirement_coverage 必须覆盖：{json.dumps(requirement_ids, ensure_ascii=False)}。
clarification_coverage 必须覆盖：{json.dumps(clarification_ids, ensure_ascii=False)}。
acceptance 必须覆盖：{json.dumps(acceptance_ids, ensure_ascii=False)}。
每项填写 verdict/reason/evidence；findings 无问题写 []。不得由 Spec 自证需求真值。"""
    process = run_model_stage("spec-review", prompt, task / "spec-review.log", args.model,
                              args.stage_timeout, {**config, "_review_cwd": str(context)})
    after = {path.relative_to(context).as_posix(): digest(path)
             for path in context.rglob("*") if path.is_file()}
    if after != manifest:
        raise ValueError("Spec 独立审查改写了只读上下文")
    try:
        report = json.loads(process.stdout)
    except (AttributeError, ValueError) as exc:
        raise ValueError("Spec 独立审查未返回有效 JSON") from exc
    result = validate_spec_review(expected, report, context, requirement_ids,
                                  clarification_ids, acceptance_ids)
    result.update(context=str(context), manifest=manifest)
    write_json(task / "spec-review-result.json", result)
    if result["status"] != "passed":
        raise WorkflowPending("pending_spec_review", "Spec 独立审查存在阻断问题：" + ", ".join(result["blockers"]))
    return result


def require_current_spec_review(task: Path, request: Path, contract: dict) -> dict:
    result = object_file(task / "spec-review-result.json")
    context = Path(result.get("context", ""))
    if (not context.is_absolute() or not context.is_dir()
            or not context.resolve().is_relative_to((task / "attempts").resolve())):
        raise ValueError("Spec 独立审查上下文缺失或越界")
    manifest = {path.relative_to(context).as_posix(): digest(path)
                for path in context.rglob("*") if path.is_file()}
    context_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    expected = spec_review_input(task, request, context_hash, result.get("review_run_id"))
    requirement_ids = [row["id"] for row in object_file(task / "requirement-contract.json")["behavior_candidates"]]
    clarification_ids = [row["id"] for row in object_file(task / "clarification-contract.json")["rules"]]
    acceptance_ids = [row["id"] for row in contract["acceptance"]]
    checked = validate_spec_review(expected, result, context, requirement_ids,
                                   clarification_ids, acceptance_ids)
    if checked["status"] != "passed":
        raise WorkflowPending("pending_spec_review", "Spec 独立审查尚未通过")
    return checked


def task_identity(task: Path) -> str:
    """Return the stable local task ID; attempts must not create a new task identity."""
    path = task / "task-identity.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"本地任务身份不可用：{path}: {exc}") from exc
        task_id = value.get("task_id") if isinstance(value, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"local-[0-9]{8}-[0-9a-f]{8}", task_id):
            raise ValueError(f"本地任务身份格式无效：{path}")
        return task_id
    task_id = f"local-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"
    write_json(path, {"schema": "factory-local/task-identity/1", "task_id": task_id,
                      "mode": "local", "issue_id": None, "created_at": now()})
    return task_id


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def interrupt_run(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def source_digest() -> str:
    """Bind HEAD, content/kind/mode and index entries; artifacts stay outside Git scope."""
    data = json.dumps(worktree_snapshot(ROOT), sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(data).hexdigest()


class CommandFailure(RuntimeError):
    def __init__(self, cmd: list[str], rc: int, reason: str):
        super().__init__(f"{reason}: {' '.join(cmd[:2])} (rc={rc})")
        self.command = cmd[:2]  # Never put the AI prompt in the state/event stream.
        self.rc = rc
        self.reason = reason


class Attempt:
    """Root files are working copies; event evidence lives in immutable attempts."""

    def __init__(self, task: Path, request: Path, authorization: dict | None = None):
        self.task = task
        self.task_id = task_identity(task)
        self.authorization = authorization or {}
        self.capabilities = skill_binding(stage_skills(load_local_factory_config()))
        self.runtime_lock_sha256 = digest(ROOT / ".factory/runtime-lock.json")
        self.request_sha256 = digest(request)
        self.request = request.resolve()
        self.id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
        self.directory = task / "attempts" / self.id
        self.directory.mkdir(parents=True)
        self.stage = "setup"
        self.receipts_path = task / "stage-receipts.json"
        self.receipts = {}
        self.state = {"schema": "factory-local/run-state/1", "task_id": self.task_id,
                      'workflow_version': WORKFLOW_VERSION,
                      "attempt_id": self.id,
                      "authorized_end_stage": self.authorization.get("authorized_end_stage"),
                      "request_sha256": self.request_sha256, "artifact_dir": str(self.directory),
                      "status": "running", "stage": self.stage}
        shutil.copy2(request, self.directory / "request.md")
        for name in ("authorization.json", "task-identity.json", "run-state.json", "stage-receipts.json", "result.json",
                     'execution-policy.json', 'clarification-answers.json', 'clarification-obligations.json',
                     'environment-evidence.json'):
            self.snapshot(name, "inputs")

    def save_state(self, status: str, **extra: object) -> None:
        self.state.update(status=status, stage=self.stage, updated_at=now(), **extra)
        restart = ["--resume"] if self.state.get("resume_from") in STAGES else ["--from-stage", "classify"]
        self.state["recovery_command"] = shlex.join([
            sys.executable, str(ROOT / ".factory/run.py"), "--task",
            str(self.task.relative_to(ROOT / "artifacts/factory-local")),
            "--request-file", str(self.request), *restart,
        ]) if status != "completed" else None
        write_json(self.task / "run-state.json", self.state)

    def snapshot(self, name: str, folder: str) -> Path | None:
        path = self.task / name
        if not path.is_file():
            return None
        target = self.directory / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    def prepare(self, start: str) -> None:
        self.stage = start
        index = STAGES.index(start)
        config = load_local_factory_config()
        # Reject legacy, changed or partial prerequisite outputs rather than trusting existence.
        for stage in STAGES[:index]:
            if stage == "design-analysis" and not self.receipts.get(stage):
                classification = json.loads((self.task / "classification.json").read_text())
                if not classification.get("has_design"):
                    continue
            receipt = self.receipts.get(stage)
            if (not receipt or receipt.get('workflow_version') != WORKFLOW_VERSION
                    or receipt["request_sha256"] != self.request_sha256):
                raise ValueError(f"恢复前置阶段无有效收据：{stage}；请从 classify 重新执行")
            if (receipt.get('runtime_lock_sha256') != self.runtime_lock_sha256
                    or receipt.get('workflow_capabilities') != self.capabilities):
                raise ValueError('公共运行时或阶段 Skill 已升级；请审核后从 classify 重跑，不继承旧通过状态')
            check_docs_context(self.task, receipt, config)
            for name, expected in receipt["outputs"].items():
                path = self.task / name
                archived = Path(receipt["artifact_dir"]) / name
                if (not path.is_file() or digest(path) != expected
                        or not archived.is_file() or digest(archived) != expected):
                    raise ValueError(f"恢复前置产物已改变或缺失：{name}；请重跑 {stage}")
                self.snapshot(name, "inputs")
            if stage == "implement" and receipt.get("source_sha256") != source_digest():
                raise ValueError("编码后源码已改变；请从 implement 重新执行后验证")
            if stage == "implement" and (self.task / "implement-changes.json").is_file():
                changes = json.loads((self.task / "implement-changes.json").read_text())
                baseline = self.task / "worktree-baseline.json"
                if not baseline.is_file() or digest(baseline) != changes.get("baseline_sha256"):
                    raise ValueError("编码内容基线已改变或缺失；拒绝恢复验证")
        for stage in STAGES[index:]:
            self.receipts.pop(stage, None)
            for name in STAGE_OUTPUTS[stage]:
                if self.snapshot(name, "superseded"):
                    (self.task / name).unlink()
        write_json(self.receipts_path, self.receipts)
        self.save_state("running", resume_from=start)
        write_json(self.task / "result.json", {**self.state, "overall_status": "running"})

    def event(self, stage: str, status: str, extra: dict) -> dict:
        if stage in STAGES:
            self.stage = stage
        if status in {'started', 'completed', 'passed', 'unavailable'}:
            current = skill_binding(stage_skills(load_local_factory_config()))
            if (current != self.capabilities or digest(ROOT / '.factory/runtime-lock.json') != self.runtime_lock_sha256):
                raise ValueError('执行期间公共运行时或 Skill 绑定已变化')
            if status == 'started' and stage in current:
                write_json(self.directory / (stage + '-skill.json'), current[stage])
        # Snapshot before publishing a terminal event, so its paths survive retries.
        if status not in {"started", "running"}:
            outputs = {}
            for name in STAGE_OUTPUTS.get(stage, []):
                target = self.snapshot(name, "outputs")
                if target:
                    outputs[name] = digest(target)
            if stage in STAGES and status in {"completed", "passed", "unavailable"}:
                self.receipts[stage] = {"schema": "factory-local/stage-snapshot/1", "task_id": self.task_id,
                                        "workflow_version": WORKFLOW_VERSION,
                                        "runtime_lock_sha256": self.runtime_lock_sha256,
                                        "workflow_capabilities": self.capabilities,
                                        "stage": stage, "attempt_id": self.id, "request_sha256": self.request_sha256,
                                        "outputs": outputs, "artifact_dir": str(self.directory / "outputs")}
                if stage in {"implement", "review"}:
                    self.receipts[stage]["source_sha256"] = source_digest()
                write_json(self.receipts_path, self.receipts)
        for key in ("artifact", "handoff_artifact", "remote_handoff_artifact"):
            artifact = extra.get(key)
            if artifact:
                path = Path(artifact)
                if path.parent == self.task:
                    saved = self.snapshot(path.name, "outputs")
                    if saved:
                        extra = {**extra, key: str(saved)}
        self.save_state("running", resume_from=self.stage)
        return {**extra, "task_id": self.task_id, "attempt_id": self.id, "artifact_dir": str(self.directory),
                "logs": [str(p) for p in sorted(self.directory.glob("*.log"))]}

    def finish(self, status: str, rc: int, **extra: object) -> None:
        if status in {"failed", "interrupted"}:
            for name in STAGE_OUTPUTS.get(self.stage, []):
                self.snapshot(name, "partial")
        self.save_state(status, rc=rc, **{"resume_from": self.stage, **extra})
        result = self.task / "result.json"
        payload = json.loads(result.read_text()) if result.exists() and status not in {"failed", "interrupted"} else {}
        write_json(result, {**payload, **self.state, "overall_status": status})
        self.snapshot("result.json", "final")
        write_json(self.directory / "state.json", self.state)
        write_json(self.directory / "manifest.json", {
            "schema": "factory-local/attempt-manifest/1", "task_id": self.task_id,
            "attempt_id": self.id, "request_sha256": self.request_sha256,
            "files": {str(p.relative_to(self.directory)): digest(p)
                      for p in sorted(self.directory.rglob("*")) if p.is_file()},
        })


def browser_acceptance_status(task: Path, classification: dict, evidence: Path | None) -> tuple[str, str]:
    """Compatibility entry; generic browser-only receipts no longer satisfy a Spec."""
    if not classification.get('needs_browser_acceptance'):
        return 'not_required', '本任务不要求浏览器验收'
    attempt = ACTIVE_ATTEMPT.get()
    contract = json.loads((task / 'spec-contract.json').read_text())
    expected = evidence_request(task, attempt.task_id, attempt.request_sha256, source_digest(), contract)
    write_json(task / 'browser-acceptance.request.json', expected)
    results, error = read_evidence(expected, evidence, attempt.directory / 'verification-evidence')
    verified = summarize(contract, results, quality_passed=True, visual_status='not_required', needs_browser=True)
    return verified['browser_status'], error or ('未通过必验项：' + ', '.join(verified['missing_required'])
                                               if verified['missing_required'] else '按 Spec 验收通过')


def enforce_scope(task: Path, contract: dict, files: list[str], *, phase: str = 'implement') -> None:
    result = scope_result(ROOT, contract['scope']['allowed_files'], files)
    write_json(task / ('verify-scope-result.json' if phase == 'verify' else 'scope-result.json'), result)
    if result['status'] != 'passed':
        raise ValueError('实际修改超出 Spec 范围：' + ', '.join(result['unexpected_files']))


def verify_spec(task: Path, contract: dict, config: dict, evidence: Path | None,
                source_hash: str, classification: dict, visual_status: str) -> dict:
    attempt = ACTIVE_ATTEMPT.get()
    expected = evidence_request(task, attempt.task_id, attempt.request_sha256, source_hash, contract)
    write_json(task / 'verification.request.json', expected)
    results, evidence_error = read_evidence(expected, evidence, attempt.directory / 'verification-evidence')
    registry = trusted_commands(config)
    for index, item in enumerate(contract['verification']):
        if item['kind'] != 'command':
            continue
        command = registry.get(tuple(item['argv']))
        if command is None or item['environment'] != 'local':
            results[item['id']] = {'status': 'blocked', 'reason': '命令未登记到可信本地验证入口，未执行'}
            continue
        before = worktree_snapshot(ROOT)
        transaction = GeneratedTransaction(ROOT, config.get('generated_files', []),
                                           attempt.directory / f'verification-{index}-generated')
        log = attempt.directory / f'verification-{index}.log'
        try:
            process = run(command['argv'], log=log, check=False, timeout=900)
            rc = process.returncode
        except CommandFailure as exc:
            rc = exc.rc
        finally:
            transaction.restore()
        if changed_files(before, worktree_snapshot(ROOT)):
            raise ValueError('Spec 验证命令改写源码或暂存区，已阻断并保留现场')
        results[item['id']] = {'status': 'passed' if rc == 0 else 'failed', 'environment': 'local',
                               'exit_code': rc, 'argv': command['argv'],
                               'artifacts': [{'path': str(log), 'sha256': digest(log)}]}
    result = summarize(contract, results, quality_passed=True, visual_status=visual_status,
                       needs_browser=classification.get('needs_browser_acceptance', False))
    result.update(task_id=attempt.task_id, attempt_id=attempt.id, source_sha256=source_hash,
                  request_sha256=attempt.request_sha256, spec_sha256=expected['spec_sha256'],
                  contract_sha256=expected['contract_sha256'], evidence_error=evidence_error)
    write_json(task / 'verification-result.json', result)
    return result


def check_preflight(args, task: Path, request: Path, contract: dict, config: dict, *, commands=True) -> dict:
    attempt = ACTIVE_ATTEMPT.get()
    policy = object_file(task / 'execution-policy.json')
    if (policy.get('workflow_version') != WORKFLOW_VERSION or policy.get('task_id') != attempt.task_id
            or policy.get('request_sha256') != digest(request)):
        raise ValueError('验收准备策略与当前任务不匹配')
    plan = readiness_plan(task, contract, policy['acceptance_scope'])
    expected = readiness_request(task, request, attempt.task_id, plan)
    guide = stage_skills(config)['preflight']
    write_json(task / 'preflight-guide.json', guide)
    print('环境证据准备方法：' + str(task / 'preflight-guide.json'), flush=True)
    write_json(task / 'preflight-request.json', expected)
    evidence_path = task / 'environment-evidence.json'
    registry = config.get('preflight_commands')
    if not isinstance(registry, list) or not registry:
        raise ValueError('缺少维护者配置的环境预检命令，不能跳过本地环境检查')
    if not commands:
        prior = object_file(task / 'preflight-result.json')
        if prior.get('execution_policy_sha256') != digest(task / 'execution-policy.json'):
            raise ValueError('验收准备范围已变化，请从 preflight 重跑')
    command_results = []
    for index, item in enumerate(registry):
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not isinstance(item.get('argv'), list) or not item['argv']
                or not all(isinstance(x, str) and x for x in item['argv'])):
            raise ValueError('环境预检命令配置无效')
        log = attempt.directory / f'{attempt.stage}-preflight-{index}.log'
        before = worktree_snapshot(ROOT)
        try:
            process = run(item['argv'], log=log, check=False, timeout=30)
            rc = process.returncode
            minimum = item.get('minimum_version')
            if minimum is not None:
                found = re.search(r'(?<![\d.])(\d+)\.(\d+)\.(\d+)(?!\d)', process.stdout)
                if (not isinstance(minimum, list) or len(minimum) != 3
                        or not all(type(v) is int for v in minimum)):
                    raise ValueError('环境最低版本配置无效')
                if not found or tuple(map(int, found.groups())) < tuple(minimum):
                    rc = 2
        except CommandFailure as exc:
            rc = exc.rc
        if changed_files(before, worktree_snapshot(ROOT)):
            raise ValueError('环境预检命令改写源码或暂存区，已阻断并保留现场')
        command_results.append({'id': item['id'], 'argv': item['argv'], 'exit_code': rc,
                                'log': str(log), 'sha256': digest(log)})
    rows, error = read_readiness(expected, evidence_path if evidence_path.is_file() else None,
                                 attempt.directory / ('preflight-evidence' if commands else 'verify-environment-evidence'),
                                 max_age=config.get('readiness_max_age_seconds', 86400))
    if not plan:
        error = None
    result = {**readiness_result(plan, rows, command_results), 'workflow_version': WORKFLOW_VERSION,
              'task_id': attempt.task_id, 'request_sha256': attempt.request_sha256,
              'acceptance_scope': policy['acceptance_scope'], 'evidence_error': error,
              'execution_policy_sha256': digest(task / 'execution-policy.json'),
              'spec_sha256': expected['spec_sha256'], 'contract_sha256': expected['contract_sha256']}
    write_json(task / ('preflight-result.json' if commands else 'verify-preflight-result.json'), result)
    if result['status'] != 'passed':
        raise WorkflowPending('pending_environment', '验收环境预检未通过：' + ', '.join(result['missing'] + result['failed_commands']))
    return result


def capture_implementation_before(task: Path, files: list[str], before: dict) -> dict:
    attempt = ACTIVE_ATTEMPT.get()
    directory = attempt.directory / 'implementation-before'
    directory.mkdir()
    manifest = {}
    for index, name in enumerate(files):
        path = ROOT / name
        entry = before['files'].get(name, {'kind': 'missing'})
        record = {'kind': entry['kind'], 'source_sha256': entry.get('sha256')}
        if path.is_file() and not path.is_symlink():
            saved = directory / str(index)
            shutil.copy2(path, saved)
            if digest(saved) != entry.get('sha256'):
                raise ValueError('实施前内容快照不一致，拒绝使用混合版本')
            record.update(path=str(saved), sha256=digest(saved))
        elif entry['kind'] not in {'missing', 'symlink'}:
            raise ValueError('实施前内容快照缺失')
        manifest[name] = record
    return manifest


def independent_review(args, task: Path, request: Path, contract: dict, config: dict) -> dict:
    """A fresh read-only process sees requirements/AC/source, never implementation plans.

    The context directory omits ignored task artifacts and contains byte snapshots.
    Read-only tools and post-call integrity checks are not an OS security sandbox.
    """
    attempt = ACTIVE_ATTEMPT.get()
    initial_source = source_digest()
    context = attempt.directory / 'review-context' 
    context.mkdir()
    shutil.copy2(request, context / 'request.md')
    shutil.copy2(task / 'clarification-contract.json', context / 'clarification-contract.json')
    shutil.copy2(task / 'business-sources.snapshot.json', context / 'business-sources.snapshot.json')
    if (task / 'clarification-answers.json').is_file():
        shutil.copy2(task / 'clarification-answers.json', context / 'clarification-answers.json')
    write_json(context / 'acceptance.json', {'goal': contract['goal'], 'acceptance': contract['acceptance'],
                                          'verification': contract['verification'], 'scope': contract['scope']})
    actual = [f for f in (task / 'implemented-files.txt').read_text().splitlines() if f]
    changes = object_file(task / 'implement-changes.json')
    write_json(context / 'changes.json', {'files': actual, 'source_snapshot': worktree_snapshot(ROOT),
                                        'attribution': 'task.diff compares implementation entry bytes; head.diff is historical reference only'})
    task_diff = []
    for name in actual:
        record = changes.get('content_before', {}).get(name)
        if not record:
            raise ValueError('缺少实施前文件内容证据，请从 implement 重跑，不能用 HEAD 差异归属已有脏改动')
        before_text = ''
        if record['kind'] == 'file':
            original = Path(record['path'])
            if (not original.resolve().is_relative_to((task / 'attempts').resolve())
                    or not original.is_file() or digest(original) != record['sha256']
                    or record['sha256'] != changes['stage_before']['files'][name]['sha256']):
                raise ValueError('实施前内容证据缺失、越界或损坏')
            saved = context / 'before' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, saved)
            before_text = saved.read_text(errors='replace')
        elif record['kind'] != 'missing':
            raise ValueError('不能对特殊文件生成准确审查差异，需人工处理')
        elif changes['stage_before']['files'].get(name, {}).get('kind', 'missing') != 'missing':
            raise ValueError('实施前缺失文件声明与内容基线不一致')
        after_path = ROOT / name
        after_text = after_path.read_text(errors='replace') if after_path.is_file() else ''
        task_diff.extend(difflib.unified_diff(before_text.splitlines(keepends=True), after_text.splitlines(keepends=True),
                                            fromfile='before/' + name, tofile='source/' + name))
    (context / 'task.diff').write_text(''.join(task_diff))
    # Copy source independently of Git HEAD so unstaged and untracked task code is reviewed.
    for name in worktree_snapshot(ROOT)['files']:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            saved = context / 'source' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
    if actual:
        diff = run(['git', 'diff', 'HEAD', '--', *actual], check=True).stdout
        (context / 'head.diff').write_text(diff)
    manifest = {p.relative_to(context).as_posix(): digest(p) for p in context.rglob('*') if p.is_file()}
    context_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if source_digest() != initial_source:
        raise ValueError('创建独立审查快照期间源码发生变化，拒绝审查混合版本')
    expected = review_input(task, request, attempt.task_id, initial_source, context_hash, uuid.uuid4().hex)
    write_json(task / 'review-input.json', {**expected, 'context': str(context), 'files': actual, 'manifest': manifest})
    ac_ids = [a['id'] for a in contract['acceptance']]
    review_checks = [{'id': cid, 'verdict': 'covered|gap|uncertain', 'reason': '判断理由',
                      'evidence': [{'path': 'source/项目内路径', 'quote': '快照中存在的精确片段'}]} for cid in ac_ids]
    prompt = f"""执行已加载的公共独立审查 Skill，只读且全新无会话。
只读上下文目录：{context}；本次实际文件和 AC 由下面的绑定指定，不得改名或补零。
只输出 JSON；以下是编排器的输出约束。
原样复制绑定字段：{json.dumps(expected, ensure_ascii=False)}。
增加 reviewed_files={json.dumps(actual)}；每个 covered 结论必须引用源码或差异，所有实际变更文件均需定位引用，不能只重复需求。
acceptance 使用下面数组并填写结论，id 必须逐字复制，不能补零或重命名：{json.dumps(review_checks, ensure_ascii=False)}。
findings 为问题数组，每项 {{"id":"R-01","severity":"critical|high|medium|low","description":"问题及影响","evidence":[{{"path":"相对上下文路径","quote":"精确片段"}}]}}，无问题写 []。
critical/high 或 gap/uncertain 阻断完成，禁止通过自报 status、resolved 或 blocking:false 消除问题。"""
    process = run_model_stage('review', prompt, task / 'review.log', args.model, args.stage_timeout,
                              {**config, '_review_cwd': str(context)})
    after = {p.relative_to(context).as_posix(): digest(p) for p in context.rglob('*') if p.is_file()}
    if after != manifest:
        raise ValueError('独立审查改写了只读上下文，已阻断')
    try:
        report = json.loads(process.stdout)
    except (AttributeError, ValueError) as exc:
        raise ValueError('独立审查未返回有效 JSON，不能自报通过') from exc
    result = validate_review(expected, report, context, ac_ids, actual)
    result.update(context=str(context), manifest=manifest)
    write_json(task / 'review-result.json', result)
    if result['status'] != 'passed':
        raise WorkflowPending('pending_review', '独立审查存在阻断问题：' + ', '.join(result['blockers']))
    return result


def require_current_review(task: Path, request: Path, contract: dict) -> dict:
    result = object_file(task / 'review-result.json')
    expected = review_input(task, request, task_identity(task), source_digest(),
                            result.get('context_sha256'), result.get('review_run_id'))
    context = Path(result.get('context', ''))
    manifest = {p.relative_to(context).as_posix(): digest(p) for p in context.rglob('*') if p.is_file()}
    context_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if not result.get('context_sha256') or context_hash != result['context_sha256']:
        raise ValueError('独立审查上下文已改变或缺失，须重跑 review')
    files = [f for f in (task / 'implemented-files.txt').read_text().splitlines() if f]
    checked = validate_review(expected, result, context, [a['id'] for a in contract['acceptance']], files)
    if checked['status'] != 'passed':
        raise WorkflowPending('pending_review', '独立审查尚未通过')
    return checked


def load_local_factory_config(path: Path | None = None) -> dict[str, object]:
    """Load the local-only policy and fail closed if it can permit remote effects."""
    path = path or ROOT / ".factory/factory-local.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"本地 Factory 配置不可用：{path}: {exc}") from exc
    if not isinstance(config, dict):
        raise SystemExit("本地 Factory 配置根节点必须是对象")
    routing = config.get("intent_routing")
    handoff = routing.get("remote_handoff") if isinstance(routing, dict) else None
    if (
        config.get("execution_mode") != "local"
        or config.get("remote_side_effects") is not False
        or config.get("auto_commit") is not False
        or not isinstance(config.get("final_gate_cmd"), str)
        or not config["final_gate_cmd"].strip()
        or not isinstance(handoff, dict)
        or handoff.get("enabled") is not True
        or handoff.get("local_runner_may_execute_remote_actions") is not False
        or not isinstance(handoff.get("executor"), str)
        or not handoff["executor"].strip()
    ):
        raise SystemExit(
            "本地 Factory 配置违反本地执行铁律：必须禁用 remote_side_effects/auto_commit，"
            "并将远端动作限定为明确的 handoff"
        )
    local_allowed_tools(config)
    if config.get('workflow_version') != WORKFLOW_VERSION or config.get('local_stage_order') != STAGES:
        raise ValueError('Factory 流程配置版本不匹配，须由维护者审核升级，不能跳过新增阶段')
    verify_code(ROOT, config)
    stage_skills(config)
    return config


def local_allowed_tools(config: dict[str, object]) -> list[str]:
    """Return the only tools permitted to an omp implementation subprocess."""
    tools = config.get("local_allowed_tools")
    if (
        not isinstance(tools, list)
        or not tools
        or any(not isinstance(tool, str) or tool not in SAFE_LOCAL_TOOLS for tool in tools)
        or len(set(tools)) != len(tools)
    ):
        raise SystemExit(
            "本地 Factory 工具配置不安全：local_allowed_tools 必须是非空、无重复的 "
            "read/write/edit/grep/lsp 子集"
        )
    return tools


def enforce_local_command_policy(cmd: list[str], config: dict[str, object]) -> None:
    """Mechanically reject runner-owned commit, push, MR, CI and deployment commands."""
    command = " ".join(cmd).lower()
    prohibited = (
        r"\bgit\s+(?:commit|push)\b",
        r"\b(?:gh|glab)\s+(?:pr|mr)\b",
        r"\b(?:curl|mcporter)\b.*(?:pipeline|ci|cd|deploy|release|merge-request)",
    )
    if config.get("remote_side_effects") is not False or config.get("auto_commit") is not False:
        raise SystemExit("本地 Factory 禁止执行远端动作：配置未保持 local-only")
    if any(re.search(pattern, command) for pattern in prohibited):
        raise SystemExit(f"本地 Factory 禁止执行远端动作：{' '.join(cmd[:3])}")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_log(path: Path, content: str, command: list[str] | None = None) -> None:
    """Write a log with standalone task/attempt/stage metadata for direct traceability."""
    attempt = ACTIVE_ATTEMPT.get()
    if attempt:
        metadata = {
            "task_id": attempt.task_id,
            "attempt_id": attempt.id,
            "stage": attempt.stage,
            "command": (command or [])[:2],
        }
        content = "# factory-metadata " + json.dumps(metadata, ensure_ascii=False) + "\n" + content
    path.write_text(content, encoding="utf-8")


def run(
    cmd: list[str],
    *,
    log: Path | None = None,
    check: bool = True,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    attempt = ACTIVE_ATTEMPT.get()
    if log and attempt:
        log = attempt.directory / log.name
    process = None
    try:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        process = subprocess.Popen(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, env=process_env)
        if attempt:
            attempt.save_state("running", active_command={"pid": process.pid, "command": cmd[:2]})
        stdout, stderr = process.communicate(timeout=timeout)
        p = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except OSError as exc:
        if log:
            write_log(log, str(exc), cmd)
        raise CommandFailure(cmd, 127, "command_unavailable") from exc
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        if process:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            if log:
                write_log(log, stdout + stderr, cmd)
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise CommandFailure(cmd, 124, "timeout") from exc
    finally:
        if attempt:
            attempt.save_state("running", active_command=None)
    if log:
        write_log(log, p.stdout + p.stderr, cmd)
    if check and p.returncode:
        raise CommandFailure(cmd, p.returncode, "command_failed")
    return p


def bind_quality_gate_receipt(task: Path, gate: subprocess.CompletedProcess[str], command: list[str]) -> dict:
    """Archive a quality receipt in the active attempt and bind it to this verify run."""
    attempt = ACTIVE_ATTEMPT.get()
    if attempt is None:
        raise ValueError('质量门缺少当前任务尝试绑定')
    source = None
    for line in reversed((gate.stdout + gate.stderr).splitlines()):
        if line.startswith('receipt: '):
            source = Path(line.removeprefix('receipt: ').strip())
            break
    detailed = None
    if source is not None:
        if not source.is_absolute():
            source = ROOT / source
        source = source.resolve()
        quality_root = (ROOT / 'artifacts/factory-local/quality').resolve()
        if not source.is_file() or not source.is_relative_to(quality_root):
            raise ValueError('质量门收据缺失或路径越界')
        value = json.loads(source.read_text(encoding='utf-8'))
        expected = {'task_id': attempt.task_id, 'attempt_id': attempt.id, 'stage': 'verify'}
        if any(value.get(key) != expected_value for key, expected_value in expected.items()):
            raise ValueError('质量门收据未绑定当前 task/attempt/verify')
        archived = attempt.directory / 'quality'
        if archived.exists():
            shutil.rmtree(archived)
        shutil.copytree(source.parent, archived)
        detailed = archived / 'receipt.json'
    log = attempt.directory / 'final-gate.log'
    receipt = {
        'schema': 'factory-local/quality-stage-receipt/1',
        'task_id': attempt.task_id,
        'attempt_id': attempt.id,
        'stage': 'verify',
        'request_sha256': attempt.request_sha256,
        'command': command,
        'exit_code': gate.returncode,
        'log': str(log),
        'log_sha256': digest(log) if log.is_file() else None,
        'detailed_receipt': str(detailed) if detailed else None,
        'detailed_receipt_sha256': digest(detailed) if detailed else None,
    }
    write_json(task / 'quality-gate-receipt.json', receipt)
    return receipt


def append_event(path: Path, stage: str, status: str, **extra: object) -> None:
    attempt = ACTIVE_ATTEMPT.get()
    if attempt:
        extra = attempt.event(stage, status, extra)
    row = {"time": now(), "stage": stage, "status": status, **extra}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_omp_command(
    stage: str,
    prompt: str,
    model: str,
    config: dict[str, object],
) -> list[str]:
    tools = ['read', 'grep'] if stage in {'spec-review', 'review'} else local_allowed_tools(config)
    return [
        "omp",
        "--print",
        "--no-session",
        "--cwd",
        config.get('_review_cwd', str(ROOT)) if stage in {'spec-review', 'review'} else str(ROOT),
        "--model",
        model,
        "--thinking",
        "low",
        "--tools",
        ",".join(tools),
        "--",  # Skill frontmatter may start with ---; payload is never a CLI option.
        prompt,
    ]


def omp(
    stage: str,
    prompt: str,
    log: Path,
    model: str,
    timeout: int,
    config: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    cmd = build_omp_command(stage, prompt, model, config)
    enforce_local_command_policy(cmd, config)
    p = run(cmd, log=log, check=False, timeout=timeout)
    if p.returncode:
        reason = "rate_limited" if re.search(r"turn_rate_limit_exceeded|at capacity|429", p.stdout + p.stderr, re.I) else "model_failed"
        raise CommandFailure(cmd, p.returncode, reason)
    return p



def spec_review_feedback(attempt, protected: dict | None = None) -> dict | None:
    """Return diagnostic gaps, never prior approval or new business authority."""
    base = (ROOT / 'artifacts/factory-local').resolve()
    buffers = {}

    def read(path):
        path = Path(path)
        if path.is_symlink() or not path.resolve().is_relative_to(base) or not path.is_file():
            raise ValueError('Spec 审查反馈证据缺失、越界或使用符号链接')
        path = path.resolve()
        if path not in buffers:
            buffers[path] = path.read_bytes()
        return buffers[path]

    def load(path, owner, expected_hash=None, historical=False):
        raw = read(path)
        if expected_hash is not None and hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError('Spec 审查反馈证据摘要已改变')
        report = json.loads(raw)
        if not isinstance(report, dict) or report.get('schema') != 'factory-local/spec-review-result/1':
            raise ValueError('Spec 审查反馈报告格式无效')
        if report.get('task_id') != owner:
            raise ValueError('Spec 审查反馈父任务绑定无效')
        if report.get('request_sha256') != attempt.request_sha256:
            if historical:
                return None
            raise ValueError('Spec 审查反馈请求绑定无效')
        context = Path(report.get('context', ''))
        if (not context.is_absolute() or context.is_symlink()
                or not context.resolve().is_relative_to(base) or not context.is_dir()):
            raise ValueError('Spec 审查反馈缺少有效只读上下文')
        context = context.resolve()
        # Revalidate against the immutable original input/output and context,
        # not a few self-asserted report headers. Pending reviews have no pass to inherit.
        if read(context.parent/'outputs/spec-review-result.json') != raw:
            raise ValueError('Spec 审查反馈与原始归档输出不匹配')
        original = json.loads(read(context.parent/'outputs/spec-review-input.json'))
        if (not isinstance(original, dict) or original.get('schema') != 'factory-local/spec-review-input/1'
                or original.get('workflow_version') != WORKFLOW_VERSION
                or original.get('task_id') != owner or original.get('request_sha256') != attempt.request_sha256
                or original.get('context') != str(context)):
            raise ValueError('Spec 审查反馈原始输入绑定无效')
        manifest = {file.relative_to(context).as_posix(): hashlib.sha256(read(file)).hexdigest()
                    for file in context.rglob('*') if file.is_file() or file.is_symlink()}
        if manifest != report.get('manifest') or manifest != original.get('manifest'):
            raise ValueError('Spec 审查反馈只读上下文已改变')
        context_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        names = {'request_sha256': 'request.md', 'requirement_sha256': 'requirement.md',
                 'requirement_contract_sha256': 'requirement-contract.json',
                 'clarification_sha256': 'clarification-contract.json', 'spec_sha256': 'spec.md',
                 'contract_sha256': 'spec-contract.json', 'skill_selection_sha256': 'skill-selection.json'}
        if any(original.get(key) != manifest.get(name) for key, name in names.items()):
            raise ValueError('Spec 审查反馈上游摘要不匹配')
        contract = json.loads(read(context/'spec-contract.json'))
        if (original.get('context_sha256') != context_hash or contract.get('task_id') != owner
                or original.get('source_sha256') != contract.get('source_sha256')
                or not isinstance(original.get('review_run_id'), str) or not original['review_run_id']):
            raise ValueError('Spec 审查反馈上下文或源码绑定无效')
        requirement = json.loads(read(context/'requirement-contract.json'))
        clarification = json.loads(read(context/'clarification-contract.json'))
        expected = {key: value for key, value in original.items() if key not in {'context', 'manifest'}}
        return validate_spec_review(expected, report, context,
                                    [row['id'] for row in requirement['behavior_candidates']],
                                    [row['id'] for row in clarification['rules']],
                                    [row['id'] for row in contract['acceptance']],
                                    context_contents={name: read(context/name) for name in manifest})

    current = attempt.directory / 'superseded/spec-review-result.json'
    candidates = []
    if current.exists() or current.is_symlink():
        candidates.append((current, attempt.task_id, None, False))
    revision_path = attempt.task / 'revision.json'
    if revision_path.is_file() and not candidates:
        revision = object_file(revision_path)
        for reference in revision.get('historical_evidence', []):
            path = base / reference['path']
            if path.name != 'spec-review-result.json':
                continue
            candidates.append((path, revision['parent_task_id'], reference['sha256'], True))
    reports, inputs = [], []
    def diagnostic(row, fields):
        return {**{key: row[key] for key in fields},
                'evidence': [{key: item[key] for key in ('path', 'quote')} for item in row['evidence']]}
    for path, owner, expected_hash, historical in candidates:
        report = load(path, owner, expected_hash, historical)
        if report is None:
            continue
        gaps = {field: [diagnostic(row, ('id', 'verdict', 'reason')) for row in report[field]
                        if row['verdict'] in {'gap', 'uncertain'}]
                for field in ('requirement_coverage', 'clarification_coverage', 'acceptance')}
        findings = [diagnostic(row, ('id', 'severity', 'description')) for row in report['findings']]
        if not any(gaps.values()) and not findings:
            continue
        inputs.append({'path': str(path), 'sha256': hashlib.sha256(read(path)).hexdigest()})
        reports.append({'originating_task_id': owner, 'gaps': gaps, 'findings': findings})
    if protected is not None:
        protected.update(buffers)
    if not reports:
        return None
    return {'schema': 'factory-local/spec-review-feedback/1', 'task_id': attempt.task_id,
            'attempt_id': attempt.id, 'request_sha256': attempt.request_sha256,
            'role': 'diagnostic-only; not business authority or inherited approval',
            'inputs': inputs, 'reports': reports}


def run_model_stage(stage: str, prompt: str, log: Path, model: str, timeout: int,
                    config: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """Keep runner-owned inputs in memory across the untrusted model call.

    This detects stage writes; it is not an OS sandbox against hostile processes.
    Never roll back source edits automatically: retain them for inspection and block.
    """
    attempt = ACTIVE_ATTEMPT.get()
    task = attempt.task
    paths = [task / "authorization.json", task / "task-identity.json", attempt.request]
    paths.extend(task / name for name in ('clarification-answers.json', 'clarification-obligations.json',
                                         'execution-policy.json', 'environment-evidence.json', 'business-sources.snapshot.json'))
    for prior in STAGES[:STAGES.index(stage)]:
        paths.extend(task / name for name in STAGE_OUTPUTS[prior] if (task / name).is_file())
    for name in ("requirement-input.json", "spec-input.json", "spec-review-input.json"):
        if (task / name).is_file():
            paths.append(task / name)
    if stage != 'requirement' and (task/'requirement-source-snapshot.json').is_file():
        for row in object_file(task/'requirement-contract.json')['observations']:
            if row['kind'] == 'source':
                paths.append(_requirement_source(task, attempt.request, row['source']['path'], historical=True))
        receipt = object_file(task/'stage-receipts.json')['requirement']
        paths.append(Path(receipt['artifact_dir'])/'requirement-source-snapshot.json')
    if stage == 'implement':
        paths.extend(task / name for name in ('spec.md', 'spec-contract.json', 'worktree-baseline.json')
                     if (task / name).is_file())
    paths.extend(task / name for name in ('revision.json', 'revision-source.json') if (task / name).is_file())
    paths.extend(path for path in (task / 'parent-snapshot').glob('*') if path.is_file())
    if stage == 'implement':
        paths.extend(p for p in (attempt.directory / 'implementation-before').rglob('*') if p.is_file())
    retained = {path: path.read_bytes() if path.is_file() else None for path in paths}
    if stage == 'spec':
        revision = object_file(task/'revision.json') if (task/'revision.json').is_file() else {}
        if revision.get('required_verification'):
            prompt += ('\n关联修订必须保留以下必验 ID、类型和环境，所有结果重新从 NOT_RUN 开始，不继承通过状态：'
                       + json.dumps(revision['required_verification'], ensure_ascii=False) + '\n')
        feedback = spec_review_feedback(attempt, retained)
        if feedback:
            feedback_path = attempt.directory / 'spec-review-feedback.json'
            write_json(feedback_path, feedback)
            retained[feedback_path] = feedback_path.read_bytes()
            prompt += '\n运行器提供的历史技术审查反馈（不是新增业务授权或旧通过状态）：' + str(feedback_path) + '\n'
    docs_path = capture_docs_context(stage, config) if stage != 'review' else None
    if docs_path:
        retained[docs_path] = docs_path.read_bytes()
        record = json.loads(retained[docs_path])
        prompt += docs_module().prompt(record)
    capabilities = stage_skills(config)
    if stage in capabilities:
        prompt = capabilities[stage]['content'] + '\n\n以下为运行器指定的输入、输出和绑定：\n' + prompt
    before_source = source_digest() if stage != "implement" else None
    try:
        process = omp(stage, prompt, log, model, timeout, config)
    finally:
        errors = []
        try:
            verify_code(ROOT, config)
            validate_selected_skills(task)
            if skill_binding(stage_skills(config)) != skill_binding(capabilities):
                errors.append('模型运行期间公共 Skill 发生变化')
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        for path, expected in retained.items():
            if expected is None:
                if path.exists() or path.is_symlink():
                    errors.append(f'模型创建运行器输入: {path.name}')
                    if path.is_file() or path.is_symlink():
                        path.unlink()
            elif not path.is_file() or path.read_bytes() != expected:
                errors.append(f"模型改写运行器输入: {path.name}")
                temporary = path.with_name(path.name + ".restore.tmp")
                temporary.write_bytes(expected)
                temporary.replace(path)
        if docs_path:
            changes = docs_module().check(ROOT, json.loads(retained[docs_path]), config.get("docs_context"), {
                "task_id": attempt.task_id, "attempt_id": attempt.id, "stage": stage,
                "request_sha256": attempt.request_sha256,
            })
            if changes:
                errors.append("阶段执行期间 docs 引用变化：" + json.dumps(changes, ensure_ascii=False))
        if before_source is not None and source_digest() != before_source:
            errors.append("分析阶段源码被改动；已阻断，请检查工作树，不自动回退源码")
        if errors:
            receipt = {"schema": "factory-local/stage-integrity/1", "task_id": attempt.task_id,
                       "stage": stage, "status": "blocked", "errors": errors,
                       "source_sha256_before": before_source}
            write_json(task / "stage-integrity.json", receipt)
            if stage == "spec":
                write_json(task / "spec-validation.json", {
                    "schema": "factory-local/spec-validation/1", "task_id": attempt.task_id,
                    "status": "blocked", "errors": errors,
                    "boundary": "模型阶段输入完整性检查失败，不能通过 Spec 门禁"})
            raise ValueError("阶段输入完整性失败: " + "; ".join(errors))
    return process


def required(path: Path) -> None:
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        raise SystemExit(f"阶段产物缺失或为空：{path}")


def _skill_frontmatter(path: Path) -> tuple[str, str]:
    """Read only the skill frontmatter; full skill files remain stage inputs."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return path.parent.name, ""
    end = text.find("\n---", 3)
    if end < 0:
        return path.parent.name, ""
    block = text[3:end]
    name = re.search(r"^name:\s*(.+)$", block, re.MULTILINE)
    description = re.search(r"^description:\s*>?\s*$([\s\S]*?)(?=^\w+:|\Z)", block, re.MULTILINE)
    skill_name = name.group(1).strip() if name else path.parent.name
    description_text = " ".join(line.strip() for line in (description.group(1).splitlines() if description else []))
    return skill_name, description_text


def _selected_skill_manifest(name: str, skill_file: Path) -> dict[str, str]:
    folder = skill_file.parent
    ignored_parts = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
    ignored_names = {".DS_Store"}
    paths = sorted(
        path for path in folder.rglob("*")
        if path.is_file()
        and not any(part in ignored_parts for part in path.relative_to(folder).parts)
        and path.name not in ignored_names
        and path.suffix != ".pyc"
    )
    if skill_file not in paths:
        raise ValueError("领域 Skill 缺少 SKILL.md：" + str(skill_file))
    manifest = {}
    for path in paths:
        resolved = path.resolve()
        if (path.is_symlink() or not resolved.is_relative_to(AWESOME_RULES_ROOT)
                or not resolved.is_relative_to(folder.resolve())):
            raise ValueError("领域 Skill 文件缺失、为符号链接或越界：" + str(path))
        relative = resolved.relative_to(AWESOME_RULES_ROOT).as_posix()
        if not relative.startswith(f"skills/{name}/"):
            raise ValueError("领域 Skill 文件路径与名称不一致：" + relative)
        manifest[relative] = digest(resolved)
    return manifest


def validate_selected_skills(task: Path) -> dict:
    selection = object_file(task / "skill-selection.json")
    if selection.get("schema") != "factory-local/skill-selection/2" or selection.get("status") != "passed":
        raise ValueError("skill-selection.json 不可用")
    selected = selection.get("selected")
    if not isinstance(selected, list):
        raise ValueError("skill-selection.selected 必须是数组")
    seen = set()
    for row in selected:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("已选领域 Skill 记录无效")
        if row["name"] in seen:
            raise ValueError("已选领域 Skill 名称重复：" + row["name"])
        seen.add(row["name"])
        expected = row.get("files")
        skill_file = Path(row.get("path", ""))
        if not isinstance(expected, dict) or not expected:
            raise ValueError("已选领域 Skill 缺少内容摘要，须从 skill-discovery 重跑")
        current = _selected_skill_manifest(row["name"], skill_file)
        if current != expected:
            raise ValueError("已选领域 Skill 内容已变化，须从 skill-discovery 重跑：" + row["name"])
    return selection


def discover_skills(request: Path, classification: dict[str, object], task: Path) -> tuple[str, str]:
    """Scan awesome-rules and persist the skill selection before AI stages run."""
    output = task / "skill-selection.json"
    skills_root = AWESOME_RULES_ROOT / "skills"
    request_text = request.read_text(encoding="utf-8", errors="replace")
    if not skills_root.is_dir():
        payload = {
            "schema": "factory-local/skill-selection/2",
            "status": "unavailable",
            "source": str(skills_root),
            "scanned": [],
            "selected": [],
            "reason": "awesome-rules/skills 不存在；普通任务可继续，设计专项由后续能力门禁阻断",
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return "unavailable", payload["reason"]

    policy_path = skills_root / "factory-routing.json"
    scanned: list[dict[str, object]] = []
    selected: list[dict[str, object]] = []
    try:
        policy = _load_skill_routing(policy_path)
        available = {}
        for skill_file in sorted(skills_root.glob("*/SKILL.md")):
            name, _ = _skill_frontmatter(skill_file)
            if name != skill_file.parent.name or not skill_file.resolve().is_relative_to(skills_root.resolve()):
                raise ValueError(f"技能名称或来源越界: {skill_file}")
            available[name] = skill_file
        missing = set(policy["skills"]) - set(available)
        unconfigured = set(available) - set(policy["skills"])
        if missing or unconfigured:
            raise ValueError(f"技能目录与触发配置不一致；不存在: {sorted(missing)}；未配置: {sorted(unconfigured)}")
        write_json(task / "skill-routing.snapshot.json", policy)
        for name, skill_file in available.items():
            rule = policy["skills"].get(name, {})
            explicit = intent_decisions(request_text, r"(?<![a-z0-9_-])" + re.escape(name) + r"(?![a-z0-9_-])")
            reasons = ["explicit-skill-reference"] if any(d["decision"] == "affirmed" for d in explicit) else []
            decisions = list(explicit)
            if rule.get("always"):
                reasons.append("mandatory-local-workflow")
            domain_ok = not rule.get("domains") or classification.get("domain", classification.get("scope")) in rule["domains"]
            design_evidence = (classification.get("has_design") and bool(
                _affirmed_intents(request_text, r"设计稿|原型|\bFigma\b(?!-)")
                or classification.get("design_files")))
            eligible = domain_ok and (not rule.get("requires_design") or design_evidence)
            if eligible and rule.get("requires_design"):
                reasons.append("design-evidence")
            if eligible:
                for phrase in rule.get("triggers", []):
                    matches = intent_decisions(request_text, _literal_pattern(phrase))
                    decisions.extend({**d, "trigger": phrase} for d in matches)
                    if any(d["decision"] == "affirmed" for d in matches):
                        reasons.append("trigger:" + phrase)
            entry = {"name": name, "path": str(skill_file), "matched_terms": reasons,
                     "selection_reasons": reasons, "decisions": decisions}
            scanned.append(entry)
            if reasons:
                selected.append({**entry, "files": _selected_skill_manifest(name, skill_file)})
        payload = {
            "schema": "factory-local/skill-selection/2", "status": "passed",
            "source": str(skills_root), "scanned": scanned, "selected": selected,
            "policy": {"schema": policy["schema"], "path": str(policy_path), "sha256": digest(policy_path),
                       "snapshot": "skill-routing.snapshot.json"},
            "rules": {"skill_first": True, "full_skill_read_required_before_requirement": True,
                      "empty_selection_is_recorded": True, "description_token_overlap": False},
        }
    except (OSError, ValueError, TypeError) as exc:
        reason = f"技能触发配置不可用，请同步 awesome-rules/skills/factory-routing.json 并核对技能目录: {exc}"
        write_json(output, {"schema": "factory-local/skill-selection/2", "status": "blocked",
                            "source": str(skills_root), "scanned": scanned, "selected": [], "reason": reason})
        return "blocked", reason
    write_json(output, payload)
    return "passed", f"已扫描 {len(scanned)} 个技能，命中 {len(selected)} 个（依据显式触发配置）"


def visual_evidence_status(task: Path, classification: dict[str, object]) -> tuple[str, str]:
    """调用 awesome-rules 的视觉规则；Factory 只编排，不重复实现规则。"""
    if not (classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}):
        return "not_required", "任务没有设计稿证据"
    validator = AWESOME_RULES_ROOT / "skills" / "figma-visual-guard" / "scripts" / "validate_evidence.py"
    if not validator.is_file():
        return "blocked", f"awesome-rules 视觉规则不可用: {validator}"
    cmd = [
        "python3",
        str(validator),
        "--design", str(task / "design-evidence.json"),
        "--design-contract", str(task / "design-contract.json"),
        "--contract", str(task / "visual-contract.json"),
        "--evidence", str(task / "visual-evidence.json"),
        "--root", str(task),
        "--format", "json",
    ]
    try:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        return "blocked", "awesome-rules 视觉规则超时（120s）"
    except OSError as exc:
        return "blocked", f"awesome-rules 视觉规则无法启动: {exc}"
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "blocked", f"awesome-rules 视觉规则输出不可解析: {result.stdout[-300:]} {result.stderr[-300:]}"
    if not isinstance(receipt, dict):
        return "blocked", "awesome-rules 视觉规则收据顶层必须是对象"
    status = receipt.get("status")
    if result.returncode == 0 and status == "passed":
        return "passed", receipt.get("message", "视觉证据完整且通过")
    if result.returncode != 0 and status == "passed":
        return "blocked", "awesome-rules 视觉规则退出码与 passed 结果不一致"
    if receipt.get("code", "").startswith("visual_") and receipt.get("code") == "visual_check_failed":
        return "failed", f"{receipt.get('code')}: {receipt.get('message', '')}"
    return "blocked", f"{receipt.get('code', 'visual_guard_failed')}: {receipt.get('message', '')}"


def design_contract_status(task: Path, classification: dict[str, object]) -> tuple[str, str]:
    """调用 awesome-rules 的设计实现契约规则；Factory 只编排。"""
    if not (classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}):
        return "not_required", "任务没有设计稿证据"
    generator = AWESOME_RULES_ROOT / "skills" / "figma-frontend-implementation" / "scripts" / "generate_contract.py"
    input_path = task / "design-input.json"
    output_path = task / "design-contract.json"
    if not generator.is_file():
        return "blocked", f"awesome-rules 设计实现规则不可用: {generator}"
    if not input_path.is_file():
        return "blocked", "缺少 design-input.json（需要先完成 Figma 事实采集）"
    cmd = [
        "python3", str(generator), "--input", str(input_path), "--output", str(output_path),
        "--root", str(task), "--format", "json",
    ]
    try:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        return "blocked", "awesome-rules 设计实现规则超时（120s）"
    except OSError as exc:
        return "blocked", f"awesome-rules 设计实现规则无法启动: {exc}"
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "blocked", f"awesome-rules 设计实现规则输出不可解析: {result.stdout[-300:]}"
    if result.returncode == 0 and isinstance(receipt, dict) and receipt.get("status") == "passed":
        return "passed", "设计实现契约已生成"
    return "blocked", f"{receipt.get('code', 'design_contract_failed')}: {receipt.get('message', '')}" if isinstance(receipt, dict) else "设计实现规则收据无效"


def visual_contract_status(task: Path, classification: dict[str, object]) -> tuple[str, str]:
    """由 awesome-rules 根据已验证的设计契约生成视觉检查项。"""
    if not (classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}):
        return "not_required", "任务没有设计稿证据"
    generator = AWESOME_RULES_ROOT / "skills" / "figma-visual-guard" / "scripts" / "generate_contract.py"
    input_path = task / "design-contract.json"
    output_path = task / "visual-contract.json"
    if not generator.is_file():
        return "blocked", f"awesome-rules 视觉契约规则不可用: {generator}"
    if not input_path.is_file():
        return "blocked", "缺少 design-contract.json，无法生成视觉契约"
    cmd = [
        "python3", str(generator), "--input", str(input_path), "--output", str(output_path), "--format", "json"
    ]
    try:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        return "blocked", "awesome-rules 视觉契约规则超时（120s）"
    except OSError as exc:
        return "blocked", f"awesome-rules 视觉契约规则无法启动: {exc}"
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "blocked", f"awesome-rules 视觉契约规则输出不可解析: {result.stdout[-300:]}"
    if result.returncode == 0 and isinstance(receipt, dict) and receipt.get("status") == "passed":
        return "passed", "视觉契约已由 awesome-rules 根据设计事实生成"
    return "blocked", f"{receipt.get('code', 'visual_contract_failed')}: {receipt.get('message', '')}" if isinstance(receipt, dict) else "视觉契约收据无效"


def figma_capture_status(task: Path, request: Path, classification: dict[str, object]) -> tuple[str, str]:
    """Invoke the repository-local capture adapter; never implement capture here."""
    if not (classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}):
        return "not_required", "任务没有设计稿证据"
    if (task / "design-input.json").is_file() and (task / "design-evidence.json").is_file():
        return "passed", "设计证据已存在，跳过重复采集"
    capture = ROOT / ".factory" / "figma-capture.py"
    if not capture.is_file():
        return "blocked", f"Figma 采集连接器不可用: {capture}"
    cmd = ["python3", str(capture), "--request-file", str(request), "--task", str(task)]
    try:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=180)
    except subprocess.TimeoutExpired:
        return "blocked", "Figma 采集连接器超时（180s）"
    except OSError as exc:
        return "blocked", f"Figma 采集连接器无法启动: {exc}"
    try:
        receipt = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return "blocked", f"Figma 采集连接器输出不可解析: {result.stdout[-300:]} {result.stderr[-300:]}"
    if result.returncode == 0 and isinstance(receipt, dict) and receipt.get("status") == "passed":
        return "passed", "Figma 节点截图和设计事实已采集"
    if isinstance(receipt, dict):
        reason = f"{receipt.get('code', 'connector_failed')}: {receipt.get('message', '')}"
        actions = receipt.get("action_required")
        if isinstance(actions, list) and actions:
            reason += " 操作路径: " + " ".join(str(action) for action in actions)
        return "blocked", reason
    return "blocked", "Figma 采集连接器收据无效"


def write_design_orchestration(task: Path, classification: dict[str, object], status: str, reason: str) -> None:
    """Persist the Factory hand-off without duplicating design rules.

    The runner records routing and capability status only. Figma capture,
    measurement and visual comparison stay in awesome-rules/connector work.
    """
    payload = {
        "schema": "factory-local/design-orchestration/1",
        "status": status,
        "scope": classification.get("scope"),
        "route": classification.get("route"),
        "has_design": classification.get("has_design"),
        "reason": reason,
        "owner": {
            "factory": ["classify", "skill_discovery", "route", "persist_status", "block_on_missing_artifacts"],
            "awesome_rules": ["figma_evidence", "design_contract", "visual_contract", "visual_guard"],
        },
        "required_artifacts": [
            "skill-selection.json",
            "figma-capture-receipt.json",
            "design-evidence.json",
            "design-input.json",
            "design-contract.json",
            "visual-contract.json",
            "visual-evidence.json",
        ],
    }
    (task / "design-orchestration.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_visual_templates(task: Path) -> None:
    """创建不可冒充通过的模板，帮助下一次运行继续补齐证据。"""
    templates = {
        "design-evidence.template.json": {
            "schema": "factory-local/design-evidence/1",
            "nodes": [],
            "notes": "每个 Figma 节点必须有 source_url、node_id、screenshot、viewport 和 captured_at。",
        },
        "visual-contract.template.json": {
            "schema": "factory-local/visual-contract/1",
            "route": "",
            "checks": [],
            "notes": "每个检查项必须可由截图、DOM 或尺寸数据核对，并标注 severity。",
        },
        "visual-evidence.template.json": {
            "schema": "factory-local/visual-evidence/1",
            "status": "blocked",
            "figma_screenshots": [],
            "page_screenshots": [],
            "checks": [],
            "notes": "每个 checks 项必须引用 Figma 与页面截图；全部核对通过后才可将 status 改为 passed。",
        },
        "design-input.template.json": {
            "schema": "factory-local/design-input/1",
            "source": "",
            "nodes": [],
            "layout": {},
            "tokens": {},
            "components": [],
            "interactions": [],
            "implementation_scope": [],
            "open_questions": [],
            "notes": "由 Figma Connector/CUA 采集事实后填写；不得用模型猜测替代截图和尺寸。",
        },
    }
    for name, data in templates.items():
        path = task / name
        if not path.exists():
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _routing_text(text: str) -> str:
    """Mask explicit non-goals and examples, retaining requested command blocks."""
    lines = []
    excluded_level = None
    fence_kind, fence_length, executable, context = None, 0, False, ""
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        closing = bool(fence and fence_kind == fence[1][0] and len(fence[1]) >= fence_length and not fence[2].strip())
        heading = re.match(r"^\s*(#{1,6})\s+(.+)", line) if fence_kind is None else None
        if heading:
            level, title = len(heading[1]), heading[2]
            if excluded_level is not None and level <= excluded_level:
                excluded_level = None
            if excluded_level is None and re.search(r"非目标|不在(?:本轮|本次).*范围|本轮不做|排除项|non[- ]?goals|out of scope", title, re.I):
                excluded_level = level
        if fence and fence_kind is None:
            explicit = re.search(r"(?:执行|运行)以下(?:交付)?命令|\b(?:run|execute)\s+(?:the\s+)?following\s+(?:delivery\s+)?commands?", context, re.I)
            denied = re.search(r"不要|不执行|不运行|禁止|无需|勿|\b(?:do not|don't|never|not)\b", context, re.I)
            executable = bool(explicit and not denied)
            fence_kind, fence_length = fence[1][0], len(fence[1])
            closing = False
        masked = bool(fence) or (fence_kind is not None and not executable) or excluded_level is not None
        lines.append(re.sub(r"[^\n\r]", " ", line) if masked else line)
        if closing:
            fence_kind, fence_length, executable = None, 0, False
        elif fence_kind is None and line.strip():
            context = line.strip()
    return "".join(lines)


def intent_decisions(text: str, pattern: str, *, subject_absence: bool = False) -> list[dict[str, object]]:
    """Literal occurrences plus bounded, inspectable clause-negation decisions.

    Negation spans a whole clause/list, not a fixed character window. Contrast and
    sentence boundaries or explicit new directives reset it. Comma lists retain
    their governing prohibition even across unrelated listed actions.
    This is deterministic routing evidence, never authority to execute remotely.
    """
    visible = _routing_text(text)
    negate = (r"不要|不需要|不允许|不应该|不必|不用|不准|不得|不能|无需|无须|禁止|勿|别|"
              r"不(?:做|进行|执行|启动|使用|采用|调用|提交|推送|创建|发起|触发|运行|部署|发布|合并|生成|声明|设计|修改|改|写)|"
              r"\b(?:do\s+not|don't|must\s+not|should\s+not|never|not|no)\b")
    if subject_absence:
        # Noun routing differs from action authorization: "不涉及后端" and
        # "后端不改" exclude a domain, but must not suppress a later push action.
        negate += r"|不涉及|不包含|不存在|不是|并非|不开发|不实现|不改"
    affirmative = (r"^\s*(?:[-*]\s*)?(?:请(?!求)|需要|允许|要求|务必|仅|只(?!读)|最后|然后|随后|"
                   r"(?:修复|验证|检查|完成|通过)[^，,。.!！？?；;\n]*?后|\b(?:please|only|then|finally)\b)")
    if subject_absence:
        affirmative = (r"^\s*(?:[-*]\s*)?(?:开发|实现|新增|修改|修复|调整|接入|重构|按照|使用|是)|" + affirmative)
    separators = r"([，,。.!！？?；;\n]|但是|不过|然而|而是|但|并(?:且)?请|并在|\bbut\b|\bhowever\b|\binstead\b)"
    decisions = []
    offset, carry = 0, False
    for piece in re.split(separators, visible, flags=re.I):
        if not piece:
            continue
        if re.fullmatch(separators, piece, re.I):
            if subject_absence or piece not in {",", "，"}:
                carry = False
            offset += len(piece)
            continue
        for match in re.finditer(pattern, piece, re.I):
            prefix = piece[:match.start()]
            negatives = list(re.finditer(negate, prefix, re.I))
            positives = list(re.finditer(affirmative, prefix, re.I))
            last_negative = negatives[-1].start() if negatives else -1
            last_positive = positives[-1].start() if positives else -1
            # Status modifiers such as 未处理的错误 are not action prohibitions.
            weak_prefix = re.sub(pattern, "", prefix, flags=re.I)
            weak_negative = bool(re.search(r"(?:没有|尚未|未|无|不)\s*(?:任何|提供|执行|进行)?\s*$", weak_prefix))
            postfix = piece[match.end():]
            absent_after = bool(re.match(
                r"\s*(?:(?:设计稿|原型|证据|文档|技能)\s*)*(?:尚未提供|未提供|不存在|缺失|不在(?:本轮|本次).*范围|先不做|不执行|不需要|[:：=]\s*NOT_RUN)", postfix, re.I))
            if subject_absence:
                absent_after = absent_after or bool(re.match(
                    r"\s*(?:(?:接口|服务|需求|页面|设计稿|原型)\s*)*(?:不改|不修改|不开发|不实现|不涉及|不包含|无需修改)",
                    postfix, re.I))
            negative = weak_negative or absent_after or last_negative > last_positive
            denied = negative or (carry and last_positive < 0)
            decisions.append({"text": text[offset + match.start():offset + match.end()],
                              "start": offset + match.start(), "end": offset + match.end(),
                              "decision": "negated" if denied else "affirmed",
                              "reason": "clause-negation" if negative else ("coordinated-negation" if denied else "positive-clause")})
        controls = [(m.start(), True) for m in re.finditer(negate, piece, re.I)]
        controls += [(m.start(), False) for m in re.finditer(affirmative, piece, re.I)]
        if controls:
            carry = max(controls)[1]
        offset += len(piece)
    covered = {item["start"] for item in decisions}
    for match in re.finditer(pattern, text, re.I):
        if match.start() not in covered:
            decisions.append({"text": match.group(0), "start": match.start(), "end": match.end(),
                              "decision": "excluded", "reason": "non-goal-or-example"})
    return sorted(decisions, key=lambda item: item["start"])


def _affirmed_intents(text: str, pattern: str) -> list[str]:
    return [item["text"] for item in intent_decisions(text, pattern) if item["decision"] == "affirmed"]


def _literal_pattern(phrase: str) -> str:
    parts = re.findall(r"[a-zA-Z0-9_-]+|[\u4e00-\u9fff]+|[^\s]", phrase)
    pattern = r"\s*".join(re.escape(part) for part in parts)
    if phrase[0].isascii() and phrase[0].isalnum():
        pattern = r"(?<![a-zA-Z0-9_-])" + pattern
    if phrase[-1].isascii() and phrase[-1].isalnum():
        pattern += r"(?![a-zA-Z0-9_-])"
    return pattern


def _load_skill_routing(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or policy.get("schema") != "factory-local/skill-routing/1" or not isinstance(policy.get("skills"), dict) or not policy["skills"]:
        raise ValueError("技能触发配置 schema 无效")
    for name, rule in policy["skills"].items():
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not isinstance(rule, dict):
            raise ValueError("技能触发配置条目无效")
        if set(rule) - {"always", "triggers", "domains", "requires_design", "explicit_only"}:
            raise ValueError(f"技能 {name} 含未知触发字段")
        if any(type(rule.get(key, False)) is not bool for key in ["always", "requires_design", "explicit_only"]):
            raise ValueError(f"技能 {name} 的开关必须为布尔值")
        if not isinstance(rule.get("triggers", []), list) or not all(isinstance(t, str) and t.strip() for t in rule.get("triggers", [])):
            raise ValueError(f"技能 {name} 的 triggers 必须是非空短语列表")
        if not (rule.get("always") or rule.get("requires_design") or rule.get("triggers") or rule.get("explicit_only")):
            raise ValueError(f"技能 {name} 缺少有效触发条件；只允许显式指定时须配置 explicit_only=true")
        if rule.get("explicit_only") and any(rule.get(key) for key in ["always", "requires_design", "triggers", "domains"]):
            raise ValueError(f"技能 {name} 的 explicit_only 不得混用自动条件")
        if not isinstance(rule.get("domains", []), list) or not all(d in {"frontend", "backend", "fullstack", "unknown"} for d in rule.get("domains", [])):
            raise ValueError(f"技能 {name} 的 domains 无效")
    return policy


def classify(request: Path, task: Path) -> dict[str, object]:
    text = request.read_text(encoding="utf-8")
    frontend_decisions = intent_decisions(text, r"前端|页面|组件|Vue|React|CSS|交互|浏览器|Figma|设计稿|原型", subject_absence=True)
    backend_pattern = r"后端|服务端|数据库|SQL|Controller|Service层|Spring|(?<![A-Za-z])Java(?![A-Za-z])|\bGo\b|MQ|Redis"
    backend_decisions = intent_decisions(text, backend_pattern, subject_absence=True)
    design_decisions = intent_decisions(text, r"Figma|设计稿|原型|视觉还原|截图", subject_absence=True)
    frontend = any(item['decision'] == 'affirmed' for item in frontend_decisions)
    backend = any(item['decision'] == 'affirmed' for item in backend_decisions)
    backend_context_only = False
    if frontend and backend and any(item['decision'] == 'negated' for item in backend_decisions):
        # An existing backend may only explain a UI error state. An explicit
        # exclusion wins over that context unless a separate backend change is requested.
        action = r'(?:开发|实现|新增|修改|修复|调整|接入|重构)'
        domain = '(?:' + backend_pattern + ')'
        clause = r'[^，,。.!！？?；;\n]*?'
        changes = intent_decisions(text, action + clause + domain + '|' + domain + clause + action, subject_absence=True)
        backend_context_only = not any(item['decision'] == 'affirmed' for item in changes)
        backend = not backend_context_only
    design_urls = _affirmed_intents(text, r"https?://[^\s)]+(?:figma\.com|\.figma\.)[^\s)]*")
    design_files = [
        str(p.relative_to(task))
        for p in task.rglob("*")
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf"}
        and p.relative_to(task).parts[0] != "attempts"
    ]
    design_words = any(item['decision'] == 'affirmed' for item in design_decisions)
    mode = execution_mode_for(text)
    analysis_only = mode == "analysis"
    if frontend and backend:
        scope = "fullstack"
    elif backend:
        scope = "backend"
    elif frontend:
        scope = "frontend"
    else:
        scope = "unknown"
    has_design = bool(design_urls or design_files or design_words)

    api_doc_urls = re.findall(
        r"https?://[^\s)]+(?:swagger|openapi|apifox|postman|api-docs?)[^\s)]*", text, re.I
    )
    document_paths = re.findall(
        r"(?:(?:[A-Za-z]:)?[/~][^\n\r]*?\.(?:md|json|ya?ml)|[^\s`]+\.(?:md|json|ya?ml))",
        text,
        re.I,
    )
    # A generic README.md or package.json is not API evidence. Paths must name an
    # interface/API artifact, while an explicit “接口文档” assertion remains valid.
    api_doc_paths = [
        path
        for path in document_paths
        if Path(path).name != request.name
        and re.search(r"api|openapi|swagger|apifox|postman|接口|协议|contract", Path(path).name, re.I)
    ]
    api_doc_words = bool(_affirmed_intents(text, r"接口文档|API 文档|API文档|OpenAPI|Swagger|Apifox|Postman"))
    has_api_doc = bool(api_doc_urls or api_doc_paths or api_doc_words)

    browser_keywords = bool(re.search(r"浏览器|页面验收|UI验收|视觉验收|交互验收|截图|响应式|适配", text, re.I))
    browser_acceptance_opt_out = bool(
        re.search(
            r"(?:不(?:需要|用)?|不要|无需|不用)\s*(?:进行|做)?\s*(?:浏览器|页面|UI|视觉|交互)验收",
            text,
            re.I,
        )
    )
    needs_browser_acceptance = scope in {"frontend", "fullstack"} and (
        has_design or browser_keywords or bool(re.search(r"页面|组件|交互|CSS|Vue|React", text, re.I))
    ) and not browser_acceptance_opt_out

    remote_decisions = intent_decisions(
        text,
        r"推送|(?:\bgit\s+)?\bpush\b|提交\s*(?:代码|改动|更改)|\bcommit\b|(?:创建|发起)\s*(?:MR|PR|合并请求)|"
        r"\b(?:create|open|raise)\s+(?:an?\s+)?(?:MR|PR|pull request|merge request)\b|"
        r"(?:触发|运行|执行|跑)\s*(?:云效|流水线|CI|CD)|"
        r"(?:部署|发布)\s*(?:到|至)?\s*(?:远端|线上|环境)|远端交付|远程调度|合并到",
    )
    remote_intents = [item["text"] for item in remote_decisions if item["decision"] == "affirmed"]
    needs_remote_delivery = bool(remote_intents)
    execution_route = "local-then-remote-handoff" if needs_remote_delivery else "local"

    if scope == "frontend":
        route = "frontend-with-design" if has_design else "frontend-no-design"
    elif scope == "fullstack":
        route = "fullstack-with-design" if has_design else "fullstack-no-design"
    else:
        route = scope
    return {
        "schema": "factory-local/classification/4",
        "domain": scope,
        "execution_mode": mode,
        "scope": scope,
        "has_design": has_design,
        "design_urls": design_urls,
        "design_files": design_files,
        "has_api_doc": has_api_doc,
        "api_doc_urls": api_doc_urls,
        "api_doc_paths": api_doc_paths,
        "needs_browser_acceptance": needs_browser_acceptance,
        "needs_remote_delivery": needs_remote_delivery,
        "remote_intents": remote_intents,
        "execution_route": execution_route,
        "remote_execution_supported": False,
        "route": route,
        "evidence": {
            "frontend_keywords": frontend,
            "backend_keywords": backend,
            "backend_context_only": backend_context_only,
            "domain_intent_decisions": {"frontend": frontend_decisions, "backend": backend_decisions},
            "design_intent_decisions": design_decisions,
            "design_words": design_words,
            "api_doc_words": api_doc_words,
            "browser_keywords": browser_keywords,
            "browser_acceptance_opt_out": browser_acceptance_opt_out,
            "analysis_only": analysis_only,
            "remote_intent_decisions": remote_decisions,
        },
    }


def write_remote_handoff(
    task: Path, classification: dict[str, object], config: dict[str, object]
) -> Path | None:
    """Persist a handoff request; this local runner never executes it."""
    if not classification.get("needs_remote_delivery"):
        return None
    routing = config["intent_routing"]
    assert isinstance(routing, dict)
    handoff = routing["remote_handoff"]
    assert isinstance(handoff, dict)
    path = task / "remote-handoff.json"
    payload = {
        "schema": "factory-local/remote-handoff/1",
        "task_id": task_identity(task),
        "status": "required",
        "execution_route": "local-then-remote-handoff",
        "target_executor": handoff["executor"],
        "requested_remote_actions": classification.get("remote_intents", []),
        "local_runner_policy": {
            "remote_side_effects": config["remote_side_effects"],
            "auto_commit": config["auto_commit"],
            "may_execute_remote_actions": handoff["local_runner_may_execute_remote_actions"],
        },
        "next_action": "完成本地验证后，由用户明确启动目标执行器处理此交接。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地 Factory：需求分析 -> spec -> AI 编码 -> 本地验证")
    ap.add_argument("--task", required=True, help="任务目录名，例如 20261016-callback-ui")
    ap.add_argument("--request-file", required=True, type=Path, help="需求输入文件")
    ap.add_argument("--model", default=os.environ.get("FACTORY_LOCAL_MODEL", "gpt-5.6-sol"))
    ap.add_argument("--stage-timeout", type=int, default=900, help="单个 AI 阶段最大秒数")
    restart = ap.add_mutually_exclusive_group()
    restart.add_argument("--resume", action="store_true", help="从上次失败/中断或待验收节点恢复，校验前置产物")
    restart.add_argument(
        "--from-stage",
        choices=STAGES,
        default=None,
    )
    ap.add_argument(
        "--only-stage",
        choices=STAGES,
        help="只运行一个节点并记录产物，适合先补设计分析或单独复核",
    )
    ap.add_argument("--authorize-through", choices=STAGES, help="显式授权执行上限；普通 resume/only-stage 不能扩大已有授权")
    ap.add_argument("--browser-evidence", type=Path, help="绑定本次需求和源码的浏览器验收收据")
    ap.add_argument("--verification-evidence", type=Path, help="按 Spec 编号/环境填写、绑定当前版本的验收收据")
    ap.add_argument('--clarification-answers', type=Path, help='操作者提供的业务澄清答复；模型不能代替用户确认')
    ap.add_argument('--readiness-evidence', type=Path, help='环境地址、契约和账号权限可用性证据，不包含密码/token')
    ap.add_argument('--acceptance-scope', choices=['all', 'local-mock'], help='环境准备范围；local-mock 暂缓真实环境准备但不取消原必验项')
    ap.add_argument("--revision-of", help="仅登记关联修订：父任务目录名；保留历史，从 classify 重新开始")
    ap.add_argument("--revision-reason", help="追加修复/范围变更原因，必须显式提供")
    ap.add_argument("--revision-evidence", type=Path, action='append', default=[], help="引用既有任务证据，仅作历史，不继承通过状态")
    args = ap.parse_args(argv)
    if args.verification_evidence and args.browser_evidence:
        print("--verification-evidence 与 --browser-evidence 不能同时提供；请把浏览器证据作为统一验收收据的 artifacts/checks 引用", file=sys.stderr)
        return 2
    if args.revision_of and (args.resume or args.from_stage or args.only_stage):
        ap.error('--revision-of 仅登记修订，不与恢复/阶段执行混用')
    base = (ROOT / "artifacts" / "factory-local").resolve()
    task = (base / args.task).resolve()
    if not task.is_relative_to(base) or task == base:
        ap.error("--task 必须位于 artifacts/factory-local 内")
    task.mkdir(parents=True, exist_ok=True)
    request = args.request_file if args.request_file.is_absolute() else ROOT / args.request_file
    required(request)
    with (task / ".runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("同一任务已有运行器执行，拒绝并发改写状态", file=sys.stderr)
            return 2
        state_path = task / "run-state.json"
        try:
            previous = json.loads(state_path.read_text()) if state_path.exists() else {}
            if not isinstance(previous, dict):
                raise ValueError("恢复状态结构无效")
            if previous.get('status') == 'superseded':
                print('任务已有后续修订，请使用：' + str(previous.get('recovery_command')), file=sys.stderr)
                return 2
            if args.revision_of:
                state = create_revision(ROOT, task, request, args.revision_of, args.revision_reason,
                                        args.authorize_through,
                                        [p if p.is_absolute() else ROOT / p for p in args.revision_evidence],
                                        stages=STAGES, identify=task_identity, source_digest=source_digest,
                                        snapshot=worktree_snapshot, write_json=write_json, now=now,
                                        python=sys.executable, runner=ROOT / ".factory/run.py")
                print(json.dumps(state, ensure_ascii=False))
                return 0
            validate_revision(ROOT, task)
            authorization = resolve_authorization(task, request, args, previous)
            for option, name in [(args.clarification_answers, 'clarification-answers.json'),
                                 (args.readiness_evidence, 'environment-evidence.json')]:
                if option:
                    source = option if option.is_absolute() else ROOT / option
                    data = source.read_bytes()
                    if not isinstance(json.loads(data), dict):
                        raise ValueError(f'{name}: 证据必须是 JSON 对象')
                    if name == 'environment-evidence.json':
                        import_environment_evidence(source, task)
                    else:
                        (task / name).write_bytes(data)
            if args.clarification_answers:
                clarification_answers(task, request, task_identity(task))
            policy_path = task / 'execution-policy.json'
            prior_policy = object_file(policy_path) if policy_path.is_file() else {}
            scope = args.acceptance_scope or prior_policy.get('acceptance_scope', 'all')
            restart_stage = previous.get('resume_from') if args.resume and previous else args.from_stage
            if args.acceptance_scope and previous and restart_stage not in {'classify', 'clarification', 'spec', 'preflight'}:
                if prior_policy.get('acceptance_scope') != scope:
                    raise ValueError('改变环境准备范围须显式从 preflight 或更早阶段重跑，不改写原验收义务')
            write_json(policy_path, {'workflow_version': WORKFLOW_VERSION, 'task_id': task_identity(task),
                                    'request_sha256': digest(request), 'acceptance_scope': scope})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            with (task / "authorization-events.jsonl").open("a") as output:
                output.write(json.dumps({"time": now(), "status": "denied", "reason": str(exc)}, ensure_ascii=False) + "\n")
            print(f"Factory authorization blocked：{exc}", file=sys.stderr)
            return 2
        try:
            attempt = Attempt(task, request, authorization)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            write_json(task / "run-state.json", {
                "schema": "factory-local/run-state/1", "status": "failed", "stage": "setup",
                "reason": str(exc), "rc": 2, "recovery_command": None,
            })
            print(f"Factory failed：{exc}", file=sys.stderr)
            return 2
        token = ACTIVE_ATTEMPT.set(attempt)
        previous_handler = signal.signal(signal.SIGTERM, interrupt_run)
        try:
            previous = json.loads(state_path.read_text()) if state_path.exists() else {}
            attempt.receipts = json.loads(attempt.receipts_path.read_text()) if attempt.receipts_path.exists() else {}
            if not isinstance(previous, dict) or not isinstance(attempt.receipts, dict):
                raise ValueError("恢复状态或阶段收据结构无效")
            active = previous.get("active_command")
            if active:
                attempt.state["active_command"] = active
                if previous.get("resume_from") in STAGES:
                    attempt.stage = previous["resume_from"]
                try:
                    os.killpg(int(active["pid"]), 0)
                except ProcessLookupError:
                    attempt.state["active_command"] = None
                else:
                    raise ValueError("前次子进程仍在运行，拒绝恢复或重新启动；请先确认该进程已退出")
            if args.resume:
                if not previous or previous.get("status") == "completed":
                    raise ValueError("没有可恢复的未完成任务")
                if previous.get("request_sha256") != attempt.request_sha256:
                    raise ValueError("需求已改变；请从 classify 重新执行")
                args.from_stage = previous.get("resume_from")
                if args.from_stage not in STAGES:
                    raise ValueError("恢复节点无效；请显式指定 --from-stage")
            args.from_stage = args.from_stage or "classify"
            if args.only_stage and STAGES.index(args.only_stage) < STAGES.index(args.from_stage):
                raise ValueError("--only-stage 不能早于 --from-stage")
            attempt.prepare(args.from_stage)
            rc = execute_pipeline(args, task, task / "state-events.jsonl", request)
            if attempt.stage == "verify":
                result = json.loads((task / "result.json").read_text())
                status = result["overall_status"]
                attempt.finish(status, rc)
            else:
                next_stage = STAGES[min(STAGES.index(attempt.stage) + 1, len(STAGES) - 1)]
                ceiling = authorization["authorized_end_stage"]
                status = "awaiting_authorization" if STAGES.index(next_stage) > STAGES.index(ceiling) else "stage_completed"
                attempt.finish(status, rc, resume_from=next_stage)
            print(json.dumps({"status": attempt.state["status"], "task_id": attempt.task_id, "attempt_id": attempt.id,
                              "result": str(attempt.directory / "final/result.json"),
                              "recovery_command": attempt.state["recovery_command"]}, ensure_ascii=False))
            return rc
        except (Exception, SystemExit, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            rc = 130 if interrupted else (exc.rc if isinstance(exc, CommandFailure) else 2)
            status = (exc.status if isinstance(exc, WorkflowPending) else
                      'interrupted' if interrupted else 'failed')
            if isinstance(exc, WorkflowPending):
                rc = 3
            details = {"reason": str(exc) or "interrupted", "retryable": interrupted or (
                isinstance(exc, CommandFailure) and exc.reason in {"rate_limited", "timeout"})}
            if isinstance(exc, CommandFailure):
                details["command"] = exc.command
                details["failure_code"] = exc.reason
            append_event(task / "state-events.jsonl", attempt.stage, status, rc=rc, **details)
            attempt.finish(status, rc, **details)
            print(f"Factory {status}：{details['reason']}；证据 {attempt.directory}；恢复：{attempt.state['recovery_command']}", file=sys.stderr)
            return rc
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            ACTIVE_ATTEMPT.reset(token)


def execute_pipeline(args: argparse.Namespace, task: Path, events: Path, request: Path) -> int:
    config = load_local_factory_config()
    stages = STAGES
    start = stages.index(args.from_stage)
    cap = ACTIVE_ATTEMPT.get().authorization["authorized_end_stage"]
    end = stages.index(args.only_stage or cap)
    if start > end:
        raise ValueError("执行起点超出授权上限")

    baseline = task / "baseline-files.txt"
    content_baseline = task / "worktree-baseline.json"
    if not baseline.exists():
        baseline.write_text(run(["git", "diff", "--name-only"], check=True).stdout, encoding="utf-8")
        write_json(content_baseline, {"schema": "factory-local/worktree-baseline/1",
                                     "snapshot": worktree_snapshot(ROOT), "legacy_scope_candidates": []})
    if content_baseline.exists():
        ACTIVE_ATTEMPT.get().snapshot(content_baseline.name, "inputs")

    classification_path = task / "classification.json"
    if not classification_path.exists() or args.from_stage == "classify":
        append_event(events, "classify", "started")
        classification = classify(request, task)
        classification_path.write_text(json.dumps(classification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        handoff = write_remote_handoff(task, classification, config)
        append_event(
            events,
            "classify",
            "completed",
            artifact=str(classification_path),
            route=classification["route"],
            execution_route=classification["execution_route"],
            handoff_artifact=str(handoff) if handoff else None,
        )
    else:
        classification = json.loads(classification_path.read_text(encoding="utf-8"))
        if classification.get("schema") != "factory-local/classification/4":
            raise SystemExit("分类产物版本过期：请从 --from-stage classify 重新执行")
        handoff = write_remote_handoff(task, classification, config)

    if end == 0:
        return 0

    skill_selection = task / "skill-selection.json"
    if not skill_selection.exists() or args.from_stage == "classify":
        append_event(events, "skill-discovery", "started", source=str(AWESOME_RULES_ROOT / "skills"))
        skill_status, skill_reason = discover_skills(request, classification, task)
        append_event(events, "skill-discovery", skill_status, artifact=str(skill_selection), reason=skill_reason)
        if skill_status == "blocked":
            raise ValueError(skill_reason)
    else:
        required(skill_selection)
        prior_selection = json.loads(skill_selection.read_text())
        if prior_selection.get("schema") != "factory-local/skill-selection/2":
            raise ValueError("技能选择产物版本过期；请从 classify 重跑")
        if prior_selection.get("status") == "passed":
            policy_path = AWESOME_RULES_ROOT / "skills/factory-routing.json"
            if not policy_path.is_file() or prior_selection.get("policy", {}).get("sha256") != digest(policy_path):
                raise ValueError("技能触发配置已变化；请从 classify 重跑")

    if end == 1:
        return 0

    requirement = task / "requirement.md"
    spec = task / "spec.md"
    design_analysis = task / "design-analysis.md"
    if classification.get("has_design") and start <= 2 and end >= 2:
        append_event(events, "design-analysis", "started")
        capture_status, capture_reason = figma_capture_status(task, request, classification)
        append_event(events, "figma-capture", capture_status, reason=capture_reason)
        design_analysis.write_text(
            "# Design analysis\n\n"
            "Factory 已识别为带 Figma 证据的前端路径。此节点只负责把任务交给\n"
            "awesome-rules 的设计能力；不得根据链接或模型描述猜测像素、颜色或间距。\n\n"
            f"- Figma URLs: {json.dumps(classification.get('design_urls', []), ensure_ascii=False)}\n"
            f"- Capture status: {capture_status} ({capture_reason})\n"
            "- Required hand-off: Figma/CUA 采集 design-evidence.json 与 design-input.json\n"
            "- Contract owner: awesome-rules/figma-frontend-implementation\n"
            "- Visual gate owner: awesome-rules/figma-visual-guard\n"
            "- Missing evidence blocks implementation and cannot be reported as visual pass\n",
            encoding="utf-8",
        )
        required(design_analysis)
        design_stage_status = "blocked"
        if capture_status == "blocked":
            write_design_orchestration(task, classification, "blocked", capture_reason)
            append_event(
                events,
                "design-analysis",
                "blocked",
                artifact=str(task / "design-orchestration.json"),
                reason=capture_reason,
            )
            raise SystemExit("Figma 认证/采集被阻断：请按 figma-capture-receipt.json 的操作路径完成认证后重试")
        if not (task / "design-input.json").is_file():
            write_design_orchestration(
                task,
                classification,
                "blocked",
                "design-input.json 缺失：需要先由 Figma/CUA 采集真实节点截图、viewport 和设计事实",
            )
            append_event(
                events,
                "design-analysis",
                "blocked",
                artifact=str(task / "design-orchestration.json"),
                reason="design-input-missing",
            )
        else:
            design_contract, design_contract_reason = design_contract_status(task, classification)
            write_design_orchestration(task, classification, design_contract, design_contract_reason)
            append_event(
                events,
                "design-contract",
                design_contract,
                artifact=str(task / "design-contract.json") if design_contract == "passed" else None,
                reason=design_contract_reason,
            )
            if design_contract == "passed":
                required(task / "design-contract.json")
                visual_contract, visual_contract_reason = visual_contract_status(task, classification)
                append_event(
                    events,
                    "visual-contract",
                    visual_contract,
                    artifact=str(task / "visual-contract.json") if visual_contract == "passed" else None,
                    reason=visual_contract_reason,
                )
                if visual_contract == "passed":
                    required(task / "visual-contract.json")
                    design_stage_status = "passed"
                else:
                    design_stage_status = "blocked"
        if design_stage_status == "passed":
            append_event(events, "design-analysis", "completed", artifact=str(design_analysis))
        else:
            raise SystemExit("设计分析被阻断：缺少有效的 design-contract.json 或 visual-contract.json")
        if args.only_stage == "design-analysis" and design_stage_status != "passed":
            raise SystemExit("设计分析被阻断：需要先完成 Figma/CUA 事实采集和设计契约生成")
    elif not (classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}):
        write_design_orchestration(task, classification, "not_required", "任务不包含 Figma 设计证据")

    if (
        start >= 3
        and not (task / "skill-selection.json").is_file()
    ):
        raise SystemExit("任务被阻断：进入需求分析或后续阶段前必须存在 skill-selection.json")
    if start >= 3:
        validate_selected_skills(task)

    if (
        start >= 3
        and classification.get("has_design")
        and classification.get("scope") in {"frontend", "fullstack"}
        and (not (task / "design-contract.json").is_file() or not (task / "visual-contract.json").is_file())
    ):
        raise SystemExit("设计驱动任务被阻断：进入后续阶段前必须存在 design-contract.json 和 visual-contract.json")

    if start <= stages.index('requirement') <= end:
        write_json(task / "requirement-input.json", requirement_input(task, request))
        append_event(events, "requirement", "started")
        run_model_stage(
            "requirement",
            f"""执行已加载的 factory-requirement-analysis。
运行器输入：{task / 'requirement-input.json'}；原始需求：{request}；分类：{classification_path}；技能选择：{skill_selection}。
若存在设计分析则读取：{design_analysis}。按 Skill 协议只生成 {requirement} 和 {task / 'requirement-contract.json'}。
逐字复制 requirement-input 绑定；不要修改业务代码、治理文件、答复或后续阶段产物。""",
            task / "requirement.log",
            args.model,
            args.stage_timeout,
            config,
        )
        required(requirement)
        required(task / "requirement-contract.json")
        validate_requirement(task, request, freeze_sources=True)
        append_event(events, "requirement", "completed", artifact=str(requirement), content_validation="passed")

    if start <= stages.index('clarification') <= end:
        append_event(events, 'clarification', 'started')
        frozen_sources = business_sources(task, request, config, freeze=True)
        clarity_binding = {'schema': CLARIFICATION, 'workflow_version': WORKFLOW_VERSION,
                           'task_id': task_identity(task), 'request_sha256': digest(request),
                           'requirement_sha256': digest(requirement),
                           'requirement_contract_sha256': digest(task / 'requirement-contract.json'),
                           'business_sources_sha256': digest(task / 'business-sources.snapshot.json'),
                           'answers_sha256': digest(task / 'clarification-answers.json')
                           if (task / 'clarification-answers.json').is_file() else None}
        source_index = [{key: row[key] for key in ('id', 'path', 'version', 'scope_paths')}
                        for row in frozen_sources.get('sources', [])]
        run_model_stage('clarification', f"""执行已加载的 factory-clarification，使用 Factory 节点模式。
读取原始需求 {request}、已校验需求契约 {task / 'requirement-contract.json'}、人读分析 {requirement}、已有显式答复 {task / 'clarification-answers.json'}（若存在）和冻结业务来源 {task / 'business-sources.snapshot.json'}。
按 Skill 协议只生成 {task / 'clarification-contract.json'}，schema={CLARIFICATION}；原样复制绑定字段 {json.dumps(clarity_binding, ensure_ascii=False)}。
逐项解析 requirement behavior_candidates；可引用来源索引：{json.dumps(source_index, ensure_ascii=False)}，正文只从冻结快照读取。
不要修改代码、需求分析、治理配置、答复或后续阶段产物。""",
                        task / 'clarification.log', args.model, args.stage_timeout, config)
        business_sources(task, request, config)
        clarity = validate_clarification(task, request, task_identity(task))
        write_json(task / 'clarification-result.json', clarity)
        if clarity['status'] != 'passed':
            prior = object_file(task / 'clarification-obligations.json') if (task / 'clarification-obligations.json').exists() else {}
            known = {q['id']: q for q in prior.get('questions', [])}
            for question in clarity['obligations']:
                if question['id'] in known and known[question['id']] != question:
                    raise ValueError('不能改写既有阻塞问题来绕过业务澄清')
                known[question['id']] = question
            write_json(task / 'clarification-obligations.json', {
                'workflow_version': WORKFLOW_VERSION, 'task_id': task_identity(task),
                'request_sha256': digest(request), 'questions': list(known.values())})
            raise WorkflowPending('awaiting_clarification', '存在未解决的业务澄清项：' + ', '.join(clarity['blockers']))
        append_event(events, 'clarification', 'completed', artifact=str(task / 'clarification-result.json'))

    if start <= stages.index('spec') <= end:
        write_json(task / "spec-input.json", spec_input(task, request))
        append_event(events, "spec", "started")
        run_model_stage(
            "spec",
            f"""执行已加载的 factory-spec-authoring。
读取 {task / 'spec-input.json'}、{task / 'requirement-contract.json'}、{task / 'clarification-contract.json'}、{skill_selection} 和相关设计/接口契约。
按 Skill 协议只生成 {spec} 与 {task / 'spec-contract.json'}，schema=factory-local/spec-contract/2；逐字复制 spec-input 的全部绑定。
命令 argv 只能原样选择维护者可信清单：{json.dumps([item['argv'] for item in trusted_commands(config).values()], ensure_ascii=False)}。
若存在 {task / 'revision.json'}，保留全部父任务必验义务并重新验证。不要修改源码、治理文件、spec-input 或授权。""",
            task / "spec.log",
            args.model,
            args.stage_timeout,
            config,
        )
        required(spec)
        validate_spec(task, request, config)
        append_event(events, "spec", "completed", artifact=str(spec), content_validation="passed")

    if start <= stages.index('spec-review') <= end:
        append_event(events, "spec-review", "started")
        validate_requirement(task, request)
        validate_spec(task, request, config)
        contract = object_file(task / "spec-contract.json")
        independent_spec_review(args, task, request, contract, config)
        append_event(events, "spec-review", "completed", artifact=str(task / "spec-review-result.json"))

    if start <= stages.index('preflight') <= end:
        append_event(events, 'preflight', 'started')
        validate_requirement(task, request, check_source=False)
        validate_spec(task, request, config)
        contract = object_file(task / 'spec-contract.json')
        require_current_spec_review(task, request, contract)
        check_preflight(args, task, request, contract, config)
        append_event(events, 'preflight', 'completed', artifact=str(task / 'preflight-result.json'))

    if start <= stages.index('implement') <= end:
        append_event(events, 'implement', 'started')
        validate_requirement(task, request, check_source=False)
        validate_spec(task, request, config, check_source=False)
        contract = json.loads((task / "spec-contract.json").read_text())
        require_current_spec_review(task, request, contract)
        check_preflight(args, task, request, contract, config, commands=False)
        if not content_baseline.exists():
            # Legacy filename-only data cannot distinguish edits to the same file.
            # Migrate explicitly and conservatively include already dirty Spec scope.
            write_json(content_baseline, {"schema": "factory-local/worktree-baseline/1",
                "snapshot": worktree_snapshot(ROOT),
                "legacy_scope_candidates": legacy_candidates(ROOT, contract["scope"]["allowed_files"]),
                "migration": "legacy-filename-list; scope candidates are conservative, not historical proof"})
            ACTIVE_ATTEMPT.get().snapshot(content_baseline.name, "inputs")
        baseline_state = json.loads(content_baseline.read_text())
        if baseline_state.get("schema") != "factory-local/worktree-baseline/1":
            raise ValueError("Factory 内容基线无效")
        frozen_baseline = baseline_state['snapshot']
        if hashlib.sha256(json.dumps(frozen_baseline, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != contract['source_sha256']:
            raise ValueError('实施任务基线与 Spec 源码绑定不一致，须建立关联修订')
        enforce_scope(task, contract, changed_files(frozen_baseline, worktree_snapshot(ROOT)))
        implementation_before = worktree_snapshot(ROOT)
        content_before = capture_implementation_before(task, contract['scope']['allowed_files'], implementation_before)
        if classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}:
            if not (task / "design-contract.json").is_file():
                append_event(events, "implement", "blocked", reason="design-contract-missing")
                raise SystemExit("设计驱动前端编码被阻断：缺少 design-contract.json")
        run_model_stage(
            "implement",
            f"""你是本地 Factory 的 AI 编码节点。读取 {requirement}、{spec}、{skill_selection}、分类结果 {classification_path} 和（若存在）{task / 'design-contract.json'}，检查当前工作树。
编码前读取 skill-selection.json 中适用 awesome-rules 技能原文；涉及接口、架构、设计稿、视觉验收或测试规范时，必须按对应技能执行并保留其验证证据。
按 Spec 实现需求，只修改业务源码、测试和本任务允许的 artifacts；不要修改 .factory、MISSION.md、package.json、锁文件、docs、vite.config.ts 或 scripts/factory-gate.sh。
复用现有依赖和代码模式，不提交、不推送。完成后运行与改动相关的最小验证，并将变更摘要写入 {task / 'implement.md'}。""",
            task / "implement.log",
            args.model,
            args.stage_timeout,
            config,
        )
        implementation_after = worktree_snapshot(ROOT)
        introduced = sorted(set(changed_files(baseline_state["snapshot"], implementation_after))
                            | set(baseline_state["legacy_scope_candidates"])
                            | set(changed_files(implementation_before, implementation_after)))
        write_json(task / "implement-changes.json", {
            "schema": "factory-local/implement-changes/1",
            "baseline_sha256": digest(content_baseline), "files": introduced,
            "stage_before": implementation_before, "stage_after": implementation_after,
            "content_before": content_before,
            "legacy_scope_candidates": baseline_state["legacy_scope_candidates"],
        })
        (task / "implemented-files.txt").write_text("\n".join(introduced) + "\n", encoding="utf-8")
        enforce_scope(task, contract, introduced)
        if not (task / "implement.md").exists():
            (task / "implement.md").write_text("# Implement\n\n" + "\n".join(f"- {f}" for f in introduced), encoding="utf-8")
        append_event(events, "implement", "completed", files=introduced)

    if start <= stages.index('review') <= end:
        append_event(events, 'review', 'started')
        validate_requirement(task, request, check_source=False)
        validate_spec(task, request, config, check_source=False)
        contract = object_file(task / 'spec-contract.json')
        require_current_spec_review(task, request, contract)
        independent_review(args, task, request, contract, config)
        append_event(events, 'review', 'completed', artifact=str(task / 'review-result.json'))

    if start <= stages.index('verify') <= end:
        append_event(events, "verify", "started")
        capture_docs_context("verify", config)
        validate_requirement(task, request, check_source=False)
        validate_spec(task, request, config, check_source=False)
        contract = json.loads((task / 'spec-contract.json').read_text())
        require_current_spec_review(task, request, contract)
        require_current_review(task, request, contract)
        preflight_result = check_preflight(args, task, request, contract, config, commands=False)
        verification_source = source_digest()
        verification_snapshot = worktree_snapshot(ROOT)
        files = [f for f in (task / "implemented-files.txt").read_text(encoding="utf-8").splitlines() if f]
        changes = json.loads((task / 'implement-changes.json').read_text())
        baseline_state = json.loads(content_baseline.read_text())
        actual = sorted(set(changed_files(baseline_state['snapshot'], verification_snapshot))
                        | set(baseline_state['legacy_scope_candidates'])
                        | set(changed_files(changes['stage_before'], verification_snapshot)))
        if files != actual or changes['files'] != actual:
            raise ValueError('实现变更清单与实际内容差异不一致，拒绝漏检或沿用过期清单')
        enforce_scope(task, contract, actual, phase='verify')
        if files:
            guard = run(["python3", ".factory/guard.py", "--files", *files], log=task / "guard.log", check=False)
            if guard.returncode:
                raise CommandFailure(["python3", ".factory/guard.py"], guard.returncode, "guard_failed")
        gate_command = shlex.split(config["final_gate_cmd"])
        current_attempt = ACTIVE_ATTEMPT.get()
        gate = run(
            gate_command,
            log=task / "final-gate.log",
            check=False,
            env={
                'FACTORY_QUALITY_TASK_ID': current_attempt.task_id,
                'FACTORY_QUALITY_ATTEMPT_ID': current_attempt.id,
                'FACTORY_QUALITY_STAGE': 'verify',
            },
        )
        bind_quality_gate_receipt(task, gate, gate_command)
        if verification_source != source_digest():
            raise ValueError("源码在验证期间发生变化；请重新执行 implement 和 verify")
        if gate.returncode:
            raise CommandFailure(gate_command, gate.returncode, "quality_gate_failed")
        visual = None
        if classification.get("has_design") and classification.get("scope") in {"frontend", "fullstack"}:
            write_visual_templates(task)
            visual_status, visual_reason = visual_evidence_status(task, classification)
            visual = f"{visual_status}: {visual_reason}"
            (task / "visual-guard-result.json").write_text(
                json.dumps(
                    {"schema": "factory-local/visual-guard-result/1", "status": visual_status, "reason": visual_reason},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (task / "visual-verify.todo").write_text(
                "采集 design-evidence.json、visual-contract.json、Figma 截图和真实页面截图，填写 visual-evidence.json；未完成前不得标记 passed。\n",
                encoding="utf-8",
            )
        else:
            visual_status = "not_required"
        evidence = args.verification_evidence or args.browser_evidence
        if evidence is not None and not evidence.is_absolute():
            evidence = ROOT / evidence
        verified = verify_spec(task, contract, config, evidence, verification_source, classification, visual_status)
        browser_status = verified['browser_status']
        browser_reason = verified['evidence_error'] or ('未通过必验项：' + ', '.join(verified['missing_required'])
                                                        if verified['missing_required'] else 'Spec 必验项均已通过')
        if verification_source != source_digest() or changed_files(verification_snapshot, worktree_snapshot(ROOT)):
            raise ValueError("源码在验收期间发生变化；请重新执行 implement 和 verify")
        functional_status = ('failed' if verified['failed_required'] else
                             'pending' if verified['missing_required'] else 'passed')
        integration_status = verified['integration_status']
        delivery_status = "local-only"
        overall_status = verified['overall_status']
        result = {
            "task_id": ACTIVE_ATTEMPT.get().task_id,
            "attempt_id": ACTIVE_ATTEMPT.get().id,
            "request_sha256": ACTIVE_ATTEMPT.get().request_sha256,
            "source_sha256": verification_source,
            "scope": classification.get("scope"),
            "route": classification.get("route"),
            "execution_route": classification.get("execution_route", "local"),
            "has_api_doc": classification.get("has_api_doc", False),
            "needs_browser_acceptance": classification.get("needs_browser_acceptance", False),
            "needs_remote_delivery": classification.get("needs_remote_delivery", False),
            "remote_handoff_artifact": str(handoff) if handoff else None,
            "guard": not files or guard.returncode == 0,
            "final_gate_rc": gate.returncode,
            "functional_status": functional_status,
            "quality_status": "passed",
            "clarification_status": "passed",
            "spec_review_status": "passed",
            "review_status": "passed",
            "preflight_status": preflight_result['status'],
            "acceptance_scope": preflight_result['acceptance_scope'],
            "deferred_environments": preflight_result['deferred'],
            "verification_result": str(task / 'verification-result.json'),
            "missing_required": verified['missing_required'],
            "visual_verify": visual,
            "visual_status": visual_status,
            "browser_status": browser_status,
            "browser_reason": browser_reason,
            "integration_status": integration_status,
            "delivery_status": (
                "remote-handoff-required"
                if classification.get("needs_remote_delivery")
                else delivery_status
            ),
            "overall_status": overall_status,
            "claims": {
                "functional": "Spec 必验项通过" if functional_status == "passed" else "业务必验项尚未全部通过",
                "visual": "已按设计证据还原" if visual_status == "passed" else "未完成 Figma 级视觉验收",
                "integration": "真实接口已验证" if integration_status == "passed" else "真实接口未验证",
            },
            "files": files,
        }
        (task / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_status = overall_status
        append_event(events, "verify", verify_status, **result)
        return 1 if overall_status == 'failed' else (3 if overall_status in {'blocked', 'pending_acceptance'} else 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
