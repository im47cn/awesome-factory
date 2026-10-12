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
#   ② lefthook.yml pre-push mutation-gate job 幂等注入（锚位: sourcery-gate
#      之后; 注入先例: awesome-rules install.sh 的 package.json scripts）
#   ③ .lefthook/gates-lock.json 锚点（上游 HEAD-9 + 落地文件 sha256）
# 用法: bash gates-install.sh [--repo <path>] [--check]
#   --repo  目标仓（默认当前目录所属仓）
#   --check 只对账不落盘：full 漂移/缺失 exit 1；local/profile/job 缺席仅报告
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
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$REPO 不是 git 工作仓"
[ -f "$MANIFEST" ] || die "清单缺失: $MANIFEST"
command -v python3 >/dev/null 2>&1 || die "需要 python3（解析 gates.json / 写 lock）"

ANCHOR=$(git -C "$UP" rev-parse --short=9 HEAD 2>/dev/null || echo unknown)

sha() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1; fi
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

fail=0

# ── ① 语言声明 ──────────────────────────────────────────────────────────────
if [ -f "$REPO/.lefthook-profile" ]; then
  echo "✔ [gates] .lefthook-profile 已存在, 保留（人工覆写优先）: $(head -1 "$REPO/.lefthook-profile")"
elif [ "$MODE" = install ]; then
  prof=$(detect_profile "$REPO")
  if [ -n "$prof" ]; then
    printf '%s\n' "$prof" > "$REPO/.lefthook-profile"
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
    mkdir -p "$(dirname "$d")"
    cp "$s" "$d"
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
      mkdir -p "$(dirname "$d")"
      cp "$s" "$d"
      echo "✔ [gates] local 首落地: $src → ${dst}（此后归仓所有, 上游只报告不覆盖）"
    elif [ "$(sha "$s")" != "$(sha "$d")" ]; then
      echo "○ [gates] local 漂移(不覆盖, 仅报告): $dst 与模板有差异——仓侧定制属预期"
    else
      echo "✔ [gates] local 一致: $dst"
    fi
  else
    if [ -f "$d" ]; then echo "✔ [gates] local 已落地: $dst"
    else echo "○ [gates] local 未落地: ${dst}（install 时首落地）"; fi
  fi
done < <(list_face local)

# ── ④ lefthook.yml pre-push job 幂等注入（仅 install 面） ──────────────────
JOB_BLOCK='    mutation-gate:
      use_stdin: true
      run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/mutation-gate.sh {push_files}'

if [ "$MODE" = install ]; then
  LW="$REPO/lefthook.yml"
  if [ ! -f "$LW" ]; then
    echo "⚠ [gates] 无 lefthook.yml（先经 awesome-rules install.sh 初始化）; 请手动挂 pre-push mutation-gate job"
  elif grep -q '^    mutation-gate:' "$LW"; then
    echo "✔ [gates] lefthook.yml mutation-gate job 已在位（幂等跳过）"
  else
    tmp=$(mktemp "${TMPDIR:-/tmp}/gates-lw.XXXXXX")
    # 锚位: sourcery-gate run 行之后（tier 0 无依赖组内, 串行次序无语义）;
    # 回退: coderabbit-gate: 之前; 再回退: 文件尾追加
    # 注: block 经 ENVIRON 传值——BSD awk(macOS) 的 -v 赋值不接受字面换行(newline in string)
    JOB_BLOCK="$JOB_BLOCK" awk '
      BEGIN { block = ENVIRON["JOB_BLOCK"] }
      { lines[++n] = $0 }
      END {
        a1 = 0; a2 = 0
        for (i = 1; i <= n; i++) {
          if (index(lines[i], "sourcery-gate.sh {push_files}") > 0) a1 = i
          if (a2 == 0 && lines[i] ~ /^    coderabbit-gate:/) a2 = i
        }
        if (a1 > 0) {
          for (i = 1; i <= n; i++) { print lines[i]; if (i == a1) print block }
        } else if (a2 > 0) {
          for (i = 1; i <= n; i++) { if (i == a2) print block; print lines[i] }
        } else {
          for (i = 1; i <= n; i++) print lines[i]
          print block
        }
      }
    ' "$LW" > "$tmp" && mv "$tmp" "$LW" \
      && echo "✔ [gates] lefthook.yml mutation-gate job 已注入" \
      || { echo "✗ [gates] lefthook.yml job 注入失败 (awk/mv)"; rm -f "$tmp"; fail=1; }
  fi
else
  if [ -f "$REPO/lefthook.yml" ] && ! grep -q '^    mutation-gate:' "$REPO/lefthook.yml"; then
    echo "⚠ [gates] lefthook.yml 缺 mutation-gate job（--check 仅报告; install 可幂等注入）"
  fi
fi

# ── ⑤ 锚点 lock（仅 install 面） ────────────────────────────────────────────
if [ "$MODE" = install ]; then
  python3 - "$UP" "$REPO" "$ANCHOR" "$LOCK_DST" <<'PY'
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
    json.dump({"anchor": anchor, "files": files}, f, indent=1, ensure_ascii=False, sort_keys=True)
    f.write("\n")
PY
  echo "✔ [gates] 锚点已写: $LOCK_DST (anchor=$ANCHOR)"
fi

if [ $fail -ne 0 ]; then echo "✗ [gates] --check 未通过"; exit 1; fi
echo "✔ [gates] $MODE 完成 (anchor=$ANCHOR)"
exit 0
