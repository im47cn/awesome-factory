#!/usr/bin/env bash
# 变异测试红线（awesome-factory tools/git/gates 分发，由 lefthook pre-push 调用）
# 用法: bash .lefthook/mutation-gate.sh [--ci]
#   --ci  CI 全量档（v2 契约预留，未交付——fail-closed 拒绝执行而非假装跑过）
# 语言分派: 读仓根 .lefthook-profile 声明（gates-install.sh 安装时探测生成，人工可覆写；
#   多语言逗号分隔，含 java/ts 各走各分支，互不干扰）
#   java → PIT 红线（awesome-rules 原逻辑原样保留，行为契约不变）
#   ts   → StrykerJS 快速档: diff 驱动只变异本次变更文件、--incremental 增量、
#          预算封顶（默认 180s，MUTATION_GATE_BUDGET 可调）超时放行转提示（不绑架 push）
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
changed=$(git diff --name-only "$COMPARE"...HEAD 2>/dev/null)

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

# ── Java 分支: PIT 红线（awesome-rules 原逻辑保留） ─────────────────────────
if [ "$want_java" = 1 ]; then
  # 非阻断告警: 删除测试文件会削弱断言强度，本门禁不拦纯测试变更，
  # 弱化在下次生产代码变更时由 mutationThreshold 兜底吸收 —— 删除意图请走 MR/评审自行把关
  deleted_tests=$(git diff --name-only --diff-filter=D "$COMPARE"...HEAD 2>/dev/null | grep -E 'src/test/.*\.(java|kt)$' || true)
  if [ -n "$deleted_tests" ]; then
    echo "⚠ [mutation] 本次推送删除了测试文件(不拦截; 变异分由下次生产变更时阈值兜底):"
    # shellcheck disable=SC2086  # $deleted_tests 是换行分隔的文件清单，按空白分词逐条打印是意图
    printf '  - %s\n' $deleted_tests
  fi

  # 触发条件: 生产 java 代码变更（src/main/java）；纯测试/文档/配置变更直接跳过
  # shellcheck disable=SC2086  # $changed 是换行分隔的变更文件清单，按空白分词喂给 grep 过滤是意图
  prod=$(printf '%s\n' $changed | grep -E 'src/main/java/.*\.java$' || true)
  if [ -n "$prod" ]; then
    # 候选模块: 变更文件一级目录；根结构(src/main/java 直接在仓库根)映射为 "."
    # shellcheck disable=SC2086  # $prod 同上：src/main/java 清单按空白分词喂给 awk 提取模块名是意图
    mods=$(printf '%s\n' $prod | awk -F/ '{print ($1=="src") ? "." : $1}' | sort -u)
    for m in $mods; do
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
    done
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
      # shellcheck disable=SC2086  # $deleted_ts 换行清单按空白分词逐条打印是意图
      printf '  - %s\n' $deleted_ts
    fi

    # 触发条件: src/ 下生产 .ts/.tsx 变更（排除声明文件/测试/mock/脚本）
    # shellcheck disable=SC2086  # $changed 换行分隔清单按空白分词喂给 grep 过滤是意图
    prod_ts=$(printf '%s\n' $changed | grep -E '^src/.*\.tsx?$' \
      | grep -vE '(\.d\.ts|\.test\.|\.spec\.|/mock/|/scripts/)' || true)
    if [ -z "$prod_ts" ]; then
      echo "○ [mutation] ts: 无 src/ 生产 TS 代码变更, 跳过"
    else
      budget="${MUTATION_GATE_BUDGET:-180}"
      n=$(printf '%s\n' $prod_ts | wc -l | tr -d ' ')
      echo "▶ [mutation] ts: StrykerJS 快速档 ($n 个变更文件, 预算 ${budget}s, 阈值见 stryker.config.json thresholds.break)"
      MUT=()
      # shellcheck disable=SC2086  # $prod_ts 换行清单按空白分词展开为逐文件 --mutate 实参是意图
      for f in $prod_ts; do MUT+=(--mutate "$f"); done
      # --no-install: 本地未装 stryker 即失败（fail-closed 接入不完整），不静默联网装
      # 看门狗: 后台预算封顶, 超时 SIGTERM 放行（已知局限: stryker 子进程可能残留, 由其自身增量文件容错）
      # 注: 看门狗子壳必须整体重定向 stdout/stderr——否则它继承调用方(lefthook/测试)输出捕获
      # 管道的写端, gate 退出后孤儿 sleep 持管道不放, 捕获方阻塞到预算耗尽才见 EOF
      npx --no-install stryker run --incremental "${MUT[@]}" &
      s_pid=$!
      ( sleep "$budget"; kill "$s_pid" ) >/dev/null 2>&1 &
      w_pid=$!
      wait "$s_pid"; rc=$?
      kill "$w_pid" 2>/dev/null; wait "$w_pid" 2>/dev/null
      if [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ]; then
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

[ $fail -ne 0 ] && echo "✗ [mutation] 变异测试红线未通过"
exit $fail
