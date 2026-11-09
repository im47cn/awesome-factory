#!/usr/bin/env bash
# 变更行覆盖率红线（awesome-factory tools/git 分发，由 lefthook 调用）
# 用法: bash .lefthook/coverage.sh light   # pre-commit 轻检：复用已有 coverage 产物，不跑测试
#       bash .lefthook/coverage.sh full    # pre-push 兜底：跑 pytest --cov / vitest --coverage
# 红线（分层，与 steering/testing-standards.md「覆盖率阈值」同口径）:
#   变更行: java ≥98%、python/ts ≥90%（diff-cover 增量检查，light/full 双模式）
#   java 全量（存量+新增）: JaCoCo 报告级行/分支 ≥98%（full 模式执行，红线主门槛）
# 无测试基础设施/无产物时提示后放行（全量兜底在 full）
set -u
MODE="${1:-light}"
FAIL_UNDER_JAVA=98
FAIL_UNDER_PY=90
FAIL_UNDER_TS=90

REPO=$(git rev-parse --show-toplevel)
# cd 失败即终止（fail-closed）：后续 diff/产物收集全按相对路径，静默继续会整门假绿
cd "$REPO" || { echo "✗ [cov] 无法进入仓库根目录 ($REPO), 终止"; exit 1; }

# 变更集: light=staged; full=增量基线 @{push}(上次推送点)...HEAD —— 度量"本次推送新增行"，
# 回退 @{u}(上游)；均不可解析=首次推送，跳过。不回退主干：长命特性分支对 master 的全量
# diff 会把历史欠账算进每次推送，红线语义从"变更行"失真为"整分支"。
if [ "$MODE" = "light" ]; then
  changed=$(git diff --cached --name-only --diff-filter=ACMR)
else
  COMPARE=""
  for b in '@{push}' '@{u}'; do
    git rev-parse --verify -q "$b" >/dev/null 2>&1 && { COMPARE="$b"; break; }
  done
  [ -z "$COMPARE" ] && { echo "[cov] 跳过 (@{push}/@{u} 均不可解析, 疑首次推送)"; exit 0; }
  changed=$(git diff --name-only "$COMPARE...HEAD" 2>/dev/null || true)
fi
echo "$changed" | grep -qE '\.py$'       && HAS_PY=1 || HAS_PY=0
echo "$changed" | grep -qE '\.(ts|tsx)$' && HAS_TS=1 || HAS_TS=0
echo "$changed" | grep -qE '\.java$'     && HAS_JAVA=1 || HAS_JAVA=0
[ $((HAS_PY + HAS_TS + HAS_JAVA)) -eq 0 ] && { echo "[cov] 跳过 (变更集无 .py/.ts/.java)"; exit 0; }

# python 解释器探测: python3 → python → py -3 (Windows 常无 python3, Git Bash 下回退 py 启动器)
# pip --user 装出的 diff-cover.exe 在 Windows 落用户 Scripts 目录(多不在 PATH), 故一律以 -m 方式调用
if   command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v python  >/dev/null 2>&1; then PY=(python)
elif command -v py      >/dev/null 2>&1; then PY=(py -3)
else PY=(); fi

# diff-cover 入口: PATH → 当前 python 环境 → uv 按需拉取 → pip 装到用户目录后复用; 均失败才提示后放行
dc_pip_install() {
  [ ${#PY[@]} -eq 0 ] && return 1
  "${PY[@]}" -m pip install --user -q diff-cover >/dev/null 2>&1 && return 0
  # PEP 668 受管环境 (Homebrew/Debian 系 python) 二次尝试; Windows 无此限制, 走不到这
  "${PY[@]}" -m pip install --user -q --break-system-packages diff-cover >/dev/null 2>&1
}
dc() {
  if command -v diff-cover >/dev/null 2>&1; then diff-cover "$@"
  elif [ ${#PY[@]} -gt 0 ] && "${PY[@]}" -c 'import diff_cover' >/dev/null 2>&1; then "${PY[@]}" -m diff_cover.diff_cover_tool "$@"
  elif command -v uv >/dev/null 2>&1; then uv tool run diff-cover "$@"
  elif dc_pip_install; then
    echo "[cov] 已自动安装 diff-cover (pip --user)"
    "${PY[@]}" -m diff_cover.diff_cover_tool "$@"
  else echo "[cov] diff-cover 自动安装失败, 跳过 (可手动: pip install --user diff-cover / uv tool install diff-cover)"; return 0
  fi
}

# 版本序比较: ver_ge <a> <b> → 真 当 a≥b（按 . 分段整数比较，剥 -SNAPSHOT 类后缀；10# 防前导 0 被当八进制）
ver_ge() {
  local a1 a2 a3 b1 b2 b3
  IFS=. read -r a1 a2 a3 <<<"${1%%-*}"
  IFS=. read -r b1 b2 b3 <<<"${2%%-*}"
  a1=${a1:-0}; a2=${a2:-0}; a3=${a3:-0}; b1=${b1:-0}; b2=${b2:-0}; b3=${b3:-0}
  [ "$((10#$a1))" -ne "$((10#$b1))" ] && { [ "$((10#$a1))" -gt "$((10#$b1))" ]; return; }
  [ "$((10#$a2))" -ne "$((10#$b2))" ] && { [ "$((10#$a2))" -gt "$((10#$b2))" ]; return; }
  [ "$((10#$a3))" -ge "$((10#$b3))" ]
}

# 汇总各 jacoco.xml 报告级（report 直接子级）LINE/BRANCH 计数 → "missed_line covered_line missed_branch covered_branch"
# JaCoCo 生成器不写换行（整个报告是一行），且报告级 counter 在文档序最后（全部 </package> 之后）；
# 故逐文件取「文档序最后一个」LINE/BRANCH counter 即报告级值（属性序 type→missed→covered 固定，总量 0 不写出），
# 跨文件（多模块）累加；属性序/顺序已对真实 mvn 产物核实（wc -l = 0，与 python ElementTree 同值）
jacoco_totals() {
  awk '
    function upd(s,   kv) {
      split(s, kv, /"/)   # kv[2]=type kv[4]=missed kv[6]=covered
      if (kv[2] == "LINE") { lm = kv[4]; lc = kv[6] } else { bm = kv[4]; bc = kv[6] }
    }
    FILENAME != cur { tlm += lm; tlc += lc; tbm += bm; tbc += bc; lm = lc = bm = bc = 0; cur = FILENAME }
    {
      s = $0
      while (match(s, /<counter type="(LINE|BRANCH)" missed="[0-9]+" covered="[0-9]+"/)) {
        upd(substr(s, RSTART, RLENGTH)); s = substr(s, RSTART + RLENGTH)
      }
    }
    END { tlm += lm; tlc += lc; tbm += bm; tbc += bc; printf "%d %d %d %d\n", tlm+0, tlc+0, tbm+0, tbc+0 }
  ' "$@"
}

fail=0

# ---- python: . 或 backend/ 下有 pyproject.toml ----
if [ "$HAS_PY" = 1 ]; then
  py_dirs=$(for d in . backend; do [ -f "$d/pyproject.toml" ] && printf '%s ' "$d"; done)
  [ -n "$py_dirs" ] || echo "[cov] python: 跳过 (无 pyproject.toml)"
  for d in $py_dirs; do
    if [ "$MODE" = "light" ]; then
      [ -f "$d/coverage.xml" ] || { echo "[cov] $d 无 coverage.xml, 跳过轻检 (跑一次 pytest --cov 生成; 红线在 pre-push full)"; continue; }
      echo "[cov] pre-commit $d python staged 变更覆盖检查 (≥${FAIL_UNDER_PY}%)"
      out=$(cd "$d" && dc coverage.xml --compare-branch=HEAD --ignore-unstaged --fail-under="$FAIL_UNDER_PY")
      rc=$?
      printf '%s\n' "$out"
      if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q 'No lines with coverage information'; then
        echo "[cov] ⚠ 轻检放行语义: 变更行均未命中覆盖产物, 本次未实际检查任何行, 红线强制在 pre-push full"
      fi
      if [ "$rc" -ne 0 ]; then fail=1; fi
    else
      (
        cd "$d" || exit 1
        if [ -f uv.lock ] && command -v uv >/dev/null 2>&1; then
          PYCHK=(uv run python -c); PYTEST=(uv run pytest)
        else
          [ ${#PY[@]} -gt 0 ] || { echo "[cov] $d 无 python3/python/py, 跳过 python 覆盖"; exit 0; }
          PYCHK=("${PY[@]}" -c); PYTEST=("${PY[@]}" -m pytest)
        fi
        if ! "${PYCHK[@]}" 'import pytest, pytest_cov' >/dev/null 2>&1; then
          echo "[cov] $d 缺 pytest/pytest-cov, 跳过 python 覆盖 (uv add --dev pytest pytest-cov 启用)"; exit 0
        fi
        cov_target="."
        [ -d src ] && cov_target="src"
        echo "[cov] ▶ $d: pytest --cov=$cov_target + diff-cover (≥${FAIL_UNDER_PY}%)"
        "${PYTEST[@]}" --cov="$cov_target" --cov-report=xml:coverage.xml -q
        rc=$?
        [ $rc -eq 5 ] && { echo "[cov] $d 未收集到测试 (pytest rc=5), 跳过"; exit 0; }
        [ $rc -ne 0 ] && exit 1
        dc coverage.xml --compare-branch="$COMPARE" --fail-under="$FAIL_UNDER_PY"
      ) || fail=1
    fi
  done
fi

# ---- java: . 或 backend/ 下有 pom.xml (Maven + JaCoCo, diff-cover 原生读 jacoco.xml) ----
# 多模块: 收集根 + 一级子模块的 jacoco.xml 一并交给 diff-cover（多份 coverage 文件为位置参数），
# 避免多模块 reactor 下根目录无产物导致门禁静默失效
if [ "$HAS_JAVA" = 1 ]; then
  jv_dirs=$(for d in . backend; do [ -f "$d/pom.xml" ] && printf '%s ' "$d"; done)
  [ -n "$jv_dirs" ] || echo "[cov] java: 跳过 (无 pom.xml)"
  for d in $jv_dirs; do
    # mvnd (Maven Daemon) 优先，回退标准 mvn
    if command -v mvnd >/dev/null 2>&1; then MVN=(mvnd)
    elif command -v mvn >/dev/null 2>&1; then MVN=(mvn)
    else echo "[cov] $d 缺 mvn/mvnd, 跳过 java 覆盖"; continue
    fi
    if [ "$MODE" = "light" ]; then
      (
        cd "$d" || exit 0
        xmls=$(ls -- target/site/jacoco/jacoco.xml */target/site/jacoco/jacoco.xml 2>/dev/null || true)
        [ -n "$xmls" ] || { echo "[cov] 无 jacoco 产物, 跳过轻检 (跑一次 mvn test 生成; 红线在 pre-push full)"; exit 0; }
        echo "[cov] pre-commit $d java staged 变更覆盖检查 (≥${FAIL_UNDER_JAVA}%)"
        # shellcheck disable=SC2086  # $xmls 是空白分词的多份 jacoco.xml 清单，分词是意图
        out=$(dc $xmls --compare-branch=HEAD --ignore-unstaged --fail-under="$FAIL_UNDER_JAVA")
        rc=$?
        printf '%s\n' "$out"
        if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q 'No lines with coverage information'; then
          echo "[cov] ⚠ 轻检放行语义: 变更行均未命中覆盖产物, 本次未实际检查任何行, 红线强制在 pre-push full"
        fi
        exit "$rc"
      ) || fail=1
    else
      (
        cd "$d" || exit 1
        # pom 钉了 jacoco-maven-plugin 版本时, 无版本 CLI 坐标会复用 pom 声明的版本；
        # 「名为 Generated 的注解」过滤自 0.8.2 引入（简单名等值, #731）, 0.8.3 起放宽为子串匹配（#822）；
        # <0.8.2 生成代码会进分母使门禁数字失真 → 前提破坏直接拦
        jver=$(grep -A3 '<artifactId>jacoco-maven-plugin</artifactId>' pom.xml 2>/dev/null \
          | grep -o '<version>[^<]*</version>' | sed -n '1p' | sed 's/<[^>]*>//g')
        # 版本可为属性占位符 ${...}: 先解析同 pom 单层属性值; 仍非字面量则无法静态判定, 提示放行（不误拦合法项目）
        case "$jver" in
          *"\$"'{'*'}'*)
            prop=${jver#\$\{}; prop=${prop%\}}
            pval=$(grep -o "<$prop>[^<]*</$prop>" pom.xml 2>/dev/null | sed -n '1p' | sed 's/<[^>]*>//g')
            case "$pval" in *[!0-9.-]*) pval= ;; esac
            if [ -n "$pval" ]; then jver=$pval
            else
              echo "[cov] ⚠ $d jacoco 版本为属性引用 $jver, 无法静态解析, 跳过版本校验（确保运行时 ≥0.8.2）"
              jver=
            fi
            ;;
        esac
        case "$jver" in
          *[!0-9.-]*)
            echo "[cov] ⚠ $d jacoco 版本「${jver}」非数字字面量, 跳过版本校验（确保运行时 ≥0.8.2）"
            jver=
            ;;
        esac
        if [ -n "$jver" ] && ! ver_ge "$jver" 0.8.2; then
          echo "✗ [cov] $d jacoco-maven-plugin $jver < 0.8.2（Generated 注解过滤缺失, 生成代码会误入分母）, 升级后重试"
          exit 1
        fi
        echo "[cov] ▶ $d: mvn clean test + jacoco report + 全量红线(行/分支 ≥${FAIL_UNDER_JAVA}%) + diff-cover 变更行(≥${FAIL_UNDER_JAVA}%)"
        # 全限定插件三连: 无需 pom 预配 jacoco（prepare-agent 默认注入 argLine）
        # clean 防增量漏编: ff/切分支后 mtime 粒度内批量写文件, 增量编译可能漏编致旧 class 语义误拦（gateway 2026-09-05 实证: 脏 target 8 测试失败, clean 后 1212 全绿）
        "${MVN[@]}" -q clean org.jacoco:jacoco-maven-plugin:prepare-agent test org.jacoco:jacoco-maven-plugin:report
        rc=$?
        [ $rc -ne 0 ] && exit 1
        xmls=$(ls -- target/site/jacoco/jacoco.xml */target/site/jacoco/jacoco.xml 2>/dev/null || true)
        [ -n "$xmls" ] || { echo "[cov] $d 未生成任何 jacoco.xml, 跳过"; exit 0; }
        # 全量红线（存量+新增）: 汇总报告级 LINE/BRANCH 计数（生成代码剔除由 JaCoCo ≥0.8.2 注解过滤保证）
        # shellcheck disable=SC2086  # $xmls 同上：多份 jacoco.xml 按空白分词传入
        read -r lm lc bm bc <<<"$(jacoco_totals $xmls)"
        # 豁免预算（可选）: .lefthook/coverage-budget.env 提供 BUDGET_LINE_MISSED / BUDGET_BRANCH_MISSED
        # （纯数字）。语义: 报告级 missed 扣除预算后须仍达红线——预算是棘轮上限而非目标, 任何新增
        # 未覆盖分支/行即爆红; 预算逐项台账（类:行 + 不可达理由/债务 TODO, CR 审查口径）放
        # .lefthook/coverage-exemptions.md, 生效预算 = min(预算, 台账 LEDGER_TOTAL 申报行), 预算>0
        # 而台账缺失/无申报行 → 拒绝（fail-closed）。无预算文件则零预算。
        BUDGET_LINE_MISSED=0; BUDGET_BRANCH_MISSED=0  # 预置零: 阻断继承同名环境变量绕过
        budget_f="$d/.lefthook/coverage-budget.env"
        if [ -f "$budget_f" ]; then
          eval "$(grep -E '^(BUDGET_LINE_MISSED|BUDGET_BRANCH_MISSED)=[0-9]+$' "$budget_f")"
          # 前导零十进制化: 08/09 在算术展开按八进制解析会语法错误中止
          BUDGET_LINE_MISSED=$((10#${BUDGET_LINE_MISSED:-0})); BUDGET_BRANCH_MISSED=$((10#${BUDGET_BRANCH_MISSED:-0}))
          if [ "$BUDGET_LINE_MISSED" -gt 0 ] || [ "$BUDGET_BRANCH_MISSED" -gt 0 ]; then
            ledger_f="$d/.lefthook/coverage-exemptions.md"
            led=$(grep -m1 -E '^LEDGER_TOTAL: *line=[0-9]+ +branch=[0-9]+' "$ledger_f" 2>/dev/null || true)
            if [ -z "$led" ]; then
              echo "✗ [cov] $d 预算>0 但台账缺失或无 'LEDGER_TOTAL: line=N branch=M' 申报行（$ledger_f, fail-closed）"
              exit 1
            fi
            led_l=${led##*line=}; led_l=${led_l%% *}
            led_b=${led##*branch=}
            led_l=$((10#$led_l)); led_b=$((10#$led_b))
            [ "$led_l" -lt "$BUDGET_LINE_MISSED" ] && BUDGET_LINE_MISSED=$led_l
            [ "$led_b" -lt "$BUDGET_BRANCH_MISSED" ] && BUDGET_BRANCH_MISSED=$led_b
            echo "[cov] $d 豁免预算生效: 行 ≤$BUDGET_LINE_MISSED / 分支 ≤${BUDGET_BRANCH_MISSED}（min(申报, 台账 LEDGER_TOTAL 行$led_l/分支$led_b), 逐项条目归 CR 审查）"
          fi
        fi
        lme=$(( lm > BUDGET_LINE_MISSED ? lm - BUDGET_LINE_MISSED : 0 ))
        bme=$(( bm > BUDGET_BRANCH_MISSED ? bm - BUDGET_BRANCH_MISSED : 0 ))
        if [ $((lm + lc)) -eq 0 ]; then
          echo "[cov] $d jacoco 报告无 LINE 计数, 跳过全量红线"
        else
          lp=$(awk "BEGIN{printf \"%.1f\", 100*$lc/($lme+$lc)}")
          # shellcheck disable=SC2004  # 算术内 $ 前缀系既有写法（豁免类不改行），指令豁免以过分发面 lint 门
          if [ $((lc * 100)) -lt $((FAIL_UNDER_JAVA * ($lme + lc))) ]; then
            echo "✗ [cov] $d 全量行覆盖 ${lp}%（实 missed $lm, 预算扣除后 ${lme}）< ${FAIL_UNDER_JAVA}%（存量+新增红线, 补测或按排除实践豁免后重跑）"
            exit 1
          fi
          if [ $((bm + bc)) -gt 0 ]; then
            # 预算吃满 missed 且 covered=0 时分母为 0, awk 会打出空串——显式置 100.0
            if [ $((bme + bc)) -eq 0 ]; then bp="100.0"; else bp=$(awk "BEGIN{printf \"%.1f\", 100*$bc/($bme+$bc)}"); fi
            # shellcheck disable=SC2004  # 同上：$bme/$bc 算术内 $ 前缀豁免
            if [ $((bc * 100)) -lt $((FAIL_UNDER_JAVA * ($bme + bc))) ]; then
              echo "✗ [cov] $d 全量分支覆盖 ${bp}%（实 missed $bm, 预算扣除后 ${bme}）< ${FAIL_UNDER_JAVA}%（存量+新增红线）"
              exit 1
            fi
          else
            bp="n/a"
          fi
          echo "[cov] ✓ $d 全量行覆盖 ${lp}% / 分支 ${bp}% ≥ ${FAIL_UNDER_JAVA}%（原始 missed 行 ${lm}/分支 ${bm}；预算扣除 行-${BUDGET_LINE_MISSED:-0}/分支-${BUDGET_BRANCH_MISSED:-0}，% 为扣除后生效口径，原始数以台账为准）"
        fi
        # 增量补充检查（变更行）: 不替代上面的全量红线
        # shellcheck disable=SC2086  # $xmls 同上：多份 jacoco.xml 按空白分词传入
        dc $xmls --compare-branch="$COMPARE" --fail-under="$FAIL_UNDER_JAVA"
      ) || fail=1
    fi
  done
fi

# ---- node: . 或 frontend/ 下 package.json 声明 vitest ----
if [ "$HAS_TS" = 1 ]; then
  ts_dirs=$(for d in . frontend; do [ -f "$d/package.json" ] && grep -q '"vitest"' "$d/package.json" && printf '%s ' "$d"; done)
  [ -n "$ts_dirs" ] || echo "[cov] node: 跳过 (无声明 vitest 的 package.json)"
  for d in $ts_dirs; do
    if [ "$MODE" = "light" ]; then
      [ -f "$d/coverage/lcov.info" ] || { echo "[cov] $d 无 coverage/lcov.info, 跳过轻检 (跑一次 vitest --coverage 生成; 红线在 pre-push full)"; continue; }
      echo "[cov] pre-commit $d ts staged 变更覆盖检查 (≥${FAIL_UNDER_TS}%)"
      (cd "$d" && dc coverage/lcov.info --compare-branch=HEAD --ignore-unstaged --fail-under="$FAIL_UNDER_TS") || fail=1
    else
      (
        cd "$d" || exit 1
        echo "[cov] ▶ $d: vitest run --coverage + diff-cover (≥${FAIL_UNDER_TS}%)"
        # vitest 0 测试文件 exit 1 属既有 --passWithNoTests 语义（test:ci 同口径）非质量信号，
        # 与真实测试失败二分——否则无测试基建的仓全量红线必挂且无可达豁免路径
        if ! cov_out=$(npx vitest run --coverage 2>&1); then
          echo "$cov_out"
          if echo "$cov_out" | grep -q 'No test files found'; then
            echo "[cov] $d 0 测试文件, 跳过 diff-cover (对齐 test:ci --passWithNoTests 语义)"
            exit 0
          fi
          exit 1
        fi
        echo "$cov_out"
        # 无 lcov 二分定位：provider 缺失 vs reporter 缺 lcov（vitest≥5 默认 reporter 不含 lcov，
        # provider 已装照样空转——gtsp-wop-developer 实证：装了 coverage-v8 门禁仍静默放行）
        [ -f coverage/lcov.info ] || {
          if [ ! -e node_modules/@vitest/coverage-v8 ]; then
            echo "[cov] $d 缺 @vitest/coverage-v8, 跳过 diff-cover (启用: pnpm/npm add -D @vitest/coverage-v8)"
          else
            echo "[cov] $d provider 已装仍无 lcov: vitest≥5 默认 reporter 不含 lcov, 需在 vite.config.ts test.coverage.reporter 显式加 'lcov', 跳过 diff-cover"
          fi
          exit 0; }
        dc coverage/lcov.info --compare-branch="$COMPARE" --fail-under="$FAIL_UNDER_TS"
      ) || fail=1
    fi
  done
fi

[ $fail -ne 0 ] && echo "✗ [cov] 覆盖率红线未通过 (变更行: java ≥${FAIL_UNDER_JAVA}% / python・ts ≥${FAIL_UNDER_PY}%; java 全量行/分支 ≥${FAIL_UNDER_JAVA}%)"
exit $fail
