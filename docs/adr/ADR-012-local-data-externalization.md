# ADR-012 · 2026-09-13 · 公开中心仓的本地数据外置（downstream.local.json + slug 主机白名单 env 化）

**背景**：PR #183 存量净化暴露 ADR-009 未预见的情形——**中心仓本身公开**
（awesome-rules），而其「仓特定数据」载体（downstream.json 10 个下游仓
真实路径、hosting.py 的 bot ssh Host 别名正则）都是 tracked 文件：ADR-009
的 factory-local.json 模式（skip 分发、每仓一份 tracked）对下游私仓成立，
对公开中心仓则只是把泄露换了文件。scrub 即断功能（downstream-check 巡检
键 / dispatch host 锚定），形成「公开上游 × 私有下游拓扑」的结构张力。

**决策**：不是回滚 ADR-009，是补一类更严的载体：
1. **downstream.json 模板化**：tracked 文件降为空模板 + 指引，真实清单
   写 `.factory/downstream.local.json`（**gitignored，永不入库**——比
   skip 更严的第三态：不跨仓、也不跨 clone）。downstream-check.sh 读链
   local 优先；tracked 空清单 fail-closed（exit 2 + 指引文案）。
2. **slug 主机白名单 env 化**：hosting.py `_SLUG_RE` 的内部 bot Host 别名
   改由 `FACTORY_SLUG_EXTRA_HOSTS`（逗号分隔）注入——与 PR #61 forge
   退役后「平台配置走 env 两级回退」同模式。token 严格校验
   `[A-Za-z0-9_.-]+`（进 regex 交替支，防注入），坏配置 fail-closed 抛错。
3. **P1 禁词补 `wop[-\s]`**（与 `gtsp-` 同级，含空格裸词形态——独立审查
   实证 `wop 6 仓` 注释形态曾漏）：内部 bot Host 别名曾以 full 面
   驻留 hosting.py 而门不拦——盲区实证（NC13w 负控制盯防）。

**fail-closed 豁免说明**：factory-local.json 的「缺键即非零」约束不适用于
`FACTORY_SLUG_EXTRA_HOSTS`——hosting.py 是 full 面（分发至 10 下游仓），
新键必填会即时破坏全部下游仓的 factory-local 有效性；可选键（缺省=基础
白名单）是向后兼容的下限，与「显式配置了坏值必须炸」不冲突。

**验证**：test_dispatch.py（env 注入/未配置拒近似 host/注入面 fail-closed）
+ test_downstream_check.py（local 优先/空模板指引 rc2）+ NC13w 全绿；
本机真实清单迁 downstream.local.json 后 downstream-check 巡检语义不变。
