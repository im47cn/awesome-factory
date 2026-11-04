"""Hermetic runner regressions: completion, resumability and immutable attempts.

AI and quality commands are replaced at their process boundary; real temporary Git
repositories exercise request/source binding without touching business files/network.
"""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / 'tests/local'))
from support import prepare
# Pytest also collects .factory/tests/test_acceptance.py under the short name.
# Load the local-runner fixture by path so collection order cannot choose it.
_case_spec = importlib.util.spec_from_file_location(
    'factory_local_acceptance_fixture', SOURCE / 'tests/local/test_acceptance.py')
_case_module = importlib.util.module_from_spec(_case_spec)
_case_spec.loader.exec_module(_case_module)
FactoryCase = _case_module.FactoryCase
from runtime.local import runner as factory
MODULE = SOURCE / 'templates/local/run.py'


def make_spec(factory, task):
    inputs = json.loads((task / "spec-input.json").read_text())
    (task / "spec.md").write_text(inputs["task_id"] + " AC-01 source.txt")
    factory.write_json(task / "spec-contract.json", {
        **inputs, "schema": "factory-local/spec-contract/2", "goal": "fixture behavior",
        "scope": {"allowed_files": ["source.txt"], "non_goals": ["unrelated changes"]},
        "acceptance": [{"id": "AC-01", "criterion": "observable behavior", "requirement_ids": ["RQ-1"], "clarification_ids": ["CL-1"], "verification_ids": ["V-1"]}],
        "verification": [{"id": "V-1", "kind": "command", "argv": ["python3", "-V"],
                          "required": True, "status": "NOT_RUN", "environment": "local"}],
        "external_requirements": [],
    })
    classification = json.loads((task / 'classification.json').read_text())
    if classification.get('needs_browser_acceptance'):
        value = json.loads((task / 'spec-contract.json').read_text())
        value['acceptance'][0]['verification_ids'].append('V-B')
        value['verification'].append({'id':'V-B', 'kind':'manual', 'evidence_type':'browser', 'environment':'mock',
                                     'required':True, 'status':'NOT_RUN', 'steps':['Observe the mock page behavior']})
        factory.write_json(task / 'spec-contract.json', value)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    for args in [["init", "-q"], ["config", "user.name", "Runtime Test"],
                 ["config", "user.email", "runtime@example.invalid"]]:
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / ".gitignore").write_text("artifacts/\n__pycache__/\n")
    (root / "source.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], cwd=root, check=True)
    monkeypatch.setattr(factory, "ROOT", root)
    config = {"execution_mode": "local", "remote_side_effects": False, "auto_commit": False,
              "local_allowed_tools": ["read", "write", "edit", "grep", "lsp"],
              "final_gate_cmd": "bash scripts/factory-gate.sh",
              "intent_routing": {"remote_handoff": {"enabled": True, "executor": "awesome-factory",
                 "local_runner_may_execute_remote_actions": False}}}
    config.update(workflow_version=factory.WORKFLOW_VERSION, local_stage_order=factory.STAGES,
                  generated_files=[], verification_commands=[{'id':'python', 'argv':['python3','-V']}],
                  preflight_commands=[{'id':'python', 'argv':[sys.executable,'--version'], 'minimum_version':[3,10,0]}])
    prepare(root, config)
    factory.write_json(root / '.rules/skills/factory-routing.json', {
        'schema': 'factory-local/skill-routing/1',
        'skills': {name: {'explicit_only': True} for name in config['workflow_skills'].values()},
    })
    config_path = root / "factory-test-config.json"
    config_path.write_text(json.dumps(config))
    original_load = factory.load_local_factory_config
    monkeypatch.setattr(factory, "load_local_factory_config", lambda: original_load(config_path))
    monkeypatch.setattr(factory, "AWESOME_RULES_ROOT", root / ".rules")
    request = tmp_path / "request.md"
    request.write_text("修复 Vue 页面按钮样式")
    task = root / "artifacts/factory-local/runtime"
    calls = []
    behavior = {"fail_stage": None, "gate_rc": 0, "skip_output": None}
    original_run = factory.run

    def fake_run(cmd, *, log=None, **kwargs):
        if cmd[0] == "omp":
            attempt = factory.ACTIVE_ATTEMPT.get()
            stage = attempt.stage
            calls.append(stage)
            if behavior["fail_stage"] == stage:
                if log:
                    (attempt.directory / log.name).write_text("Codex is at capacity turn_rate_limit_exceeded")
                return subprocess.CompletedProcess(cmd, 1, "turn_rate_limit_exceeded", "")
            if stage == 'clarification':
                FactoryCase.clarify(SimpleNamespace(config=config), task, request)
                return subprocess.CompletedProcess(cmd, 0, '', '')
            if stage == 'requirement' and behavior['skip_output'] != stage:
                FactoryCase.requirement(None, task, request)
                return subprocess.CompletedProcess(cmd, 0, '', '')
            if stage == 'spec-review':
                expected = json.loads((task/'spec-review-input.json').read_text())
                expected.pop('context'); expected.pop('manifest')
                proof = [{'path': 'request.md', 'quote': request.read_text()}]
                expected.update(schema='factory-local/spec-review-result/1',
                    requirement_coverage=[{'id': 'RQ-1', 'verdict': 'covered', 'reason': 'Original fixture request', 'evidence': proof}],
                    clarification_coverage=[{'id': 'CL-1', 'verdict': 'covered', 'reason': 'Confirmed fixture request', 'evidence': proof}],
                    acceptance=[{'id': 'AC-01', 'verdict': 'covered', 'reason': 'Observable fixture request', 'evidence': proof}], findings=[])
                return subprocess.CompletedProcess(cmd, 0, json.dumps(expected), '')
            if stage == 'review':
                expected = json.loads((task / 'review-input.json').read_text())
                context = Path(expected['context'])
                report = {**expected, 'reviewed_files':expected['files'], 'findings':[],
                    'acceptance':[{'id':'AC-01','verdict':'covered','reason':'Fixture behavior and source compared',
                        'evidence':[{'path':'source/source.txt','quote':(context/'source/source.txt').read_text()}]}]}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(report), '')
            if behavior["skip_output"] != stage:
                output = {"requirement": "requirement.md", "spec": "spec.md", "implement": "implement.md"}[stage]
                (task / output).write_text(f"{stage} from {attempt.id}\n")
                if stage == "spec":
                    make_spec(factory, task)
                if stage == "implement":
                    (root / "source.txt").write_text("implemented\n")
            if log:
                (attempt.directory / log.name).write_text("passed " + attempt.id)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] in [["bash", "scripts/factory-gate.sh"], ["python3", ".factory/guard.py"]]:
            calls.append("gate" if cmd[0] == "bash" else "guard")
            if behavior.get("mutate_gate") and cmd[0] == "bash":
                (root / "source.txt").write_text("changed during verification")
            if log:
                (factory.ACTIVE_ATTEMPT.get().directory / log.name).write_text("quality output")
            return subprocess.CompletedProcess(cmd, behavior["gate_rc"] if cmd[0] == "bash" else 0, "", "")
        return original_run(cmd, log=log, **kwargs)

    monkeypatch.setattr(factory, "run", fake_run)
    original_event = factory.append_event
    def event(path, stage, status, **extra):
        if stage == 'preflight' and status == 'started':
            import datetime as dt
            contract = json.loads((task / 'spec-contract.json').read_text())
            plan = factory.readiness_plan(task, contract, 'all')
            value = factory.readiness_request(task, request, factory.task_identity(task), plan)
            proof = task / 'environment.txt'; proof.write_text('Fixture operator observed mock endpoint')
            value.update(executor='fixture operator', checked_at=dt.datetime.now(dt.timezone.utc).isoformat())
            for check in value['checks']:
                check.update(status='passed', artifacts=[{'path':proof.name, 'sha256':factory.digest(proof)}],
                             details={'url':'http://127.0.0.1:6277', 'observation':'fixture observation'})
            factory.write_json(task / 'environment-evidence.json', value)
        return original_event(path, stage, status, **extra)
    monkeypatch.setattr(factory, 'append_event', event)

    class Runtime:
        def invoke(self, *args, authorize=True):
            grant = ["--authorize-through", "verify"] if authorize else []
            return factory.main(["--task", "runtime", "--request-file", str(request), *grant, *args])

        def read(self, name):
            return json.loads((task / name).read_text())

        def events(self):
            return [json.loads(line) for line in (task / "state-events.jsonl").read_text().splitlines()]

        def evidence(self):
            receipt = self.read("verification.request.json")
            receipt["status"] = "passed"
            screenshot = task / "page.png"
            screenshot.write_bytes(b"test-only screenshot bytes")
            receipt['executor'] = 'fixture browser observer'
            receipt['checks'] = [{**item, 'status':'passed',
                                  'artifacts':[{'path':'page.png','sha256':factory.digest(screenshot)}]}
                                 for item in receipt['checks']]
            path = task / "browser-receipt.json"
            factory.write_json(path, receipt)
            return path

    instance = Runtime()
    instance.root, instance.task, instance.request = root, task, request
    instance.calls, instance.behavior = calls, behavior
    instance.config_path = config_path
    return instance


def enable_docs(runtime):
    docs = runtime.root / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("# Docs\n[Form](form.md)\n[History](history.md)\n[Cache](cache.md)\n")
    (docs / "form.md").write_text("# Form\nUse existing fields.\n")
    (docs / "history.md").write_text("# History\nObsolete endpoint.\n")
    (docs / "cache.md").write_text("# Cache\nKeep state.\n")
    policy = {"schema": "factory-local/docs-context-policy/1", "enabled": True,
              "index": "docs/README.md", "documents": [
                  {"path": "docs/form.md", "kind": "normative", "triggers": ["表单"]},
                  {"path": "docs/cache.md", "kind": "guidance", "triggers": ["缓存"]},
                  {"path": "docs/history.md", "kind": "historical", "triggers": []},
              ]}
    config = json.loads(runtime.config_path.read_text())
    config["docs_context"] = policy
    runtime.config_path.write_text(json.dumps(config))
    runtime.request.write_text("仅做前端表单需求分析和 Spec；不修改业务代码。不改缓存。")
    return policy


def test_docs_context_is_selected_injected_hashed_and_bound(runtime, monkeypatch):
    enable_docs(runtime)
    original = factory.omp
    prompts = []

    def inspect(stage, prompt, *args):
        prompts.append(prompt)
        return original(stage, prompt, *args)

    monkeypatch.setattr(factory, "omp", inspect)
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 0
    record = runtime.read("spec-docs-context.json")
    assert [s["path"] for s in record["sources"]] == ["docs/README.md", "docs/form.md"]
    source = record["sources"][1]
    assert source["sha256"] == factory.digest(runtime.root / source["path"])
    assert source["version"]["tracked"] is False
    assert source["version"]["last_commit"] is None
    assert record["task_id"] == runtime.read("run-state.json")["task_id"]
    assert all("Use existing fields." in p and "Obsolete endpoint." not in p for p in prompts)
    receipt = runtime.read("stage-receipts.json")["spec"]
    assert receipt["outputs"]["spec-docs-context.json"] == factory.digest(runtime.task / "spec-docs-context.json")


@pytest.mark.parametrize("damage", ["content", "missing", "index", "policy", "disable", "snapshot"])
def test_docs_drift_blocks_real_resume_before_model_and_retains_evidence(runtime, damage):
    enable_docs(runtime)
    runtime.behavior["fail_stage"] = "spec"
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 1
    old = Path(runtime.read("run-state.json")["artifact_dir"])
    before = {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}
    if damage == "content":
        (runtime.root / "docs/form.md").write_text("Changed rule")
    elif damage == "missing":
        (runtime.root / "docs/form.md").unlink()
    elif damage == "index":
        (runtime.root / "docs/README.md").write_text("Changed catalog")
    elif damage in {"policy", "disable"}:
        config = json.loads(runtime.config_path.read_text())
        if damage == "disable":
            config.pop("docs_context")
        else:
            config["docs_context"]["documents"][0]["triggers"] = ["编辑"]
        runtime.config_path.write_text(json.dumps(config))
    else:
        receipt = runtime.read("stage-receipts.json")["requirement"]
        (Path(receipt["artifact_dir"]) / "requirement-docs-context.json").write_text("{}")
    runtime.calls.clear()
    assert runtime.invoke("--resume", authorize=False) == 2
    assert not runtime.calls
    assert "docs" in runtime.read("run-state.json")["reason"]
    if damage != "snapshot":
        assert all(p.read_bytes() == data for p, data in before.items())


def test_docs_unchanged_resume_continues_same_task(runtime):
    enable_docs(runtime)
    runtime.behavior["fail_stage"] = "spec"
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 1
    identity = runtime.read("run-state.json")["task_id"]
    runtime.behavior["fail_stage"] = None
    runtime.calls.clear()
    assert runtime.invoke("--resume", authorize=False) == 0
    assert runtime.calls == ["spec"]
    assert runtime.read("run-state.json")["task_id"] == identity
    checks = list(Path(runtime.read("run-state.json")["artifact_dir"]).glob("*-docs-resume-check.json"))
    assert checks and all(json.loads(p.read_text())["status"] == "passed" for p in checks)


@pytest.mark.parametrize("bad", ["missing", "unlisted", "escape", "historical-auto", "budget", "malformed"])
def test_invalid_docs_configuration_fails_before_model(runtime, bad):
    enable_docs(runtime)
    config = json.loads(runtime.config_path.read_text())
    if bad == "missing":
        (runtime.root / "docs/form.md").unlink()
    elif bad == "unlisted":
        config["docs_context"]["documents"][0]["path"] = "docs/other.md"
    elif bad == "escape":
        config["docs_context"]["documents"][0]["path"] = "../outside.md"
    elif bad == "historical-auto":
        config["docs_context"]["documents"][2]["always"] = True
    elif bad == "budget":
        (runtime.root / "docs/form.md").write_text("a" * 2000)
        config["docs_context"]["max_bytes"] = 1024
    else:
        config["docs_context"] = {"enabled": True}
    runtime.config_path.write_text(json.dumps(config))
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 2
    assert not runtime.calls


def test_model_cannot_modify_frozen_docs_record(runtime, monkeypatch):
    enable_docs(runtime)
    original = factory.omp

    def tamper(stage, *args):
        result = original(stage, *args)
        (runtime.task / f"{stage}-docs-context.json").write_text("{}")
        return result

    monkeypatch.setattr(factory, "omp", tamper)
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 2
    assert runtime.read("stage-integrity.json")["status"] == "blocked"
    assert runtime.read("requirement-docs-context.json")["schema"] == "factory-local/docs-context/1"


def test_explicit_history_is_labeled_and_unselected_change_is_not_docs_drift(runtime):
    enable_docs(runtime)
    runtime.request.write_text("仅分析前端表单；对照 docs/history.md 的历史记录，不修改代码。")
    assert runtime.invoke("--only-stage", "requirement", authorize=False) == 0
    record = runtime.read("requirement-docs-context.json")
    assert next(s for s in record["sources"] if s["path"] == "docs/history.md")["kind"] == "historical"
    (runtime.root / "docs/cache.md").write_text("Unselected change")
    binding = {key: record[key] for key in ("task_id", "attempt_id", "request_sha256", "stage")}
    assert not factory.docs_module().check(runtime.root, record, record["policy"], binding)
    # This selective docs check does not weaken the separate full source binding.


@pytest.mark.parametrize("text,expected", [
    ("参考 docs/history.md，输出分析", True),
    ("不要读取 docs/history.md", False),
    ("# 非目标\n阅读 docs/history.md", False),
    ("示例：\n```\n读取 docs/history.md\n```", False),
])
def test_docs_literal_paths_preserve_negation_and_non_goals(text, expected):
    assert factory.affirmed_doc_reference(text, "docs/history.md") is expected


def test_selected_document_symlink_cannot_escape_repository(runtime, tmp_path):
    enable_docs(runtime)
    outside = tmp_path / "secret.md"
    outside.write_text("outside source")
    path = runtime.root / "docs/form.md"
    path.unlink()
    path.symlink_to(outside)
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 2
    assert not runtime.calls


def test_tracked_docs_version_records_committed_and_dirty_state(runtime):
    enable_docs(runtime)
    subprocess.run(["git", "add", "docs"], cwd=runtime.root, check=True)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "documentation fixture"],
                   cwd=runtime.root, check=True)
    path = runtime.root / "docs/form.md"
    path.write_text(path.read_text() + "Uncommitted clarification\n")
    assert runtime.invoke("--only-stage", "requirement", authorize=False) == 0
    record = runtime.read("requirement-docs-context.json")
    version = record["sources"][1]["version"]
    assert version["tracked"] is True
    assert version["working_tree_changed"] is True
    assert version["head"] == version["last_commit"]


def test_browser_required_never_completes_on_quality_gate_alone(runtime):
    assert runtime.invoke() == 3
    result = runtime.read("result.json")
    assert result["quality_status"] == "passed"
    assert result["functional_status"] == "pending"
    assert result["browser_status"] == "pending"
    assert result["overall_status"] == "pending_acceptance"
    assert runtime.read("run-state.json")["resume_from"] == "verify"


def test_local_task_id_is_created_and_stable_across_attempts(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    first = runtime.read("run-state.json")
    identity = json.loads((runtime.task / "task-identity.json").read_text())
    assert identity["task_id"].startswith("local-")
    assert first["task_id"] == identity["task_id"]
    assert runtime.read("stage-receipts.json")["spec"]["task_id"] == identity["task_id"]
    assert runtime.invoke("--resume") == 3
    second = runtime.read("run-state.json")
    assert second["task_id"] == first["task_id"]
    assert second["attempt_id"] != first["attempt_id"]


def test_damaged_local_task_identity_fails_closed_before_running(runtime):
    runtime.task.mkdir(parents=True)
    (runtime.task / "task-identity.json").write_text(json.dumps({"task_id": "wrong"}))
    assert runtime.invoke() == 2
    assert not (runtime.task / "run-state.json").exists()
    assert (runtime.task / "authorization-events.jsonl").exists()


def test_backend_without_browser_requirement_can_complete(runtime):
    runtime.request.write_text("修复后端 Service层逻辑")
    assert runtime.invoke() == 0
    result = runtime.read("result.json")
    assert result["browser_status"] == "not_required"
    assert result["overall_status"] == "completed"


def test_partial_stage_does_not_claim_task_complete(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    assert runtime.read("result.json")["overall_status"] == "stage_completed"
    assert runtime.read("run-state.json")["resume_from"] == "spec-review"
    assert "implement" not in runtime.calls


def test_resume_acceptance_reuses_prerequisites_and_preserves_old_evidence(runtime):
    assert runtime.invoke() == 3
    old_state = runtime.read("run-state.json")
    old_dir = Path(old_state["artifact_dir"])
    before = {p: p.read_bytes() for p in old_dir.rglob("*") if p.is_file()}
    evidence = runtime.evidence()
    runtime.calls.clear()
    assert runtime.invoke("--resume", "--browser-evidence", str(evidence)) == 0
    assert runtime.calls == ["guard", "gate"]
    result = runtime.read("result.json")
    assert result["overall_status"] == "completed"
    assert result["attempt_id"] != old_state["attempt_id"]
    assert runtime.read("run-state.json")["status"] == "completed"
    assert all(p.read_bytes() == data for p, data in before.items())
    new_dir = Path(runtime.read("run-state.json")["artifact_dir"])
    assert (new_dir / "verification-evidence/receipt.json").is_file()
    assert (new_dir / "verification-evidence/0-page.png").is_file()


@pytest.mark.parametrize("damage", ["request", "source", "schema", "missing_file", "hash", "empty_file",
                                    "empty_checks", "failed_check", "partial_checks", "missing_required"])
def test_browser_evidence_must_be_complete_and_bound_to_current_inputs(runtime, damage):
    assert runtime.invoke() == 3
    path = runtime.evidence()
    receipt = json.loads(path.read_text())
    if damage in {"request", "source"}:
        receipt[damage + "_sha256"] = "outdated"
    elif damage == "schema":
        receipt["schema"] = "unknown"
    elif damage == "missing_file":
        (runtime.task / "page.png").unlink()
    elif damage == "hash":
        receipt["checks"][0]["artifacts"][0]["sha256"] = "wrong"
    elif damage == "empty_file":
        (runtime.task / "page.png").write_bytes(b"")
    elif damage == "empty_checks":
        receipt["checks"] = []
    elif damage == "failed_check":
        receipt["checks"][0]["status"] = "failed"
    elif damage == "partial_checks":
        receipt["checks"].append({"id": "narrow", "status": "pending", "artifacts": []})
    else:
        receipt["checks"][0]["id"] = "unrelated"
    factory.write_json(path, receipt)
    assert runtime.invoke("--resume", "--browser-evidence", str(path)) != 0
    assert runtime.read("result.json")["overall_status"] != "completed"


def test_model_failure_is_terminal_and_resume_does_not_repeat_completed_stages(runtime):
    runtime.behavior["fail_stage"] = "implement"
    assert runtime.invoke() == 1
    first = runtime.read("run-state.json")
    assert first["status"] == "failed"
    assert first["stage"] == first["resume_from"] == "implement"
    assert first["failure_code"] == "rate_limited"
    assert first["retryable"] is True
    failure = runtime.events()[-1]
    assert failure["status"] == "failed" and failure["rc"] == 1
    failure_log = Path(failure["logs"][0]) if len(failure["logs"]) == 1 else Path(first["artifact_dir"]) / "implement.log"
    original = failure_log.read_bytes()
    runtime.behavior["fail_stage"] = None
    runtime.calls.clear()
    assert runtime.invoke("--resume") == 3
    assert "requirement" not in runtime.calls and "spec" not in runtime.calls
    assert failure_log.read_bytes() == original
    assert "turn_rate_limit_exceeded" in failure_log.read_text()


def test_missing_fresh_output_cannot_reuse_previous_success(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    old = (runtime.task / "spec.md").read_text()
    runtime.behavior["skip_output"] = "spec"
    assert runtime.invoke("--from-stage", "spec", "--only-stage", "spec") == 2
    assert runtime.read("run-state.json")["status"] == "failed"
    assert not (runtime.task / "spec.md").exists()
    archive = Path(runtime.read("run-state.json")["artifact_dir"])
    assert (archive / "superseded/spec.md").read_text() == old


@pytest.mark.parametrize("damage", ["spec", "request", "source"])
def test_resume_rejects_changed_prerequisites_and_code(runtime, damage):
    assert runtime.invoke() == 3
    if damage == "spec":
        (runtime.task / "spec.md").write_text("changed spec")
    elif damage == "request":
        runtime.request.write_text("different requirement")
    else:
        (runtime.root / "source.txt").write_text("unverified source")
    runtime.calls.clear()
    assert runtime.invoke("--resume") == 2
    assert runtime.calls == []
    if damage == "request":
        assert runtime.read("result.json")["overall_status"] == "pending_acceptance"
        assert (runtime.task / "authorization-events.jsonl").exists()
    else:
        assert runtime.read("result.json")["overall_status"] == "failed"


def test_nonzero_quality_gate_can_never_complete(runtime):
    runtime.behavior["gate_rc"] = 5
    assert runtime.invoke() == 5
    assert runtime.read("result.json")["overall_status"] == "failed"
    assert runtime.read("run-state.json")["failure_code"] == "quality_gate_failed"
    assert runtime.read("run-state.json")["command"] == ["bash", "scripts/factory-gate.sh"]
    runtime.behavior["gate_rc"] = 0
    assert runtime.invoke("--resume") == 3


def test_source_change_during_verification_invalidates_results(runtime):
    runtime.behavior["mutate_gate"] = True
    assert runtime.invoke() == 2
    assert runtime.read("result.json")["overall_status"] == "failed"
    assert "源码在验证期间" in runtime.read("run-state.json")["reason"]


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), factory.CommandFailure(["omp", "--print"], 124, "timeout"),
                                     factory.CommandFailure(["omp", "--print"], 127, "command_unavailable")])
def test_interrupt_timeout_and_launch_failure_are_recorded_with_recovery(runtime, monkeypatch, failure):
    assert runtime.invoke("--only-stage", "preflight") == 0
    original = factory.omp

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(factory, "omp", fail)
    expected = 130 if isinstance(failure, KeyboardInterrupt) else failure.rc
    assert runtime.invoke("--resume") == expected
    state = runtime.read("run-state.json")
    assert state["status"] == ("interrupted" if expected == 130 else "failed")
    assert state["resume_from"] == "implement"
    monkeypatch.setattr(factory, "omp", original)
    assert runtime.invoke("--resume") == 3


def test_hard_interruption_running_state_can_resume(runtime):
    assert runtime.invoke("--only-stage", "spec-review") == 0
    state = runtime.read("run-state.json")
    state.update(status="running", resume_from="preflight")
    factory.write_json(runtime.task / "run-state.json", state)
    assert runtime.invoke("--resume") == 3


def test_concurrent_runner_is_rejected_without_overwriting_state(runtime):
    import fcntl

    assert runtime.invoke("--only-stage", "spec") == 0
    original = (runtime.task / "run-state.json").read_bytes()
    with (runtime.task / ".runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert runtime.invoke("--resume") == 2
    assert (runtime.task / "run-state.json").read_bytes() == original


def test_manifest_and_event_paths_survive_fresh_restart(runtime):
    assert runtime.invoke() == 3
    old = runtime.read("run-state.json")
    archived = Path(old["artifact_dir"])
    manifest = json.loads((archived / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        assert factory.digest(archived / name) == expected
    old_events = runtime.events()
    source = (runtime.root / 'source.txt').read_bytes()
    runtime.calls.clear()
    # A fresh replay cannot rebind an already implemented task to new source.
    assert runtime.invoke() == 2
    assert '基线与 Spec 源码绑定不一致' in runtime.read('run-state.json')['reason']
    assert 'implement' not in runtime.calls
    assert (runtime.root / 'source.txt').read_bytes() == source
    for event in old_events:
        if event.get("artifact"):
            assert archived in Path(event["artifact"]).parents
            assert Path(event["artifact"]).is_file()
    for name, expected in manifest["files"].items():
        assert factory.digest(archived / name) == expected


@pytest.mark.parametrize("contents", ["not-json", "[]"])
def test_malformed_recovery_state_is_recorded_not_uncaught(runtime, contents):
    runtime.task.mkdir(parents=True)
    (runtime.task / "run-state.json").write_text(contents)
    assert runtime.invoke("--resume") == 2
    assert (runtime.task / "run-state.json").read_text() == contents


def test_run_timeout_preserves_partial_output_and_original_log_per_attempt(tmp_path):
    log = tmp_path / "timeout.log"
    with pytest.raises(factory.CommandFailure) as exc:
        factory.run([sys.executable, "-u", "-c", "import time; print('before timeout'); time.sleep(10)"],
                    log=log, timeout=1)
    assert exc.value.rc == 124
    assert "before timeout" in log.read_text()


def test_run_missing_executable_writes_failure_log(tmp_path):
    log = tmp_path / "launch.log"
    with pytest.raises(factory.CommandFailure) as exc:
        factory.run([str(tmp_path / "not-installed")], log=log)
    assert exc.value.rc == 127
    assert log.stat().st_size > 0


def test_resume_rejects_damaged_archived_prerequisite(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    receipts = runtime.read("stage-receipts.json")
    (Path(receipts["spec"]["artifact_dir"]) / "spec.md").write_text("damaged archive")
    runtime.calls.clear()
    assert runtime.invoke("--resume") == 2
    assert runtime.calls == []
    assert "spec.md" in runtime.read("run-state.json")["reason"]


def test_untracked_source_change_also_invalidates_resume(runtime):
    assert runtime.invoke() == 3
    (runtime.root / "new-source.ts").write_text("unverified source")
    assert runtime.invoke("--resume") == 2
    assert "源码已改变" in runtime.read("run-state.json")["reason"]


def test_receipt_with_malformed_json_cannot_complete(runtime):
    assert runtime.invoke() == 3
    evidence = runtime.task / "bad-receipt.json"
    evidence.write_text("not-json")
    assert runtime.invoke("--resume", "--browser-evidence", str(evidence)) == 3
    assert runtime.read("result.json")["browser_status"] == "pending"


def test_surviving_process_group_blocks_recovery_without_launching_commands(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    state = runtime.read("run-state.json")
    state.update(status="running", active_command={"pid": os.getpgrp(), "command": ["omp", "--print"]})
    factory.write_json(runtime.task / "run-state.json", state)
    runtime.calls.clear()
    assert runtime.invoke("--resume") == 2
    assert runtime.calls == []
    assert "前次子进程仍在运行" in runtime.read("run-state.json")["reason"]
    # A repeated resume must continue to block, rather than dropping the orphan PID.
    assert runtime.invoke("--resume") == 2
    assert runtime.calls == []


def test_exited_process_group_does_not_block_recovery(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    child.wait(timeout=5)
    state = runtime.read("run-state.json")
    state.update(status="running", active_command={"pid": child.pid, "command": ["omp", "--print"]})
    factory.write_json(runtime.task / "run-state.json", state)
    assert runtime.invoke("--resume") == 3


def test_cli_sigterm_records_interruption_kills_child_and_resumes(runtime):
    """Run the real CLI/process supervisor with a local fake model executable."""
    runtime.request.write_text('修复后端 Service层逻辑')
    scripts = runtime.root / "scripts"
    scripts.mkdir()
    runner = scripts / "factory-local-run.py"
    runner.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.factory'))\nfrom bridge import main\nraise SystemExit(main('run'))\n")
    config_dir = runtime.root / ".factory"
    config_dir.mkdir(exist_ok=True)
    shutil.copy2(runtime.root / "factory-test-config.json", config_dir / "factory-local.json")
    bin_dir = runtime.root / "test-bin"
    bin_dir.mkdir()
    fake_omp = bin_dir / "omp"
    fake_omp.write_text(f"#!{sys.executable}\n" + '''
import os, sys, time, json
from pathlib import Path
prompt = sys.argv[-1]
stage = 'requirement' if '需求分析节点' in prompt else 'spec' if '规格节点' in prompt else 'implement'
task = Path(os.environ['TEST_TASK'])
if stage == 'implement' and os.environ.get('TEST_PAUSE') == '1':
    Path(os.environ['TEST_READY']).write_text(str(os.getpid()))
    print('before SIGTERM', flush=True)
    time.sleep(60)
(task / (stage + '.md')).write_text(stage + ' fresh output')
if stage == 'spec':
    inputs = json.loads((task / 'spec-input.json').read_text())
    (task / 'spec.md').write_text(inputs['task_id'] + ' AC-01 source.txt')
    (task / 'spec-contract.json').write_text(json.dumps({
        **inputs, 'schema': 'factory-local/spec-contract/1', 'goal': 'fixture',
        'scope': {'allowed_files': ['source.txt'], 'non_goals': ['no extra changes']},
        'acceptance': [{'id':'AC-01','criterion':'observable','verification_ids':['V-1']}],
        'verification': [{'id':'V-1','kind':'command','argv':['python3','-V'],
            'required':True,'status':'NOT_RUN','environment':'local'}],
        'external_requirements': []}))
''')
    fake_omp.chmod(0o755)
    assert runtime.invoke('--only-stage', 'spec-review') == 0
    ready = runtime.root.parent / "ready"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "TEST_TASK": str(runtime.task), "TEST_READY": str(ready), "TEST_PAUSE": "1"}
    command = [sys.executable, str(runner), "--task", "runtime", "--request-file", str(runtime.request), "--authorize-through", "verify"]
    process = subprocess.Popen([*command, "--from-stage", "preflight", "--only-stage", "implement"], cwd=runtime.root,
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), process.communicate(timeout=1)
        child_pid = int(ready.read_text())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        assert process.returncode == 130
        first = runtime.read("run-state.json")
        assert first["status"] == "interrupted" and first["resume_from"] == "implement"
        assert "--resume" in first["recovery_command"]
        log = Path(first["artifact_dir"]) / "implement.log"
        assert "before SIGTERM" in log.read_text()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        recovered = subprocess.run([*command, "--resume", "--only-stage", "implement"],
                                   cwd=runtime.root, env={**env, "TEST_PAUSE": "0"},
                                   capture_output=True, text=True, timeout=10)
        assert recovered.returncode == 0, recovered.stderr
        assert runtime.read("run-state.json")["status"] == "stage_completed"
        assert runtime.read("run-state.json")["attempt_id"] != first["attempt_id"]
        assert "before SIGTERM" in log.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_external_browser_evidence_cannot_cross_tasks(runtime):
    assert runtime.invoke() == 3
    evidence = runtime.evidence()
    receipt = json.loads(evidence.read_text())
    receipt["task_id"] = "local-20261021-00000000"
    factory.write_json(evidence, receipt)
    assert runtime.invoke("--resume", "--browser-evidence", str(evidence)) == 3


def test_log_metadata_has_task_stage_attempt(runtime):
    assert runtime.invoke("--only-stage", "spec") == 0
    state = runtime.read("run-state.json")
    token = factory.ACTIVE_ATTEMPT.set(factory.Attempt(runtime.task, runtime.request))
    try:
        attempt = factory.ACTIVE_ATTEMPT.get()
        attempt.stage = "verify"
        path = attempt.directory / "probe.log"
        factory.write_log(path, "output")
        metadata = json.loads(path.read_text().splitlines()[0].removeprefix("# factory-metadata "))
        assert metadata["task_id"] == state["task_id"]
        assert metadata["attempt_id"] == attempt.id
        assert metadata["stage"] == "verify"
    finally:
        factory.ACTIVE_ATTEMPT.reset(token)


def test_spec_ceiling_survives_resume_and_cannot_be_bypassed(runtime):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    state = runtime.read('run-state.json')
    assert state['status'] == 'awaiting_authorization'
    assert state['authorized_end_stage'] == 'spec'
    before = (runtime.task/'run-state.json').read_bytes()
    attempts = set((runtime.task/'attempts').iterdir())
    runtime.calls.clear()
    for args in [('--resume',), ('--from-stage', 'implement'), ('--from-stage', 'spec', '--only-stage', 'verify')]:
        assert runtime.invoke(*args, authorize=False) == 2
        assert runtime.calls == []
        assert (runtime.task/'run-state.json').read_bytes() == before
        assert set((runtime.task/'attempts').iterdir()) == attempts
    assert runtime.read('authorization.json')['authorized_end_stage'] == 'spec'


def test_only_explicit_authorization_can_enter_implementation(runtime):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    task_id = runtime.read('run-state.json')['task_id']
    runtime.calls.clear()
    assert runtime.invoke('--resume', '--authorize-through', 'implement', authorize=False) == 0
    assert runtime.calls == ['spec-review', 'implement']
    assert runtime.read('run-state.json')['task_id'] == task_id
    assert runtime.read('run-state.json')['status'] == 'awaiting_authorization'
    assert runtime.read('authorization.json')['grants'][-1]['through'] == 'implement'


def test_analysis_mode_preserves_frontend_browser_requirement(runtime):
    runtime.request.write_text('只做需求分析和 Spec，先不修改任何业务代码；目标是 Vue 页面按钮修复')
    assert runtime.invoke(authorize=False) == 0
    classification = runtime.read('classification.json')
    assert classification['domain'] == classification['scope'] == 'frontend'
    assert classification['execution_mode'] == 'analysis'
    assert classification['route'] == 'frontend-no-design'
    assert classification['needs_browser_acceptance'] is True
    assert runtime.read('run-state.json')['authorized_end_stage'] == 'spec-review'
    assert runtime.calls == ['requirement', 'clarification', 'spec', 'spec-review']


@pytest.mark.parametrize('request_text,domain', [
    ('只分析 Vue 前端页面，不改代码', 'frontend'),
    ('只分析后端 Service层，不改代码', 'backend'),
    ('只分析前端页面和后端接口，不改代码', 'fullstack'),
])
def test_domain_is_independent_of_analysis_mode(tmp_path, request_text, domain):
    source = tmp_path/'request.md';source.write_text(request_text)
    result = factory.classify(source, tmp_path)
    assert result['domain'] == domain
    assert result['execution_mode'] == 'analysis'


def test_legacy_task_without_authorization_cannot_resume(runtime):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    (runtime.task/'authorization.json').unlink()
    before = (runtime.task/'run-state.json').read_bytes()
    runtime.calls.clear()
    assert runtime.invoke('--resume', authorize=False) == 2
    assert runtime.calls == []
    assert (runtime.task/'run-state.json').read_bytes() == before


@pytest.mark.parametrize('damage', ['missing', 'task_id', 'request_hash', 'requirement_hash', 'goal',
    'files', 'non_goals', 'acceptance', 'duplicate_ac', 'verification_mapping', 'argv',
    'test_passed', 'external', 'escape', 'perimeter', 'document'])
def test_nonempty_but_invalid_spec_is_blocked(runtime, monkeypatch, damage):
    original = factory.omp
    def generate(stage, *args, **kwargs):
        original(stage, *args, **kwargs)
        if stage != 'spec': return
        file = runtime.task/'spec-contract.json'
        data = json.loads(file.read_text())
        if damage == 'missing': file.unlink();return
        if damage == 'task_id': data['task_id'] = 'other'
        elif damage == 'request_hash': data['request_sha256'] = 'other'
        elif damage == 'requirement_hash': data['requirement_sha256'] = 'other'
        elif damage == 'goal': data['goal'] = ''
        elif damage == 'files': data['scope']['allowed_files'] = []
        elif damage == 'non_goals': data['scope']['non_goals'] = []
        elif damage == 'acceptance': data['acceptance'] = []
        elif damage == 'duplicate_ac': data['acceptance'].append(data['acceptance'][0].copy())
        elif damage == 'verification_mapping': data['acceptance'][0]['verification_ids'] = ['absent']
        elif damage == 'argv': data['verification'][0]['argv'] = []
        elif damage == 'test_passed': data['verification'][0]['status'] = 'PASSED'
        elif damage == 'external': data.pop('external_requirements')
        elif damage == 'escape': data['scope']['allowed_files'] = ['../outside.ts']
        elif damage == 'perimeter': data['scope']['allowed_files'] = ['.factory/run.py']
        elif damage == 'document': (runtime.task/'spec.md').write_text('nonempty but no bindings')
        factory.write_json(file, data)
    monkeypatch.setattr(factory, 'omp', generate)
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 2
    early_rejections = {
        'missing': '契约格式修复次数已达上限',
        'task_id': '模型提供了冲突的运行器绑定：task_id',
        'request_hash': '模型提供了冲突的运行器绑定：request_sha256',
        'requirement_hash': '模型提供了冲突的运行器绑定：requirement_sha256',
        'test_passed': 'Spec 不能自报已执行结果',
        'external': '契约格式修复次数已达上限',
    }
    if damage in early_rejections:
        state = runtime.read('run-state.json')
        assert state['status'] == 'failed' and state['stage'] == 'spec'
        assert early_rejections[damage] in state['reason']
    else:
        assert runtime.read('spec-validation.json')['status'] == 'blocked'
    assert 'spec' not in runtime.read('stage-receipts.json')
    assert 'implement' not in runtime.calls


def test_valid_spec_receipt_is_bound_and_archived(runtime):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    result = runtime.read('spec-validation.json')
    assert result['status'] == 'passed'
    assert result['spec_sha256'] == factory.digest(runtime.task/'spec.md')
    outputs = runtime.read('stage-receipts.json')['spec']['outputs']
    assert {'spec-input.json','spec-contract.json','spec-validation.json'}.issubset(outputs)


def test_classifier_only_does_not_exceed_early_ceiling(runtime):
    assert runtime.invoke('--only-stage', 'classify', authorize=False) == 0
    assert not (runtime.task/'skill-selection.json').exists()
    assert runtime.read('run-state.json')['stage'] == 'classify'
    assert runtime.calls == []


@pytest.mark.parametrize("text", ["Vue 页面，先不要修改文件", "先不要实现，分析 Vue 页面问题", "只分析后端接口"])
def test_negative_execution_intent_keeps_analysis_ceiling(runtime, text):
    runtime.request.write_text(text)
    assert runtime.invoke(authorize=False) == 0
    assert runtime.read("run-state.json")["authorized_end_stage"] == "spec-review"
    assert runtime.calls == ["requirement", "clarification", "spec", "spec-review"]


def test_malformed_spec_input_still_produces_blocked_receipt(runtime):
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 0
    (runtime.task / "spec-input.json").write_text("[]")
    with pytest.raises(ValueError, match="spec-input"):
        factory.validate_spec(runtime.task, runtime.request, {})
    assert runtime.read("spec-validation.json")["status"] == "blocked"


def test_all_optional_verifications_cannot_satisfy_acceptance(runtime):
    assert runtime.invoke("--only-stage", "spec", authorize=False) == 0
    file = runtime.task / "spec-contract.json"
    data = runtime.read("spec-contract.json")
    for item in data["verification"]:
        item["required"] = False
    factory.write_json(file, data)
    with pytest.raises(ValueError, match="必需验证"):
        factory.validate_spec(runtime.task, runtime.request, {})


@pytest.mark.parametrize('stage', ['requirement', 'spec', 'implement'])
@pytest.mark.parametrize('model_fails', [False, True])
def test_model_cannot_widen_authorization_even_when_it_fails(runtime, monkeypatch, stage, model_fails):
    original = factory.omp
    def generate(current, *args, **kwargs):
        result = original(current, *args, **kwargs)
        if current == stage:
            data = runtime.read('authorization.json')
            data['authorized_end_stage'] = 'verify'
            factory.write_json(runtime.task / 'authorization.json', data)
            if model_fails:
                raise RuntimeError('model failure after unauthorized write')
        return result
    monkeypatch.setattr(factory, 'omp', generate)
    ceiling = 'implement' if stage == 'implement' else 'spec'
    assert runtime.invoke('--only-stage', ceiling, authorize=False) == 2
    assert runtime.read('authorization.json')['authorized_end_stage'] == ceiling
    assert runtime.read('authorization.json')['grants'] == []
    assert runtime.read('stage-integrity.json')['status'] == 'blocked'
    assert stage not in runtime.read('stage-receipts.json')
    runtime.calls.clear()
    # Even explicit stage selection cannot extend the restored ceiling.
    assert runtime.invoke('--from-stage', 'verify', authorize=False) == 2
    assert runtime.calls == []


def test_spec_cannot_rewrite_source_and_refresh_its_own_baseline(runtime, monkeypatch):
    original = factory.omp
    retained = {}
    def generate(stage, *args, **kwargs):
        if stage == 'spec':
            retained.update(runtime.read('spec-input.json'))
        original(stage, *args, **kwargs)
        if stage == 'spec':
            (runtime.root / 'source.txt').write_text('unauthorized business change')
            inputs = runtime.read('spec-input.json')
            inputs['source_sha256'] = factory.source_digest()
            factory.write_json(runtime.task / 'spec-input.json', inputs)
    monkeypatch.setattr(factory, 'omp', generate)
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 2
    assert runtime.read('spec-input.json') == retained
    assert runtime.read('spec-validation.json')['status'] == 'blocked'
    assert 'spec' not in runtime.read('stage-receipts.json')
    assert 'implement' not in runtime.calls
    # Preserve evidence of an unexpected source edit for the operator to inspect.
    assert (runtime.root / 'source.txt').read_text() == 'unauthorized business change'


@pytest.mark.parametrize('name', ['./.factory/run.py', './MISSION.md', '.', './', 'nested/'])
def test_allowed_files_normalizes_perimeter_and_rejects_directories(runtime, name):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    if name == 'nested/': (runtime.root / 'nested').mkdir()
    data = runtime.read('spec-contract.json')
    data['scope']['allowed_files'] = [name]
    factory.write_json(runtime.task / 'spec-contract.json', data)
    with (runtime.task / 'spec.md').open('a') as stream:
        stream.write(' ' + name)
    with pytest.raises(ValueError, match='周界|目录'):
        factory.validate_spec(runtime.task, runtime.request, {})


def test_contract_source_hash_must_match_runner_input(runtime):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    data = runtime.read('spec-contract.json')
    data['source_sha256'] = 'forged'
    factory.write_json(runtime.task / 'spec-contract.json', data)
    with pytest.raises(ValueError, match='基线'):
        factory.validate_spec(runtime.task, runtime.request, {})


def test_validation_never_executes_model_proposed_command(runtime):
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 0
    sentinel = runtime.root / 'must-not-exist'
    data = runtime.read('spec-contract.json')
    data['verification'][0]['argv'] = [sys.executable, '-c',
        f'from pathlib import Path; Path({str(sentinel)!r}).write_text("executed")']
    factory.write_json(runtime.task / 'spec-contract.json', data)
    assert factory.validate_spec(runtime.task, runtime.request, {})['status'] == 'passed'
    assert not sentinel.exists()


@pytest.mark.parametrize('text,remote', [
    ('不要启动实现、全量质量门或远端交付。', False),
    ('本次禁止修改项目所有文件、执行检查、调用浏览器、创建任务、推送代码或远端交付。', False),
    ('本地分析，不提交代码、不推送、不创建MR、不触发流水线。', False),
    ('Do not edit files, run tests, push or create a PR.', False),
    ('本次禁止修改文件，发送接口请求，推送代码或远端交付。', False),
    ('本次禁止发送接口请求、推送代码或远端交付。', False),
    ('不要在完成后推送代码', False),
    ('禁止在测试通过后推送代码', False),
    ('不要随后推送代码', False),
    ('本次禁止修改文件，执行检查，调用浏览器，推送代码或远端交付。', False),
    ('修复未处理的 Vue 页面错误并推送代码。', True),
    ('把所有尚未修复的页面按钮样式统一调整为设计规范后推送代码并创建MR。', True),
    ('Do not change any source files. Please push and open a PR.', True),
    ('不要改动业务源码并在全部测试检查通过之后推送代码', True),
    ('Do not commit, push or open a PR.', False),
    ("Don't push or create a PR.", False),
    ('无需提交代码，推送或创建MR。', False),
    ('# 非目标\n- 推送代码\n- 创建MR\n# 目标\n修复 Vue 按钮', False),
    ('示例：\n```bash\ngit push\n```\n仅分析 Vue 页面', False),
    ('## 非目标\n### 本轮不做\n推送代码\n### 部署\n创建MR\n## 目标\n修复页面', False),
    ('请执行以下交付命令：\n```bash\ngit push\n```', True),
    ('不要执行以下交付命令：\n```bash\ngit push\n```', False),
    ('仅推送代码', True),
    ('只创建 MR', True),
    ('不要推送，但创建MR', True),
    ('不要创建MR；推送代码', True),
    ('不要修改业务代码，修复后推送代码', True),
    ('不要修改业务代码，请推送已有提交', True),
    ('Without changing code, push the current branch.', True),
    ('提交代码，然后推送并创建MR', True),
    ('Please push and open a PR.', True),
])
def test_remote_routing_respects_clause_scope(tmp_path, text, remote):
    request = tmp_path / 'request.md'; request.write_text(text)
    result = factory.classify(request, tmp_path)
    assert result['needs_remote_delivery'] is remote
    assert result['execution_route'] == ('local-then-remote-handoff' if remote else 'local')
    if not remote: assert result['remote_intents'] == []


def test_mixed_remote_actions_keep_individual_decisions(tmp_path):
    request = tmp_path / 'request.md'; request.write_text('不要推送，但创建MR')
    result = factory.classify(request, tmp_path)
    assert result['remote_intents'] == ['创建MR']
    assert [(d['text'], d['decision']) for d in result['evidence']['remote_intent_decisions']] == [
        ('推送', 'negated'), ('创建MR', 'affirmed')]


@pytest.fixture
def skill_catalog(runtime, monkeypatch):
    rules = runtime.root.parent / 'catalog'
    skills = rules / 'skills'; skills.mkdir(parents=True)
    policy = {'schema': 'factory-local/skill-routing/1', 'skills': {
        'factory-acceptance': {'always': True},
        'api-guard': {'triggers': ['审查API', '接口设计']},
        'work-report': {'triggers': ['生成日报']},
        'figma-frontend-implementation': {'domains': ['frontend', 'fullstack'], 'requires_design': True},
        'figma-visual-guard': {'domains': ['frontend', 'fullstack'], 'requires_design': True},
    }}
    for name in policy['skills']:
        folder = skills / name; folder.mkdir()
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: >\n  Factory 前端 接口 任务 需求 测试 spec\n---\n')
    factory.write_json(skills / 'factory-routing.json', policy)
    monkeypatch.setattr(factory, 'AWESOME_RULES_ROOT', rules)
    return skills


@pytest.mark.parametrize('text,names', [
    ('Vue 页面按钮修复；接口、测试、任务、spec、Factory 只是背景词。', {'factory-acceptance'}),
    ('只分析 Vue 页面，不需要接口设计，不生成日报。', {'factory-acceptance'}),
    ('# 非目标\n接口设计与生成日报\n# 目标\n修复Vue按钮', {'factory-acceptance'}),
    ('审查 API，检查接口设计', {'factory-acceptance', 'api-guard'}),
    ('请生成日报', {'factory-acceptance', 'work-report'}),
    ('使用 work-report 技能', {'factory-acceptance', 'work-report'}),
    ('不要使用 work-report 技能', {'factory-acceptance'}),
    ('Do not change any source files. Please generate the work-report.', {'factory-acceptance', 'work-report'}),
    ('只修复按钮，设计稿尚未提供，请使用 figma-visual-guard 技能', {'factory-acceptance', 'figma-visual-guard'}),
    ('修复 Vue 页面，没有 Figma 设计稿', {'factory-acceptance'}),
    ('按照 Figma 设计稿还原 Vue 页面', {'factory-acceptance', 'figma-frontend-implementation', 'figma-visual-guard'}),
])
def test_skill_routing_uses_capabilities_not_description_tokens(runtime, skill_catalog, text, names):
    runtime.request.write_text(text)
    assert runtime.invoke('--only-stage', 'skill-discovery', authorize=False) == 0
    selection = runtime.read('skill-selection.json')
    assert selection['schema'] == 'factory-local/skill-selection/2'
    assert {item['name'] for item in selection['selected']} == names
    assert selection['rules']['description_token_overlap'] is False
    assert selection['policy']['sha256'] == factory.digest(skill_catalog / 'factory-routing.json')
    archived = runtime.read('stage-receipts.json')['skill-discovery']['outputs']
    assert 'skill-routing.snapshot.json' in archived
    assert not runtime.calls


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'unknown-skill', 'unknown-key', 'bad-flag', 'bad-phrases', 'empty-policy', 'empty-rule', 'missing-rule'])
def test_invalid_skill_policy_blocks_before_model(runtime, skill_catalog, damage):
    path = skill_catalog / 'factory-routing.json'
    data = json.loads(path.read_text())
    if damage == 'missing': path.unlink()
    elif damage == 'malformed': path.write_text('[]')
    else:
        if damage == 'empty-policy': data['skills'] = {}
        if damage == 'empty-rule': data['skills']['api-guard'] = {}
        if damage == 'missing-rule': data['skills'].pop('api-guard')
        if damage == 'unknown-skill': data['skills']['missing-skill'] = {'always': True}
        if damage == 'unknown-key': data['skills']['api-guard']['random'] = True
        if damage == 'bad-flag': data['skills']['api-guard']['always'] = 'yes'
        if damage == 'bad-phrases': data['skills']['api-guard']['triggers'] = ['']
        factory.write_json(path, data)
    assert runtime.invoke('--only-stage', 'spec', authorize=False) == 2
    assert runtime.calls == []
    assert runtime.read('skill-selection.json')['status'] == 'blocked'
    assert 'skill-discovery' not in runtime.read('stage-receipts.json')


def test_changed_skill_policy_requires_reclassification(runtime, skill_catalog):
    assert runtime.invoke('--only-stage', 'spec') == 0
    path = skill_catalog / 'factory-routing.json'
    data = json.loads(path.read_text()); data['skills']['api-guard']['triggers'].append('检查API')
    factory.write_json(path, data)
    runtime.calls.clear()
    assert runtime.invoke('--resume') == 2
    assert runtime.calls == []
    assert '触发配置已变化' in runtime.read('run-state.json')['reason']


def test_explicit_only_rule_requires_direct_skill_reference(runtime, skill_catalog):
    path = skill_catalog / 'factory-routing.json'
    data = json.loads(path.read_text()); data['skills']['api-guard'] = {'explicit_only': True}
    factory.write_json(path, data)
    runtime.request.write_text('审查API')
    assert runtime.invoke('--only-stage', 'skill-discovery', authorize=False) == 0
    assert {s['name'] for s in runtime.read('skill-selection.json')['selected']} == {'factory-acceptance'}
    runtime.request.write_text('使用 api-guard')
    # New request must have its own task; exercise discovery without inheriting authorization.
    output = runtime.root / 'artifacts/probe'; output.mkdir()
    classification = factory.classify(runtime.request, output)
    status, _ = factory.discover_skills(runtime.request, classification, output)
    assert status == 'passed'
    assert {s['name'] for s in json.loads((output/'skill-selection.json').read_text())['selected']} == {'factory-acceptance', 'api-guard'}
