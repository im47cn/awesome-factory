# 任务书:P3-分发层数据验证与设计-开发工程师Sam

> 背景:ADR-009 把 awesome-rules 拆分为可分发套件(.factory/ + prompts + guard.py 等),下游 10 个消费仓(open-platform/*)经 tools/git/install.sh 等链路采纳。P1(配置变更 CI 硬闸)与 P2(replay-eval 收尾)落地后,P3 剩余工作 = **分发层套件与消费侧治理**。本任务为「阶段一:数据验证」+「阶段二:设计输出」,**不写分发代码**——分发投入大,消费仓库真实拉取率未验证,先拿数据说话。

# Target
- 文件/符号(只读调研,不改):
  - .factory/downstream.json(10 消费仓清单:gtsp-xx-service/gateway、xx-python/java/typescript/php/dotnet/go-sdk、xx-skills、xx-web-tools)
  - tools/git/install.sh(分发安装入口;安装侧)
  - .factory/sync-from-upstream.sh、downstream-check.sh(消费侧同步/巡检链路)
  - .factory/cron-dispatch.sh(消费侧定时采纳通道)
  - .factory/README.md「移植到其他仓库」四步 + M2/M4 checklist(现有采纳指引)
  - .factory/decisions.md(ADR 决策记录,shlock/osascript 平台假设、C 阶段 CI composite action 另案)
  - .factory/DISTRIBUTION.json(分发元数据)
  - github 消费仓实测(git ls-remote / gh api 只读查询)
- 非目标:不写分发代码、不新建分发套件、不改 downstream.json、不动消费仓
- 所有权:纯只读调研 + 一份设计文档产出,无共享写面
- 引用:docs/design/factory-harness-design.md、.factory/README.md、.factory/decisions.md、templates/task-brief.md.template

# Change
阶段一:数据验证(全部只读,产出量化结论)
1. **真实拉取率**:对 downstream.json 10 仓逐个统计——① 当前 .factory/ 是否存在于工作树(git ls-tree HEAD -- .factory 计数);② .factory/ 相对上游最新版的滞后度(比较关键文件 hash 或文件数);③ cron-dispatch/launchd 采纳迹象(仓库内 .factory/locks、metrics 目录存在性,只读);④ 最近同步 commit 时间跨度
2. **安装侧真实调用**:tools/git/install.sh 是否有消费侧调用证据(仓库内引用、README 提及、下游 clone 后手动跑无痕——只能证存在性);git log 查 install.sh 最近变更频率(投入活跃度代理)
3. **上游-下游适配成本**:检查 .factory/factory-local.json 是否存在于下游仓(本地化外置的采纳证据)——抽查 2-3 个代表仓(一个 SDK、xx-skills、xx-web-tools)
4. **平台假设清单**:shlock(macOS 专有)/osascript 通知/launchd cron——下游消费环境平台分布假设是否成立(从下游仓 CI runner 平台看:grep workflows runs-on)
5. 输出:量化数据表(仓 × 采纳状态 × 滞后度 × 适配成本),标注哪些仓是真消费、哪些是登记未采纳

阶段二:设计输出(基于数据,不写分发代码)
6. 若数据支持「真消费 ≥ 半数」:设计分发套件——intent/ 目录约定 + REVIEW.md 配套 + CI gate 模板注入方案(设计文档,含条款/验收矩阵雏形,参照 task-brief 模板结构)
7. 若数据不支持:输出「不实施」建议 + 依据(采纳率不足、维护成本 > 收益),或建议先做单仓试点验证价值
8. 无论结论,更新 docs/design/factory-harness-design.md 或新增分节记录数据结论与决策(交付为设计文档,不改任何生产文件)

# Acceptance
- 阶段一数据表完整:10 仓逐仓量化结论(采纳/滞后/适配成本),非印象式描述
- 阶段二明确结论:实施 / 单仓试点 / 不实施,附数据支撑与成本收益
- 产出设计文档一份(docs/design/ 下或交付文件),含条款清单(供后续 spec 化);**无生产代码变更**
- 平台假设(shlock/osascript)对分发目标环境的适配风险在文档中显式标注(ubuntu CI 已证实无 shlock——P1 PR #120 实测)

# 强制条款(MUST,逐条照办,不得裁剪)
1. 【禁止顺延】禁止以既有架构/所有权/历史原因为由顺延或降级 spec 条款;发现 spec 与现状冲突,必须上报(本任务数据阶段即实证层),不得自行包装成"设计取舍"。违反即失败。(教训:gtsp-xx-gateway 2026-08-28)
2. 【条款完整性】设计文档每条款对应验收手段(后续 spec 化时转测试,Python `# spec:<ID>`);否定式条款(如"不实施"也是结论)必须有对应判据。(教训:D2 digest 无条件必填共存 31 绿测)
3. 【覆盖率闭合】本任务无生产代码,覆盖率条款不适用;但数据结论必须终局复核(全部仓查完再下结论,中途样本不算数)。(教训:98.17% -> 97.62% 稀释回退)
4. 【证据纪律】每条数据结论附来源(仓路径 + 命令 + 输出摘要);未直接观察的结论标 [INFERENCE];禁止编造工具输出。
5. 【验证豁免】本任务跳过 formatter/linter/全量测试(纯调研无代码);验证由集成负责人最后统一执行一次。
6. 【工具回退链】先 cbm -> tokensave -> LSP -> grep/glob;大体积输出 headroom_compress;远程仓查询用 gh api/git ls-remote(只读)。
7. 【输出纪律】交付给数据表/结论摘要,不重印完整文件;只读文件用片段。
8. 【完成定义】不 yield 未完成工作;阶段边界不停止,同一轮内完成数据验证与设计输出。

[备注:若后续 P3 真实施分发套件,CI 平台约束同 P1(shlock 硬编码 macOS)——设计时须含跨平台锁方案,勿重蹈 ubuntu 恒红覆辙。]
