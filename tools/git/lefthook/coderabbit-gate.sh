#!/usr/bin/env bash
# CodeRabbit pre-push 建议性评审闸（awesome-factory tools/git 分发，由 lefthook 调用）
# opt-in 门禁：仅当仓库根存在 .coderabbit.yaml（主动声明，同 sourcery-gate 的 .sourcery.yaml 模式）才启用。
# 建议性而非硬闸的依据：① LLM 评审分钟级延迟（官方文档 7-30 分钟，--light 更快）；② review 退出码语义
# 未文档化（官方仅 doctor/config validate 文档化退出码；认证失败实测 exit 1，见下），findings 与失败
# 无可靠区分——硬闸须解析 --agent JSON 流，超出 bash 闸复杂度预算。评审后台运行、结果落盘日志，
# 不阻塞也不拦 push（--light 同步实测 208s，会把 push 占住分钟级）；硬闸语义由平台侧 CodeRabbit PR 评审承担。
# fail-safe：未 opt-in / 未装 coderabbit CLI / 未登录均跳过（不因环境缺失误伤）。
# {push_files} 参数仅作 lefthook 触发条件（无变更恒 skip），评审范围由 CLI 按 git 状态自定（--committed）。
# 跳过评审: git push --no-verify
set -u

# opt-in 信号：仓库根 .coderabbit.yaml（CodeRabbit 原生配置文件，兼作门禁开关载体，删除即停用）
[ -f .coderabbit.yaml ] || { echo "[coderabbit] 无 .coderabbit.yaml（未 opt-in），跳过"; exit 0; }

command -v coderabbit >/dev/null 2>&1 || { echo "[coderabbit] 未安装 coderabbit CLI，跳过（brew install coderabbit）"; exit 0; }

# 登录预检：auth status --agent 秒级返回紧凑 JSON（"authenticated":true 即机器契约，不 grep 散文本）；
# review 认证失败实测 exit 1 且耗时数秒，前置探测把失败降级为一行跳过提示
coderabbit auth status --agent 2>&1 | grep -q '"authenticated":true' || { echo "[coderabbit] 未登录，跳过（coderabbit auth login）"; exit 0; }

# 评审异步化（PR #126 Sourcery 评论 3921352579）：建议性闸不占 push——
# 后台 nohup 运行、日志落盘，push 即时返回；结果跟踪走日志或 coderabbit review findings
LOG="/tmp/coderabbit-review-$$.log"
echo "[coderabbit] review --light --committed 后台运行中（建议性评审，分钟级延迟，不阻塞 push）"
echo "[coderabbit] 日志: ${LOG}（tail -f 跟踪；终态回看: coderabbit review findings）"
nohup coderabbit review --light --committed >"$LOG" 2>&1 </dev/null &
exit 0
