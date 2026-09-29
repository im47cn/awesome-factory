#!/usr/bin/env bash
# dispatch_watch.sh — 派发侧工作区守护（L1）
# 记录型 watcher：所有权清单外的新增/修改/删除/rename 只记录不拦截；仅对不可逆操作（HEAD 移动）报警/拦截。
# 用法:
#   dispatch_watch.sh --dir <工作区> --own <所有权文件清单文件> [--pid <执行方PID>] \
#                     [--interval <秒>] [--max <秒>] [--kill-on-commit]
#   所有权清单文件：每行一个相对工作区的路径（文件或目录）；尾斜杠可有可无；空清单 = 一律判清单外
# 输出: stdout JSONL 事件流；退出码 0=正常结束 1=参数错 2=目录非法 3=HEAD 移动（已按需 kill）
# 已知漏报面：单轮询窗口内产生又消失的变更（如修改后立即回滚）不产生事件，
# 含 \n/TAB 的极端文件名场景由 \037 分隔与控制字符转义覆盖，其余由 L2/L3 兜底。
# 设计依据: steering/task-package-standards.md §2 L1（记录优先，只拦不可逆）

set -u

DIR="" OWN_FILE="" PID="" INTERVAL=5 MAX=0 KILL_ON_COMMIT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dir|--own|--pid|--interval|--max)
      [ $# -ge 2 ] || { echo "missing value for $1" >&2; exit 1; }
      case "$1" in
        --dir) DIR=$2 ;; --own) OWN_FILE=$2 ;; --pid) PID=$2 ;;
        --interval) INTERVAL=$2 ;; --max) MAX=$2 ;;
      esac
      shift 2 ;;
    --kill-on-commit) KILL_ON_COMMIT=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

[ -n "$DIR" ] && [ -n "$OWN_FILE" ] || { echo "usage: $0 --dir <dir> --own <list-file> [--pid PID] [--interval N] [--max N] [--kill-on-commit]" >&2; exit 1; }
case "$INTERVAL$MAX" in
  ''|*[!0-9]*) echo "--interval/--max must be non-negative integers" >&2; exit 1 ;;
esac
[ "$(git -C "$DIR" rev-parse --is-inside-work-tree 2>/dev/null)" = "true" ] || { echo "not a git worktree: $DIR" >&2; exit 2; }
[ -f "$OWN_FILE" ] || { echo "own list not found: $OWN_FILE" >&2; exit 1; }

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
json_esc() { # 控制字符一并转义，保证任意合法文件名产出合法 JSON
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}
  s=${s//$'\t'/\\t}; s=${s//$'\n'/\\n}; s=${s//$'\r'/\\r}
  printf '%s' "$s"
}
emit() { # emit <event> <path> <extra>
  printf '{"ts":"%s","event":"%s","path":"%s","detail":"%s"}\n' "$(ts)" "$1" "$(json_esc "$2")" "$(json_esc "${3:-}")"
}

# 所有权判定：路径以清单中任一条目为前缀（文件或其目录下）即属清单内
# 条目与路径均先剥尾斜杠（porcelain 对未跟踪目录折叠为 "dir/"）
owned() { # owned <relpath>
  local p="${1%/}" e
  while IFS= read -r e || [ -n "$e" ]; do
    e="${e%$'\r'}"; e="${e%/}"
    [ -n "$e" ] || continue
    [ "$p" = "$e" ] && return 0
    case "$p" in "$e"/*) return 0 ;;
    esac
  done < "$OWN_FILE"
  return 1
}

# 快照：-z 避免路径 C 转义与空格截断；rename 拆为 XY new\0old\0 两记录（old 紧随）
# 展开为「XY<US>new[<US>old]」行式快照供 sort/comm 比对；US=\037 文件名不可能含的
# 字面 TAB（文件名可含 TAB），弃用。read -d '' 处理 NUL（awk RS="\0" 在 macOS BWK
# awk 下按 C 字符串截断丢记录，实测弃用）
snapshot() {
  git -C "$DIR" status --porcelain -z | {
    xy="" path="" pending=""
    while IFS= read -r -d '' rec; do
      if [ -n "$pending" ]; then
        printf '%s\037%s\037%s\n' "$xy" "$path" "$rec"
        pending=""; continue
      fi
      xy=${rec:0:2}; path=${rec:3}
      if [ "${xy:0:1}" = "R" ]; then
        pending=1
      else
        printf '%s\037%s\n' "$xy" "$path"
      fi
    done
  }
}

# 折叠目录判定：未跟踪目录以 "dir/" 整体出现（dir 下全为未跟踪内容时 git 不展开）。
# 若任一条目落在该目录下（条目 == dir 或以 dir/ 开头），粒度不足以判外 → 跳过
# （记录优先、防误杀；漏报面 = 同目录下非条目文件，由 L2 审查兜底）
dir_covered() { # dir_covered <dirpath（已剥尾斜杠）>
  # 条目 == p / 条目在 p 下 / p 在条目下（祖先覆盖，#1：tests/ 覆盖折叠的 tests/sub/）
  local p="$1" e
  while IFS= read -r e || [ -n "$e" ]; do
    e="${e%$'\r'}"; e="${e%/}"
    [ -n "$e" ] || continue
    [ "$p" = "$e" ] && return 0
    case "$e" in "$p"/*) return 0 ;; esac
    case "$p" in "$e"/*) return 0 ;; esac
  done < "$OWN_FILE"
  return 1
}

# 事件分类：XY 双列均参与（暂存态 M /D /A 不落 other）；R 双端各自过 owned
report_line() { # report_line "XY<US>new[<US>old]"（US=\037，与 snapshot 对齐）
  local xy path old
  xy=${1%%$'\037'*}; rest=${1#*$'\037'}
  if [[ "$rest" == *$'\037'* ]]; then path=${rest%%$'\037'*}; old=${rest#*$'\037'}; else path=$rest; old=""; fi
  [ -n "$path" ] || return 0
  case "$xy" in
    '??')
      case "$path" in
        */) d="${path%/}"; dir_covered "$d" || emit untracked_outside "$path" "" ;;
        *)  owned "$path" || emit untracked_outside "$path" "" ;;
      esac ;;
    R*)  if [ -n "$old" ]; then
           owned "$path" || owned "$old" || emit rename_outside "$old -> $path" "code=$xy"
         else
           owned "$path" || emit other_outside "$path" "code=$xy"
         fi ;;
    *)
      if [[ "$xy" == *M* ]]; then owned "$path" || emit modified_outside "$path" "code=$xy"
      elif [[ "$xy" == *D* ]]; then
        if owned "$path"; then emit owned_deleted "$path" "code=$xy"; else emit deleted_outside "$path" "code=$xy"; fi
      elif [[ "$xy" == *A* ]]; then owned "$path" || emit untracked_outside "$path" "code=$xy"
      else owned "$path" || emit other_outside "$path" "code=$xy"
      fi ;;
  esac
}

BASE_HEAD=$(git -C "$DIR" rev-parse -q --verify HEAD 2>/dev/null)
if [ -z "$BASE_HEAD" ]; then
  emit watcher_warn "$DIR" "unborn_head: HEAD 比较禁用，仅记录工作区事件"
  BASE_HEAD="<unborn>"
fi
emit watcher_start "$DIR" "head=$BASE_HEAD interval=${INTERVAL}s max=${MAX}s kill_on_commit=$KILL_ON_COMMIT"

ELAPSED=0
LAST_SNAP=$(snapshot | sort)
while :; do
  sleep "$INTERVAL"; ELAPSED=$((ELAPSED + INTERVAL))

  CUR_HEAD=$(git -C "$DIR" rev-parse -q --verify HEAD 2>/dev/null)
  if [ -n "$CUR_HEAD" ] && [ "$CUR_HEAD" != "$BASE_HEAD" ]; then
    emit head_moved "$DIR" "before=$BASE_HEAD after=$CUR_HEAD"
    if [ "$KILL_ON_COMMIT" -eq 1 ] && [ -n "$PID" ]; then
      kill "$PID" 2>/dev/null && emit killed "$PID" "reason=head_moved"
    fi
    exit 3
  fi

  CUR_SNAP=$(snapshot | sort)
  NEW_LINES=$(comm -13 <(printf '%s\n' "$LAST_SNAP") <(printf '%s\n' "$CUR_SNAP"))
  LAST_SNAP=$CUR_SNAP
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    report_line "$line"
  done <<EOF
$NEW_LINES
EOF

  [ "$MAX" -gt 0 ] && [ "$ELAPSED" -ge "$MAX" ] && { emit watcher_timeout "$DIR" "elapsed=${ELAPSED}s"; break; }
done
exit 0
