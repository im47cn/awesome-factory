# ADR-013 · 2026-09-16 · 滞留可见性接线（regression-routing + dispatch-liveness 第三死法）

**背景**：issue #165 实证——零改动验证轮按 §7.5 留守 `factory:in-progress`
（防已验证诉求被重派，by design），但 GitHub 侧无任何痕迹，5 天静默；
09-15 日回归失败时 `daily-regression.sh` 只按标题复用 open 回归 issue，
新失败投进派发器永久跳过的容器（死信筒），漂移从未进入派发队列，
修复靠人工会话接力（PR #187）。同构缺口：4 个 rejected issue 静默滞留
只在 dispatch 日志尾部对账，无告警面。

**决策**（三条接线，决策与副作用分离）：
1. **routing 纯函数化**：`factory_lib.py regression-routing` 决定失败结果
   投递（wake/new/append）；副作用仍在 daily-regression.sh 经 factory-lib
   收口出口执行。in-progress→wake（新失败 ≠ 已验证诉求）；rejected→new
   （不推翻人工拒裁，另开容器）；其余→append。
2. **零改动轮终态评论**：fix-issue.sh §7.5 收官前经 `issue_comment`（幂等
   键 `r<ROUND>-zero-diff`）留终态痕——留守标签的语义对人类可见。
3. **dispatch-liveness 第三死法**：open issue 挂 `factory:in-progress` 而
   `locks/leases/issue:<n>.lock` 不存在 = 滞留（锁先于标签获取、链存活期间
   持续持有，无阈值窗口）；挂 `factory:rejected` 且 updatedAt 超
   `--stale-days`（默认 7d，拒裁回执评论会刷新 updatedAt）无人工跟进 = 滞留。
   hosting 不可用降级 note 不误报（合法缺省信条）；hosting.py 须以目标仓为
   cwd 运行（slug 由 `git -C . remote` 解析，CWD 劫持会多仓混查同一仓——
   本 ADR 实现期实证踩坑）。

**验证**：test_regression_routing.py（13 例，含 #165 事故负控制：滞留态
必须路由 wake）+ test_dispatch_liveness_stall.py（10 例，含滞留必须驱动
回归退出码 1 的端到端负控制 + hosting 降级不误报）+ 本机 11 仓实跑
（etf-radar 无凭据正确降级 note）。

**审查修正**（独立 reviewer 首轮 NO PASS：1 blocker + 2 major，2026-09-16）：
1. 空容器解析在 set -e 下炸主路径（blocker）——提取容错，空输入出空串；
2. 租约存活判定硬编码单写者形态，PG 形态全量误报（major）——liveness 的
   in-progress 检测在 SUPABASE_DB 形态整体降级 note；daily-regression 侧
   同形态保守 unknown 不自动唤醒；
3. wake 不校验租约，与滞留定义自相矛盾（major）——路由升四态
   （wake|live|new|append），lease_alive 维度入纯函数，存活链/形态未知
   一律 live 不唤醒；判据经 factory_lib lease-fresh（SIGKILL 残锁 mtime
   过期同判死）；
4. hosting 瞬时失败未按 rc=2 契约兜底——两处命令替换补守卫；
5. 零改动回执措辞限定回归容器（wake 只匹配 [factory-regression] 标题）。
**已知成本**（接受）：滞留不可由代码修复时存在「日 FAIL → wake → 链 →
zero-diff → 留守 → 次日再 wake」的每日一轮整链循环，唯一出口是人工裁决
关闭；轮次上限/退避暂不实现（每次新失败即真实新证据，重派语义正确）。
