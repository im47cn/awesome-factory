#!/usr/bin/env bash
# workspaces-guard（team-os 需求工作区规范门禁）确定性测试
# 用法: bash tools/git/gates/gates-tests/test_workspaces-guard.sh
# 覆盖: 合法普通仓(W1) / linked worktree 源仓的 prunable 残链照报(W2) /
#       slug 违规(W3) / 纯输出目录(W4) / 空壳目录(W5) /
#       prunable 残链含空格路径报全径且计入退出码(W6) / 空根 PASS(W7)
# 兼容 macOS bash 3.2: 不用 mapfile; 空数组用 ${DIRS[@]+...} 守卫
set -u

GATES=$(cd "$(dirname "$0")/.." && pwd)
GUARD="$GATES/workspaces-guard.sh"
PASS=0; FAIL=0
DIRS=()

ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ $1"; }
# shellcheck disable=SC2329  # 经 trap cleanup EXIT 间接调用
cleanup() {
  for d in ${DIRS[@]+"${DIRS[@]}"}; do rm -rf "$d" 2>/dev/null; done
}
trap cleanup EXIT

# prunable 段默认扫 $HOME/sources/open-platform, 测试必须隔离掉
NOSRC=/nonexistent-gates-wg-test

newroot() { # newroot → $R: 新 workspaces 根(已 realpath, 与 guard 输出路径一致)
  R=$(mktemp -d "${TMPDIR:-/tmp}/gates-wg.XXXXXX")
  DIRS+=("$R")
  R=$(cd "$R" && pwd -P)
}

newsrc() { # newsrc → $SB: 假 open-platform 源仓根
  SB=$(mktemp -d "${TMPDIR:-/tmp}/gates-wg-src.XXXXXX")
  DIRS+=("$SB")
  SB=$(cd "$SB" && pwd -P)
}

mkrepo() { # mkrepo <path>: 最小 git 仓(含一次提交, worktree add 依赖)
  mkdir -p "$1"
  git -C "$1" init -q
  echo init > "$1/f.txt"
  git -C "$1" add -A
  git -C "$1" -c commit.gpgsign=false commit -qm init
}

# W1 合法普通仓: slug 目录 + .git 目录子仓 → PASS
newroot
mkdir -p "$R/KFPT-100/myrepo"
mkrepo "$R/KFPT-100/myrepo"
out=$(SOURCES_BASE="$NOSRC" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "W1 合法普通仓 PASS" || bad "W1: rc=$rc out=$out"

# W3 slug 违规: 裸短名目录
newroot
mkdir -p "$R/dist-fix/stuff"
mkrepo "$R/dist-fix/stuff"
out=$(SOURCES_BASE="$NOSRC" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "slug 命名违规: dist-fix" \
  && ok "W3 slug 违规 exit 1" || bad "W3: rc=$rc out=$out"

# W4 纯输出目录: slug 合法但无 worktree
newroot
mkdir -p "$R/20261127-report"
echo hi > "$R/20261127-report/notes.md"
out=$(SOURCES_BASE="$NOSRC" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "纯输出目录: 20261127-report" \
  && ok "W4 纯输出目录 exit 1" || bad "W4: rc=$rc out=$out"

# W5 空壳目录
newroot
mkdir -p "$R/20261127-empty"
out=$(SOURCES_BASE="$NOSRC" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "空壳目录: 20261127-empty" \
  && ok "W5 空壳目录 exit 1" || bad "W5: rc=$rc out=$out"

# W6 prunable 残链含空格路径: 报全径且计入退出码
newroot; newsrc
mkdir -p "$R/KFPT-7"
mkrepo "$SB/repo"
git -C "$SB/repo" worktree add -q -b wt-fix "$R/KFPT-7/spaced wt"
rm -rf "$R/KFPT-7/spaced wt"
out=$(SOURCES_BASE="$SB" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "prunable 残链 $R/KFPT-7/spaced wt" \
  && ok "W6 prunable 残链含空格路径 exit 1" || bad "W6: rc=$rc out=$out"

# W2 源仓自身是 linked worktree (.git 是文件): 不跳过, 残链照报
newroot; newsrc
MAIN=$(mktemp -d "${TMPDIR:-/tmp}/gates-wg-main.XXXXXX")
DIRS+=("$MAIN")
mkrepo "$MAIN/main-repo"
git -C "$MAIN/main-repo" worktree add -q "$SB/linked"
mkdir -p "$R/KFPT-8"
git -C "$MAIN/main-repo" worktree add -q -b wt-gone "$R/KFPT-8/gone"
rm -rf "$R/KFPT-8/gone"
out=$(SOURCES_BASE="$SB" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "prunable 残链 $R/KFPT-8/gone" \
  && ok "W2 linked worktree 源仓不跳过" || bad "W2: rc=$rc out=$out"

# W7 空根: glob 字面误报不再发生
newroot
out=$(SOURCES_BASE="$NOSRC" bash "$GUARD" "$R" 2>&1); rc=$?
[ "$rc" -eq 0 ] && echo "$out" | grep -q "PASS" \
  && ok "W7 空根 PASS" || bad "W7: rc=$rc out=$out"

echo "pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ]
