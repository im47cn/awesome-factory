#!/usr/bin/env bash
# gates 面确定性测试（周界 PR 验证门, Q10; 仿 awesome-rules tools/test_* 先例）
# 用法: bash tools/git/gates/gates-tests/test_mutation-gate.sh
# 覆盖: 语言分派跳过链(T1-T4/T7/T9) / --ci 契约位 fail-closed(T6) / 接入不完整
#       fail-closed(T5) / 143 终止预算放行(T5b) / PIT 模块循环 stub(T8) /
#       gates-install 落地+对账(T10) / lefthook job 幂等注入(T11)
# 兼容 macOS bash 3.2: 不用 mapfile/${var,,}; set -u 空数组用 ${DIRS[@]+...} 守卫
set -u

GATES=$(cd "$(dirname "$0")/.." && pwd)
PASS=0; FAIL=0
DIRS=()

ok()  { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ $1"; }

cleanup() {
  for d in ${DIRS[@]+"${DIRS[@]}"}; do rm -rf "$d" "$d.git" 2>/dev/null; done
}
trap cleanup EXIT

mkrepo() { # → $T: 临时仓, 已推 origin 并设上游(@{u} 可解析)
  T=$(mktemp -d "${TMPDIR:-/tmp}/gates-t.XXXXXX")
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

ahead() { # 增量提交(@{u}...HEAD 的变更集由此决定)
  git -C "$T" add -A
  git -C "$T" -c commit.gpgsign=false commit -qm ahead
}

stage_ts() { # 工作树置 ts 三件套: profile 声明 + stryker 配置 + vitest 基线
  printf 'ts\n' > "$T/.lefthook-profile"
  cp "$GATES/stryker.config.json" "$T/stryker.config.json"
  printf '{\n  "devDependencies": {\n    "vitest": "^1.0.0"\n  }\n}\n' > "$T/package.json"
}

run_gate() { out=$(cd "$T" && bash "$GATES/mutation-gate.sh" ${1:+"$1"} 2>&1); rc=$?; }

# ── mutation-gate.sh ────────────────────────────────────────────────────────

# T1: 无 .lefthook-profile → 提示跳过
mkrepo; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无 .lefthook-profile'; then ok "T1 无 profile 声明 → 跳过"; else bad "T1 (rc=$rc) $out"; fi

# T2: ts + 无 stryker.config.json → 跳过
mkrepo; stage_ts; rm "$T/stryker.config.json"; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无 stryker.config.json'; then ok "T2 ts 无 stryker 配置 → 跳过"; else bad "T2 (rc=$rc) $out"; fi

# T3: ts + 配置 + 无 vitest/jest 基线 → 跳过
mkrepo; stage_ts; printf '{\n  "name": "x"\n}\n' > "$T/package.json"; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无 vitest/jest'; then ok "T3 ts 无测试基线 → 跳过"; else bad "T3 (rc=$rc) $out"; fi

# T4: ts + 双前置 + 无 src/ 生产变更 → 跳过
mkrepo; stage_ts; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无 src/'; then ok "T4 ts 无生产 TS 变更 → 跳过"; else bad "T4 (rc=$rc) $out"; fi

# T5: ts + 全前置 + src 变更 + npx 失败 → fail-closed（接入不完整不得静默放行）
# npx 打桩(照 T8 mvn 桩先例): 真 npx --no-install 无 lockfile 时部分 npm 版本仍做
# registry 解析 → 挂起撞 180s 看门狗 → rc 143 走放行出口, 断言非确定性且拖慢套件;
# 这里测 gate 对非零 rc 的分派契约, 不是 npx 本身
NPX=$(mktemp -d "${TMPDIR:-/tmp}/gates-npx.XXXXXX"); DIRS+=("$NPX")
printf '#!/usr/bin/env bash\nexit 1\n' > "$NPX/npx"; chmod +x "$NPX/npx"
mkrepo; stage_ts; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead
out=$(cd "$T" && PATH="$NPX:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q '✗'; then ok "T5 接入不完整(npx 桩 exit 1) → fail"; else bad "T5 (rc=$rc) $out"; fi

# T5b: stryker 被终止(rc 143/137) → 预算放行出口, 不绑架 push（零覆盖分支补测）
printf '#!/usr/bin/env bash\nkill -TERM $$\n' > "$NPX/npx"
mkrepo; stage_ts; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead
out=$(cd "$T" && PATH="$NPX:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '放行'; then ok "T5b rc 143 → 预算放行"; else bad "T5b (rc=$rc) $out"; fi

# T6: --ci 契约位 → fail-closed 拒绝
mkrepo; run_gate --ci
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q '\-\-ci'; then ok "T6 --ci 未交付 → fail-closed"; else bad "T6 (rc=$rc) $out"; fi

# T7: java + 无 src/main/java 变更 → 跳过
mkrepo; printf 'java\n' > "$T/.lefthook-profile"; echo tweak >> "$T/README.md"; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无 src/main/java'; then ok "T7 java 无生产变更 → 跳过"; else bad "T7 (rc=$rc) $out"; fi

# T8: java + pitest 模块 + stub mvn → 通过/失败两态（模块循环不真跑 mvn, 只验分派与出口码）
STUB=$(mktemp -d "${TMPDIR:-/tmp}/gates-mvn.XXXXXX"); DIRS+=("$STUB")
printf '#!/usr/bin/env bash\nexit "${MVN_RC:-0}"\n' > "$STUB/mvn"; chmod +x "$STUB/mvn"
mkrepo; printf 'java\n' > "$T/.lefthook-profile"
printf '<project>\n  <plugin>org.pitest:pitest-maven</plugin>\n</project>\n' > "$T/pom.xml"
mkdir -p "$T/src/main/java"; echo "class App {}" > "$T/src/main/java/App.java"; ahead
out=$(cd "$T" && MVN_RC=0 PATH="$STUB:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '✓'; then ok "T8a PIT 达标(stub) → 通过"; else bad "T8a (rc=$rc) $out"; fi
out=$(cd "$T" && MVN_RC=1 PATH="$STUB:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q '✗'; then ok "T8b PIT 低于阈值(stub) → 失败"; else bad "T8b (rc=$rc) $out"; fi

# T9: python profile → 不含 java/ts 跳过
mkrepo; printf 'python\n' > "$T/.lefthook-profile"; echo tweak >> "$T/README.md"; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '不含 java/ts'; then ok "T9 非 java/ts profile → 跳过"; else bad "T9 (rc=$rc) $out"; fi

# ── gates-install.sh ────────────────────────────────────────────────────────

# T10: 落地 + 对账: full 覆盖 / profile 探测 / local 首落地 / lock / 漂移即败
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] \
  && cmp -s "$GATES/mutation-gate.sh" "$T/.lefthook/mutation-gate.sh" \
  && [ "$(cat "$T/.lefthook-profile")" = "ts" ] \
  && cmp -s "$GATES/stryker.config.json" "$T/stryker.config.json" \
  && [ -f "$T/.lefthook/gates-lock.json" ]; then ok "T10 install 落地四件套"; else bad "T10 (rc=$irc) $out"; fi
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1; crc=$?
if [ "$crc" -eq 0 ]; then ok "T10 --check 一致通过"; else bad "T10 --check 应通过却 rc=$crc"; fi
echo "# drift" >> "$T/.lefthook/mutation-gate.sh"
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1; crc=$?
if [ "$crc" -eq 1 ]; then ok "T10 full 漂移 → --check 失败"; else bad "T10 漂移后 --check 应 rc=1 却 rc=$crc"; fi

# T11: lefthook.yml job 幂等注入: 锚位在 sourcery 之后, 重复安装不重复
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
cat > "$T/lefthook.yml" <<'YML'
pre-push:
  piped: true
  jobs:
    sourcery-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/sourcery-gate.sh {push_files}
    coderabbit-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/coderabbit-gate.sh {push_files}
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
c1=$(grep -c '^    mutation-gate:' "$T/lefthook.yml" 2>/dev/null || echo 0)
lm=$(grep -n '^    mutation-gate:' "$T/lefthook.yml" 2>/dev/null | head -1 | cut -d: -f1)
ls=$(grep -n 'sourcery-gate.sh {push_files}' "$T/lefthook.yml" 2>/dev/null | head -1 | cut -d: -f1)
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
c2=$(grep -c '^    mutation-gate:' "$T/lefthook.yml" 2>/dev/null || echo 0)
if [ "$c1" = "1" ] && [ "$c2" = "1" ] && [ -n "$lm" ] && [ -n "$ls" ] && [ "$lm" -gt "$ls" ] \
  && grep -q 'bash .lefthook/mutation-gate.sh {push_files}' "$T/lefthook.yml"; then
  ok "T11 job 注入锚位正确且幂等"
else
  bad "T11 c1=$c1 c2=$c2 lm=$lm ls=$ls"
fi

echo
echo "gates 测试: $PASS 通过, $FAIL 失败"
if [ "$FAIL" -eq 0 ]; then echo "✓ 全绿"; else echo "✗ 有失败"; fi
exit "$FAIL"
