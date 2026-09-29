# 决策记录索引（docs/adr/）

> 用途：设计文档 §2.3——决策记录喂给 skills/skill-evo/，完成「流程数据 → 规则进化」闭环（原 `.factory/decisions.md`，2026-09-20 拆分迁入，全文逐字保留）。
> 新增 ADR：在本目录落全文（标题 `# ADR-NNN · 日期 · 标题`，命名 `ADR-NNN-<slug>.md`）+ 本索引追加一行。
> 纪律：ADR 风格，每条 ≤30 行（附记随主条目同文件追加）；ADR-002 生效后，进程管理类缺陷修复必须在 [ADR-002 证据清单](./ADR-002-a3-maintenance-ledger.md)追加一行。

| ADR | 日期 | 标题 |
|---|---|---|
| [ADR-001](./ADR-001-lease-arbitration.md) | 2026-08-24 | 租约仲裁层引入（单机锁 → 多写者） |
| [ADR-002](./ADR-002-a3-maintenance-ledger.md) | 2026-08-24 | A3 维护成本记账（核心） |
| [ADR-003](./ADR-003-single-writer-fallback.md) | 2026-08-24 | SUPABASE_DB 未设降级单写者（双态铁律） |
| [ADR-004](./ADR-004-daily-regression-loop.md) | 2026-08-24 | 自挖掘周回归闭环（借鉴 dark-factory comprehensive-test） |
| [ADR-005](./ADR-005-dispatch-orchestration-python.md) | 2026-08-24 | dispatch 进程编排下沉 factory_lib.py |
| [ADR-006](./ADR-006-trigger-counting-scope.md) | 2026-08-25 | ADR-002 触发器计数口径（合并前自愈不计数） |
| [ADR-007](./ADR-007-forge-adapter.md) | 2026-08-25 | forge 平台适配层（GitHub 单平台 → gh 兼容多平台）【已被 ADR-008 取代】 |
| [ADR-008](./ADR-008-hosting-abstraction.md) | 2026-08-26 | 托管平台抽象层 hosting.py（取代 ADR-007：核心与 GitHub/Codeup 解耦） |
| [ADR-009](./ADR-009-distribution-decoupling.md) | 2026-08-27 | 拆分前置：本地化全量数据化 + 引擎单点 + portability 门 |
| [ADR-010](./ADR-010-git-sealing-gate.md) | 2026-08-27 | 测试 git 密封制度化（机械化门）+ final gate 双实现漂移锁 |
| [ADR-GH1](./ADR-GH1-codeql-inline-suppression.md) | 2026-08-30 | test_hosting.py CodeQL 行内抑制（上游根修） |
| [ADR-011](./ADR-011-downstream-sync-b.md) | 2026-09-01 | 下游追平自动化 B 阶段（--repo/--commit + 中心巡检 + blame-ignore） |
| [ADR-012](./ADR-012-local-data-externalization.md) | 2026-09-13 | 公开中心仓的本地数据外置（downstream.local.json + slug 主机白名单 env 化） |
| [ADR-013](./ADR-013-stall-visibility.md) | 2026-09-16 | 滞留可见性接线（regression-routing + dispatch-liveness 第三死法） |
| [ADR-015](./ADR-015-rejection-receipt.md) | 2026-09-20 | #207 拒绝回执缺失：标签假阴性和解 + 回执存在性断言 |
| [ADR-016](./ADR-016-parallel-test-gate.md) | 2026-09-20 | 并行测试门工厂化（段 fan-out 下沉 factory_lib，随 full 面分发） |
| [ADR-017](./ADR-017-dual-feedback-paradigm.md) | 2026-09-19 | 双层反馈范式自述（执行层 OODA × 治理层 PDCA） |

ADR-014 缺号：squash 合并历史事实，编号不回填。
