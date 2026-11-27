#!/usr/bin/env bash
# comment-gate（Java 注释棘轮 + merge 后基线自愈 sync）+ gates-install 注入 确定性测试
# 用法: bash tools/git/gates/gates-tests/test_comment-gate.sh
# 覆盖: 基线缺席=未武装放行(C1) / 等于基线过(C2) / 增长拦(C3) / 基线外新文件带注释拦(C4)
#       非 java 跳过(C5) / 收缩放行(C6) / 已删文件跳过(C7)
#       scan next=紧邻代码行(SC1) / test 根级标记(SC2) / 同文本 id 序号消歧+精准删(SC3) /
#       符号链接源拒读: baseline 跳过·check fail-closed·scan 告警·apply ABORT(SC4) /
#       删注释算子融合 ABORT exit 2(SC5)
#       install 双 job 注入+幂等+lock 含 comment-gate(I1) / --check 缺 job 失败(I2)
#       无 pre-commit 段注入(I3) / 无 lefthook.yml install 警告·check 失败(I4)
#       full 漂移检测含 comment-gate.py(I5) /
#       sync 增长不 bless(S1) / 收缩重写降基线(S1c) / 无漂移静默(S2) / 无 baseline not-armed(S3) /
#       sync 前后 check 红(S4b/S1b) / 收缩重写后 check 绿(S4) /
#       HEAD 语义: 未提交改动不进 sync(S5) / 畸形基线 ABORT(S6) / baseline 符号链接拒写穿(SC6)
#       install 注入 post-merge comment-sync + 幂等 + 对账(P1/P1b/P1c) /
#       旧装 yml 缺 post-merge job 时 --check fail-closed(P2)
# 兼容 macOS bash 3.2: 不用 mapfile/${var,,}; set -u 空数组用 ${DIRS[@]+...} 守卫
set -u

GATES=$(cd "$(dirname "$0")/.." && pwd)
PASS=0; FAIL=0
DIRS=()

ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ $1"; }
jid() { python3 -c "import json,sys; print(json.loads(sys.stdin.readline())['id'])"; }
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

echo "── SC: scan/apply 语义与拒读 ──"

# SC1 scan next=紧邻代码行(此前 off-by-one 跳过一行)
mkrepo
mkdir -p "$T/src"
printf 'class A {\n  // doc\n  int x = 1;\n}\n' > "$T/src/A.java"
git -C "$T" add -A
( cd "$T" && python3 "$GATES/comment-gate.py" scan --repo . ) > "$T/scan.jsonl"
grep -q '"next": "int x = 1;"' "$T/scan.jsonl" \
  && ok "SC1 scan next=紧邻代码行" || bad "SC1: $(grep -o '"next": "[^"]*"' "$T/scan.jsonl" | tr '\n' ' ')"

# SC2 test 目录标记: 根级 src/test 与嵌套 app/src/test 均标记, src/main 不标记
mkrepo
mkdir -p "$T/src/test/java" "$T/app/src/test/java" "$T/src/main/java"
printf 'class T {\n  // t\n}\n' > "$T/src/test/java/T.java"
printf 'class U {\n  // u\n}\n' > "$T/app/src/test/java/U.java"
printf 'class M {\n  // m\n}\n' > "$T/src/main/java/M.java"
git -C "$T" add -A
( cd "$T" && python3 "$GATES/comment-gate.py" scan --repo . ) > "$T/scan.jsonl"
[ "$(grep -c '"test": true' "$T/scan.jsonl")" -eq 2 ] && [ "$(grep -c '"test": false' "$T/scan.jsonl")" -eq 1 ] \
  && ok "SC2 test 目录标记(根级+嵌套)" || bad "SC2: true=$(grep -c '"test": true' "$T/scan.jsonl") false=$(grep -c '"test": false' "$T/scan.jsonl")"

# SC3 同文本双块 id 消歧 + apply 定点删除
mkrepo
mkdir -p "$T/src"
printf 'class A {\n  // dup\n  int a = 1;\n  // dup\n  int b = 2;\n}\n' > "$T/src/A.java"
git -C "$T" add -A
( cd "$T" && python3 "$GATES/comment-gate.py" scan --repo . ) > "$T/scan.jsonl"
id1=$(grep '"// dup"' "$T/scan.jsonl" | head -1 | jid)
id2=$(grep '"// dup"' "$T/scan.jsonl" | tail -1 | jid)
[ -n "$id1" ] && [ "$id1" != "$id2" ] \
  && ok "SC3 同文双块 id 消歧" || bad "SC3: $id1 vs $id2"
printf '{"id": "%s", "action": "delete"}\n' "$id1" > "$T/decisions.jsonl"
( cd "$T" && python3 "$GATES/comment-gate.py" apply --repo . --decisions "$T/decisions.jsonl" ) >/dev/null 2>&1
[ $? -eq 0 ] && [ "$(grep -c '// dup' "$T/src/A.java")" -eq 1 ] \
  && ok "SC3b apply 按 id 定点删除单块" || bad "SC3b: 剩 $(grep -c '// dup' "$T/src/A.java") 处"

# SC4 符号链接源拒读: baseline 跳过 / check fail-closed / scan 跳过告警 / apply ABORT
mkrepo
mkdir -p "$T/src"
printf 'class R {\n  // real\n}\n' > "$T/src/R.java"
ln -s /etc/hosts "$T/src/S.java"
git -C "$T" add -A
BASE 2>/dev/null
python3 -c "import json,sys; d=json.load(open('$T/.lefthook/comment-baseline.json')); sys.exit(0 if 'src/S.java' not in d['files'] else 1)" \
  && ok "SC4a baseline 跳过符号链接源" || bad "SC4a baseline 不应计符号链接文件"
out4=$(gate src/S.java 2>&1); rc4=$?
[ "$rc4" -eq 1 ] && echo "$out4" | grep -q "symlink" \
  && ok "SC4b check 拒读 fail-closed" || bad "SC4b: rc=$rc4 out=$out4"
( cd "$T" && python3 "$GATES/comment-gate.py" scan --repo . ) > "$T/scan.jsonl" 2> "$T/scan.err"
if grep -q '"file": "src/S.java"' "$T/scan.jsonl"; then
  bad "SC4c scan 不应产出符号链接记录"
elif grep -q "skipped" "$T/scan.err"; then
  ok "SC4c scan 跳过并告警"
else
  bad "SC4c scan 应告警跳过: $(cat "$T/scan.err")"
fi
id1=$(grep '"// real"' "$T/scan.jsonl" | head -1 | jid)
printf '{"id": "%s", "action": "delete"}\n' "$id1" > "$T/decisions.jsonl"
out4=$( ( cd "$T" && python3 "$GATES/comment-gate.py" apply --repo . --decisions "$T/decisions.jsonl" ) 2>&1 ); rc4=$?
[ "$rc4" -eq 2 ] && echo "$out4" | grep -q "symlink" \
  && ok "SC4d apply 拒读 ABORT exit 2" || bad "SC4d: rc=$rc4 out=$out4"

# SC5 删注释致算子融合 → ABORT exit 2(code_only 去空白检不出)
mkrepo
mkdir -p "$T/src"
printf 'class F {\n  int y = 1 +/*fuse*/+ 2;\n}\n' > "$T/src/F.java"
git -C "$T" add -A
( cd "$T" && python3 "$GATES/comment-gate.py" scan --repo . ) > "$T/scan.jsonl"
id1=$(grep -F '/*fuse*/' "$T/scan.jsonl" | head -1 | jid)
printf '{"id": "%s", "action": "delete"}\n' "$id1" > "$T/decisions.jsonl"
out5=$( ( cd "$T" && python3 "$GATES/comment-gate.py" apply --repo . --decisions "$T/decisions.jsonl" ) 2>&1 ); rc5=$?
[ "$rc5" -eq 2 ] && echo "$out5" | grep -q "fuse" \
  && ok "SC5 算子融合 ABORT exit 2" || bad "SC5: rc=$rc5 out=$out5"

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

# I6 hook 标题带行内注释仍识别(MMB): 注入进既有段, 不在文件尾重复建段
mkrepo
cat > "$T/lefthook.yml" <<'YML'
pre-push: # legacy runner
  enabled: true
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
[ "$(grep -c '^pre-push:' "$T/lefthook.yml")" -eq 1 ] && grep -q "    mutation-gate:" "$T/lefthook.yml" \
  && ok "I6 行内注释标题识别不重复建段" || bad "I6 段识别失败 ($(grep -c '^pre-push:' "$T/lefthook.yml") 个 pre-push:)"

# I7 注入边界止于 2 空格属性行(MMJ): job 不越过 jobs: 子段挂进 skip
mkrepo
cat > "$T/lefthook.yml" <<'YML'
pre-push:
  jobs:
    - tags: smoke
  skip:
    - tags: smoke
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
mg=$(grep -n "    mutation-gate:" "$T/lefthook.yml" | cut -d: -f1)
sk=$(grep -n "^  skip:" "$T/lefthook.yml" | cut -d: -f1)
[ -n "$mg" ] && [ -n "$sk" ] && [ "$mg" -lt "$sk" ] \
  && ok "I7 注入止于子段边界 (mg@$mg < skip@$sk)" || bad "I7 注入越界 (mg@$mg skip@$sk)"

# I8 job 内容漂移检测(MLn): --check 红 + install 原位替换修复
mkrepo
cat > "$T/lefthook.yml" <<'YML'
pre-push:
  jobs:
    smoke:
      run: echo ok
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
sed -i '' 's|bash .lefthook/mutation-gate.sh|bash .lefthook/TAMPERED.sh|' "$T/lefthook.yml"
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1 && bad "I8a 篡改内容应 --check 失败" || ok "I8a 内容漂移 --check 拦截"
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
! grep -q TAMPERED "$T/lefthook.yml" && [ "$(grep -c "    mutation-gate:" "$T/lefthook.yml")" -eq 1 ] \
  && ok "I8b install 原位替换不叠加" || bad "I8b 替换失败 ($(grep -c '    mutation-gate:' "$T/lefthook.yml") 份 job)"

# I9 yml 就绪 ≠ shim 激活(MLr): pre-push shim 在位而 post-merge 缺 → --check 红
mkrepo
cat > "$T/lefthook.yml" <<'YML'
pre-push:
  jobs:
    mutation-gate:
      run: echo ok
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
rm -f "$T/.git/hooks/post-merge"
printf '#!/bin/sh\nexec lefthook run pre-push "$@"\n' > "$T/.git/hooks/pre-push"
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1 && bad "I9a shim 缺失应 --check 失败" || ok "I9a post-merge shim 缺失 --check 拦截"
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
if command -v lefthook >/dev/null 2>&1; then
  grep -q lefthook "$T/.git/hooks/post-merge" \
    && ok "I9b binary 在位 install 激活 post-merge shim" || bad "I9b install 未激活 post-merge shim"
else
  ok "I9b 跳过 (lefthook binary 缺席)"
fi

echo "── S: comment-gate sync（HEAD 重算 + min 只降不升棘轮 + 原子写） ──"

# S1 增长漂移: sync 不 bless, 基线不变 + 增长警告
mkrepo
printf 'public class A {\n    // keep me\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" add -A && git -C "$T" commit -qm "t1"
BASE
BASEF="$T/.lefthook/comment-baseline.json"
printf 'public class A {\n    // keep me\n    // merged in\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" commit -aqm "merge-grow"
out=$(gate 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "S4b sync 前 check 红" || bad "S4b: rc=$rc out=$out"
sync_run
[ "$rc" -eq 0 ] && echo "$out" | grep -q "grew" \
  && [ "$(python3 -c "import json; print(json.load(open('$BASEF'))['total'])")" = "1" ] \
  && ok "S1 增长漂移不 bless(棘轮保旧值)" || bad "S1: rc=$rc out=$out"
out=$(gate 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "S1b sync 后 check 仍红" || bad "S1b: rc=$rc out=$out"

# S1c 收缩漂移: sync 从 HEAD 重写降基线
mkrepo
printf 'public class A {\n    // keep me\n    // merged in\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" add -A && git -C "$T" commit -qm "t2"
BASE
BASEF="$T/.lefthook/comment-baseline.json"
printf 'public class A {\n    // keep me\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" commit -aqm "merge-shrink"
sync_run
[ "$rc" -eq 0 ] && echo "$out" | grep -q "baseline drift after merge" \
  && [ "$(python3 -c "import json; print(json.load(open('$BASEF'))['total'])")" = "1" ] \
  && ok "S1c 收缩漂移重写降基线" || bad "S1c: rc=$rc out=$out"
gate
[ "$rc" -eq 0 ] && ok "S4 收缩重写后 check 绿" || bad "S4: rc=$rc out=$out"

# S2 无漂移: sync 静默退出 0
sync_run
[ "$rc" -eq 0 ] && [ -z "$out" ] && ok "S2 无漂移静默" || bad "S2: rc=$rc out=$out"

# S5 HEAD 语义: 未提交的工作树增长不进 sync, 基线不动
mkrepo
printf 'public class A {\n    // keep me\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" add -A && git -C "$T" commit -qm "t3"
BASE
BASEF="$T/.lefthook/comment-baseline.json"
printf 'public class A {\n    // keep me\n    // dirty\n    int x = 1;\n}\n' > "$T/A.java"
sync_run
[ "$rc" -eq 0 ] && [ -z "$out" ] \
  && [ "$(python3 -c "import json; print(json.load(open('$BASEF'))['total'])")" = "1" ] \
  && ok "S5 sync 读 HEAD, 未提交改动不重写" || bad "S5: rc=$rc out=$out"
out=$(gate 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "S5b 工作树增长 check 仍红" || bad "S5b: rc=$rc out=$out"

# SC6 baseline 是符号链接: 拒写穿 exit 2, 目标字节不变
mkrepo
printf 'public class A {\n    // keep me\n    // merged in\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" add -A && git -C "$T" commit -qm "t4"
BASE
BASEF="$T/.lefthook/comment-baseline.json"
cp "$BASEF" "$T/decoy"
cp "$BASEF" "$T/expected"
printf 'public class A {\n    // keep me\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" commit -aqm "merge-shrink2"
rm "$BASEF"
ln -s "$T/decoy" "$BASEF"
sync_run
[ "$rc" -eq 2 ] && echo "$out" | grep -q "symlink" && cmp -s "$T/expected" "$T/decoy" && [ -L "$BASEF" ] \
  && ok "SC6 baseline 符号链接拒写穿 exit 2" || bad "SC6: rc=$rc out=$out"

# S6 畸形基线: ABORT exit 2, 不静默重写
mkrepo
printf 'public class A {\n    // keep me\n    int x = 1;\n}\n' > "$T/A.java"
git -C "$T" add -A && git -C "$T" commit -qm "t5"
mkdir -p "$T/.lefthook"
BASEF="$T/.lefthook/comment-baseline.json"
printf 'not json at all\n' > "$BASEF"
sync_run
[ "$rc" -eq 2 ] && echo "$out" | grep -q "malformed" \
  && ok "S6 畸形基线 ABORT exit 2" || bad "S6: rc=$rc out=$out"

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
