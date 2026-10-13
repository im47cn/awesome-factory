#!/bin/sh
# check_docs_human_review.sh 自测（issue #7 先行失败清单，先于实现成文）：
#  T1  docs 变更 + 合格人审 PASS（非 author/非 denylist/active）→ 0
#  T2  docs 变更 + 仅 author 自审 PASS → 1（明细含「author 自审」）
#  T3  docs 变更 + PASS reviewer 在 denylist → 1（明细含「denylist bot」）
#  T4  变更不含 docs 面 → 0 + skip 行
#  T5  docs 变更 + reviewer NOT_PASS → 1
#  T9  MR status=CLOSED → 0 + skip 行（不适用面）
#  T10 docs 变更 + PASS 但 reviewer state=blocked → 1（明细含「state=blocked」）
#  T6  mcporter 失败 → 2（金丝雀：stderr 含 mcporter-mock-canary）
#  T7  changeTree 拉取失败 → 2（金丝雀：stderr 含本门前缀）
#  T12 patchSets 缺 MERGE_SOURCE → 2（fail-closed）
#  T8  参数数错 / 非数字 / 空参数 → 2
#  T11 缺 YUNXIAO_ACCESS_TOKEN → 2
# mock：MCPORTER_BIN/CURL_BIN 注入假可执行（mcporter 按 fixture 文件吐 JSON，
# curl 吐 TREE fixture；失败开关吐金丝雀串），不触真实 API，幂等可重跑。
set -e
cd "$(dirname "$0")/.."

W=$(cd "$(dirname "$0")" && pwd)/check_docs_human_review.sh
BIN=$(mktemp -d /tmp/cdhr.XXXXXX)/bin
mkdir -p "$BIN"
fails=0
ok()   { echo "  ok:   $1"; }
bad()  { echo "  FAIL: $1"; fails=$((fails + 1)); }

# mcporter mock：argv 含 get_change_request → 吐 $FIX/detail.json；
# 含 list_change_request_patch_sets → 吐 $FIX/patches.json；fixture 缺失 → 金丝雀失败
cat > "$BIN/mcporter" <<EOF
#!/bin/sh
FIX=\${MOCK_FIXDIR:?}
case " \$* " in
  *" get_change_request "*)
    [ -f "\$FIX/detail.json" ] || { echo "mcporter-mock-canary: detail fixture missing" >&2; exit 1; }
    cat "\$FIX/detail.json" ;;
  *" list_change_request_patch_sets "*)
    [ -f "\$FIX/patches.json" ] || { echo "mcporter-mock-canary: patches fixture missing" >&2; exit 1; }
    cat "\$FIX/patches.json" ;;
  *) echo "mcporter-mock-canary: unknown tool \$*" >&2; exit 1 ;;
esac
EOF
# curl mock：GET changeTree → 吐 $FIX/tree.json；CURL_MOCK_FAIL=1 → rc22 金丝雀
cat > "$BIN/curlmock" <<EOF
#!/bin/sh
FIX=\${MOCK_FIXDIR:?}
case " \$* " in
  *"changeTree"*) ;;
  *) echo "curl-mock-canary: unexpected url \$*" >&2; exit 22 ;;
esac
[ "\${CURL_MOCK_FAIL:-}" = 1 ] && { echo "curl-mock-canary: fail flag" >&2; exit 22; }
cat "\$FIX/tree.json"
EOF
chmod +x "$BIN/mcporter" "$BIN/curlmock"

# fixture 构造：detail/patches/tree 三件套（reviewer 与变更文件按用例覆盖）
mkfix() { # mkfix <status> <reviewers-json> <tree-files...>；stdout=fixture 目录
  FIX=$(mktemp -d /tmp/cdhr-fix.XXXXXX)
  printf '{"status":"%s","author":{"userId":"u-author","name":"作者","state":"active"},"reviewers":[%s]}' \
    "$1" "$2" > "$FIX/detail.json"
  printf '%s' '[{"patchSetBizId":"ps-tgt-2","relatedMergeItemType":"MERGE_TARGET","versionNo":2},{"patchSetBizId":"ps-src-1","relatedMergeItemType":"MERGE_SOURCE","versionNo":1}]' \
    > "$FIX/patches.json"
  shift 2
  {
    printf '{"count":%d,"changedTreeItems":[' "$#"
    first=1
    for f in "$@"; do
      if [ $first = 1 ]; then first=0; else printf ','; fi
      printf '{"newPath":"%s","oldPath":"%s"}' "$f" "$f"
    done
    printf ']}'
  } > "$FIX/tree.json"
  printf '%s' "$FIX"
}

RV() { # RV <fixture-dir> [extra env...] -- 运行被测门，置 rc/out/err
  rc=0
  out=$(MOCK_FIXDIR=$1 YUNXIAO_ACCESS_TOKEN=test-token \
    MCPORTER_BIN="$BIN/mcporter" CURL_BIN="$BIN/curlmock" \
    MCPORTER_SERVER_CMD=mock-server bash "$W" 7248986 8 2>"$BIN/err") || rc=$?
  err=$(cat "$BIN/err")
}

PASS_RV='{"userId":"u-h","name":"人工甲","hasReviewed":true,"reviewOpinionStatus":"PASS","reviewTime":"2026-10-13T10:00:00+08:00","state":"active"}'
SELF_RV='{"userId":"u-author","name":"作者","hasReviewed":true,"reviewOpinionStatus":"PASS","reviewTime":"2026-10-13T10:00:00+08:00","state":"active"}'
BOT_RV='{"userId":"u-bot","name":"机器人","hasReviewed":true,"reviewOpinionStatus":"PASS","reviewTime":"2026-10-13T10:00:00+08:00","state":"active"}'
NOPASS_RV='{"userId":"u-h","name":"人工甲","hasReviewed":true,"reviewOpinionStatus":"NOT_PASS","reviewTime":"2026-10-13T10:00:00+08:00","state":"active"}'
BLK_RV='{"userId":"u-h","name":"停用者","hasReviewed":true,"reviewOpinionStatus":"PASS","reviewTime":"2026-10-13T10:00:00+08:00","state":"blocked"}'

# ── T1 docs 变更 + 合格人审 → 0 ────────────────────────────────────────
F=$(mkfix UNDER_REVIEW "$PASS_RV" docs/design/a.md src/main.py)
RV "$F"
if [ "$rc" = 0 ] && [ "$out" = "ok: MR 含 docs 变更 1 件，合格人审: 人工甲(u-h)@2026-10-13T10:00:00+08:00" ]; then
  ok "T1 docs+人审 → 0"
else bad "T1 rc=$rc out=$out"; fi

# ── T2 仅 author 自审 → 1 且明细指出自审 ──────────────────────────────
F=$(mkfix UNDER_REVIEW "$SELF_RV" docs/design/a.md)
RV "$F"
if [ "$rc" = 1 ] && [ "$out" = "FAIL: MR docs/** 变更 1 件但无合格人审（PASS 且非 author 且非 denylist 且 active）
  docs: docs/design/a.md
  reviewers:
  - 作者(u-author) PASS → 排除: author 自审
  处置: 补人工 review PASS 后重跑本门" ]; then
  ok "T2 author 自审 → 1"
else bad "T2 rc=$rc out=$out"; fi

# ── T3 PASS 但在 denylist → 1 ─────────────────────────────────────────
F=$(mkfix UNDER_REVIEW "$BOT_RV" docs/design/a.md)
rc=0
out=$(MOCK_FIXDIR=$F YUNXIAO_ACCESS_TOKEN=t DOCS_REVIEW_BOT_DENYLIST=u-bot \
  MCPORTER_BIN="$BIN/mcporter" CURL_BIN="$BIN/curlmock" \
  MCPORTER_SERVER_CMD=mock-server bash "$W" 7248986 8 2>"$BIN/err") || rc=$?
if [ "$rc" = 1 ] && printf '%s' "$out" | grep -q "排除: denylist bot"; then
  ok "T3 denylist → 1"
else bad "T3 rc=$rc out=$out"; fi

# ── T4 变更不含 docs → 0 + skip ───────────────────────────────────────
F=$(mkfix UNDER_REVIEW "$PASS_RV" src/a.py src/b.py)
RV "$F"
if [ "$rc" = 0 ] && printf '%s' "$out" | grep -q "^skip: 变更不含 docs 面"; then
  ok "T4 无 docs 变更 → 0 skip"
else bad "T4 rc=$rc out=$out"; fi

# ── T5 NOT_PASS → 1 ───────────────────────────────────────────────────
F=$(mkfix UNDER_REVIEW "$NOPASS_RV" docs/x.md)
RV "$F"
if [ "$rc" = 1 ] && printf '%s' "$out" | grep -q "reviewOpinionStatus=NOT_PASS"; then
  ok "T5 NOT_PASS → 1"
else bad "T5 rc=$rc out=$out"; fi

# ── T9 CLOSED → 0 + skip（不适用面）───────────────────────────────────
F=$(mkfix CLOSED "$PASS_RV" docs/x.md)
RV "$F"
if [ "$rc" = 0 ] && [ "$out" = "skip: MR !8 状态 CLOSED，不在人审门适用面（评审中/已合并）" ]; then
  ok "T9 CLOSED → 0 skip"
else bad "T9 rc=$rc out=$out"; fi

# ── T10 PASS 但 state=blocked → 1 ─────────────────────────────────────
F=$(mkfix UNDER_REVIEW "$BLK_RV" docs/x.md)
RV "$F"
if [ "$rc" = 1 ] && printf '%s' "$out" | grep -q "state=blocked"; then
  ok "T10 blocked → 1"
else bad "T10 rc=$rc out=$out"; fi

# ── T6 mcporter 失败 → 2（金丝雀证明失败真实抵达）─────────────────────
F=$(mkfix UNDER_REVIEW "$PASS_RV" docs/x.md)
rm "$F/detail.json"
RV "$F"
if [ "$rc" = 2 ] && printf '%s' "$err" | grep -q "mcporter-mock-canary"; then
  ok "T6 mcporter 失败 → 2"
else bad "T6 rc=$rc err=$err"; fi

# ── T7 changeTree 失败 → 2 ────────────────────────────────────────────
F=$(mkfix UNDER_REVIEW "$PASS_RV" docs/x.md)
rc=0
out=$(MOCK_FIXDIR=$F YUNXIAO_ACCESS_TOKEN=t CURL_MOCK_FAIL=1 \
  MCPORTER_BIN="$BIN/mcporter" CURL_BIN="$BIN/curlmock" \
  MCPORTER_SERVER_CMD=mock-server bash "$W" 7248986 8 2>"$BIN/err") || rc=$?
err=$(cat "$BIN/err")
if [ "$rc" = 2 ] && printf '%s' "$err" | grep -q "changeTree 拉取失败"; then
  ok "T7 changeTree 失败 → 2"
else bad "T7 rc=$rc err=$err"; fi

# ── T12 patchSets 缺 MERGE_SOURCE → 2 fail-closed ─────────────────────
F=$(mkfix UNDER_REVIEW "$PASS_RV" docs/x.md)
printf '%s' '[{"patchSetBizId":"ps-tgt-2","relatedMergeItemType":"MERGE_TARGET","versionNo":2}]' > "$F/patches.json"
RV "$F"
if [ "$rc" = 2 ] && printf '%s' "$err" | grep -q "解析失败"; then
  ok "T12 缺 MERGE_SOURCE → 2"
else bad "T12 rc=$rc err=$err"; fi

# ── T8 参数形误 → 2 ───────────────────────────────────────────────────
for bad_args in "7248986" "7248986 8 9" "abc 8"; do
  rc=0
  # shellcheck disable=SC2086  # 有意分词构造坏参数形态
  out=$(YUNXIAO_ACCESS_TOKEN=t bash "$W" $bad_args 2>&1) || rc=$?
  if [ "$rc" = 2 ]; then :; else bad "T8 参数[$bad_args] rc=$rc"; fi
done
rc=0
out=$(YUNXIAO_ACCESS_TOKEN=t bash "$W" "" 8 2>&1) || rc=$?   # 空串
if [ "$rc" = 2 ]; then
  ok "T8 参数形误 → 2（含空串/非数字/多参）"
else bad "T8 空参数 rc=$rc"; fi

# ── T11 缺 token → 2 ──────────────────────────────────────────────────
rc=0
out=$(env -u YUNXIAO_ACCESS_TOKEN MCPORTER_BIN="$BIN/mcporter" bash "$W" 7248986 8 2>&1) || rc=$?
if [ "$rc" = 2 ]; then
  ok "T11 缺 token → 2"
else bad "T11 rc=$rc"; fi

rm -rf "${BIN%/*}" /tmp/cdhr-fix.*
if [ "$fails" = 0 ]; then echo "all pass"; else echo "fails=$fails"; exit 1; fi
