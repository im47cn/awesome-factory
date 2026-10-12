#!/usr/bin/env python3
"""check_evals_baseline.py — Evals 基线单调性校验（SDLC 手册 v2.1 §04）。

独立双参数比较器（old.json vs new.json），机器兜底基线纪律第 2 条
（基线只升不降），防「人为调基线」后门。基线重算规则（min 收敛）
不在本门范围——只校验单调性。

规则（issue #2）：
  R1 单调不降：new.pass_rate < old.pass_rate → exit 1（报差异值）
  R2 留痕放行：--allow-downgrade <TL批准引用> 时降级放行（exit 0），
     引用写入输出行留痕
  R3 fail-closed：文件缺失 / 非 JSON / 缺必填字段 pass_rate → exit 2
     （门自身错误，宁关闭不放过）

退出码：0 = 通过（或留痕放行）；1 = 基线下调违例；2 = 门自身错误。
用法: check_evals_baseline.py <old.json> <new.json> [--allow-downgrade <引用>]
      check_evals_baseline.py --self-check
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _rate(data: object, src: str) -> float:
    """取出 pass_rate（缺失/非数值/NaN/溢出/超 [0,1] → fail-closed exit 2）。"""
    rate = data.get("pass_rate") if isinstance(data, dict) else None
    # bool 是 int 子类，一并排除：true/false 不是合法通过率
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        print(f"check_evals_baseline: {src} 缺必填字段 pass_rate（或非数值）",
              file=sys.stderr)
        sys.exit(2)
    try:
        value = float(rate)  # 超大 int（如 10**400）转 float 溢出
    except OverflowError:
        print(f"check_evals_baseline: {src} pass_rate 溢出（无法转 float）",
              file=sys.stderr)
        sys.exit(2)
    # json 非严格模式可解析出 NaN/Infinity（1e999），一律拒之门外
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        print(f"check_evals_baseline: {src} pass_rate 非法（须为 [0,1] 内有限数）: "
              f"{rate!r}", file=sys.stderr)
        sys.exit(2)
    return value


def _load(path: str) -> float:
    """加载单个基线 JSON → pass_rate（读失败/解析失败/编码错误 → exit 2）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"check_evals_baseline: {path} 不可读/非 JSON: {exc}",
              file=sys.stderr)
        sys.exit(2)
    return _rate(data, path)


def compare(old: float, new: float, allow_ref: str | None) -> int:
    """R1/R2：降级违例 exit 1；留痕放行或本就通过 exit 0。"""
    if new >= old:
        print(f"evals-baseline: pass_rate {old} -> {new}（单调不降，通过）")
        return 0
    diff = round(old - new, 6)
    if allow_ref is None:
        print(f"evals-baseline 违例: pass_rate {old} -> {new}"
              f"（降 {diff}）——下调须 PR + TL approve 留痕"
              "（--allow-downgrade <引用>）", file=sys.stderr)
        return 1
    print(f"evals-baseline: pass_rate {old} -> {new}（降 {diff}）"
          f"——TL批准放行，引用: {allow_ref}")
    return 0


def _self_check() -> int:
    """inline fixture 自检（升/降/缺字段 + NaN/超界）——ponytail 式最小自检。"""
    assert compare(0.80, 0.90, None) == 0, "升基线应 exit 0"
    assert compare(0.90, 0.80, None) == 1, "降基线应 exit 1"
    for bad in ({"total": 10}, {"pass_rate": float("nan")},
                {"pass_rate": 1.5}, {"pass_rate": float("inf")}):
        try:
            _rate(bad, "fixture.json")
        except SystemExit as e:
            assert e.code == 2, f"非法 pass_rate {bad} 应 exit 2，实际 {e.code}"
        else:
            raise AssertionError(f"非法 pass_rate 未 fail-closed: {bad}")
    print("self-check: 升/降/缺字段/NaN/超界全过")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Evals 基线单调性校验（issue #2）")
    ap.add_argument("old", nargs="?", help="旧基线 JSON（如 evals/baseline.json）")
    ap.add_argument("new", nargs="?", help="新基线 JSON")
    ap.add_argument("--allow-downgrade", metavar="REF", default=None,
                    help="TL批准引用：降级放行并把引用写入输出行留痕")
    ap.add_argument("--self-check", action="store_true",
                    help="inline 三例自检（升/降/缺字段），不带此参按比较器走")
    # argv 含 argv[0]（脚本路径），须切片后交 argparse
    args = ap.parse_args(argv[1:])
    if args.self_check:
        return _self_check()
    # argparse 的 ap.error 自身 exit 2，恰合 fail-closed 语义
    if args.old is None or args.new is None:
        ap.error("须提供 <old.json> <new.json>")
    # 空白放行引用 = 审计留痕失效，按门自身错误拒（Sourcery #3）
    if args.allow_downgrade is not None and not args.allow_downgrade.strip():
        ap.error("--allow-downgrade 引用不能为空或纯空白")
    return compare(_load(args.old), _load(args.new), args.allow_downgrade)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
