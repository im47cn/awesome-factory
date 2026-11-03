import json
from pathlib import Path

import pytest

from validate_stage_receipts import STAGES, ReceiptError, validate


def make_case(tmp_path):
    (tmp_path / "request.md").write_text("需求", encoding="utf-8")
    names = []
    for stage in STAGES:
        name = f"receipts/{stage}.json"
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        data = {
            "schema": "factory-stage-receipt/1", "task_id": "TASK-1", "stage": stage,
            "status": "passed", "started_at": "2026-10-21T00:00:00Z", "finished_at": "2026-10-21T00:01:00Z",
            "exit_code": 0, "input_files": ["request.md"], "output_files": ["request.md"], "evidence": ["request.md"]
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        names.append(name)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "factory-acceptance-manifest/1", "task_id": "TASK-1",
                                    "request_file": "request.md", "expected_stages": list(STAGES), "receipts": names}), encoding="utf-8")
    return manifest


def test_valid_manifest(tmp_path):
    assert validate(make_case(tmp_path), tmp_path)["status"] == "passed"


def test_task_id_drift_is_blocked(tmp_path):
    manifest = make_case(tmp_path)
    path = tmp_path / "receipts/spec.json"
    data = json.loads(path.read_text())
    data["task_id"] = "OTHER"
    path.write_text(json.dumps(data))
    with pytest.raises(ReceiptError, match="task_id"):
        validate(manifest, tmp_path)


def test_failed_stage_requires_recovery_and_blocks_tail(tmp_path):
    manifest = make_case(tmp_path)
    path = tmp_path / "receipts/implement.json"
    data = json.loads(path.read_text())
    data.update(status="blocked", exit_code=1, failure_category="missing-context", failure_message="缺设计证据", recovery_command="补充设计证据后 resume")
    path.write_text(json.dumps(data))
    with pytest.raises(ReceiptError, match="后续阶段通过"):
        validate(manifest, tmp_path)
    verify = tmp_path / "receipts/verify.json"
    tail = json.loads(verify.read_text())
    tail["status"] = "blocked"
    tail["exit_code"] = 1
    tail.update(failure_category="upstream-blocked", failure_message="前置阶段阻断", recovery_command="resume")
    verify.write_text(json.dumps(tail))
    assert validate(manifest, tmp_path)["status"] == "blocked"
