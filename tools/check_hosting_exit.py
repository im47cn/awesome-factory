#!/usr/bin/env python3
"""check_hosting_exit.py — 托管平台出口收口门（ADR-007 层级契约）。

两条机械化规则（gauntlet 层 lint-factory-hosting-exit）：
  R1 零 gh 直调：tracked .factory/*.sh 不得出现命令位 `gh <子命令>` 调用
     （注释/字符串里的行文不算）——hosting.py 是唯一平台出口（含
     --repo/凭据/slug 解析）。.factory/*.py（除 hosting.py 本体）不得以
     subprocess argv 形态调 gh。
  R2 issue 副作用收口：tracked .factory/*.sh 中 `${HOST} issue comment` /
     `${HOST} issue set-labels` 调用形态只允许出现在 factory-lib.sh
     （收口本体）、fix-issue.sh（issue_label 链属主包装，自带租约围栏）与
     factory-state.sh（sync 声明式收敛的执行者——TRANSITIONS owner=sync
     的标签应用，非链侧旁路）。issue/pr 创建与 PR 侧写不在收口范围
     （层级契约见 hosting.py 头注释：S3/M2 流程无 issue 租约上下文，
     PR 标签漂移由 sync 兜底收敛）。

负控制（tools/test_gauntlet_checks.sh check_hosting_exit 组）：
  绕过形态（命令位 gh 直调 / 收口外 `${HOST} issue …` 写）必须 exit 1。

用法: check_hosting_exit.py <root>（仓库根；扫描面 = git ls-files，
对齐 must_not_match 的 tracked 面原则）。
"""
import re
import subprocess
import sys
from pathlib import Path

# R1 命令位锚定：行首 / ; / & / | / ( 之后（$(gh …)、>(gh …) 形态均命中）；
# 注释句中的 "gh label 过滤是…" 不在命令位，不误报
_GH_CALL = re.compile(r"(?:^|[;(&|])\s*gh\s+(?:issue|pr|label|api|auth|repo)\b")
_GH_PY = re.compile(r"\[\s*[\"']gh[\"']")
# R2 只认调用形态（${HOST} 前缀的 CLI 实参），行文/报错文案不误报
_ISSUE_WRITE = re.compile(r"\$\{HOST\}\s+issue\s+(?:comment|set-labels)\b")
_ALLOWED_SH = {"factory-lib.sh", "fix-issue.sh", "factory-state.sh"}


def _tracked(root: Path, pattern: str) -> list[Path]:
    r = subprocess.run(["git", "-C", str(root), "ls-files", "--", pattern],
                       capture_output=True, text=True)
    return [root / ln for ln in r.stdout.splitlines() if ln.strip()]


def main(root_str: str) -> int:
    root = Path(root_str)
    violations: list[str] = []

    for f in _tracked(root, ".factory/*.sh"):
        text = f.read_text(encoding="utf-8", errors="replace")
        if _GH_CALL.search(text):
            violations.append(f"R1 {f.relative_to(root)}: gh 直调（须走 hosting.py）")
        if _ISSUE_WRITE.search(text) and f.name not in _ALLOWED_SH:
            violations.append(
                f"R2 {f.relative_to(root)}: ${'{'}HOST{'}'} issue 写绕过 "
                "factory-lib 收口出口（issue_comment/issue_label_swap）")

    for f in _tracked(root, ".factory/*.py"):
        if f.name == "hosting.py":
            continue  # 适配器本体：gh 调用的唯一合法居所
        if _GH_PY.search(f.read_text(encoding="utf-8", errors="replace")):
            violations.append(f"R1 {f.relative_to(root)}: py 侧 gh 直调")

    if violations:
        print("hosting-exit 违例:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    n_sh = len(_tracked(root, ".factory/*.sh"))
    print(f"hosting-exit: {n_sh} 个 tracked .factory/*.sh 通过"
          "（零 gh 直调 / issue 副作用经 factory-lib 收口）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
