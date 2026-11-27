#!/usr/bin/env bash
# comment-gate（Java 注释棘轮 + merge 后基线自愈 sync）+ gates-install 注入 确定性测试
# 用法: bash tools/git/gates/gates-tests/test_comment-gate.sh
# 覆盖: 基线缺席=未武装放行(C1) / 等于基线过(C2) / 增长拦(C3) / 基线外新文件带注释拦(C4)
#       非 java 跳过(C5) / 收缩放行(C6) / 已删文件跳过(C7) /
#       install 双 job 注入+幂等+lock 含 comment-gate(I1) / --check 缺 job 失败(I2)
#       无 pre-commit 段注入(I3) / 无 lefthook.yml install 警告·check 失败(I4)
#       full 漂移检测含 comment-gate.py(I5) /
#       sync 漂移检出重写(S1) / 无漂移静默(S2) / 无 baseline not-armed(S3) /
#       漂移修复后 check 绿(S4) /
#       install 注入 post-merge comment-sync + 幂等 + 对账(P1/P1b/P1c) /
#       旧装 yml 缺 post-merge job 时 --check fail-closed(P2)
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

mkrepo() { # → $T: 临时仓, 已推 origin 并设上游
  T=$(mktemp -d "${TMPDIR:-/tmp}/gates-ct.XXXXXX")
  DIRS+=("$T")
  git -C "$T" init -q
  git -C "$T" config user.email t@t.t
  git -C "$T" config user.name t
  echo init > "$T/README.md"
  git -C "$T" add -A
  git -C "$T" -c commit.gpgsign=false commit -qm init
  git -C "$T" init -q --bare "$T.git"
  git -C "$T" remote add origin "$T.git"
  git -C "$T" push -q -u origin HEAD
}

gate() { # gate <mode> [files...]  — 以仓为 cwd 跑门禁
  ( cd "$T" && python3 "$GATES/comment-gate.py" check --repo . --baseline .lefthook/comment-baseline.json "$@" )
}

BASE() { # BASE — 按当前工作树生成基线(tracked_java 只数已跟踪文件, 先 add)
  git -C "$T" add -A
  mkdir -p "$T/.lefthook"
  ( cd "$T" && python3 "$GATES/comment-gate.py" baseline --repo . --out .lefthook/comment-baseline.json ) >/dev/null
}

sync_run() { # sync_run — 跑 sync 子命令, 结果在 $out/$rc; baseline 路径 $BASEF
  out=$(cd "$T" && python3 "$GATES/comment-gate.py" sync --repo "$T" --baseline "$BASEF" 2>&1); rc=$?
}

echo "── C: comment-gate 棘轮语义 ──"

mkrepo
mkdir -p "$T/src"
printf 'class A {\n  // note one\n  /* block two\n     more */\n  int x = 1; // tail\n}\n' > "$T/src/A.java"

BASE
( cd "$T" && git add -A )
out=$(gate src/A.java); rc=$?
[ "$rc" -eq 0 ] && echo "$out" | grep -q "ok" && ok "C2 等于基线放行" || bad "C2 等于基线应放行 (rc=$rc out=$out)"

# C1 基线缺席
mv "$T/.lefthook/comment-baseline.json" "$T/.lefthook/comment-baseline.json.bak"
out=$(gate src/A.java); rc=$?
[ "$rc" -eq 0 ] && echo "$out" | grep -q "not armed" && ok "C1 基线缺席=未武装放行" || bad "C1 缺基线应放行 (rc=$rc out=$out)"
mv "$T/.lefthook/comment-baseline.json.bak" "$T/.lefthook/comment-baseline.json"

# C3 增长拦截
printf '// extra\n' >> "$T/src/A.java"
out=$(gate src/A.java 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "may not grow" && ok "C3 注释增长拦截" || bad "C3 增长应拦 (rc=$rc out=$out)"

# C4 基线外新文件带注释拦截
printf 'class B {\n  // new comment\n}\n' > "$T/src/B.java"
out=$(gate src/A.java src/B.java 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "not in baseline" && ok "C4 基线外新文件带注释拦截" || bad "C4 新文件应拦 (rc=$rc out=$out)"

# C5 非 java 跳过（基线外也不拦）
printf 'SELECT 1;\n' > "$T/src/q.sql"
out=$(gate src/A.java src/B.java src/q.sql 2>&1); rc=$?
[ "$rc" -eq 1 ] && echo "$out" | grep -q "B.java" && ! echo "$out" | grep -q "q.sql" && ok "C5 非 java 跳过" || bad "C5 q.sql 不应被判 (out=$out)"

# C6 收缩放行
printf 'class C {\n}\n' > "$T/src/C.java"
rm "$T/src/B.java"
out=$(gate src/C.java); rc=$?
[ "$rc" -eq 0 ] && ok "C6 注释收缩(降棘轮)放行" || bad "C6 收缩应放行 (rc=$rc out=$out)"

# C7 已删文件跳过
rm "$T/src/C.java"
out=$(gate src/C.java); rc=$?
[ "$rc" -eq 0 ] && ok "C7 已删文件跳过" || bad "C7 删除文件应跳过 (rc=$rc out=$out)"

echo "── I: gates-install pre-commit 注入 ──"

mkrepo
cat > "$T/lefthook.yml" <<'YML'
pre-commit:
  jobs:
    spec-check:
      run: echo {staged_files}
pre-push:
  jobs:
    tests:
      run: echo ok
YML
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); rc=$?
[ "$rc" -eq 0 ] || bad "I1 install 失败 (rc=$rc): $(echo "$out" | tail -3)"
grep -q "    comment-gate:" "$T/lefthook.yml" && grep -q "    mutation-gate:" "$T/lefthook.yml" \
  && ok "I1 双 job 注入 (pre-commit comment-gate + pre-push mutation-gate)" || bad "I1 双 job 缺一"
[ "$(grep -c "    comment-gate:" "$T/lefthook.yml")" -eq 1 ] && ok "I1b 注入唯一" || bad "I1b 注入应唯一"
precommit_line=$(grep -n "    comment-gate:" "$T/lefthook.yml" | cut -d: -f1)
prepush_line=$(grep -n "^pre-push:" "$T/lefthook.yml" | cut -d: -f1)
[ -n "$precommit_line" ] && [ -n "$prepush_line" ] && [ "$precommit_line" -lt "$prepush_line" ] \
  && ok "I1c comment-gate 落在 pre-commit 段内" || bad "I1c 段位错误 (comment@$precommit_line pre-push@$prepush_line)"
python3 -c "import json,sys; d=json.load(open('$T/.lefthook/gates-lock.json')); sys.exit(0 if '.lefthook/comment-gate.py' in d.get('files',{}) else 1)" \
  && ok "I1d lock 记录 comment-gate.py" || bad "I1d lock 缺 comment-gate.py"
out2=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); rc2=$?
[ "$rc2" -eq 0 ] && [ "$(grep -c "    comment-gate:" "$T/lefthook.yml")" -eq 1 ] && ok "I1e 重跑幂等" || bad "I1e 重跑应幂等 (rc=$rc2)"
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1 && ok "I1f --check 全绿" || bad "I1f --check 应绿"

# I2 缺 job 时 --check 失败
python3 -c "
p = '$T/lefthook.yml'
s = open(p).read()
s = s.replace('    comment-gate:\n      run: python3 .lefthook/comment-gate.py check --repo . --baseline .lefthook/comment-baseline.json {staged_files}\n', '')
open(p, 'w').write(s)
"
grep -q "    comment-gate:" "$T/lefthook.yml" && bad "I2 删 job 失败(仍在)" || true
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1 && bad "I2 缺 comment-gate job 应 --check 失败" || ok "I2 缺 job --check 拦截"

# I3 无 pre-commit 段的 yml 也能注入
mkrepo
printf 'pre-push:\n  jobs:\n    tests:\n      run: echo ok\n' > "$T/lefthook.yml"
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1 && \
  awk '/^pre-commit:/{f=1} f && /    comment-gate:/{print "yes"; exit}' "$T/lefthook.yml" | grep -q yes \
  && ok "I3 无 pre-commit 段注入成功" || bad "I3 无 pre-commit 段注入失败"

# I4 无 lefthook.yml: install 警告放行, --check 失败
mkrepo
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1 && ok "I4a 无 yml install 放行(警告)" || bad "I4a 无 yml install 不应硬失败"
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1 && bad "I4b 无 yml --check 应失败" || ok "I4b 无 yml --check 拦截"

# I5 full 漂移检测含 comment-gate.py
mkrepo
cat > "$T/lefthook.yml" <<'YML'
pre-commit:
  jobs:
    x:
      run: echo ok
pre-push:
  jobs:
    mutation-gate:
      run: echo ok
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
printf '# drift\n' >> "$T/.lefthook/comment-gate.py"
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1 && bad "I5 comment-gate.py 漂移应 --check 失败" || ok "I5 full 漂移检测含 comment-gate.py"

echo "── S: comment-gate sync（merge 后基线漂移自愈） ──"

# S1 漂移检出: merge 引入新注释后 sync 重写 baseline
mkrepo
printf 'public class A {\n    // keep me\n    int x = 1;\n}\n' > "$T/A.java"
BASE
BASEF="$T/.lefthook/comment-baseline.json"
printf 'public class A {\n    // keep me\n    // merged in\n    int x = 1;\n}\n' > "$T/A.java"
sync_run
[ "$rc" -eq 0 ] && echo "$out" | grep -q "baseline drift after merge" \
  && [ "$(python3 -c "import json; print(json.load(open('$BASEF'))['total'])")" = "2" ] \
  && ok "S1 漂移检出并重写 baseline" || bad "S1: rc=$rc out=$out"

# S2 无漂移: sync 静默退出 0
sync_run
[ "$rc" -eq 0 ] && [ -z "$out" ] && ok "S2 无漂移静默" || bad "S2: rc=$rc out=$out"

# S4 sync 重写后 check 恢复绿（merge 后接一次 commit 即对齐）
gate src/A.java
[ "$rc" -eq 0 ] && ok "S4 漂移修复后 check 绿" || bad "S4: rc=$rc out=$out"

# S3 无 baseline: not-armed 放行且不隐性生成
mkrepo
BASEF="$T/.lefthook/comment-baseline.json"
sync_run
[ "$rc" -eq 0 ] && echo "$out" | grep -q "not armed" && [ ! -f "$BASEF" ] \
  && ok "S3 无 baseline not-armed 放行" || bad "S3: rc=$rc out=$out"

echo "── P: gates-install post-merge comment-sync 注入 ──"

# P1 install 注入 post-merge comment-sync job
mkrepo
mkdir -p "$T/.lefthook"
cp "$GATES/mutation-gate.sh" "$GATES/comment-gate.py" "$T/.lefthook/"
cat > "$T/lefthook.yml" <<'EOF'
pre-push:
  commands:
    mutation-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/mutation-gate.sh
EOF
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); rc=$?
[ "$rc" -eq 0 ] && grep -q "^post-merge:" "$T/lefthook.yml" \
  && grep -q "    comment-sync:" "$T/lefthook.yml" && grep -q "comment-gate.py sync" "$T/lefthook.yml" \
  && ok "P1 post-merge comment-sync 注入" || bad "P1: rc=$rc out=$(echo "$out" | tail -3)"

# P1b 重跑 install 幂等: lefthook.yml 字节不变
h1=$(shasum -a 256 "$T/lefthook.yml" | cut -d' ' -f1)
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
h2=$(shasum -a 256 "$T/lefthook.yml" | cut -d' ' -f1)
[ "$h1" = "$h2" ] && ok "P1b 重跑幂等(yml 字节不变)" || bad "P1b 幂等: 两次 yml 不同"

# P1c --check 对账通过（full 一致 + 三个 job 在位）
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1; crc=$?
[ $crc -eq 0 ] && ok "P1c --check 对账通过" || bad "P1c --check: rc=$crc"

# P2 旧装 yml 缺 post-merge job: --check fail-closed
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
[ $crc -ne 0 ] && echo "$cout" | grep -q "post-merge" \
  && ok "P2 缺 post-merge job: --check fail-closed 并指名" || bad "P2 fail-closed: rc=$crc out=$cout"

echo
echo "pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ]
