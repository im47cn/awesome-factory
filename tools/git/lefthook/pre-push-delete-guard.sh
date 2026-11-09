#!/usr/bin/env bash
# pre-push 删除型 push 守卫（awesome-factory tools/git 分发，与 coverage/run-tests 等 pre-push 门禁配套）
# 用法（lefthook.yml 中每条 pre-push 命令前置，且命令须声明 use_stdin: true）：
#   run: bash .lefthook/pre-push-delete-guard.sh || bash .lefthook/coverage.sh full {push_files}
# 判定：读 git 传给 pre-push 的 stdin（每行 <local_ref> <local_sha> <remote_ref> <remote_sha>），
#   纯删除 push（≥1 行且所有行 local_sha 全零）→ exit 0，短路跳过该门禁；
#   正常推送 / 混合 / 空 stdin / TTY / 读取失败 / 行格式异常（非四字段）→ exit 1，门禁照常执行（fail-open）。
# 背景：lefthook {push_files} 实为 git diff HEAD @{push}（internal/git/repo.go PushFiles），
#   与本次推送内容无关——删远程分支时若本地领先会误触发全部门禁，lefthook 无原生删除感知。
# 守卫自身缺失/崩溃时经 `||` 兜底走门禁（fail-open），绝不因守卫问题拦死正常 push。
set -u

# 手动 lefthook run / interactive 调试时 stdin 是 TTY：不读（cat 会挂住），直接放行给门禁
[ -t 0 ] && exit 1

# cat 读错误（stdin 重定向自目录等）不得静默当成空输入——fail-open 给门禁
if ! input="$(cat)"; then
  exit 1
fi
[ -n "$input" ] || exit 1

# 任一行非 git pre-push 四字段格式、或 local_sha（第 2 字段）非全零
# → 存在真实上传内容或输入不可信 → 不跳过（畸形行宁可跑门禁也不误短路）
if printf '%s\n' "$input" | awk 'NF != 4 || $2 !~ /^0+$/ { real = 1 } END { exit real ? 1 : 0 }'; then
  echo "[pre-push] 纯删除 push（无本地提交上传），跳过 pre-push 门禁"
  exit 0
fi
exit 1
