#!/usr/bin/env bash
# 变异测试红线（awesome-factory tools/git/gates 分发，由 lefthook pre-push 调用）
# 用法: bash .lefthook/mutation-gate.sh [--ci]
#   --ci  CI 全量档（v2 契约预留，未交付——fail-closed 拒绝执行而非假装跑过）
# 语言分派: 读仓根 .lefthook-profile 声明（gates-install.sh 安装时探测生成，人工可覆写；
#   多语言逗号分隔，含 java/ts 各走各分支，互不干扰）
#   java → PIT 红线（awesome-rules 迁入; PIT 执行契约不变, 模块判定升级为变更文件
#          全路径向上最近 pom.xml——修复嵌套 Maven 模块漏检）
#   ts   → StrykerJS 快速档: diff 驱动只变异本次变更文件、--incremental 增量、
#          预算封顶（默认 180s，MUTATION_GATE_BUDGET 可调, 须正整数秒）；
#          仅"看门狗确证"的超时放行转提示（不绑架 push），外部终止/OOM 同状态码不放行
# 红线: java PIT 变异分 ≥ pom <mutationThreshold>; ts 变异分 ≥ stryker.config.json
#       thresholds.break（模板默认 50，仓可调）
# 语义: 只约束生产代码变更——纯测试/文档/脚本变更跳过；无 stryker 配置或无
#       vitest/jest 测试基线的仓提示后跳过（Q6-C 语义，不绑架无基线仓）
# 命名消歧: 本件是 StrykerJS/PIT 源码变异测试门禁; .factory/mutations/ 是工厂验门
#       灵敏度的缺陷注入体系——同名异物（见 gates.json description）
set -u

REPO=$(git rev-parse --show-toplevel)
# cd 失败即终止（fail-closed）：后续 diff/文件收集全按相对路径，静默继续会整门假绿
cd "$REPO" || { echo "✗ [mutation] 无法进入仓库根目录 ($REPO), 终止"; exit 1; }

# --ci 契约预留: CI 全量档 v2 未交付，fail-closed（有仓先接 CI 也不允许静默空跑）
if [ "${1:-}" = "--ci" ]; then
  echo "✗ [mutation] --ci 全量档为 v2 契约预留，未交付; 拒绝执行 (fail-closed)"
  exit 1
fi

# 增量基线: @{push}(上次推送点)...HEAD，回退 @{u}(上游)；均不可解析=首次推送，跳过
# （与 coverage.sh full 模式同口径：度量"本次推送"，不回退主干避免长命分支欠账失真）
COMPARE=""
for b in '@{push}' '@{u}'; do
  git rev-parse --verify -q "$b" >/dev/null 2>&1 && { COMPARE="$b"; break; }
done
[ -z "$COMPARE" ] && { echo "○ [mutation] 无推送基线(首次推送), 跳过"; exit 0; }
# fail-closed: 范围算不出时 changed 会静默为空 → 语言分支误报"无生产变更"放行
if ! changed=$(git diff --name-only "$COMPARE"...HEAD 2>&1); then
  echo "✗ [mutation] 无法计算变更范围 ($COMPARE...HEAD): $changed"
  exit 1
fi

# 语言声明: 缺席即跳过（运行时只读声明，判定收口在安装时——Q2 裁定）
PROFILE=""
[ -f .lefthook-profile ] && PROFILE=$(head -1 .lefthook-profile | tr -d '[:space:]')
if [ -z "$PROFILE" ]; then
  echo "○ [mutation] 无 .lefthook-profile 语言声明, 跳过 (接入: bash <awesome-factory>/tools/git/gates/gates-install.sh --repo .)"
  exit 0
fi

want_java=0; want_ts=0
case ",$PROFILE," in *,java,*) want_java=1 ;; esac
case ",$PROFILE," in *,ts,*)   want_ts=1   ;; esac
if [ "$want_java" = 0 ] && [ "$want_ts" = 0 ]; then
  echo "○ [mutation] profile($PROFILE) 不含 java/ts, 跳过"
  exit 0
fi

fail=0

# 递归 SIGTERM 进程树（先子后父）: 看门狗超时收割 stryker 全树——防残留子进程与
# 后续运行竞争共享增量/临时文件（macOS/Linux 均自带 pgrep）
kill_tree() {
  local c
  # shellcheck disable=SC2046  # pgrep 输出为纯 pid 清单, 按词展开是意图
  for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done
  kill "$1" 2>/dev/null
}

# ── Java 分支: PIT 红线（PIT 执行契约自 awesome-rules 原样保留） ──────────────
if [ "$want_java" = 1 ]; then
  # 非阻断告警: 删除测试文件会削弱断言强度，本门禁不拦纯测试变更，
  # 弱化在下次生产代码变更时由 mutationThreshold 兜底吸收 —— 删除意图请走 MR/评审自行把关
  deleted_tests=$(git diff --name-only --diff-filter=D "$COMPARE"...HEAD 2>/dev/null | grep -E 'src/test/.*\.(java|kt)$' || true)
  if [ -n "$deleted_tests" ]; then
    echo "⚠ [mutation] 本次推送删除了测试文件(不拦截; 变异分由下次生产变更时阈值兜底):"
    while IFS= read -r f; do printf '  - %s\n' "$f"; done <<<"$deleted_tests"
  fi

  # 触发条件: 生产 java 代码变更（src/main/java）；纯测试/文档/配置变更直接跳过
  # 清单全程 quoted 展开 + while read 按行处理——路径含空白不被拆词
  prod=$(printf '%s\n' "$changed" | grep -E 'src/main/java/.*\.java$' || true)
  if [ -n "$prod" ]; then
    # 候选模块: 自变更文件全路径向上找最近 pom.xml（嵌套 Maven 模块命中子模块而非
    # 一级目录误判）; 根结构(src/main/java 直接在仓库根)或全程无 pom → "."
    mods=""
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      dir=$(dirname "$f"); m=""
      while :; do
        [ -f "$dir/pom.xml" ] && { m="$dir"; break; }
        [ "$dir" = "." ] && break
        dir=$(dirname "$dir")
      done
      mods="${mods:+$mods$'\n'}${m:-.}"
    done <<<"$prod"
    mods=$(printf '%s\n' "$mods" | sort -u)
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      pom="pom.xml"; [ "$m" != "." ] && pom="$m/pom.xml"
      if [ ! -f "$pom" ] || ! grep -q 'pitest-maven' "$pom"; then
        echo "○ [mutation] $m 未配置 pitest, 跳过 (接入: pom 加 pitest-maven + mutationThreshold)"
        continue
      fi
      PL=(); [ "$m" != "." ] && PL=(-pl "$m")
      echo "▶ [mutation] $m: PIT mutationCoverage (阈值见 pom mutationThreshold)"
      # 单模块跑 PIT 时 minion 从本地仓库取兄弟模块 jar —— 先静默 clean install 依赖(跳测试):
      # clean 防增量编译陈旧 class(2026-09-06 实证: 残留产物致 lambda$new$0 假红), 变异体须基于当前源码
      mvn -B -q ${PL[@]+"${PL[@]}"} -am clean install -DskipTests >/dev/null 2>&1 || true
      if mvn -B ${PL[@]+"${PL[@]}"} org.pitest:pitest-maven:mutationCoverage; then
        echo "✓ [mutation] $m 变异分达标"
      else
        echo "✗ [mutation] $m 变异分低于阈值或执行失败 (报告: target/pit-reports)"
        fail=1
      fi
    done <<<"$mods"
  else
    echo "○ [mutation] 无 src/main/java 生产代码变更, 跳过"
  fi
fi

# ── TS 分支: StrykerJS 快速档（diff 驱动 + 预算封顶） ───────────────────────
if [ "$want_ts" = 1 ]; then
  if [ ! -f stryker.config.json ]; then
    echo "○ [mutation] ts: 无 stryker.config.json, 跳过 (接入: gates-install.sh 落模板 + devDependencies 装 @stryker-mutator/core 与 vitest-runner)"
  elif ! grep -qE '"(vitest|jest)"' package.json 2>/dev/null; then
    echo "○ [mutation] ts: 无 vitest/jest 测试基线, 跳过 (先建测试基线, 门禁自动生效)"
  else
    # 非阻断告警: 删除 TS 测试文件（与 Java 分支同语义，阈值兜底吸收）
    deleted_ts=$(git diff --name-only --diff-filter=D "$COMPARE"...HEAD 2>/dev/null | grep -E '(\.test|\.spec)\.(ts|tsx|js|mjs)$' || true)
    if [ -n "$deleted_ts" ]; then
      echo "⚠ [mutation] ts: 本次推送删除了测试文件(不拦截; 变异分由下次生产变更时阈值兜底):"
      while IFS= read -r f; do printf '  - %s\n' "$f"; done <<<"$deleted_ts"
    fi

    # 触发条件: src/ 下生产 .ts/.tsx 变更（排除声明文件/测试/mock/脚本）; 按行处理防空白路径拆词
    prod_ts=$(printf '%s\n' "$changed" | grep -E '^src/.*\.tsx?$' \
      | grep -vE '(\.d\.ts|\.test\.|\.spec\.|/mock/|/scripts/)' || true)
    if [ -z "$prod_ts" ]; then
      echo "○ [mutation] ts: 无 src/ 生产 TS 代码变更, 跳过"
    else
      # 变异目标只收工作树存续文件: 纯删除推送把已删路径喂给 stryker 会因目标缺失报错绑架 push
      MUT=()
      while IFS= read -r f; do
        [ -n "$f" ] && [ -f "$f" ] && MUT+=(--mutate "$f")
      done <<<"$prod_ts"
      if [ ${#MUT[@]} -eq 0 ]; then
        echo "○ [mutation] ts: 生产 TS 变更仅含已删除文件(无可变异目标), 跳过"
      elif ! printf '%s' "${MUTATION_GATE_BUDGET:-180}" | grep -qE '^[1-9][0-9]*$'; then
        # 预算先验: 非法值会被 sleep 拒收, 看门狗行为不可预期(早杀或永不杀)——不进该路径
        echo "✗ [mutation] ts: MUTATION_GATE_BUDGET 非法(须为正整数秒): '${MUTATION_GATE_BUDGET:-180}'"
        fail=1
      else
        budget="${MUTATION_GATE_BUDGET:-180}"
        n=$(( ${#MUT[@]} / 2 ))
        echo "▶ [mutation] ts: StrykerJS 快速档 ($n 个变更文件, 预算 ${budget}s, 阈值见 stryker.config.json thresholds.break)"
        # --no-install: 本地未装 stryker 即失败（fail-closed 接入不完整），不静默联网装
        # 看门狗三防(确定性极性):
        #   1) 子壳整体重定向——否则继承调用方(lefthook/测试)捕获管道写端, gate 退出后
        #      孤儿 sleep 持管道不放, 捕获方阻塞到预算耗尽才见 EOF
        #   2) sleep 成功走完才写 flag 并 kill_tree 先子后父收割全树; 主壳见 flag 只
        #      wait 让收割收完——真超时确定性放行
        #   3) sleep 被主壳 pkill 打杀则 && 短路, flag 永不落盘——外部终止/OOM 同状态码
        #      (143/137) 确定性不放行; 放行只认"flag 在 + rc 143/137"双证
        flag=$(mktemp "${TMPDIR:-/tmp}/mgate-flag.XXXXXX"); [ -n "$flag" ] && rm -f "$flag"
        npx --no-install stryker run --incremental "${MUT[@]}" &
        s_pid=$!
        ( sleep "$budget" && { : > "$flag"; kill_tree "$s_pid"; } ) >/dev/null 2>&1 &
        w_pid=$!
        wait "$s_pid"; rc=$?
        # 看门狗组清理: 先打杀其 sleep 子进程(防孤儿 sleep 存活到预算耗尽);
        # flag 已落盘说明 sleep 自然走完 → 只 wait 让 kill_tree 收割完, 不打断
        pkill -TERM -P "$w_pid" 2>/dev/null
        if [ -f "$flag" ]; then wait "$w_pid" 2>/dev/null; else kill "$w_pid" 2>/dev/null; wait "$w_pid" 2>/dev/null; fi
        timed=0; [ -n "$flag" ] && [ -f "$flag" ] && timed=1
        [ -n "$flag" ] && rm -f "$flag"
        if [ "$timed" = 1 ] && { [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ]; }; then
          echo "○ [mutation] ts: 预算 ${budget}s 封顶, 放行 (增量缓存已留存下次续跑; 全量兜底待 CI 档 v2)"
        elif [ "$rc" -eq 0 ]; then
          echo "✓ [mutation] ts: 变异分达标"
        else
          echo "✗ [mutation] ts: 变异分低于阈值或执行失败 (报告: reports/mutation/)"
          fail=1
        fi
      fi
    fi
  fi
fi

[ $fail -ne 0 ] && echo "✗ [mutation] 变异测试红线未通过"
exit $fail
