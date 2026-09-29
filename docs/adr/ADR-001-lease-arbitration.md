# ADR-001 · 2026-08-24 · 租约仲裁层引入（单机锁 → 多写者）

- **背景**：S2 派发形态假设单实例，互斥靠本地锁（`locks/dispatcher`）；多写者（多机/多租户）下本地锁互不可见，且 GitHub 换标签是读-改-写整集替换、非 CAS，claim（accepted→in-progress）存在跨机竞态窗口。
- **决策**：引入 Postgres 线性化仲裁层——claim / heartbeat / release / fence 全部服务端原子，epoch 每次易主 +1（fencing token）；链副作用经出口围栏（`lease_guard`）在发送前校验 epoch，被夺/吊销的诈尸链在出口被拒。同机保留 dispatcher 锁；锁标签只是租约的投影。
- **后果**：新增外部依赖 Supabase/PG（`SUPABASE_DB` 必设；未设/不可达 fail-closed exit 4，绝不降级裸跑——降级等于重开多写者竞态）；单实例假设退役，dispatch / M2 确定性 PR 流可多机并行。
- **引用**：`.factory/README.md`「租约仲裁」节；`.factory/db/schema.sql`；设计文档 §2.1 修正 1、§7 落地记录 1。
