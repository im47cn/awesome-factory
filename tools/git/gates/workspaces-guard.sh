#!/usr/bin/env bash
# workspaces-guard.sh — team-os 需求工作区规范门禁（awesome-factory tools/git/gates 分发）
# 用法: bash workspaces-guard.sh <workspaces-root>   # 例: bash workspaces-guard.sh ~/team-os/workspaces
# 校验（对应 workspaces/README.md + team-os/CLAUDE.md）：
#   ① slug 命名正则 ^(KFPT-[0-9]+|2026[0-9]{4})(-[a-z0-9-]+)?$
#   ② slug 目录下至少有一个 git worktree（禁纯输出目录）
#   ③ worktree 注册表内无指向规范外路径的 team-os 残留（prunable 残链）
#   ④ 空壳目录（无任何文件）
# 只读检查，不改任何状态；违规 exit 1 并逐条列出。t3-*/mr-patrol 等 harness 目录不在本门禁范围。
set -u

ROOT="${1:-}"
if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
  echo "usage: workspaces-guard.sh <workspaces-root>" >&2; exit 2
fi
ROOT=$(cd "$ROOT" && pwd -P)
SLUG_RE='^(KFPT-[0-9]+|2026[0-9]{4})(-[a-z0-9-]+)?$'

violations=0
fail() { echo "VIOLATION: $*"; violations=$((violations+1)); }

for d in "$ROOT"/*/; do
  slug=$(basename "$d")
  [ -d "$d" ] || continue
  echo "$slug" | grep -Eq "$SLUG_RE" || fail "slug 命名违规: $slug（须 KFPT-xxx 或 YYYYMMDD 开头）"

  if ! find "$d" -maxdepth 3 -name .git \( -type f -o -type d \) | grep -q .; then
    # 无 worktree：纯输出目录或空壳
    if [ -z "$(find "$d" -type f ! -name .DS_Store -print -quit 2>/dev/null)" ]; then
      fail "空壳目录: $slug（无任何文件）"
    else
      fail "纯输出目录: $slug（slug 下无 git worktree，交付物应放 docs/<slug>/）"
    fi
  fi
done

# prunable 残链：扫描本机 open-platform 各仓注册表中 team-os workspaces 路径的可移动性
# （worktree prune 已在常规巡检做，这里只报"注册了但目录已消失"）
SOURCES_BASE="${SOURCES_BASE:-$HOME/sources/open-platform}"
for repo in "$SOURCES_BASE"/*/; do
  [ -e "$repo/.git" ] || continue
  # porcelain 对含空格路径输出带引号: substr 取全段再剥引号, 避免 awk $2 截断
  while read -r wt; do
    case "$wt" in "$ROOT"/*) [ -e "$wt" ] || fail "prunable 残链 $wt（git worktree prune 可清）";; esac
  done < <(git -C "$repo" worktree list --porcelain 2>/dev/null | awk '/^worktree /{wt=substr($0,10); gsub(/^"|"$/,"",wt); print wt}')
done

if [ "$violations" -gt 0 ]; then
  echo "workspaces-guard: FAIL（$violations 条违规）"; exit 1
fi
echo "workspaces-guard: PASS"
