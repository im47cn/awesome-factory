# ADR-003 · 2026-08-24 · SUPABASE_DB 未设降级单写者（双态铁律）

- **背景**：ADR-001 定为 `SUPABASE_DB` 必设、未设即 fail-closed；下游复制工厂在无 PG 环境下链直接不可跑，最小形态被外部依赖堵死。人类决策（2026-08-24）：未设是显式选择单写者形态，应降级而非阻断。
- **决策**：双态——未设 `SUPABASE_DB` = 单写者降级：claim / heartbeat / release / fence 全走本地锁（主树 `.factory/locks/leases/`，git-common-dir 锚定，worktree 共享），O_EXCL 判代、过期 = mtime+租期、过期可夺 epoch+1、fence 校验 machine-id+epoch、过期不复活；`<key>.epoch` 计数器使 fencing token 跨 release 单调不回零（对齐 PG 行常驻）。已设但 psql 不可达 = 配置错误，**维持 fail-closed**——把配置错误伪装成单写者形态等于重开多写者竞态。
- **后果**：下游无 PG 可跑；同机持有中二次 claim 拒（严于 PG 同机续约——本地锁互斥对象就是同机进程，同机并发双链正是要防的）。代价：跨机互斥不存在（本地锁互不可见，每条降级路径 stderr 告警是唯一防线）、无配额。出口围栏（`lease_guard`）经 `LEASE_KEY/LEASE_EPOCH` 契约在降级态继续生效。ADR-001「必设」表述由本条修正，ADR 不可变故不就地改写。
- **引用**：`.factory/factory-lease.sh`「单写者降级」节；`.factory/README.md`「单写者降级」；`tests/test-lease-sql.sh` 单写者用例组（PASS=14）。
