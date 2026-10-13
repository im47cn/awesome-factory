#!/usr/bin/env bash
# check_docs_human_review.sh — docs/** 人审标记校验（工厂仓 issue #7，SDLC 手册 v2.1 评审项 15）
# Codeup MR 的变更文件命中 docs/** 前缀时，必须存在至少一名「人」reviewer PASS。
# 「人」判定三重过滤（issue #4 实探证据：无原生 bot 标志，author 自审样本即 MR 8）：
#   reviewOpinionStatus == PASS 且 userId != author.userId 且 userId ∉ BOT_DENYLIST 且 state == active
# 数据链：
#   1. mcporter get_change_request            → status / author / reviewers[]
#   2. mcporter list_change_request_patch_sets → MERGE_TARGET/MERGE_SOURCE 各取最新 versionNo
#   3. REST GET .../diffs/changeTree           → changedTreeItems[].newPath/oldPath
#      （MCP 无 MR 变更文件工具，#4 报告印证工具面缺口；REST 鉴权同 x-yunxiao-token）
# 用法:
#   check_docs_human_review.sh <repositoryId> <localId>
# 环境变量:
#   YUNXIAO_ACCESS_TOKEN      必填（mcporter --env 透传 + REST x-yunxiao-token）
#   CODEUP_ORG_ID             默认 610b3c9d86508f8da8b08436（issue #4 实证组织）
#   YUNXIAO_API_BASE_URL      默认 https://openapi-rdc.aliyuncs.com（中心版）
#   DOCS_REVIEW_BOT_DENYLIST  逗号分隔 bot userId，默认空
#   DOCS_REVIEW_PATHS         逗号分隔路径前缀，默认 docs/
#   MCPORTER_BIN              mcporter 可执行（默认 mcporter；测试注入 mock）
#   MCPORTER_SERVER_CMD       --stdio server 命令串（默认 npx -y alibabacloud-devops-mcp-server）
#   CURL_BIN                  curl 可执行（默认 curl；测试注入 mock）
# 输出: 通过/跳过一行说明；违例时列 MR 状态、docs 变更文件与逐 reviewer 排除原因
# 退出码: 0=通过或跳过 1=docs 变更但无合格人审 2=用法/环境/API 失败（fail-closed）
# @date 2026-10-13

set -u

ORG=${CODEUP_ORG_ID:-610b3c9d86508f8da8b08436}
BASE=${YUNXIAO_API_BASE_URL:-https://openapi-rdc.aliyuncs.com}
DENYLIST=${DOCS_REVIEW_BOT_DENYLIST:-}
DOCS_PATHS=${DOCS_REVIEW_PATHS:-docs/}
MC=${MCPORTER_BIN:-mcporter}
SERVER=${MCPORTER_SERVER_CMD:-npx -y alibabacloud-devops-mcp-server}
CURL=${CURL_BIN:-curl}
TOKEN=${YUNXIAO_ACCESS_TOKEN:-}

die2() { echo "check_docs_human_review: $*" >&2; exit 2; }

# ── 参数/环境 fail-closed ──────────────────────────────────────────────
[ $# -eq 2 ] || die2 "用法: $0 <repositoryId> <localId>"
REPO_ID=$1
MR_ID=$2
[ -n "$REPO_ID" ] && [ -n "$MR_ID" ] || die2 "repositoryId/localId 不得为空"
case "$REPO_ID$MR_ID" in
  *[!0-9]*) die2 "repositoryId/localId 须为数字 ID（防路径注入），得: $REPO_ID $MR_ID" ;;
esac
[ -n "$TOKEN" ] || die2 "缺 YUNXIAO_ACCESS_TOKEN"
command -v python3 >/dev/null 2>&1 || die2 "缺 python3"

# ── 数据拉取（任一失败 fail-closed exit 2）─────────────────────────────
# ponytail: 走临时文件 + if 判定而非 VAR=$(cmd)：bash 3.2 下命令替换包失败函数
# 不中断赋值语句、且「赋值续行 || func "$VAR"」展开时序错乱（TO_PS 实证 unbound）
WORK=$(mktemp -d) || die2 "mktemp 失败"
trap 'rm -rf "$WORK"' EXIT

if ! "$MC" call --stdio "$SERVER" --env "YUNXIAO_ACCESS_TOKEN=$TOKEN" \
    get_change_request "organizationId=$ORG" "repositoryId=$REPO_ID" "localId=$MR_ID" \
    > "$WORK/detail.json"; then
  die2 "mcporter get_change_request 失败（token/网络/参数，见上方 stderr）"
fi
if ! "$MC" call --stdio "$SERVER" --env "YUNXIAO_ACCESS_TOKEN=$TOKEN" \
    list_change_request_patch_sets "organizationId=$ORG" "repositoryId=$REPO_ID" "localId=$MR_ID" \
    > "$WORK/patches.json"; then
  die2 "mcporter list_change_request_patch_sets 失败（见上方 stderr）"
fi
DETAIL=$(cat "$WORK/detail.json")
PATCHES=$(cat "$WORK/patches.json")

# ── 阶段一：解析 MR 面与版本对（python 单点解析，输出 tab 分隔 4 元组）──
# 输出: <fromPatchSetBizId>\t<toPatchSetBizId>\t<status>\t<authorUserId>
# MERGE_TARGET/MERGE_SOURCE 各取 versionNo 最大者为 from/to（changeTree 文档语义）
export DETAIL PATCHES
if ! python3 > "$WORK/meta" <<'PYEOF'; then die2 "MR 详情/版本解析失败（缺字段或非预期 JSON）"; fi
import json, os, sys


def load(name):
    raw = os.environ.get(name)
    if not raw:
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(1)


detail = load("DETAIL")
patches = load("PATCHES")
status = detail.get("status") or ""
author = (detail.get("author") or {}).get("userId") or ""
if not status or not author:
    sys.exit(1)

by_type = {}
for p in patches if isinstance(patches, list) else []:
    t, v = p.get("relatedMergeItemType"), p.get("versionNo") or 0
    pid = p.get("patchSetBizId")
    if t in ("MERGE_SOURCE", "MERGE_TARGET") and pid:
        if t not in by_type or v > by_type[t][0]:
            by_type[t] = (v, pid)
if "MERGE_SOURCE" not in by_type or "MERGE_TARGET" not in by_type:
    sys.exit(1)
print("{}\t{}\t{}\t{}".format(
    by_type["MERGE_TARGET"][1], by_type["MERGE_SOURCE"][1], status, author))
PYEOF
IFS=$'\t' read -r FROM_PS TO_PS STATUS AUTHOR_ID < "$WORK/meta"

# 门禁适用面：已合并/评审中（issue #7）。CLOSED 的 MR 不再追人审。
case "$STATUS" in
  CLOSED) echo "skip: MR !$MR_ID 状态 CLOSED，不在人审门适用面（评审中/已合并）"; exit 0 ;;
esac

# ── 阶段二：变更文件树（REST；curl -f 使非 2xx 转 rc22 fail-closed）──
if ! "$CURL" -sS -f -H "x-yunxiao-token: $TOKEN" \
    "$BASE/oapi/v1/codeup/organizations/$ORG/repositories/$REPO_ID/changeRequests/$MR_ID/diffs/changeTree?fromPatchSetId=$FROM_PS&toPatchSetId=$TO_PS" \
    > "$WORK/tree.json"; then
  die2 "changeTree 拉取失败（from=$FROM_PS to=${TO_PS}）"
fi
TREE=$(cat "$WORK/tree.json")

# ── 阶段三：docs 命中判定 + 三重过滤（单点解析，exit 0/1/2 直出）──────
export DETAIL TREE AUTHOR_ID DENYLIST DOCS_PATHS
python3 - <<'PYEOF'
import json, os, sys

def out(code, msg=""):
    if msg:
        print(msg)
    sys.exit(code)

try:
    detail = json.loads(os.environ["DETAIL"])
    tree = json.loads(os.environ["TREE"])
except json.JSONDecodeError:
    out(2, "check_docs_human_review: DETAIL/TREE 非法 JSON")

items = tree.get("changedTreeItems") if isinstance(tree, dict) else None
reviewers = detail.get("reviewers") if isinstance(detail, dict) else None
if not isinstance(items, list) or not isinstance(reviewers, list):
    out(2, "check_docs_human_review: 响应缺 changedTreeItems/reviewers（结构变更？）")

prefixes = [p.strip().rstrip("/") + "/" for p in os.environ.get("DOCS_PATHS", "docs/").split(",") if p.strip()]
deny = {u.strip() for u in os.environ.get("DENYLIST", "").split(",") if u.strip()}
author = os.environ.get("AUTHOR_ID", "")

docs_files = sorted({
    it.get("newPath") or it.get("oldPath") or ""
    for it in items if isinstance(it, dict)
    if any((it.get("newPath") or it.get("oldPath") or "").startswith(p) for p in prefixes)
})
if not docs_files:
    out(0, f"skip: 变更不含 docs 面（{len(items)} 件变更文件，前缀 {'/'.join(prefixes)} 零命中）")

reasons = []
qualified = []
for r in reviewers:
    if not isinstance(r, dict):
        continue
    uid = r.get("userId") or ""
    name = r.get("name") or uid
    st = r.get("reviewOpinionStatus") or ""
    ustate = r.get("state") or ""
    if st != "PASS":
        reasons.append(f"  - {name}({uid}) reviewOpinionStatus={st or '无'} → 未通过")
    elif uid == author:
        reasons.append(f"  - {name}({uid}) PASS → 排除: author 自审")
    elif uid in deny:
        reasons.append(f"  - {name}({uid}) PASS → 排除: denylist bot")
    elif ustate != "active":
        reasons.append(f"  - {name}({uid}) PASS → 排除: state={ustate or '无'}")
    else:
        qualified.append(f"{name}({uid})@{r.get('reviewTime') or '?'}")

if qualified:
    out(0, f"ok: MR 含 docs 变更 {len(docs_files)} 件，合格人审: {'; '.join(qualified)}")

lines = [f"FAIL: MR docs/** 变更 {len(docs_files)} 件但无合格人审（PASS 且非 author 且非 denylist 且 active）"]
lines += [f"  docs: {' '.join(docs_files)}", "  reviewers:"]
lines += reasons or ["  - （reviewers 为空，无人评审）"]
lines.append("  处置: 补人工 review PASS 后重跑本门")
out(1, "\n".join(lines))
PYEOF
