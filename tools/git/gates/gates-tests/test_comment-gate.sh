#!/usr/bin/env bash
# comment-gate sync 子命令与 gates-install post-merge 注入测试（仿 test_mutation-gate.sh 模式）
# 用法: bash tools/git/gates/gates-tests/test_comment-gate.sh
# 覆盖: sync 漂移检出重写(T1/T1b) / 无漂移静默(T2) / 无 baseline not-armed(T3) /
#       漂移修复后 check 绿(T4) / install 注入 post-merge + 幂等 + 对账(T5/T5b) /
#       旧装 yml 缺 post-merge job 时 --check fail-closed(T6)
# 兼容 macOS bash 3.2: 不用 mapfile/${var,,}; set -u 空数组用 ${DIRS[@]+...} 守卫
set -u

GATES=$(cd "$(dirname "$0")/.." && pwd)
PASS=0; FAIL=0
DIRS=()

ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ $1"; }
# shellcheck disable=SC2329  # 经 trap cleanup EXIT 间接调用
cleanup() {
  for d in ${DIRS[@]+"${DIRS[@]}"}; do rm -rf "$d" "$d.git" 2>/dev/null; done
}
trap cleanup EXIT

mkrepo() { # → $T: 临时仓, java 文件已跟踪（comment-gate 只扫 git 跟踪的 *.java）
  T=$(mktemp -d "${TMPDIR:-/tmp}/gates-cg.XXXXXX")
  DIRS+=("$T")
  git -C "$T" init -q
  git -C "$T" config user.email t@t.t
  git -C "$T" config user.name t
  cat > "$T/A.java" <<'EOF'
public class A {
    // keep me
    int x = 1;
}
EOF
  git -C "$T" add -A
  git -C "$T" -c commit.gpgsign=false commit -qm init
}

sync_run() { out=$(cd "$T" && python3 "$GATES/comment-gate.py" sync --repo "$T" --baseline "$BASE" 2>&1); rc=$?; }

echo "── comment-gate sync ──"

# T1 漂移检出: 增一行注释后 sync 重写 baseline
mkrepo; mkdir -p "$T/.lefthook"; BASE="$T/.lefthook/comment-baseline.json"
python3 "$GATES/comment-gate.py" baseline --repo "$T" --out "$BASE" >/dev/null
printf 'public class A {\n    // keep me\n    // drifted line\n    int x = 1;\n}\n' > "$T/A.java"
sync_run
if [ $rc -eq 0 ] && echo "$out" | grep -q "baseline drift after merge"; then
  ok "T1 漂移检出: exit 0 + drift 提示"
else bad "T1 漂移检出: rc=$rc out=$out"; fi
new_total=$(python3 -c "import json; print(json.load(open('$BASE'))['total'])" 2>/dev/null)
if [ "$new_total" = "2" ]; then
  ok "T1b baseline 已重写为漂移后计数(total=2)"
else bad "T1b baseline 重写: total=${new_total:-unreadable}"; fi

# T4 sync 修复后 ratchet check 恢复绿（漂移不再拦正常工作）
chk=$(cd "$T" && python3 "$GATES/comment-gate.py" check --repo "$T" --baseline "$BASE" 2>&1); crc=$?
if [ $crc -eq 0 ]; then ok "T4 sync 后 check 绿"; else bad "T4 sync 后 check: rc=$crc $chk"; fi

# T2 无漂移: sync 静默 exit 0
mkrepo; mkdir -p "$T/.lefthook"; BASE="$T/.lefthook/comment-baseline.json"
python3 "$GATES/comment-gate.py" baseline --repo "$T" --out "$BASE" >/dev/null
sync_run
if [ $rc -eq 0 ] && [ -z "$out" ]; then ok "T2 无漂移静默: exit 0 无输出"; else bad "T2 无漂移静默: rc=$rc out=$out"; fi

# T3 无 baseline: not-armed 语义, 不隐性生成
mkrepo; BASE="$T/.lefthook/comment-baseline.json"
sync_run
if [ $rc -eq 0 ] && echo "$out" | grep -q "not armed" && [ ! -f "$BASE" ]; then
  ok "T3 无 baseline: not armed + exit 0 + 未隐性生成"
else bad "T3 无 baseline: rc=$rc out=$out armed=$([ -f "$BASE" ] && echo yes || echo no)"; fi

echo "── gates-install post-merge 注入 ──"

# T5 install 注入 post-merge comment-sync + 幂等（lefthook.yml 由消费仓 install 预置, gates-install 只注入）
mkrepo; mkdir -p "$T/.lefthook"
printf 'pre-push:\n  commands:\n    mutation-gate:\n      use_stdin: true\n      run: bash .lefthook/mutation-gate.sh\n' > "$T/lefthook.yml"
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1; rc=$?
if [ $rc -eq 0 ] \
   && grep -q "^post-merge:" "$T/lefthook.yml" \
   && grep -q "comment-sync:" "$T/lefthook.yml" \
   && grep -q "comment-gate.py sync" "$T/lefthook.yml"; then
  ok "T5 install 注入 post-merge comment-sync"
else bad "T5 install 注入: rc=$rc"; sed -n '1,30p' "$T/lefthook.yml" 2>/dev/null; fi
h1=$(shasum -a 256 "$T/lefthook.yml" | cut -d' ' -f1)
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
h2=$(shasum -a 256 "$T/lefthook.yml" | cut -d' ' -f1)
if [ "$h1" = "$h2" ]; then ok "T5b 重跑 install 幂等(lefthook.yml 字节不变)"; else bad "T5b 幂等: 两次 yml 不同"; fi

# T5c --check 对账通过（full 一致 + 两个 job 在位）
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1; crc=$?
if [ $crc -eq 0 ]; then ok "T5c --check 对账通过"; else bad "T5c --check: rc=$crc"; fi

# T6 旧装 yml 缺 post-merge job: --check fail-closed
mkrepo; mkdir -p "$T/.lefthook"
cp "$GATES/mutation-gate.sh" "$GATES/comment-gate.py" "$T/.lefthook/"
cat > "$T/lefthook.yml" <<'EOF'
pre-push:
  commands:
    mutation-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/mutation-gate.sh
EOF
cout=$(bash "$GATES/gates-install.sh" --repo "$T" --check 2>&1); crc=$?
if [ $crc -ne 0 ] && echo "$cout" | grep -q "post-merge"; then
  ok "T6 缺 post-merge job: --check fail-closed 并指名"
else bad "T6 fail-closed: rc=$crc out=$cout"; fi

echo
echo "通过 $PASS / 失败 $FAIL"
[ $FAIL -eq 0 ]
