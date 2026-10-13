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
#   ② lefthook.yml pre-push mutation-gate job 幂等注入（检测/注入均限定 pre-push
#      jobs/commands 段内; python3 行级手术 + 回读校验, 失败不报成功）
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

# ── ④ lefthook.yml pre-push mutation-gate job 注入（幂等; 检测/注入均限定 pre-push 段） ──
# run 链语义: awesome-rules 删除防护契约——纯删除推送 exit 0 短路跳过门禁(sourcery/
# coderabbit job 同款既定语义), 正常推送/读取异常 exit 1 门禁照跑, 故 || 链在正常推送
# 必然落 mutation-gate; 不用 {push_files}: 新分支首推无 @{push} 引用时 lefthook 展开
# 即失败绑架首推, 且 mutation-gate.sh 自算 diff 基线无需文件清单实参
JOB_BLOCK='    mutation-gate:
      use_stdin: true
      # 删除防护极性契约: guard 0=纯删除推送→短路跳过门禁(awesome-rules 既定语义);
      # guard 1=正常推送/读取异常(fail-open)→|| 落本门禁, 退出码即 job 退出码
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/mutation-gate.sh'

# python3 行级手术(不引入 yaml 依赖): 只认 pre-push.jobs|commands 段内同名 job——挂在
# 其他 hook 下的同名 job 不算在位; 注入插到子段末尾并回读校验, 失败不报成功
# exit 0=已在位或注入成功 / 1=check 发现缺席 / 3=注入失败
lw_job() { # $1=lefthook.yml $2=check|inject $3=job_block
  python3 - "$1" "$2" "$3" <<'PY'
import os, re, sys, tempfile
path, mode, block = sys.argv[1], sys.argv[2], sys.argv[3]
TOP = re.compile(r"^[^\s#]")
with open(path) as f:
    lines = f.read().splitlines()

def find_job(lines):
    """pre-push 的 jobs|commands 子段内 '    mutation-gate:' 行号(0 基), 无则 -1"""
    s = next((i for i, l in enumerate(lines) if l.rstrip() == "pre-push:"), None)
    if s is None:
        return -1
    e = next((i for i in range(s + 1, len(lines)) if TOP.match(lines[i])), len(lines))
    j = next((i for i in range(s + 1, e) if lines[i].rstrip() in ("  jobs:", "  commands:")), -1)
    if j < 0:
        return -1
    return next((i for i in range(j + 1, e) if lines[i].startswith("    mutation-gate:")), -1)

if find_job(lines) >= 0:
    print("✔ [gates] lefthook.yml pre-push mutation-gate job 已在位（幂等跳过）")
    sys.exit(0)
if mode == "check":
    sys.exit(1)

s = next((i for i, l in enumerate(lines) if l.rstrip() == "pre-push:"), None)
if s is None:
    if lines and lines[-1].strip():
        lines.append("")
    lines += ["pre-push:", "  jobs:"] + block.splitlines()
else:
    e = next((i for i in range(s + 1, len(lines)) if TOP.match(lines[i])), len(lines))
    j = next((i for i in range(s + 1, e) if lines[i].rstrip() in ("  jobs:", "  commands:")), -1)
    if j < 0:
        # pre-push 无 jobs/commands 子段: 紧随其后插入 jobs: 头(其余 2 空格属性行后移仍合法)
        lines[s + 1:s + 1] = ["  jobs:"] + block.splitlines()
    else:
        # 插到子段末尾: 最后一条缩进≥4 的非空行之后, 不扰其后的 2 空格属性/顶层键
        last = j
        for i in range(j + 1, e):
            if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) >= 4:
                last = i
        lines[last + 1:last + 1] = block.splitlines()

tmp = tempfile.NamedTemporaryFile("w", dir=os.path.dirname(os.path.abspath(path)), delete=False)
try:
    with tmp:
        tmp.write("\n".join(lines) + "\n")
    with open(tmp.name) as f:
        if find_job(f.read().splitlines()) < 0:
            print("✗ [gates] lefthook.yml job 注入后回读校验未通过")
            sys.exit(3)
    os.replace(tmp.name, path)
except BaseException:
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    raise
print("✔ [gates] lefthook.yml pre-push mutation-gate job 已注入")
PY
}

LW="$REPO/lefthook.yml"
if [ "$MODE" = install ]; then
  if [ ! -f "$LW" ]; then
    echo "⚠ [gates] 无 lefthook.yml（先经 awesome-rules install.sh 初始化）; 请手动挂 pre-push mutation-gate job"
  else
    guard_dest "$LW"
    lw_job "$LW" inject "$JOB_BLOCK" || { echo "✗ [gates] lefthook.yml job 注入失败"; fail=1; }
  fi
else
  if [ ! -f "$LW" ]; then
    echo "✗ [gates] lefthook.yml 缺席: pre-push mutation-gate job 无处挂载（--check 失败; 先经 awesome-rules install.sh 初始化）"
    fail=1
  elif lw_job "$LW" check "$JOB_BLOCK" >/dev/null 2>&1; then
    echo "✔ [gates] lefthook.yml pre-push mutation-gate job 在位"
  else
    echo "✗ [gates] lefthook.yml pre-push jobs 缺 mutation-gate job（--check 失败; 重跑 install 幂等注入）"
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
