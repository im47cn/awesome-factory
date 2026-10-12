#!/usr/bin/env bash
# 契约门的共用接线；source 后调用，调用方提供 REPO/DIR/DRY。
acceptance_init() {
  local issue="$1"
  ACCEPTANCE_SOURCE=""
  ACCEPTANCE_FROZEN="${DIR}/acceptance-contract.json"
  ACCEPTANCE_RECEIPT="${DIR}/acceptance-evidence.json"
  if [ "${DRY}" = 1 ]; then
    echo '[dry-run] acceptance: 启用时先核批准并冻结，后独立执行逐项验收'
    return 0
  fi
  ACCEPTANCE_SOURCE="$(python3 "${REPO}/.factory/acceptance.py" source --repo "${REPO}" --issue "${issue}")" || return 1
  if [ -n "${ACCEPTANCE_SOURCE}" ]; then
    python3 "${REPO}/.factory/acceptance.py" freeze "${ACCEPTANCE_SOURCE}" "${ACCEPTANCE_FROZEN}" || return 1
  else
    echo '[acceptance] 未启用契约门：保留旧流程，不声明完整需求覆盖'
  fi
}

acceptance_run() {
  local worktree="$1"
  if [ "${DRY}" = 1 ]; then
    echo '[dry-run] acceptance: 针对最终提交运行每条批准的验收命令并保存证据'
    return 0
  fi
  if [ -n "${ACCEPTANCE_SOURCE:-}" ]; then
    python3 "${REPO}/.factory/acceptance.py" run --source "${ACCEPTANCE_SOURCE}" \
      --frozen "${ACCEPTANCE_FROZEN}" --repo "${worktree}" --receipt "${ACCEPTANCE_RECEIPT}"
  fi
}

acceptance_context() {
  local worktree="$1"
  if [ -n "${ACCEPTANCE_SOURCE:-}" ]; then
    python3 "${REPO}/.factory/acceptance.py" verify --source "${ACCEPTANCE_SOURCE}" \
      --frozen "${ACCEPTANCE_FROZEN}" --repo "${worktree}" --receipt "${ACCEPTANCE_RECEIPT}"
  else
    printf '%s\n' 'legacy：无冻结契约与逐项证据，仅可裁决标题与测试输出，不能背书完整需求覆盖。'
  fi
}

acceptance_pr_head() {
  local branch="$1" remote sha
  if [ -z "${ACCEPTANCE_SOURCE:-}" ]; then return 0; fi
  remote="$(git ls-remote origin "refs/heads/${branch}")" || return 1
  read -r sha _ <<< "${remote}"
  [ -n "${sha}" ] && [ "$(git rev-parse HEAD)" = "${sha}" ] || return 1
  if [ -n "${ACCEPTANCE_REMOTE_SHA:-}" ] && [ "${sha}" != "${ACCEPTANCE_REMOTE_SHA}" ]; then
    return 1
  fi
  ACCEPTANCE_REMOTE_SHA="${sha}"
}
