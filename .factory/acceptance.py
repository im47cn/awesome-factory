#!/usr/bin/env python3
"""批准契约 → 冻结 → 独立执行 → 校验证据；CLI 用法见 acceptance-README.md。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


class GateError(ValueError):
    """无效配置、漂移或无效证据。"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError("JSON 顶层须为对象")
    return value


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".acceptance-", delete=False) as f:
        tmp = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def relative_file(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise GateError("文件须为项目内相对路径")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise GateError(f"文件缺失或越界: {name}")
    return path


def specification(contract):
    return {k: v for k, v in contract.items() if k != "approval"}


def validate(contract, approved=True):
    if contract.get("schema") != "acceptance-contract/1":
        raise GateError("契约 schema 不识别")
    for key in ("requirement_id", "goal"):
        if not isinstance(contract.get(key), str) or not contract[key].strip():
            raise GateError(f"缺 {key}")
    if not isinstance(contract.get("non_goals"), list) or not all(
            isinstance(x, str) and x.strip() for x in contract["non_goals"]):
        raise GateError("non_goals 须为字符串列表（允许空）")
    cases = contract.get("acceptance")
    if not isinstance(cases, list) or not cases:
        raise GateError("验收条目不得为空")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise GateError("验收条目须为对象")
        cid = case.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", cid) or cid in ids:
            raise GateError("验收 ID 非法或重复")
        ids.add(cid)
        if not isinstance(case.get("criterion"), str) or not case["criterion"].strip():
            raise GateError(f"{cid}: 缺 criterion")
        argv = case.get("argv")
        if not isinstance(argv, list) or not argv or not all(
                isinstance(x, str) and x and "\x00" not in x for x in argv):
            raise GateError(f"{cid}: argv 须为非空字符串数组")
        files = case.get("test_files")
        if not isinstance(files, list) or not files or len(set(files)) != len(files) or not all(
                isinstance(x, str) and x and not Path(x).is_absolute()
                and ".." not in Path(x).parts for x in files):
            raise GateError(f"{cid}: test_files 非法/重复/为空")
        timeout = case.get("timeout_seconds", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise GateError(f"{cid}: timeout_seconds 须为 1..3600 整数")
    if approved:
        for case in cases:
            hashes = case.get("test_hashes")
            if not isinstance(hashes, dict) or set(hashes) != set(case["test_files"]) or not all(
                    isinstance(x, str) and re.fullmatch(r"[0-9a-f]{64}", x) for x in hashes.values()):
                raise GateError("批准契约缺测试内容摘要；须先准备独立验收测试")
        approval = contract.get("approval")
        if not isinstance(approval, dict) or not isinstance(approval.get("approved_by"), str) \
                or not approval["approved_by"].strip() \
                or approval.get("approved_revision") != digest(specification(contract)):
            raise GateError("契约未批准或批准版本已漂移")
    return contract


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git", "-C", str(root), *args], env=env,
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def identity(root):
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise GateError("验收须针对已提交且无未跟踪文件的工作树")
    return {"commit": git(root, "rev-parse", "HEAD"),
            "tree": git(root, "rev-parse", "HEAD^{tree}")}


def binding(source, frozen):
    current = validate(read(source))
    snapshot = validate(read(frozen))
    if digest(current) != digest(snapshot):
        raise GateError("冻结后契约或审批发生变化，须重新批准并冻结")
    return snapshot


def execute(argv, root, timeout):
    with tempfile.TemporaryFile() as output:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        p = subprocess.Popen(argv, cwd=root, stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True, env=env)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            p.wait()
            raise GateError("验收超时：不是正常断言失败")
        # 同一进程组遗留的后台子进程不能继续污染下一项验收。
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        output.seek(0)
        return rc, output.read()


def collect(source, frozen, root, receipt, phase="fixed"):
    run_id = uuid.uuid4().hex
    receipt_path = Path(receipt)
    if receipt_path.exists():
        previous = receipt_path.with_name(receipt_path.stem + "-previous-" + run_id + ".json")
        previous.write_bytes(receipt_path.read_bytes())
    write(receipt, {"schema": "acceptance-evidence-invalid/1", "reason": "新运行未完成", "run_id": run_id})
    contract = binding(source, frozen)
    before = identity(root)
    results = []
    started = datetime.now(timezone.utc).isoformat()
    for case in contract["acceptance"]:
        files = {name: file_hash(relative_file(root, name)) for name in case["test_files"]}
        if files != case["test_hashes"]:
            raise GateError("验收测试与批准字节不符，须人工重审重批")
        rc, output = execute(case["argv"], root, case.get("timeout_seconds", 300))
        if identity(root) != before or files != {
                name: file_hash(relative_file(root, name)) for name in files}:
            raise GateError("验收命令修改了代码或测试，证据无效")
        log = Path(receipt).parent / (Path(receipt).stem + "-" + run_id + "-" + case["id"] + ".log")
        log.write_bytes(output)
        results.append({"id": case["id"], "argv": case["argv"], "test_files": files,
                        "exit_code": rc, "log": log.name, "log_sha256": file_hash(log)})
    binding(source, frozen)
    receipt_data = {"schema": "acceptance-evidence/1", "requirement_id": contract["requirement_id"],
                    "contract_hash": digest(contract), **before, "phase": phase,
                    "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                    "results": results}
    write(receipt, receipt_data)
    if phase == "fixed" and any(r["exit_code"] != 0 for r in results):
        raise GateError("验收项失败；证据已保留")
    if phase == "baseline" and (not any(r["exit_code"] == 1 for r in results)
                                or any(r["exit_code"] not in (0, 1) for r in results)):
        raise GateError("红端须至少一次正常断言失败（exit 1）；其他退出码无效")
    return receipt_data


def verify(source, frozen, root, receipt):
    contract = binding(source, frozen)
    evidence = read(receipt)
    if evidence.get("schema") != "acceptance-evidence/1" or evidence.get("phase") != "fixed" \
            or evidence.get("contract_hash") != digest(contract) \
            or evidence.get("requirement_id") != contract["requirement_id"]:
        raise GateError("证据 schema/契约绑定/阶段无效")
    if any(evidence.get(k) != v for k, v in identity(root).items()):
        raise GateError("提交/树已变化，证据过期")
    observations = []
    results = evidence.get("results")
    if not isinstance(results, list) or len(results) != len(contract["acceptance"]):
        raise GateError("证据条目缺失")
    for case, result in zip(contract["acceptance"], results):
        if not isinstance(result, dict) or result.get("id") != case["id"] \
                or result.get("argv") != case["argv"] \
                or type(result.get("exit_code")) is not int or result["exit_code"] != 0:
            raise GateError("验收映射不符或未通过")
        if result.get("test_files") != {
                name: file_hash(relative_file(root, name)) for name in case["test_files"]}:
            raise GateError("验收测试已变化")
        log = relative_file(Path(receipt).parent, result.get("log"))
        if file_hash(log) != result.get("log_sha256"):
            raise GateError("验收日志已变化")
        if log.stat().st_size > 262144:
            raise GateError("单项验收输出超过 256KiB，须减少输出后重跑；不截断后背书")
        observations.append({"id": case["id"], "output": log.read_text(encoding="utf-8", errors="replace")})
    return {"contract": specification(contract), "evidence": evidence, "observations": observations}


def source_path(repo, issue):
    cfg = read(repo / ".factory/factory-local.json")
    enabled = cfg.get("acceptance_required", False)
    if type(enabled) is not bool:
        raise GateError("acceptance_required 须为 boolean")
    if not enabled:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", issue):
        raise GateError("issue ID 非法")
    name = cfg.get("acceptance_contract_dir", ".factory/acceptance-contracts")
    if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
        raise GateError("acceptance_contract_dir 须为 .factory 内相对路径")
    directory = (repo / name).resolve()
    if not directory.is_relative_to((repo / ".factory").resolve()):
        raise GateError("验收契约必须位于受保护 .factory 面")
    path = relative_file(repo, str(Path(name) / ("issue-" + issue + ".json")))
    if not path.is_relative_to(directory):
        raise GateError("契约文件通过 symlink 离开受保护契约目录")
    validate(read(path))
    return str(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("source")
    q.add_argument("--repo", type=Path, required=True)
    q.add_argument("--issue", required=True)
    q = sub.add_parser("approve")
    q.add_argument("contract", type=Path)
    q.add_argument("--by", required=True)
    q.add_argument("--repo", type=Path, required=True)
    q = sub.add_parser("freeze")
    q.add_argument("source", type=Path)
    q.add_argument("frozen", type=Path)
    for name in ("run", "verify"):
        q = sub.add_parser(name)
        for field in ("source", "frozen", "repo", "receipt"):
            q.add_argument("--" + field, type=Path, required=True)
        if name == "run":
            q.add_argument("--phase", choices=("baseline", "fixed"), default="fixed")
    args = p.parse_args()
    try:
        if args.cmd == "source":
            print(source_path(args.repo, args.issue))
        elif args.cmd == "approve":
            if not args.by.strip():
                raise GateError("批准人不能为空")
            contract = validate(read(args.contract), approved=False)
            for case in contract["acceptance"]:
                case["test_hashes"] = {name: file_hash(relative_file(args.repo, name))
                                       for name in case["test_files"]}
            contract["approval"] = {"approved_by": args.by,
                                    "approved_revision": digest(specification(contract))}
            write(args.contract, contract)
        elif args.cmd == "freeze":
            write(args.frozen, validate(read(args.source)))
        elif args.cmd == "run":
            collect(args.source, args.frozen, args.repo, args.receipt, args.phase)
        else:
            print(json.dumps(verify(args.source, args.frozen, args.repo, args.receipt), ensure_ascii=False))
        return 0
    except (GateError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as e:
        print(f"acceptance: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
