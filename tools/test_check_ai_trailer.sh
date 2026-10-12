#!/bin/sh
# check_ai_trailer.sh 正/负控制自测：证明「AI 痕迹 ⇒ 必须 Co-Authored-By trailer」
# 语义真实存在。必含三例：有痕迹有 trailer(0) / 有痕迹无 trailer(1) / 无痕迹(0)；
# 另覆盖：email 正则命中、小写 trailer 前缀匹配、追加标记按行生效、
# merge commit 跳过（--no-merges）、无参/多参/非法 range(2)。
# fixture 仓在 mktemp -d 内 git init（不触主仓），手法对齐 test_dispatch_watch.sh。
# 密封性（ADR-010）：剥除 hook 注入的 GIT_*，防劫持临时夹具仓的仓库发现。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
      GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE

set -e
cd "$(dirname "$0")/.."

W=$(cd "$(dirname "$0")" && pwd)/check_ai_trailer.sh   # 绝对路径：各例须 cd 进 fixture 仓运行
fails=0
ok()   { echo "  ok:   $1"; }
bad()  { echo "  FAIL: $1"; fails=$((fails + 1)); }

# 建带基线提交的临时仓；之后 REPO=仓路径、BASE=基线 sha（range 一律 BASE..HEAD）
newrepo() {
  REPO=$(mktemp -d /tmp/ait.XXXXXX)
  git -C "$REPO" init -q
  git -C "$REPO" config user.email t@t.t
  git -C "$REPO" config user.name t
  echo base > "$REPO/f.txt"
  (cd "$REPO" && git add f.txt && git commit -qm base)
  BASE=$(git -C "$REPO" rev-parse HEAD)
}
# 空提交一条消息（-F 文件保多行原样），可指定 author email
cmit() { # cmit <msg> [author-email]
  _m=$(mktemp)
  printf '%s\n' "$1" > "$_m"
  if [ $# -ge 2 ]; then
    git -C "$REPO" -c user.email="$2" commit -q --allow-empty -F "$_m"
  else
    git -C "$REPO" commit -q --allow-empty -F "$_m"
  fi
  rm -f "$_m"
}

# ── T1 有痕迹有 trailer → rc 0 且无输出 ───────────────────────────────
newrepo
cmit 'feat: with trailer

🤖 Generated with Claude Code

Co-Authored-By: Claude <noreply@anthropic.com>'
OUT=$(mktemp); rc=0
(cd "$REPO" && bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$rc" = 0 ] && [ ! -s "$OUT" ]; then
  ok "T1 痕迹+trailer → 0"
else
  bad "T1 rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T2 有痕迹无 trailer → rc 1 且列出 sha + 命中标记 ──────────────────
newrepo
cmit 'feat: no trailer

🤖 Generated with Claude Code'
SHA=$(git -C "$REPO" rev-parse HEAD)
OUT=$(mktemp); rc=0
(cd "$REPO" && bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$rc" = 1 ] && grep -qF "$SHA" "$OUT" && grep -qF 'Generated with' "$OUT"; then
  ok "T2 痕迹无trailer → 1 列出 sha+标记"
else
  bad "T2 rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T3 无痕迹（人写提交）→ rc 0 ──────────────────────────────────────
newrepo
cmit 'chore: human note

hand-written, no AI involvement'
OUT=$(mktemp); rc=0
(cd "$REPO" && bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$rc" = 0 ] && [ ! -s "$OUT" ]; then
  ok "T3 无痕迹 → 0"
else
  bad "T3 rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T4 email 正则命中（消息纯人写）缺 trailer → rc 1 ─────────────────
newrepo
cmit 'feat: machine work' 'bot@machines.dev'
OUT=$(mktemp); rc=0
(cd "$REPO" && AI_TRAILER_AUTHOR_RE='@machines\.dev$' bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$rc" = 1 ] && grep -qF 'author-email:bot@machines.dev' "$OUT"; then
  ok "T4 email正则命中无trailer → 1"
else
  bad "T4 rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T5 email 命中 + 小写 trailer → rc 0（大小写不敏感前缀匹配）────────
newrepo
cmit 'feat: machine with trailer

co-authored-by: claude <noreply@anthropic.com>' 'bot@machines.dev'
OUT=$(mktemp); rc=0
(cd "$REPO" && AI_TRAILER_AUTHOR_RE='@machines\.dev$' bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$rc" = 0 ]; then
  ok "T5 email命中+小写trailer → 0"
else
  bad "T5 rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T6 追加标记按行生效：两行 env，消息只含第二行标记 → rc 1 ──────────
#（若实现错把整串 env 当单标记，则不命中 → 本例以 rc=0 假过被抓住）
newrepo
cmit 'fix: second signature SecondSig present'
OUT=$(mktemp); rc=0
(cd "$REPO" && AI_TRAILER_EXTRA_MARKERS="$(printf 'TerminatorMark\nSecondSig')" \
  bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$rc" = 1 ] && grep -qF 'marker:SecondSig' "$OUT"; then
  ok "T6 追加标记按行生效 → 1"
else
  bad "T6 rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T7 merge commit 跳过（--no-merges）：带痕迹无 trailer 的 merge 不计 ──
newrepo
cmit 'chore: on main'
git -C "$REPO" checkout -q -b side
cmit 'chore: on side'
git -C "$REPO" checkout -q -
git -C "$REPO" merge --no-ff -q -m 'merge: bring side in

🤖 Generated with Claude Code' side
NP=$(git -C "$REPO" rev-list --parents -n 1 HEAD | wc -w | tr -d '[:space:]')
OUT=$(mktemp); rc=0
(cd "$REPO" && bash "$W" "$BASE..HEAD") >"$OUT" 2>&1 || rc=$?
if [ "$NP" = 3 ] && [ "$rc" = 0 ]; then
  ok "T7 merge带痕迹被跳过 → 0"
else
  bad "T7 父数=$NP rc=$rc out=$(cat "$OUT")"
fi
rm -rf "$REPO" "$OUT"

# ── T8 用法错误：无参数 / 多参数 → rc 2 ──────────────────────────────
rc=0; bash "$W" >/dev/null 2>&1 || rc=$?
rc2=0; bash "$W" a b >/dev/null 2>&1 || rc2=$?
if [ "$rc" = 2 ] && [ "$rc2" = 2 ]; then
  ok "T8 无参/多参 → 2"
else
  bad "T8 rc=$rc/$rc2"
fi

# ── T9 非法 range（git log 直接失败）→ rc 2 ─────────────────────────
rc=0; bash "$W" "HEAD..no-such-ref-xyz" >/dev/null 2>&1 || rc=$?
if [ "$rc" = 2 ]; then
  ok "T9 非法range → 2"
else
  bad "T9 rc=$rc"
fi

# ── T10 类选项 range 注入（如 --max-count=0）→ rc 2，不静默过门 ────────
newrepo
rc=0
(cd "$REPO" && bash "$W" "--max-count=0") >/dev/null 2>&1 || rc=$?
if [ "$rc" = 2 ]; then
  ok "T10 类选项range → 2"
else
  bad "T10 rc=$rc"
fi
rm -rf "$REPO"

# ── T11 坏正则 + 空范围（无提交）→ rc 2，配置错误不因空范围漏检 ────────
newrepo
rc=0
(cd "$REPO" && AI_TRAILER_AUTHOR_RE='([' bash "$W" "$BASE..$BASE") >/dev/null 2>&1 || rc=$?
if [ "$rc" = 2 ]; then
  ok "T11 坏正则+空范围 → 2"
else
  bad "T11 rc=$rc"
fi
rm -rf "$REPO"

echo
[ "$fails" = 0 ] && echo "check_ai_trailer 全部通过" || echo "$fails 项失败"
exit $fails
