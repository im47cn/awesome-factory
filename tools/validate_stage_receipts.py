#!/usr/bin/env python3
"""Validate the ordered, fail-closed receipts emitted by a local Factory runner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STAGES = ("classify", "skill-discovery", "design-analysis", "requirement", "spec", "implement", "verify")
STATUSES = {"passed", "failed", "blocked"}


class ReceiptError(ValueError):
    pass


def load(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"无法读取 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptError(f"顶层必须是对象: {path}")
    return value


def project_file(root: Path, name: str, label: str):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ReceiptError(f"{label} 必须是项目内相对路径: {name!r}")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ReceiptError(f"{label} 越界: {name}")
    if not path.is_file():
        raise ReceiptError(f"{label} 缺失: {name}")
    return path


def validate(manifest_path: Path, root: Path):
    manifest = load(manifest_path)
    if manifest.get("schema") != "factory-acceptance-manifest/1":
        raise ReceiptError("manifest schema 不识别")
    task_id = manifest.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ReceiptError("manifest 缺 task_id")
    project_file(root, manifest.get("request_file"), "request_file")
    expected = manifest.get("expected_stages")
    if expected != list(STAGES):
        raise ReceiptError("expected_stages 必须按标准七阶段完整排列")
    receipt_names = manifest.get("receipts")
    if not isinstance(receipt_names, list) or len(receipt_names) != len(STAGES):
        raise ReceiptError("receipts 必须逐阶段提供七份收据")

    for index, (stage, name) in enumerate(zip(STAGES, receipt_names)):
        receipt_path = project_file(root, name, f"{stage} receipt")
        receipt = load(receipt_path)
        required = ("schema", "task_id", "stage", "status", "started_at", "finished_at", "exit_code", "input_files", "output_files", "evidence")
        missing = [key for key in required if key not in receipt]
        if missing:
            raise ReceiptError(f"{stage}: 缺少字段 {', '.join(missing)}")
        if receipt["schema"] != "factory-stage-receipt/1" or receipt["task_id"] != task_id:
            raise ReceiptError(f"{stage}: schema 或 task_id 不一致")
        if receipt["stage"] != stage:
            raise ReceiptError(f"收据顺序/阶段不一致: 期望 {stage}, 实际 {receipt['stage']}")
        if receipt["status"] not in STATUSES:
            raise ReceiptError(f"{stage}: status 不合法")
        if not isinstance(receipt["exit_code"], int) or receipt["exit_code"] < 0:
            raise ReceiptError(f"{stage}: exit_code 不合法")
        if not all(isinstance(receipt[key], list) for key in ("input_files", "output_files", "evidence")):
            raise ReceiptError(f"{stage}: 文件和 evidence 必须是数组")
        for key in ("input_files", "output_files", "evidence"):
            for item in receipt[key]:
                project_file(root, item, f"{stage}.{key}")
        if receipt["status"] == "passed":
            if receipt["exit_code"] != 0:
                raise ReceiptError(f"{stage}: passed 必须 exit_code=0")
        else:
            if receipt["exit_code"] == 0:
                raise ReceiptError(f"{stage}: {status_label(receipt['status'])} 必须为非 0 exit_code")
            for key in ("failure_category", "failure_message", "recovery_command"):
                if not isinstance(receipt.get(key), str) or not receipt[key].strip():
                    raise ReceiptError(f"{stage}: {status_label(receipt['status'])} 缺 {key}")
            if index != len(STAGES) - 1 and any(
                    load(project_file(root, other, "后续收据"))["status"] == "passed"
                    for other in receipt_names[index + 1:]):
                raise ReceiptError(f"{stage}: 失败/阻断后不允许后续阶段通过")
    statuses = [load(project_file(root, name, "receipt"))["status"] for name in receipt_names]
    return {"status": "passed" if all(item == "passed" for item in statuses) else "blocked",
            "task_id": task_id, "stages": list(STAGES)}


def status_label(status):
    return "失败" if status == "failed" else "阻断"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = validate(args.manifest, args.root.resolve())
    except ReceiptError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
