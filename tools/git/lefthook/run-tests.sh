#!/usr/bin/env bash
# 项目自定义 pre-push 测试入口（awesome-factory tools/git 分发，由 lefthook 调用）
# 项目存在 scripts/pre-push-tests.sh 则执行之（入库、团队共享），否则提示跳过。
# 非零退出即阻断 push；跳过门禁: git push --no-verify
set -u
ENTRY="$(git rev-parse --show-toplevel)/scripts/pre-push-tests.sh"
if [ -f "$ENTRY" ]; then
  exec bash "$ENTRY"
fi
echo "[tests] 无 scripts/pre-push-tests.sh（项目自定义测试入口），跳过"
