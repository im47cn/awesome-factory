#!/usr/bin/env bash
# gates-install.sh — 门禁专用分发面 slim 脚本（install / --check）
# 真相源: awesome-factory tools/git/gates/（gates.json 清单）
# 三态语义复刻 .factory/sync-from-upstream.sh 哲学，但通道完全独立、互不相交：
#   full  零本地化 blob 覆盖（漂移 = --check 失败 = 门禁失败）
#   local 首落地后归消费仓所有（上游只报告不覆盖，仓侧定制属预期）
# 附带:
#   ① .lefthook-profile 语言声明——缺席时探测生成（pom.xml→java,
#      pyproject.toml→python, tsconfig.json→ts；多语言逗号分隔），
#      已存在不覆盖（人工覆写优先）
#   ② lefthook.yml job 幂等注入（检测/注入均限定对应 hook 的 jobs/commands 段内;
#      python3 行级手术 + 回读校验, 失败不报成功）:
#      - pre-push mutation-gate job
#      - pre-commit comment-gate job——Java 注释棘轮; baseline 缺席=未武装放行
#      - post-merge comment-sync job——comment-baseline.json 是全树重算单 blob,
#        git 无法内容合并, merge 后必漂移; 本 job 用 comment-gate.py sync 检漂即
#        重算落盘（仓未武装 baseline 时 sync 自身 not-armed exit 0, 无害）
#   ③ .lefthook/gates-lock.json 锚点（上游 HEAD-9 + 落地文件 sha256）
# 用法: bash gates-install.sh [--repo <path>] [--check]
#   --repo  目标仓（默认当前目录所属仓）
#   --check 只对账不落盘：full 漂移/缺失、lefthook.yml 或 pre-push job 缺席 exit 1；
#          local/profile 缺席仅报告
set -u

SELF_DIR=$(cd "$(dirname "$0")" && pwd)   # <factory>/tools/git/gates
UP=$(cd "$SELF_DIR/../../.." && pwd)      # awesome-factory 仓根（gates→git→tools 三层上溯; 清单 src 相对其解析）
MANIFEST="$SELF_DIR/gates.json"

die() { echo "✗ [gates] $*" >&2; exit 1; }

MODE="install"; REPO_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)  REPO_ARG="${2?--repo 需要参数}"; shift 2 ;;
    --check) MODE="check"; shift ;;
    *) die "未知参数: $1" ;;
  esac
done

REPO="$REPO_ARG"
if [ -z "$REPO" ]; then
  REPO=$(git rev-parse --show-toplevel 2>/dev/null)
  [ -n "$REPO" ] || die "无法定位目标仓（cd 到目标仓或传 --repo <path>）"
fi
# --repo 允许传仓内任意子目录: 归一到工作树根, 仓级文件(.lefthook/lefthook.yml)必须落根
REPO=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) || die "${REPO_ARG:-当前目录} 不是 git 工作仓"
[ -f "$MANIFEST" ] || die "清单缺失: $MANIFEST"
command -v python3 >/dev/null 2>&1 || die "需要 python3（解析 gates.json / 写 lock）"

ANCHOR=$(git -C "$UP" rev-parse --short=9 HEAD 2>/dev/null || echo unknown)

sha() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1; fi
}

# 防符号链接逃逸: 目标路径任一已存组件(含末端)是符号链接即拒绝写入——消费仓可用
# .lefthook → /仓外 软链劫持安装器沿链覆盖仓外文件（cp 与 open(...,"w") 均跟随软链）
guard_dest() { # $1 = 目标绝对路径(须位于 REPO 之下)
  case "$1" in
    "$REPO"/*) rel=${1#"$REPO"/} ;;
    *) die "目标路径越出目标仓: $1" ;;
  esac
  cur="$REPO"
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    cur="$cur/$c"
    [ -L "$cur" ] && die "拒绝写入符号链接组件: ${cur}（防仓外写入劫持; 仓内软链请改实体路径）"
  done < <(printf '%s\n' "$rel" | tr '/' '\n')
}

list_face() { # $1=full|local → 每行 "src dst"
  python3 - "$MANIFEST" "$1" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
for e in m.get(sys.argv[2], []):
    print(e["src"], e["dst"])
PY
}

LOCK_DST=$(python3 - "$MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print(m.get("anchor", {}).get("lock", ".lefthook/gates-lock.json"))
PY
)

detect_profile() { # $1=仓目录 → stdout: java[,python][,ts] 或空
  d="$1"; out=""
  [ -f "$d/pom.xml" ]        && out="java"
  [ -f "$d/pyproject.toml" ] && out="${out:+$out,}python"
  [ -f "$d/tsconfig.json" ]  && out="${out:+$out,}ts"
  printf '%s' "$out"
}

# stryker.config.json 延迟落地判据(依赖未就绪不埋 fail-closed 拦截器)。就绪 =
# package.json 键级依赖齐全(vitest/jest 基线 + @stryker-mutator/core + 对应
# *-runner; python3 解析, 防 scripts 文本误判与"装 core 缺 runner"的部分声明)
# 且已有测试文件(无测试基线时 stryker 必败, 落地即拦生产推送)。无 package.json
# 不延迟(gate 运行时自会跳过, 模板即接入文档); python3 缺席/JSON 非法一律延迟
# ——判据只会多延迟不会漏拦, 保守向安全。与 mutation-gate.sh 探测同源但更严。
stryker_defer() { # $1=仓目录 → 0=延迟落地 / 1=就绪可落地
  [ -f "$1/package.json" ] || return 1
  python3 - "$1" <<'PY' 2>/dev/null && return 1
import json, os, sys
repo = sys.argv[1]
try:
    pkg = json.load(open(os.path.join(repo, "package.json")))
except Exception:
    sys.exit(1)  # JSON 非法 → 延迟(保守)
deps = {}
for k in ("dependencies", "devDependencies", "optionalDependencies"):
    d = pkg.get(k) if isinstance(pkg, dict) else None
    if isinstance(d, dict):
        deps.update(d)
runners = [r for r in ("vitest", "jest") if r in deps]
if not runners:
    sys.exit(1)  # 无测试基线依赖键 → 延迟
need = ["@stryker-mutator/core"] + ["@stryker-mutator/%s-runner" % r for r in runners]
if any(p not in deps for p in need):
    sys.exit(1)  # core/runner 缺任一 → 延迟(部分声明跑不起来)
exts = (".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".test.mjs", ".test.cjs",
        ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx", ".spec.mjs", ".spec.cjs")
for root, dirs, files in os.walk(repo):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", "build")]
    if any(f.endswith(exts) for f in files):
        sys.exit(0)  # 就绪
sys.exit(1)  # 0 测试文件 → 延迟
PY
  return 0
}

fail=0

# ── ① 语言声明 ──────────────────────────────────────────────────────────────
if [ -f "$REPO/.lefthook-profile" ]; then
  echo "✔ [gates] .lefthook-profile 已存在, 保留（人工覆写优先）: $(head -1 "$REPO/.lefthook-profile")"
elif [ "$MODE" = install ]; then
  prof=$(detect_profile "$REPO")
  if [ -n "$prof" ]; then
    guard_dest "$REPO/.lefthook-profile"
    printf '%s\n' "$prof" > "$REPO/.lefthook-profile" || die "写入失败: .lefthook-profile"
    echo "✔ [gates] .lefthook-profile 探测生成: ${prof}（人工可覆写）"
  else
    echo "⚠ [gates] 未探测到语言面（无 pom.xml/pyproject.toml/tsconfig.json）; 请人工创建 .lefthook-profile (java|python|ts, 多语言逗号分隔)"
  fi
else
  echo "⚠ [gates] .lefthook-profile 缺席（--check 仅报告; install 可探测生成）"
fi

# ── ② full 面: 零本地化 blob 覆盖 / 漂移即失败 ──────────────────────────────
while read -r src dst; do
  [ -z "$src" ] && continue
  s="$UP/$src"; d="$REPO/$dst"
  [ -f "$s" ] || die "上游缺失: $src"
  if [ "$MODE" = install ]; then
    guard_dest "$d"
    mkdir -p "$(dirname "$d")" || die "创建目录失败: $(dirname "$d")"
    cp "$s" "$d" || die "写入失败: $dst"
    echo "✔ [gates] full: $src → $dst (anchor=$ANCHOR)"
  else
    if [ ! -f "$d" ]; then
      echo "✗ [gates] full 缺失: $dst"; fail=1
    elif [ "$(sha "$s")" != "$(sha "$d")" ]; then
      echo "✗ [gates] full 漂移: $dst ≠ 上游 ${src}（full 面零本地化, 修正法: 重跑 install 覆盖）"; fail=1
    else
      echo "✔ [gates] full 一致: $dst"
    fi
  fi
done < <(list_face full)

# ── ③ local 面: 首落地, 此后上游只报告不覆盖 ────────────────────────────────
while read -r src dst; do
  [ -z "$src" ] && continue
  s="$UP/$src"; d="$REPO/$dst"
  [ -f "$s" ] || die "上游缺失: $src"
  if [ "$MODE" = install ]; then
    if [ ! -f "$d" ]; then
      if [ "$dst" = stryker.config.json ] && stryker_defer "$REPO"; then
        echo "○ [gates] local 延迟落地: ${dst}——依赖/测试基线未就绪（runner 齐备且有测试文件前, gate 对 src/ 生产推送 fail-closed, 落地即埋拦截器）; 就绪后重跑 install 落地"
        continue
      fi
      guard_dest "$d"
      mkdir -p "$(dirname "$d")" || die "创建目录失败: $(dirname "$d")"
      cp "$s" "$d" || die "写入失败: $dst"
      echo "✔ [gates] local 首落地: $src → ${dst}（此后归仓所有, 上游只报告不覆盖）"
    elif [ "$(sha "$s")" != "$(sha "$d")" ]; then
      echo "○ [gates] local 漂移(不覆盖, 仅报告): $dst 与模板有差异——仓侧定制属预期"
    elif [ "$dst" = stryker.config.json ] && stryker_defer "$REPO"; then
      rm -f "$d" || die "回收失败: $dst"
      echo "○ [gates] local 延迟回收: ${dst}——旧装落地仍为未修改模板, 依赖/测试基线未就绪时在场即埋拦截器; 已回收, 就绪后重跑 install 落地（仓侧定制不受影响）"
    else
      echo "✔ [gates] local 一致: $dst"
    fi
  else
    if [ -f "$d" ]; then
      if [ "$dst" = stryker.config.json ] && [ "$(sha "$s")" = "$(sha "$d")" ] && stryker_defer "$REPO"; then
        echo "○ [gates] local 延迟条件成立: ${dst} 仍为未修改模板——重跑 install 回收（就绪则保留启用）"
      else echo "✔ [gates] local 已落地: $dst"; fi
    elif [ "$dst" = stryker.config.json ] && stryker_defer "$REPO"; then
      echo "○ [gates] local 延迟落地中: ${dst}——依赖/测试基线未就绪（就绪后重跑 install 落地）"
    else echo "○ [gates] local 未落地: ${dst}（install 时首落地）"; fi
  fi
done < <(list_face local)

# ── ④ lefthook.yml job 幂等注入（pre-push mutation-gate + pre-commit comment-gate + post-merge comment-sync; 检测/注入均限定对应 hook 段） ──
# run 链语义: awesome-factory 删除防护契约——纯删除推送 exit 0 短路跳过门禁(sourcery/
# coderabbit job 同款既定语义), 正常推送/读取异常 exit 1 门禁照跑, 故 || 链在正常推送
# 必然落 mutation-gate; 不用 {push_files}: 新分支首推无 @{push} 引用时 lefthook 展开
# 即失败绑架首推, 且 mutation-gate.sh 自算 diff 基线无需文件清单实参
# ⚠️ 组装者警示（gtsp-wop-portal 2026-11-01 实证）: 本注入块门禁逻辑自洽——
# mutation-gate.sh 自算 diff 基线、不消费推送引用 stdin；但 piped:true 消费仓链中
# use_stdin 只有链首命令真正收到 Git push stdin：若本 job 非链首，其守卫读空输入
# fail-open，纯删除短路失效、门禁在纯删除时空跑（逻辑仍正确，只是白跑或可能失败）。
# 消费仓自行组装依赖推送引用 stdin 的门（如 coverage-full）更会拿空输入——勿照搬
# 「守卫||门禁」结构，应改非 piped + 逐命令守卫 + 该门「读一次 stdin 分别喂守卫与
# 门禁」的包装（先例: portal chore/gates-align-docs 分支）。
JOB_BLOCK='    mutation-gate:
      use_stdin: true
      # 删除防护极性契约: guard 0=纯删除推送→短路跳过门禁(awesome-factory 既定语义);
      # guard 1=正常推送/读取异常(fail-open)→|| 落本门禁, 退出码即 job 退出码
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/mutation-gate.sh'

PM_BLOCK='    comment-sync:
      run: python3 .lefthook/comment-gate.py sync --repo . --baseline .lefthook/comment-baseline.json'

# 棘轮语义: .lefthook/comment-baseline.json 缺席 = 未武装, gate 放行（防 17 仓装门即拦）;
# 基线生成/收缩见 awesome-rules skills/delete-comment。基线是仓侧数据, 不进本分发面。
# {staged_files}: 非本仓改动恒 skip; 本仓未 piped、不收 stdin, 不涉链首 stdin 限制。
JOB_BLOCK_C='    comment-gate:
      run: python3 .lefthook/comment-gate.py check --repo . --baseline .lefthook/comment-baseline.json {staged_files}'

# python3 行级手术(不引入 yaml 依赖): 只认对应 hook 的 jobs|commands 段内同名 job——挂在
# 其他 hook 下的同名 job 不算在位; 注入插到子段末尾并回读校验, 失败不报成功
# exit 0=已在位或注入成功 / 1=check 发现缺席 / 3=注入失败
lw_job() { # $1=lefthook.yml $2=check|inject $3=hook名 $4=job名 $5=hook缺席时插入的子段头 $6=job_block
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PY'
import os, re, sys, tempfile
path, mode, hook, job, subhead, block = sys.argv[1:7]
TOP = re.compile(r"^[^\s#]")
BLOCK = block.splitlines()
WANT = [l.rstrip() for l in BLOCK if l.strip()]  # 归一化后期望块(容忍空白差异)
with open(path) as f:
    lines = f.read().splitlines()

def sub_bound(ls, j, e):
    """jobs/commands 子段末: j 之后首个缩进≤2 的非空行索引, 无则 e"""
    for i in range(j + 1, e):
        l = ls[i]
        if l.strip() and len(l) - len(l.lstrip()) <= 2:
            return i
    return e

def hook_span(ls):
    """hook 段 [s, e): 标题行容行内注释; e = 下个顶层键"""
    s = next((i for i, l in enumerate(ls) if l.split("#", 1)[0].rstrip() == hook + ":"), None)
    if s is None:
        return None, None
    e = next((i for i in range(s + 1, len(ls)) if TOP.match(ls[i])), len(ls))
    return s, e

def find_job(ls):
    """hook 的 jobs|commands 子段内 '    <job>:' 行号(0 基), 无则 -1"""
    s, e = hook_span(ls)
    if s is None:
        return -1
    j = next((i for i in range(s + 1, e) if ls[i].rstrip() in ("  jobs:", "  commands:")), -1)
    if j < 0:
        return -1
    return next((i for i in range(j + 1, sub_bound(ls, j, e)) if ls[i].startswith("    " + job + ":")), -1)

def region_end(ls, i):
    """job 块末(不含): i 之后首个缩进≤4 的非空行"""
    for k in range(i + 1, len(ls)):
        l = ls[k]
        if l.strip() and len(l) - len(l.lstrip()) <= 4:
            return k
    return len(ls)

def have_block(ls):
    """(job 行号, 内容与期望块是否全等): 名不在位 → (-1, False)"""
    i = find_job(ls)
    if i < 0:
        return -1, False
    got = [l.rstrip() for l in ls[i:region_end(ls, i)] if l.strip()]
    return i, got == WANT

i, exact = have_block(lines)
if exact:
    print(f"✔ [gates] lefthook.yml {hook} {job} job 已在位（幂等跳过）")
    sys.exit(0)
if mode == "check":
    sys.exit(1)

if i >= 0:
    # job 名在位但内容漂移: 原位整块替换, 不叠加第二份
    lines[i:region_end(lines, i)] = BLOCK
else:
    s, e = hook_span(lines)
    if s is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [hook + ":", subhead] + BLOCK
    else:
        j = next((i for i in range(s + 1, e) if lines[i].rstrip() in ("  jobs:", "  commands:")), -1)
        if j < 0:
            # hook 无 jobs/commands 子段: 紧随其后插入子段头(其余 2 空格属性行后移仍合法)
            lines[s + 1:s + 1] = [subhead] + BLOCK
        else:
            # 插到子段末尾: 边界止于首个缩进≤2 的属性行, 不把 job 挂进其后的 skip/tags 等子段
            last = j
            for k in range(j + 1, sub_bound(lines, j, e)):
                if lines[k].strip() and len(lines[k]) - len(lines[k].lstrip()) >= 4:
                    last = k
            lines[last + 1:last + 1] = BLOCK

tmp = tempfile.NamedTemporaryFile("w", dir=os.path.dirname(os.path.abspath(path)), delete=False)
try:
    with tmp:
        tmp.write("\n".join(lines) + "\n")
    with open(tmp.name) as f:
        _, exact2 = have_block(f.read().splitlines())
        if not exact2:
            print(f"✗ [gates] lefthook.yml {hook} {job} job 注入后回读校验未通过")
            sys.exit(3)
    os.replace(tmp.name, path)
except BaseException:
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    raise
print(f"✔ [gates] lefthook.yml {hook} {job} job 已注入")
PY
}

LW="$REPO/lefthook.yml"
if [ "$MODE" = install ]; then
  if [ ! -f "$LW" ]; then
    echo "⚠ [gates] 无 lefthook.yml（先经 awesome-factory install.sh 初始化）; 请手动挂 pre-push mutation-gate 与 post-merge comment-sync job"
  else
    guard_dest "$LW"
    lw_job "$LW" inject pre-push mutation-gate "  jobs:" "$JOB_BLOCK" || { echo "✗ [gates] lefthook.yml pre-push job 注入失败"; fail=1; }
    lw_job "$LW" inject post-merge comment-sync "  commands:" "$PM_BLOCK" || { echo "✗ [gates] lefthook.yml post-merge job 注入失败"; fail=1; }
  fi
  # yml 就绪 ≠ shim 激活: lefthook 只在被 install 生成的 .git/hooks shim 里被调用, 仅改
  # yml 不会重建 shim(post-merge 这类后加 hook 尤其如此); hooksPath 外包或 binary 缺席时不代跑
  if [ -f "$LW" ]; then
    if [ -n "$(git -C "$REPO" config core.hooksPath || true)" ]; then
      echo "⚠ [gates] core.hooksPath 已设置 ($(git -C "$REPO" config core.hooksPath)), 不代跑 lefthook install（避免覆盖既有 hook 管理方式; 如需请手动跑）"
    elif command -v lefthook >/dev/null 2>&1; then
      if (cd "$REPO" && lefthook install); then
        echo "✔ [gates] lefthook install 已激活 hook shim"
      else
        echo "⚠ [gates] lefthook install 失败, hook shim 未激活（不阻塞安装; 请手动跑）"
      fi
    else
      echo "⚠ [gates] lefthook binary 缺席, hook shim 未激活（binary 就绪后手动跑 lefthook install）"
    fi
  fi
else
  if [ ! -f "$LW" ]; then
    echo "✗ [gates] lefthook.yml 缺席: pre-push mutation-gate / post-merge comment-sync job 无处挂载（--check 失败; 先经 awesome-factory install.sh 初始化）"
    fail=1
  else
    if lw_job "$LW" check pre-push mutation-gate "  jobs:" "$JOB_BLOCK" >/dev/null 2>&1; then
      echo "✔ [gates] lefthook.yml pre-push mutation-gate job 在位"
    else
      echo "✗ [gates] lefthook.yml pre-push jobs 缺 mutation-gate job（--check 失败; 重跑 install 幂等注入）"
      fail=1
    fi
    if lw_job "$LW" check post-merge comment-sync "  commands:" "$PM_BLOCK" >/dev/null 2>&1; then
      echo "✔ [gates] lefthook.yml post-merge comment-sync job 在位"
    else
      echo "✗ [gates] lefthook.yml post-merge commands 缺 comment-sync job（--check 失败; 重跑 install 幂等注入）"
      fail=1
    fi
    # shim 一致性: pre-push shim 在位(lefthook 管理)而 post-merge shim 缺 → yml 就绪但未激活,
    # 改 yml 不会自动重建 shim。整体未激活或两者齐备均视为可接受（binary 缺席时不误报）
    hooks_dir=$(git -C "$REPO" rev-parse --git-path hooks)
    case "$hooks_dir" in /*) ;; *) hooks_dir="$REPO/$hooks_dir" ;; esac
    if [ -f "$hooks_dir/pre-push" ] && grep -q lefthook "$hooks_dir/pre-push" && [ ! -f "$hooks_dir/post-merge" ]; then
      echo "✗ [gates] pre-push shim 在位而 post-merge shim 缺失: 手动跑 lefthook install（--check 失败）"
      fail=1
    else
      echo "✔ [gates] hook shim 状态可接受（pre-push 激活或整体未激活）"
    fi
  fi
fi

# ── ④c lefthook.yml pre-commit comment-gate job 注入（幂等; Java 注释棘轮） ──
if [ "$MODE" = install ]; then
  if [ ! -f "$LW" ]; then
    echo "⚠ [gates] 无 lefthook.yml; 请手动挂 pre-commit comment-gate job"
  else
    guard_dest "$LW"
    lw_job "$LW" inject pre-commit comment-gate "  jobs:" "$JOB_BLOCK_C" || { echo "✗ [gates] lefthook.yml comment-gate job 注入失败"; fail=1; }
  fi
else
  if [ ! -f "$LW" ]; then
    echo "✗ [gates] lefthook.yml 缺席: pre-commit comment-gate job 无处挂载（--check 失败）"
    fail=1
  elif lw_job "$LW" check pre-commit comment-gate "  jobs:" "$JOB_BLOCK_C" >/dev/null 2>&1; then
    echo "✔ [gates] lefthook.yml pre-commit comment-gate job 在位"
  else
    echo "✗ [gates] lefthook.yml pre-commit jobs 缺 comment-gate job（--check 失败; 重跑 install 幂等注入）"
    fail=1
  fi
fi

# ── ⑤ 锚点 lock（仅 install 面; 前面有失败项时不写锁——不完整安装不得盖成功戳） ──
if [ "$MODE" = install ] && [ "$fail" -eq 0 ]; then
  guard_dest "$REPO/$LOCK_DST"
  if python3 - "$UP" "$REPO" "$ANCHOR" "$LOCK_DST" <<'PY'
import hashlib, json, os, sys
up, repo, anchor, lock_rel = sys.argv[1:5]
manifest = json.load(open(os.path.join(up, "tools/git/gates/gates.json")))
files = {}
for face in ("full", "local"):
    for e in manifest.get(face, []):
        d = os.path.join(repo, e["dst"])
        if os.path.isfile(d):
            files[e["dst"]] = hashlib.sha256(open(d, "rb").read()).hexdigest()
lock_path = os.path.join(repo, lock_rel)
os.makedirs(os.path.dirname(lock_path), exist_ok=True)
with open(lock_path, "w") as f:
    # indent=2 + 尾换行 = prettier 默认 JSON 排版: 消费仓 prettier --write 不再确定性重排 lock
    json.dump({"anchor": anchor, "files": files}, f, indent=2, ensure_ascii=False, sort_keys=True)
    f.write("\n")
PY
  then
    [ -s "$REPO/$LOCK_DST" ] || die "锚点 lock 写入异常: $LOCK_DST"
    echo "✔ [gates] 锚点已写: $LOCK_DST (anchor=$ANCHOR)"
  else
    die "锚点 lock 写入失败: $LOCK_DST"
  fi
fi

if [ $fail -ne 0 ]; then echo "✗ [gates] $MODE 存在失败项"; exit 1; fi
echo "✔ [gates] $MODE 完成 (anchor=$ANCHOR)"
exit 0
