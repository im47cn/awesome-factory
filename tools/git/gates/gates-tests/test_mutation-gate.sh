#!/usr/bin/env bash
# gates 面确定性测试（周界 PR 验证门, Q10; 仿 awesome-rules tools/test_* 先例）
# 用法: bash tools/git/gates/gates-tests/test_mutation-gate.sh
# 覆盖: 语言分派跳过链(T1-T4/T7/T9) / --ci 契约位 fail-closed(T6) / 接入不完整
#       fail-closed(T5) / 外部 143 不放行·真看门狗放行·预算非法·纯删除·空白路径
#       (T5b-T5f) / PIT 模块循环 stub(T8) / gates-install 落地+对账+缺 lefthook 失败
#       (T10/T12) / lefthook job 段限幂等注入(T11/T13) / 软链逃逸拒绝(T14) /
#       子目录 --repo 归一(T15) / stryker 延迟就绪阶梯+旧装模板回收(T16-T17b) / lock prettier 中性(T18)
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

# T5b: 外部 SIGTERM(rc 143) → 不放行——仅"看门狗确证"的超时才放行, 同状态码不可信
printf '#!/usr/bin/env bash\nkill -TERM $$\n' > "$NPX/npx"
mkrepo; stage_ts; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead
out=$(cd "$T" && PATH="$NPX:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q '✗'; then ok "T5b 外部 143 → fail(非看门狗确证不放行)"; else bad "T5b (rc=$rc) $out"; fi

# T5c: 真看门狗超时 → 放行且进程树被收割; elapsed<10 是"孤儿 sleep 持捕获管道"的
# 确定性 oracle——kill_tree 失效时 $() 会阻塞到 sleep 30 退出才见 EOF
CHILDLOG="$NPX/child.pid"; export CHILDLOG
cat > "$NPX/npx" <<'STUB'
#!/usr/bin/env bash
sleep 30 &
echo $! > "$CHILDLOG"
wait $!
exit 143
STUB
chmod +x "$NPX/npx"
mkrepo; stage_ts; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead
t0=$(date +%s)
out=$(cd "$T" && PATH="$NPX:$PATH" MUTATION_GATE_BUDGET=1 bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
el=$(( $(date +%s) - t0 ))
child=$(cat "$CHILDLOG"); dead=0
for _ in 1 2 3 4 5; do kill -0 "$child" 2>/dev/null || { dead=1; break; }; sleep 0.3; done
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '放行' && [ "$el" -lt 10 ] && [ "$dead" -eq 1 ]; then
  ok "T5c 看门狗超时 → 放行+进程树收割(el=${el}s)"
else bad "T5c (rc=$rc el=${el}s dead=$dead) $out"; fi

# T5d: MUTATION_GATE_BUDGET 非法 → fail-closed(sleep 拒收非法值, 看门狗行为不可预期)
mkrepo; stage_ts; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"; ahead
out=$(cd "$T" && PATH="$NPX:$PATH" MUTATION_GATE_BUDGET=abc bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q 'MUTATION_GATE_BUDGET 非法'; then ok "T5d 预算非法 → fail"; else bad "T5d (rc=$rc) $out"; fi

# T5e: 纯删除推送(变更集仅含已删文件) → 无可变异目标, 跳过不绑架 push
mkrepo; stage_ts; mkdir -p "$T/src"; echo "export const a = 1" > "$T/src/foo.ts"
git -C "$T" add -A; git -C "$T" -c commit.gpgsign=false commit -qm base; git -C "$T" push -q origin HEAD
rm "$T/src/foo.ts"; ahead
out=$(cd "$T" && PATH="$NPX:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无可变异目标'; then ok "T5e 纯删除 → 跳过"; else bad "T5e (rc=$rc) $out"; fi
# T5f: 变更路径含空白 → --mutate 实参完整不被拆词
ARGLOG="$NPX/args.log"; export ARGLOG
cat > "$NPX/npx" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$ARGLOG"
exit 0
STUB
chmod +x "$NPX/npx"
mkrepo; stage_ts; mkdir -p "$T/src/my dir"; echo "export const a = 1" > "$T/src/my dir/a b.ts"; ahead
out=$(cd "$T" && PATH="$NPX:$PATH" bash "$GATES/mutation-gate.sh" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && grep -Fxq 'src/my dir/a b.ts' "$ARGLOG" && ! grep -Fxq 'src/my' "$ARGLOG"; then
  ok "T5f 空白路径 → 单实参完整传递"
else bad "T5f (rc=$rc) $(cat "$ARGLOG" 2>/dev/null | tr '\n' ' ')"; fi

# T6: --ci 契约位 → fail-closed 拒绝
mkrepo; run_gate --ci
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q '\-\-ci'; then ok "T6 --ci 未交付 → fail-closed"; else bad "T6 (rc=$rc) $out"; fi

# T7: java + 无 src/main/java 变更 → 跳过
mkrepo; printf 'java\n' > "$T/.lefthook-profile"; echo tweak >> "$T/README.md"; ahead; run_gate
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '无 src/main/java'; then ok "T7 java 无生产变更 → 跳过"; else bad "T7 (rc=$rc) $out"; fi

# T8: java + pitest 模块 + stub mvn → 通过/失败两态（模块循环不真跑 mvn, 只验分派与出口码）
STUB=$(mktemp -d "${TMPDIR:-/tmp}/gates-mvn.XXXXXX"); DIRS+=("$STUB")
# shellcheck disable=SC2016  # 故意单引号: ${MVN_RC:-0} 须原样写进桩脚本
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
cat > "$T/lefthook.yml" <<'YML'
pre-push:
  piped: true
  jobs:
    sourcery-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/sourcery-gate.sh {push_files}
YML
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
  && grep -q 'run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/mutation-gate.sh' "$T/lefthook.yml" \
  && ! grep -q 'mutation-gate.sh {push_files}' "$T/lefthook.yml"; then
  ok "T11 job 注入锚位正确且幂等(保留 || 删除防护极性, 去 {push_files} 首推不绑架)"
else
  bad "T11 c1=$c1 c2=$c2 lm=$lm ls=$ls"
fi

# T12: 无 lefthook.yml → install 仅告警放行; --check fail-closed(job 无处挂载)
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && printf '%s' "$out" | grep -q '无 lefthook.yml'; then ok "T12 install 无 lefthook.yml → 告警放行"; else bad "T12 (rc=$irc) $out"; fi
out=$(bash "$GATES/gates-install.sh" --repo "$T" --check 2>&1); crc=$?
if [ "$crc" -eq 1 ] && printf '%s' "$out" | grep -q 'lefthook.yml 缺席'; then ok "T12 --check 缺 lefthook.yml → 失败"; else bad "T12 (rc=$crc) $out"; fi

# T13: 段限注入——pre-commit 段同名 job 不算在位; 注入插 pre-push jobs 子段末尾,
# 不扰其后 2 空格属性行(parallel); 重复安装幂等
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
cat > "$T/lefthook.yml" <<'YML'
pre-commit:
  jobs:
    mutation-gate:
      run: echo wrong-hook
pre-push:
  jobs:
    sourcery-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/sourcery-gate.sh {push_files}
  parallel: true
YML
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
pc1=$(awk '/^pre-push:/{f=1;next} f&&/^[^ \t]/{f=0} f&&/^    mutation-gate:/{c++} END{print c+0}' "$T/lefthook.yml")
mg=$(grep -n '^    mutation-gate:' "$T/lefthook.yml" | tail -1 | cut -d: -f1)
pa=$(grep -n '^  parallel:' "$T/lefthook.yml" | head -1 | cut -d: -f1)
bash "$GATES/gates-install.sh" --repo "$T" >/dev/null 2>&1
pc2=$(awk '/^pre-push:/{f=1;next} f&&/^[^ \t]/{f=0} f&&/^    mutation-gate:/{c++} END{print c+0}' "$T/lefthook.yml")
bash "$GATES/gates-install.sh" --repo "$T" --check >/dev/null 2>&1; crc=$?
if [ "$pc1" = "1" ] && [ "$pc2" = "1" ] && [ -n "$mg" ] && [ -n "$pa" ] && [ "$mg" -lt "$pa" ] && [ "$crc" -eq 0 ]; then
  ok "T13 段限注入: pre-push 段内唯一, 插段末不扰后续属性, 幂等"
else bad "T13 pc1=$pc1 pc2=$pc2 mg=$mg pa=$pa crc=$crc"; fi

# T14: 目标路径含符号链接组件 → 拒绝写入(fail-closed), 仓外目录零写入
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
OUTSIDE=$(mktemp -d "${TMPDIR:-/tmp}/gates-out.XXXXXX"); DIRS+=("$OUTSIDE")
ln -s "$OUTSIDE" "$T/.lefthook"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 1 ] && printf '%s' "$out" | grep -q '符号链接' && [ -z "$(ls -A "$OUTSIDE")" ]; then
  ok "T14 .lefthook 软链 → 拒绝且仓外零写入"
else bad "T14 (rc=$irc) $out / outside: $(ls -A "$OUTSIDE")"; fi

# T15: --repo 传仓内子目录 → 归一到仓根, 仓级文件不落子目录
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
mkdir -p "$T/deep/sub"
out=$(bash "$GATES/gates-install.sh" --repo "$T/deep/sub" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && [ -f "$T/.lefthook/mutation-gate.sh" ] && [ -f "$T/.lefthook-profile" ] && [ ! -e "$T/deep/sub/.lefthook" ]; then
  ok "T15 子目录 --repo → 归一仓根落地"
else bad "T15 (rc=$irc) $out"; fi

# T16: 依赖就绪阶梯——vitest 基线缺 @stryker-mutator/* → stryker.config.json 延迟落地
# (配置在场+依赖缺席时 gate 对 src/ 生产推送 fail-closed, 首落地即埋拦截器)
mkrepo; printf '{\n  "compilerOptions": {}\n}\n' > "$T/tsconfig.json"
printf '{\n  "devDependencies": {\n    "vitest": "^1.0.0"\n  }\n}\n' > "$T/package.json"
cat > "$T/lefthook.yml" <<'YML'
pre-push:
  piped: true
  jobs:
    sourcery-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/sourcery-gate.sh {push_files}
YML
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && [ ! -e "$T/stryker.config.json" ] \
  && printf '%s' "$out" | grep -q '延迟落地' \
  && [ -f "$T/.lefthook/gates-lock.json" ]; then ok "T16 vitest 基线缺 stryker 依赖 → config 延迟落地"; else bad "T16 (rc=$irc) $out"; fi
out=$(bash "$GATES/gates-install.sh" --repo "$T" --check 2>&1); crc=$?
if [ "$crc" -eq 0 ] && printf '%s' "$out" | grep -q '延迟落地中'; then ok "T16 --check 延迟态 → 报告不拦截"; else bad "T16 --check (rc=$crc) $out"; fi

# T16b: 部分声明(装 core 缺 vitest-runner) → 仍延迟(防"配置落地了跑不起来")
printf '{\n  "devDependencies": {\n    "@stryker-mutator/core": "^8.0.0",\n    "vitest": "^1.0.0"\n  }\n}\n' > "$T/package.json"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && [ ! -e "$T/stryker.config.json" ] && printf '%s' "$out" | grep -q '延迟落地'; then ok "T16b 装核心缺 runner → 仍延迟"; else bad "T16b (rc=$irc) $out"; fi

# T16c: 依赖齐全但 0 测试文件 → 仍延迟(无测试基线时 stryker 必败)
printf '{\n  "devDependencies": {\n    "@stryker-mutator/core": "^8.0.0",\n    "@stryker-mutator/vitest-runner": "^8.0.0",\n    "vitest": "^1.0.0"\n  }\n}\n' > "$T/package.json"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && [ ! -e "$T/stryker.config.json" ] && printf '%s' "$out" | grep -q '延迟落地'; then ok "T16c 依赖齐但 0 测试文件 → 仍延迟"; else bad "T16c (rc=$irc) $out"; fi

# T17: 阶梯就绪(依赖齐 + 出现测试文件) → 延迟解除, config 正常首落地且入锁
mkdir -p "$T/src"; printf "import { test } from 'vitest'\n" > "$T/src/a.test.ts"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && cmp -s "$GATES/stryker.config.json" "$T/stryker.config.json" \
  && grep -q 'stryker.config.json' "$T/.lefthook/gates-lock.json"; then ok "T17 依赖+测试基线就绪 → config 落地入锁"; else bad "T17 (rc=$irc) $out"; fi

# T17b: 升级回收——依赖/测试基线退场后, 在场未修改模板被 install 回收; 仓侧定制保留
rm -f "$T/src/a.test.ts"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && [ ! -e "$T/stryker.config.json" ] && printf '%s' "$out" | grep -q '延迟回收'; then ok "T17b 模板在场+依赖退场 → install 回收"; else bad "T17b (rc=$irc) $out"; fi
cp "$GATES/stryker.config.json" "$T/stryker.config.json"
printf '\n' >> "$T/stryker.config.json"
out=$(bash "$GATES/gates-install.sh" --repo "$T" 2>&1); irc=$?
if [ "$irc" -eq 0 ] && [ -f "$T/stryker.config.json" ] && printf '%s' "$out" | grep -q '漂移'; then ok "T17b 仓侧定制(模板+尾空行) → 保留不回收"; else bad "T17b 定制 (rc=$irc) $out"; fi

# T18: lock prettier 中性格式: 2 空格缩进+尾换行(od 十六进制验尾字节, 命令替换会吞换行)
if grep -q '^  "anchor":' "$T/.lefthook/gates-lock.json" \
  && grep -q '^  "files":' "$T/.lefthook/gates-lock.json" \
  && [ "$(tail -c 1 "$T/.lefthook/gates-lock.json" | od -An -tx1 | tr -d ' \n')" = "0a" ]; then
  ok "T18 lock 格式 prettier 中性(2 空格+尾换行)"
else bad "T18 $(tr '\n' ' ' < "$T/.lefthook/gates-lock.json" | cut -c1-120)"; fi

echo
echo "gates 测试: $PASS 通过, $FAIL 失败"
if [ "$FAIL" -eq 0 ]; then echo "✓ 全绿"; else echo "✗ 有失败"; fi
exit "$FAIL"
