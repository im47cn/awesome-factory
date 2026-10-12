#!/usr/bin/env bash
# check_ai_trailer.sh — AI 提交署名 trailer 校验（工厂仓 issue #3，SDLC 手册 v2.1 §05）
# 逐个检查 <commit-range> 内非 merge 提交（--no-merges）：commit message 命中任一
# 「AI 痕迹」条件时，必须存在合法 Co-Authored-By: trailer（正文任意行以
# co-authored-by: 开头，大小写不敏感前缀匹配）。
# 用法:
#   check_ai_trailer.sh <commit-range>    # 如 origin/main..HEAD
# 环境变量:
#   AI_TRAILER_EXTRA_MARKERS  换行分隔的追加 AI 生成标记（并入下方内置清单）
#   AI_TRAILER_AUTHOR_RE      author email 判定 ERE，默认留空 = 只查标记
# 输出: 违例逐行 "<sha> <命中标记...>"（author-email:<email> 或 marker:<标记>）
# 退出码: 0=干净或无AI痕迹 1=有AI痕迹缺trailer 2=用法或门自身错误
# @date 2026-10-12

set -u

# 内置 AI 生成标记（issue #3）：🤖 / "Generated with" / agent 署名行
#（Co-Authored-By 本身是校验目标而非痕迹标记，不入清单；追加走 AI_TRAILER_EXTRA_MARKERS）
BUILTIN_MARKERS=(
  "🤖"
  "Generated with"
  "Assisted-by:"
)

[ $# -eq 1 ] || { echo "usage: $0 <commit-range>  # 如 origin/main..HEAD" >&2; exit 2; }
RANGE=$1
AUTHOR_RE=${AI_TRAILER_AUTHOR_RE:-}

# 合法 trailer：正文任意行以 co-authored-by: 开头（大小写不敏感前缀匹配）
has_trailer() { # has_trailer <body>
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      [Cc][Oo]-[Aa][Uu][Tt][Hh][Oo][Rr][Ee][Dd]-[Bb][Yy]:*) return 0 ;;
    esac
  done <<< "$1"
  return 1
}

# 追加标记（换行分隔；空行跳过，防空模式误匹配所有提交）
MARKERS=("${BUILTIN_MARKERS[@]}")
while IFS= read -r _m || [ -n "$_m" ]; do
  [ -n "$_m" ] || continue
  MARKERS+=("$_m")
done <<EOF
${AI_TRAILER_EXTRA_MARKERS:-}
EOF

TMP=$(mktemp) || { echo "mktemp 失败" >&2; exit 2; }
trap 'rm -f "$TMP"' EXIT

# 单遍 git log：NUL 作字段分隔（commit message 不含 NUL，安全）；
# 每条记录以 \0 起头，与循环的逐字段 read 对齐（末条 body 含 git 追加的换行，不影响匹配）
if ! git log --no-merges --format='%x00%H%x00%ae%x00%B' "$RANGE" >"$TMP" 2>/dev/null; then
  echo "非法 range 或 git 错误: $RANGE" >&2
  exit 2
fi

viol=0
sha='' email='' body=''
{
  IFS= read -r -d '' _junk || :   # 吞掉首条记录前的空段（流以 \0 开头）；组内读 TMP，不碰 stdin
  while IFS= read -r -d '' sha; do
    IFS= read -r -d '' email || break
    if ! IFS= read -r -d '' body; then [ -n "$body" ] || break; fi

    hits=''
    if [ -n "$AUTHOR_RE" ]; then
      printf '%s\n' "$email" | grep -Eq "$AUTHOR_RE" 2>/dev/null
      case $? in
        0) hits="author-email:$email" ;;
        2) echo "AI_TRAILER_AUTHOR_RE 非法正则: $AUTHOR_RE" >&2; exit 2 ;;
      esac
    fi
    for m in "${MARKERS[@]}"; do
      case "$body" in *"$m"*) hits="${hits:+$hits }marker:$m" ;; esac
    done
    # 无 AI 痕迹（人写提交）→ 不校验
    [ -n "$hits" ] || continue
    has_trailer "$body" && continue
    printf '%s %s\n' "$sha" "$hits"
    viol=$((viol + 1))
  done
} <"$TMP"

[ "$viol" -gt 0 ] && exit 1
exit 0
