#!/usr/bin/env bash
# spec 条款↔测试反向核对门（awesome-factory tools/git 分发，由 lefthook 调用）
# 用法: bash .lefthook/spec-check.sh {staged_files}
# 语义: 本次提交含"遵循 spec:<ID> 条款约定"的 spec 文档时，对该 spec 跑 spec_check.py
#       反向核对：
#       - 缺口（spec 条款无测试）→ 阻断提交
#       - 孤儿（测试有、spec 无条款）→ 阻断提交（--ignore-orphans 可降级）
#       - 无 spec 变更 / spec 文档不含 spec:<ID> 条款 → 跳过（非 spec 工作流文档）
# 判定"是 spec 文档"：文件名含 spec 子串且内容含 CLAUSE_RE 匹配的 spec:<ID> 字面量。
# 判定与核对一律读暂存快照（git show :<f>）：部分暂存（index 与工作树分叉）时，
# 核对面必须是将要提交的 index 内容——读工作树会放行未核验条款、误拦已修复内容。
# 判定后缺条款仍 fail-closed（spec_check.py 零条款 rc=1）。
# 解释器缺失 / 脚本缺失 → 提示后放行（全量兜底在 CI gauntlet spec-check-self-test 层）
# 跳过门禁: git commit --no-verify
set -u

[ "$#" -gt 0 ] || exit 0

# 筛选本次提交中的 spec 文档与测试文件。lefthook 的 {staged_files} 含已删除文件
# （git rm / 重命名的源端），删除/改名 spec 或测试的提交必须放行——文件不存在
# 的直接跳过，不进入核对面（2026-08-31 审查发现：不过滤则删除类提交必被误拦）。
SPECS=()
TESTS=()
for f in "$@"; do
  [ -f "$f" ] || continue
  case "$f" in
    *spec*.md)
      # 文件名含 spec 子串不等于 spec 工作流文档（inspection.md 等普通文档）。
      # 内容不含 spec:<ID> 条款字面量 → 视为非 spec 文档跳过，不拦（读暂存快照）。
      if git show :"$f" 2>/dev/null | grep -qE 'spec:[A-Za-z0-9][A-Za-z0-9_-]*-[0-9]+'; then
        SPECS+=("$f")
      fi;;
    *.py|*.java|*.go|*.ts|*.tsx|*.js|*.jsx|*.kt) TESTS+=("$f");;
  esac
done
if [ "${#SPECS[@]}" -eq 0 ]; then
  echo "[spec-check] 无 spec 文档变更（含 spec:<ID> 条款者），跳过"
  exit 0
fi
echo "[spec-check] 检测到 spec 变更: ${SPECS[*]}"

# 解释器探测
if   command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python  >/dev/null 2>&1; then PY=python
else echo "[spec-check] 无 python 解释器，跳过（CI gauntlet 兜底）"; exit 0; fi

ROOT="$(git rev-parse --show-toplevel)"
# 双路径：分发模式（业务项目 install.sh 拷贝到 .lefthook/）与自用模式
# （本仓库根 lefthook.yml 直引 tools/git/lefthook/，spec_check.py 在 tools/）。
SPEC_CHECK="$ROOT/.lefthook/spec_check.py"
[ -f "$SPEC_CHECK" ] || SPEC_CHECK="$ROOT/tools/spec_check.py"
[ -f "$SPEC_CHECK" ] || { echo "[spec-check] 缺 spec_check.py（.lefthook/ 与 tools/ 均无），跳过（CI gauntlet 兜底）"; exit 0; }

# 测试集始终合并 tracked 全集（与 gauntlet 扫描面一致，67c2965b 原则；显式排除
# .lefthook/ 自身——否则 spec_check.py 副本会被扫入，其源码中的示例串会误报为
# 孤儿标签）：条款可由未修改的存量测试覆盖，只传本次提交的测试文件会误报 GAP
# （2026-08-31 审查发现）。工作树已删的 tracked 文件跳过。
while IFS= read -r f; do
  [ -f "$f" ] && TESTS+=("$f")
done < <(git ls-files '*.py' '*.java' '*.go' '*.ts' '*.tsx' '*.js' '*.jsx' '*.kt' \
  | grep -v '^\.lefthook/')
if [ "${#TESTS[@]}" -eq 0 ]; then
  # 有 spec 条款却无任何测试文件：全部条款按缺口计，fail-closed 拦截。
  # 不给 spec_check.py 传裸 --tests（argparse rc=2 用法错误，误导排障）。
  echo "[spec-check] 仓库无测试文件，spec 条款全部无测试，阻断提交"
  exit 1
fi

# 暂存快照导出：SPECS/TESTS 逐个 git show 到镜像目录，spec_check.py 在镜像内
# 以相对路径核对（报告路径与仓内一致）。导出失败（如 index 并发变化）不静默
# 放行：spec 导出失败 / 测试全导出失败均 fail-closed 拦截。
SNAP="$(mktemp -d)"
trap 'rm -rf "$SNAP"' EXIT
for f in "${SPECS[@]}" "${TESTS[@]}"; do
  mkdir -p "$SNAP/$(dirname "$f")"
  git show :"$f" > "$SNAP/$f" 2>/dev/null || true
done
SPEC_SNAP=()
TEST_SNAP=()
for f in "${SPECS[@]}"; do [ -f "$SNAP/$f" ] && SPEC_SNAP+=("$f"); done
for f in "${TESTS[@]}"; do [ -f "$SNAP/$f" ] && TEST_SNAP+=("$f"); done
if [ "${#SPEC_SNAP[@]}" -eq 0 ]; then
  echo "[spec-check] spec 暂存快照导出失败，阻断提交"; exit 1
fi
if [ "${#TEST_SNAP[@]}" -eq 0 ]; then
  echo "[spec-check] 测试暂存快照导出失败，阻断提交"; exit 1
fi

rc=0
# --tests 是 argparse append 单值项：多文件必须逐个 --tests <f>，
# 展开成裸位置参数会被拒（rc=2 用法错误，旧回落路径同此 bug）。
TEST_ARGS=()
for f in "${TEST_SNAP[@]}"; do TEST_ARGS+=(--tests "$f"); done
for s in "${SPEC_SNAP[@]}"; do
  echo "[spec-check] 核对 $s"
  (cd "$SNAP" && "$PY" "$SPEC_CHECK" --spec "$s" "${TEST_ARGS[@]}") || rc=$?
done
exit $rc
