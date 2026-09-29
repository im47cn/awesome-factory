# ADR-004 · 2026-08-24 · 自挖掘周回归闭环（借鉴 dark-factory comprehensive-test）

- **背景**：仓库已有三层门（badcase `--strict-exact` / gauntlet / doc-freshness）但无定时串联与失败自动流转——bug 靠人发现，与「工厂找自己的 bug 排队给自己修」差最后一环。
- **决策**：`.factory/regression/daily-regression.sh` 每日 03:00（LaunchAgent `com.im47cn.factory.daily`，Hour=3；2026-08-25 由周频提至日频）串联四层，不短路（triage 输入完整）；第四层 dispatch-liveness 抓调度器两种死法（停摆标记在 / streak 超 26h 未更新=LaunchAgent 断档——slug 回归 4h 静默 + 历史断档 13h 的结构性补丁）；失败自动 `gh issue create`（`[factory-regression]` 前缀，**零标签**——机器 issue 与人写 issue 走同一 triage 入口，不绕过裁决）；幂等（open 查重 → 评论追加，不重复开）；全绿记 `.factory/metrics/daily-regression.jsonl`；`--dry-run` 真跑检查但不开 issue。
- **后果**：自挖掘闭环成立；每次失败消耗一轮 triage 裁决（R4 breaker 保护仍在）；LaunchAgent 已 lint 未 load——加载由人类决定。
- **验证**：dry-run 实跑三层全 PASS（13/13 badcase、gauntlet 全层、doc-freshness 0 漂移）；`plutil -lint` 过。
