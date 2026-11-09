#!/usr/bin/env python3
"""spec 条款 <-> 测试标签 反向核对矩阵校验。

用法:
  spec_check.py --spec spec.md --tests tests/                        # 目录递归(默认 .py)
  spec_check.py --spec spec.md --tests tests/ --ext .java --ext .go  # 多语言测试文件
  spec_check.py --spec spec.md --tests tests/ --ignore-orphans       # 孤儿标签降级

从 spec.md 提取条款 ID（spec:<代号>-<序号>，去重）；
从测试文件提取标签（宽松匹配 `spec:<ID>` token，兼容 `// spec:` 与 `# spec:` 注释）；
输出「条款 <- 测试文件::用例名」矩阵，从条款出发找测试（反向核对）。

退出码:
  0 = 全覆盖
  1 = 有缺口（spec 条款无测试）或孤儿标签（测试有、spec 无，默认失败）
  2 = 用法错误
"""

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

# 示例：代号可含连字符，序号为数字结尾（如 spec:claim-status-<N>）。
# 注释刻意不写成可匹配字面（spec:<代号>-<数字>），否则检查器扫描本文件自身时
# 会把示例误判为测试标签（孤儿）。保留完整形式作 key 的语义见 README。
CLAUSE_RE = re.compile(r"spec:[A-Za-z0-9][A-Za-z0-9_-]*-\d+")

# 测试名/用例名提取，按语言分派（矩阵「条款 <- 测试::用例名」的可读性要求：
# 非 Python 测试方法名不得退化为 <line N>；2026-08-31 审查发现，已补）。
GO_TEST_FUNC_RE = re.compile(r"^\s*func\s+(Test\w+)\s*\(")                    # .go
JAVA_ANNOT_RE   = re.compile(r"^\s*@Test\b")                                  # .java 注解行
JAVA_SIG_RE     = re.compile(r"(?:public|protected|private)?\s*(?:static\s+)?[\w<>\[\],. ]+\s+(\w+)\s*\(")
JS_TEST_RE      = re.compile(r"^\s*(?:test|it|describe)\(\s*['\"]([^'\"]+)['\"]")  # .ts/.tsx/.js/.jsx


def extract_spec_clauses(spec_path: Path) -> dict[str, int]:
    """返回 {条款ID(含spec:前缀): 首次出现行号}。spec 内 ID 去重（条款表与验收矩阵会重复）。"""
    clauses: dict[str, int] = {}
    try:
        lines = spec_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        sys.exit(f"spec_check: 无法读取 {spec_path}: {exc}")
    for i, line in enumerate(lines, 1):
        for m in CLAUSE_RE.finditer(line):
            clauses.setdefault(m.group(0), i)
    return clauses


def _collect_py_tags(
    f: Path, text: str, tags: dict[str, list[tuple[Path, str, int]]]
) -> None:
    """收集 .py 测试标签：仅 test_ 函数体内 COMMENT token 计入覆盖。

    ast 定 test_ 函数体行范围（装饰器行不算体；嵌套 def 归最外层
    test_）；tokenize 只认 COMMENT token——字符串字面量/docstring 里的
    spec:<ID> 天然不是注释，不计入（D2 教训：贴字面不等于贴测试）。
    语法错误 → 无函数范围，全部标签不计（宽松降级：不炸进程，条款按
    缺口拦，fail-closed 语义保守）；TokenError（截断文件）→ 保留已
    收集部分。
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return
    ranges = [
        (n.lineno, n.end_lineno or n.lineno, n.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("test_")
    ]
    if not ranges:
        return
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            for m in CLAUSE_RE.finditer(tok.string):
                for lo, hi, name in ranges:
                    if lo <= tok.start[0] <= hi:
                        tags.setdefault(m.group(0), []).append(
                            (f, name, tok.start[0]))
                        break
    except tokenize.TokenError:
        pass


def extract_test_tags(files: list[Path]) -> dict[str, list[tuple[Path, str, int]]]:
    """返回 {条款ID: [(文件, 用例名, 行号)]}。

    标签计入覆盖的规则：.py 文件要求标签位于 test_ 函数体内的注释
    （_collect_py_tags：ast+tokenize 作用域判定——旧逐行状态机
    current_test 首次设置后永不清除，test_ 函数之后的模块级/普通函数
    注释会被误计入，2026-08-31 审查发现）。非 .py（java/go/ts 等，
    测试方法由框架注解标记）维持宽松匹配（current_test 为空时记录为
    <line N>）。
    """
    tags: dict[str, list[tuple[Path, str, int]]] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            sys.exit(f"spec_check: 无法读取 {f}: {exc}")
        ext = f.suffix
        if ext == ".py":
            _collect_py_tags(f, text, tags)
            continue
        current_test = ""
        java_pending = False  # @Test 独占一行时，方法签名在下一行
        for i, line in enumerate(text.splitlines(), 1):
            if ext == ".go":
                if m := GO_TEST_FUNC_RE.match(line):
                    current_test = m.group(1)
            elif ext == ".java":
                if JAVA_ANNOT_RE.match(line):
                    if m := JAVA_SIG_RE.search(line):
                        current_test = m.group(1)
                        java_pending = False
                    else:
                        java_pending = True
                elif java_pending:
                    if m := JAVA_SIG_RE.match(line):
                        current_test = m.group(1)
                        java_pending = False
            elif ext in (".ts", ".tsx", ".js", ".jsx"):
                if m := JS_TEST_RE.match(line):
                    current_test = m.group(1)
            for m in CLAUSE_RE.finditer(line):
                tags.setdefault(m.group(0), []).append((f, current_test or f"<line {i}>", i))
    return tags


def collect_test_files(paths: list[Path], exts: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            for ext in exts:
                files.extend(sorted(p.rglob(f"*{ext}")))
        elif p.is_file():
            files.append(p)
        else:
            sys.exit(f"spec_check: 路径不存在: {p}")
    return sorted(set(files))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="spec 条款与测试标签反向核对矩阵校验",
        epilog="退出码: 0=全覆盖 1=缺口/孤儿(默认) 2=用法错误",
    )
    ap.add_argument("--spec", action="append", required=True, metavar="FILE",
                    help="spec.md 路径（可多次，多个 spec 合并核对）")
    ap.add_argument("--tests", action="append", required=True, metavar="PATH",
                    help="测试文件或目录（可多次，目录递归按 --ext 匹配）")
    # default=None：argparse append 会把显式值追加到 default 之后（--ext .java 得
    # ['.py','.java']，混语言目录误扫 .py）。改为解析后回退默认。
    ap.add_argument("--ext", action="append", default=None, metavar="EXT",
                    help="测试文件扩展名（可多次，如 --ext .java --ext .go；默认 .py）")
    ap.add_argument("--ignore-orphans", action="store_true",
                    help="孤儿标签（测试有、spec 无）降级为警告，不失败")
    args = ap.parse_args()
    exts = args.ext or [".py"]

    spec_clauses: dict[str, int] = {}
    for s in args.spec:
        spec_clauses |= extract_spec_clauses(Path(s))
    test_tags = extract_test_tags(collect_test_files([Path(t) for t in args.tests], exts))

    if not spec_clauses:
        sys.exit("spec_check: spec 中未提取到任何 spec:<代号>-<序号> 条款")

    # 反向核对矩阵：条款 -> 测试
    gaps = sorted(set(spec_clauses) - set(test_tags))
    orphans = sorted(set(test_tags) - set(spec_clauses))

    print("==== 条款 -> 测试 反向核对矩阵 ====")
    for cid in sorted(spec_clauses):
        if hits := test_tags.get(cid):
            for f, case, ln in hits:
                print(f"  {cid}  <-  {f}::{case}  (line {ln})")
        else:
            print(f"  {cid}  <-  [GAP] 无测试 (spec.md line {spec_clauses[cid]})")

    if gaps:
        print(f"\n[GAP] {len(gaps)} 条款无测试:")
        for cid in gaps:
            print(f"  {cid}  (spec.md line {spec_clauses[cid]})")
    if orphans:
        print(f"\n[ORPHAN] {len(orphans)} 标签无对应条款:")
        for cid in orphans:
            for f, case, ln in test_tags[cid]:
                print(f"  {f}::{case} (line {ln})  ->  {cid}")

    total = len(spec_clauses)
    covered = total - len(gaps)
    print(f"\n覆盖: {covered}/{total} 条款有测试")

    rc = 1 if gaps else 0
    if orphans and not args.ignore_orphans:
        rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
