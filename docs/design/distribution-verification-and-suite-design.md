# P3 分发层数据验证与分发套件设计

- 日期: 2026-09-03
- 作者: P3 分发层验证(distribution verify)
- 来源: `docs/P3-分发层数据验证与设计-task-brief.md`(P3 任务书)
- 状态: accepted(数据已实测,设计待后续 spec 化实施)
- 关联: `factory-harness-design.md` §11(M1–M4)、ADR-009/010/011(`docs/adr/`)

> 迁移注（2026-09-29）：本文所述工厂已自 awesome-rules 整体迁入本仓
> （awesome-factory）。DIST-10「不建独立分发仓」按当时实例阈值（2<3）
> 成立；本迁移即该条款的显式推翻与接管——现行上游真相源为本仓。

## 0. 摘要与结论

对 `.factory/downstream.json` 登记的 10 个下游仓做只读实测(git ls-tree/ls-remote/日志与运行时产物巡检,零写入),得到三条核心结论:

1. **真消费成立**:严口径(main 已合 + 锚点/追平史 + 链当日活跃)6/10,加 Codeup 开发线 2 仓为 8/10,≥ 半数 → 按任务书条款 6 **设计分发套件**,而非「不实施」或「单仓试点」。
2. **滞后是普遍态而非例外**:全部仓落后上游 HEAD(full 面 49 件中追平 0–35 件,中位数约 23);ADR-011(9-01)新增的 7 件巡检面只有 xx-go-sdk 追平。分发套件设计的第一目标不是「发出去」,而是**漂移收敛与防再散**。
3. **schema 已分叉、平台假设已收窄**:下游 5 仓自扩 `docstring_gate_cmd` 键(上游模板无);`/usr/bin/shlock` 在 9 仓的 cron-dispatch.sh 硬编码(P1 PR #120 实测 ubuntu ENOENT)——C 阶段(上游 PR #120 已实测 ubuntu 无 shlock)把链搬进 CI 前必须先换锁。

设计输出:intent/ 目录约定、REVIEW.md 配套、CI gate 模板注入方案、跨平台锁方案,条款化为 DIST-1..DIST-10,附验收矩阵雏形(条款 6 要求的结构,供后续 spec 化时逐条继承)。

## 1. 阶段一:数据验证(只读实测)

### 1.1 数据口径与方法

- 上游基准:本仓 worktree HEAD `7d96941` = 主仓 main `08e1611` + 纯 docs 提交,两者 `.factory` full 面(按 `DISTRIBUTION.json` 展开,49 件)树差异 = 0 → 以 worktree HEAD 为基准合法。
- 滞后度:优先用各仓 `.factory/locks/upstream-lock.json` 锚点分解(=锚点数 / 锚点 full 数 + 本地改件数);无锚点仓用「与上游 HEAD 一致件数」口径并标 [INFERENCE]。
- 链活跃:`.factory/locks/dispatch.log`(mtime + 尾行 exit 码)、`ledger.jsonl` 条数、`leases/` 租约纪元、`metrics/` 标记。均属 skip 面(运行时产物),不入库,反映本机/中心驱动实际运行。
- 远端口径:GitHub 仓 `origin/main` ls-tree;Codeup 两仓 main 为 Initial commit 占位,以远端开发分支为准。
- 采集窗口:2026-09-03;全部命令只读(`ls-tree` / `ls-remote` / `cat` / `stat` / `git grep`),未 fetch、未改任何被调研仓。

### 1.2 表 1:10 仓 × 采纳状态 × 滞后度 × 适配成本

| 仓 | 托管 | .factory 入库位置(件数) | 锚点 | =锚/锚full | 本地改 vs 锚 | vs 上游HEAD/49 | 链活跃证据 | 追平提交数 | local.json 键数 |
|---|---|---|---|---|---|---|---|---|---|
| gtsp-xx-service | Codeup | 开发分支 origin/feature/demo-v1(50) | 无 | – | – | 27 | locks 初始化(9-01),无 dispatch.log,ledger 0 条 | 0 | 11 |
| gtsp-xx-gateway | Codeup | 开发分支 origin/fix/sourcery-review(56) | 04ee96d0(8-28) | 29/42 | 13 | 23 | ledger 2 条(8-28)+ leases/T-22/26 + metrics/auto-merge-unlocked;远端 factory/* 分支(sync-2be9f99、issue-T-26) | 1 | 11 |
| xx-python-sdk | GitHub | origin/main(55,9-02) | 无 | – | – | 23 | dispatch.log 9-03 10:58 exit=0(1301 行) | 0 | 11 |
| xx-java-sdk | GitHub | origin/main(57,9-02) | 20f6a632(8-31) | 41/42 | 1 | 21 | dispatch.log 9-03 exit=0 | 0 | 12(+docstring_gate_cmd) |
| xx-typescript-sdk | GitHub | origin/main(55,9-02) | 无 | – | – | 23 | dispatch.log 9-03 exit=0 | 4(手工形态) | 11 |
| xx-php-sdk | GitHub | origin/main(61,9-03) | 20f6a632(8-31) | 29/42 | 13 | 23 | dispatch.log 9-03 exit=0 | 0 | 12(+docstring_gate_cmd) |
| xx-dotnet-sdk | GitHub | origin/main(56,9-02) | 63301f27(9-01) | 43/43 | 0 | 27 | dispatch.log 9-03 exit=0;分支 factory/sync-63301f27 | 3 | 13(+docstring_gate_cmd+port_point) |
| xx-skills | GitHub | origin/main(36,9-02) | 04ee96d0 + sync_debt 登记 | 20/42 | 7 | 14 | dispatch.log 9-03 exit=0 | 0 | 11 |
| xx-web-tools | GitHub | main 无(0);移植分支 factor/install-blackbox-factory(50)未合并;工作树 `?? .factory/` 未跟踪 | 无 | – | – | 0 | dispatch.log 9-03 exit=0(本机未跟踪副本) | 0 | 13 |
| xx-go-sdk | GitHub | origin/main(62,9-03) | 2be9f99d(9-02) | 47/47 | 0 | 35 | dispatch.log 9-03 exit=0 | 6(最近 `factory: 上游同步追平(2be9f99d4)` 9-02) | 13 |

注:锚点 full 数不同(42/43/47)因各锚点对应上游不同时期的 full 面大小;「vs 上游HEAD」是统一分母(49)的可比口径。逐仓复核命令与冻结 sha 见 §4.2;其中 php/dotnet 两行件数与 gateway/go 追平计数未能按当前 refs 复现,已标 [INFERENCE]。

### 1.3 逐仓量化细节(要点)

- **xx-go-sdk(标杆形态)**:唯一 =锚点 47/47 且 0 本地改的仓;唯一带 `upstream` 字段新格式 lock + `blame_ignore: true`;唯一追平了 ADR-011 巡检面(downstream-check.sh 等)的仓;6 次追平提交呈自动化命名(`factory: 上游同步追平(<sha8>)`)(审计注:该 6 按当前 refs 任何 grep 口径至多得 3,且计数只增不减、非采集后演进可解释——不可机械复现,标 [INFERENCE],见 §4.2)。中心驱动追平(ADR-011 B 阶段)在它身上已闭环。
- **xx-dotnet-sdk**:43/43 = 锚点、0 本地改、3 次追平、分支 factory/sync-63301f27 与 PR 流痕迹——自动化形态第二例,但锚点(9-01)后上游又演进 22 件未追。
- **xx-java-sdk**:41/42,唯一本地改是 `tests/test_hosting.py`(hosting 适配演练残留);锚后 28 件演进未追。
- **xx-php-sdk / gtsp-xx-gateway**:各 13 件本地改(factory_lib/feedback/guard/hosting/state + 7 个 tests;php 另含 cron-dispatch.sh)——下游深度定制(forge 适配 + docstring 门),是 full 面覆盖策略与下游演化冲突最尖锐的两仓。
- **xx-python-sdk / xx-typescript-sdk**:无 upstream-lock(M2 未启用),内容停留在 20f6a632 世代([INFERENCE]:与 php 同批移植、追平口径一致 23/49);typescript 有 4 次手工追平提交,python 0。
- **xx-skills(滞后最深)**:=锚 20/42,缺 `tests/` 15 件(conftest/gitenv/全部 test_*.py),即**测试面整体未移植**;7 件本地改含 `prompts/triage.md` 定制;`upstream-lock.json` 带 `sync_debt` 字段显式登记漂移债务——10 仓中唯一「下游自报债务」实例。
- **gtsp-xx-service**:9-01 刚移植(3 commits),locks 目录已初始化但链未跑(dispatch.log 不存在,ledger 0 条);Codeup main 为 Initial commit 占位,治理资产在 feature 分支。
- **gtsp-xx-gateway**:链史最丰富的 Codeup 仓——远端存在 factory/sync-2be9f99、factory/issue-T-26、factory/hotfix-review-s1-s2-p1、feat/factory-forge 等分支,leases/ 下 T-22/26 租约纪元,证明 Codeup 托管的 S2 链真实运行过(8-28 前后)。
- **xx-web-tools(死亡谷形态)**:8-31 移植分支完成移植 + 门灵敏度重证(mutations 13/13,kill 11/11 = 100%),但 origin/main 无 .factory,移植分支至今未合并;当前工作树的 .factory 为未跟踪副本,链在本机照跑。**移植完成 ≠ 采纳完成**。

### 1.4 install.sh 调用证据

- 引用实证:仅 gtsp 两仓——`lefthook.yml` 头注释「由 awesome-rules tools/git/install.sh 分发」「团队成员激活:跑一次 install.sh」+ `.lefthook/commitmsg-check.sh` 提示文案;GitHub 8 仓 `git grep install.sh` 零命中。
- 变更节奏:8-17 → 9-01 共 8 commits,最近三次均伴随新 hook 面分发(sourcery-review-gate 8-31、mutation-gate/docstring 门 9-01)。
- 结论:install.sh 的活跃消费面是 **Codeup 两仓的 git 工具链激活通道**(commitlint/lefthook/versionrc),GitHub SDK 仓不走它;分发入口分裂(中心推送 .factory vs 手工 install.sh)是现状事实。

### 1.5 factory-local.json 适配成本

- 10/10 仓均有,11–13 键,高度模板化:`final_gate_cmd` 9 仓为 `scripts/run_tests.sh --no-lock`(service 例外 `mvn -q test`);perimeter/reject_guidance/review_basis 等为誊抄 + 措辞微调。**新增一仓的配置成本 ≈ 12 键誊抄 + MISSION 撰写 + kill-rate 重证,无结构性成本**。
- **schema 分叉实证**:上游模板 12 键;下游 java/php/dotnet/go/web-tools 自扩 `docstring_gate_cmd`(上游无此键),dotnet/go/web-tools 另有 `port_point`。`docstring_gate_cmd` 是「下游先行、上游未收编」的键——分发面与下游演化已经互相领先,无版本协商机制。
- 真实适配成本集中在:**mutations/run.py kill-rate 全绿重证**与 **CI 接线**,不在 json 本身。

### 1.6 平台假设清单(显式标注)

| 原语 | 位置 | 硬/软依赖 | 10 仓现状 | 风险面 |
|---|---|---|---|---|
| `/usr/bin/shlock` | cron-dispatch.sh L32/36(上游);downstream-check.sh L60/64 | **硬**:失败即 exit,静默退出或上抛 | 9 仓分发副本 git grep 命中(web-tools HEAD 无 .factory 不在列);另有 locks/ 目录 ENOENT 已修(PR#79) | macOS 本地链安全;**ubuntu CI 直接炸**(P1 PR #120 实测 ENOENT);C 阶段 composite action 的前置阻塞项 |
| `/usr/bin/osascript` | cron-dispatch.sh L71(停摆通知);downstream-check.sh L137-138(巡检通知) | **软**:前者 `\|\| true` 兜底;后者 `FACTORY_NO_NOTIFY=1` + `command -v` 守卫 | 6 仓 cron-dispatch 命中 | Linux 上静默退化为文件标记/飞书聚合告警,不阻塞;可接受 |
| launchd/LaunchAgent | 中心仓回归调度(ADR-004) | 不随 .factory 分发 | 10 仓 git grep 零命中 | 无(设计边界正确) |
| CI runs-on | GitHub 8 仓 workflows | – | 8/8 含 ubuntu-latest;python/go 加 macos-latest;typescript 加 windows-latest;workflows **不调用** .factory 脚本(仅 xx-skills codeql.yml 注释提及) | 现状零冲突;C 阶段把 dispatch/downstream-check 搬进 ubuntu runner 时 shlock 必炸 |

### 1.7 数据结论:真消费判定与三大病

**判定口径**(三维度):.factory 入库(main 或开发主线)+ 链运行证据 + 锚点/追平史。

- 严口径真消费(A 类:三要素齐)= **6/10**:java、php、dotnet、go、python、typescript(python/ts 锚点缺失但 main 已合 + 链当日活跃,内容世代 20f6a632)。
- 加 C 类开发线(gateway 链史 + T-N 租约实证;service 初装)→ 宽口径 **8/10**。
- D 类滞后(skills,main 已合但 =锚 20/42)与 E 类未合(web-tools)各有 1 仓。
- **6/10 ≥ 半数 → 任务书条款 6 路径成立:设计分发套件。** 同时数据否决了「无条件全面铺开」:

**三大病(设计必须正面回应)**:

1. **滞后普遍病**:vs 上游 HEAD 追平 0–35/49(中位 ~23);上游锚点后演进速度 14–29 件/数天(04ee96d0→HEAD 29 件,20f6a632→28,63301f27→22,2be9f99d→14)。下游自发追平提交:8 仓为 0(口径 [INFERENCE],系与表 1 对读后的唯一自洽读法:分母=main 有 .factory 的 9 仓,剔 E 类 web-tools;gateway/dotnet/go 的追平非自发,按仓区分——gateway 两次 sync(8-26/8-28,后者 ADR-009 数据化)早于 ADR-011,dotnet 自动化命名追平(9-01)与 ADR-011 同日,二者均不属其战绩;go 即下句 ADR-011 中心驱动闭环仓;唯一自发非零=typescript 4 次手工;php 的 0 属锚点世代存疑值,可靠性与他仓零值不同层,见 §4.2 php 行 *。表 1 追平列零值行仅 6,勿按仓直读)。ADR-011 中心驱动追平目前闭环 1 仓(go-sdk)。
2. **schema 分叉病**:`docstring_gate_cmd` 下游先行未收编;php/gateway 各 13 件 full 面本地改随时会被 blob 覆盖冲掉——反哺管线在用(PR #86 go-sdk 单仓、PR #88 multi-repo 批量)但吞吐跟不上漂移产生速度。
3. **移植死亡谷病**:web-tools 移植 + 重证 100% 完成后卡在「未合 main」3 天,治理资产散在移植分支与本机未跟踪副本;skills 缺 tests/ 15 件属同族(移植不完整无门禁可见)。

## 2. 阶段二:分发套件设计(数据驱动)

### 2.1 决策与依据

**决策:实施分发套件(条款化设计,暂不写分发代码)**。

- 依据 1(必要性):真消费 6/10(严)~ 8/10(宽)≥ 半数,任务书条款 6 路径触发。
- 依据 2(紧迫性):三大病各自都有实证受害者(滞后:skills;分叉:php/gateway 13 件;死亡谷:web-tools),不治理会随上游演进速度恶化。
- 依据 3(可行性):go-sdk/dotnet 证明「锚点 + 中心驱动追平 + 0 本地改」形态可稳定运行;factory-local.json 模板化使新仓边际成本低。
- 否决项:「不实施」(条款 7)不成立——采纳率过半;「仅单仓试点」不必要——试点仓事实上已存在(go-sdk 全绿),风险已探明。

### 2.2 设计原则(从数据反推)

1. **收敛优先于分发**:套件第一职责是让存量 10 仓追平并保持追平,其次才是新增仓。
2. **状态显式化**:采纳状态从「考古式推断」(本报告 1.2 表的工作)变为「仓内自声明 + 中心巡检可读」——intent/ 目录即为此设。
3. **分叉走正道**:下游改动只能经反哺合入上游后再分发(full 面覆盖不吞下游演化);schema 变更必须有版本协商。
4. **平台假设收窄到零**:分发包内任何脚本在 macOS/Linux/ubuntu CI 三环境可跑;锁原语不得绑定单平台。

### 2.3 intent/ 目录约定

每仓 `.factory/intent/`(属 skip 面,仓自定义,不下发覆盖),收录该仓对工厂治理的**采纳声明**,文件即审计记录:

- `adopt.md`:从上游 `templates/intent.md.template` 实例化,必填字段——`upstream_anchor`(当前锚点 sha)、`adopted_surface`(采纳面:full/skip/local 三清单引用)、`platform_targets`(链运行环境:macos-local / linux-ci / both)、`schema_version`(见 DIST-6)。
- `sync-debt.md`:漂移债务登记(skills 的 `sync_debt` 字段与 php/gateway 的 13 件本地改的规范化归宿),每条含:文件、偏离类型(定制/滞后/先行)、去向(反哺 PR 链接 / 待追平 / 永久本地化——永久本地化必须移入 skip 面)。
- 状态机沿用模板:`draft | review | accepted | rejected`,被拒留档。

**为什么是 intent/**:任务链四模板(intent/plan/spec/task-brief)已入库,下游对「治理意图」的表达复用同一套词汇,不发明第二约定;下游现状里 `sync_debt` 字段、`docstring_gate_cmd` 键本质都是无结构的 intent,给它们结构化落点。

### 2.4 REVIEW.md 配套

`.factory/REVIEW.md`(属 full 面,随分发更新,追加式):

- 每次分发动作(追平/反哺/门禁变更)追加一节:日期、上游锚点变迁(from→to)、diff 面(full/skip)、下游决策(接受/改造/拒绝+理由)、反哺链接。
- 作用:把「下游为什么和上游不一样」从 git 考古变成一页可读记录;downstream-check 漂移报告的链接目标;死亡谷条款(DIST-8)的检查对象(移植完成但 REVIEW.md 无「合入 main」记录 → 触发告警)。
- 形态参照 `docs/adr/`(ADR 追加式),不引入新格式。

### 2.5 CI gate 模板注入方案

现状:GitHub 8 仓 workflows 全部含 ubuntu-latest 且零调用 .factory;gate(sourcery-review-gate.yml 等)是各仓手写副本。方案:

- 上游新增 `templates/ci/` workflow 模板(sourcery-gate / mutation-gate / docstring-gate / sync-drift-gate),模板内**只调用纯 python3 与 POSIX bash 脚本**(guard.py、factory_lib.py 均可跨平台),**禁止调用 cron-dispatch.sh / downstream-check.sh**(shlock 依赖,见 2.6)。
- 注入通道:并入 `tools/git/install.sh --update` 现有刷新机制(它已有「不碰非本工具 hook」的边界语义),新增 `--ci` 增量面;不另造第二分发入口(1.4 节的入口分裂教训)。
- sync-drift-gate(新):PR 检查 `.factory` full 面 vs `intent/adopt.md` 声明锚点的一致性,漂移未登记 sync-debt.md 则红——把「滞后」从巡检发现提前到 PR 拦截。
- 试点顺序:先注入 go-sdk(=锚 47/47,0 噪声),再 dotnet,验证模板无环境假设后铺开。

### 2.6 跨平台锁方案(shlock 替代)

- 目标原语:mkdir 原子锁(POSIX 100% 可用,语义=持锁目录)替换 `/usr/bin/shlock` 硬编码;锁目录内写入当前持锁进程的 `pid` 与 `epoch` 元数据,并由 trap 清理;竞争时校验元数据,仅在 PID 对应进程已退出且 epoch 已超过最小保护期时删除整个锁目录并重试,元数据缺失或非法不得自动删除;`mkdir` 失败后仅当锁目录确实存在才判定为锁竞争,否则按权限、磁盘空间、父目录等基础设施错误直接失败,locks/ ENOENT 修复(PR#79)语义保留。
- 落点:cron-dispatch.sh 与 downstream-check.sh 的互斥段;适配层(ADR-011 的 sync-from-upstream.sh)同步替换,消除 9 仓副本的逐仓重打。
- 过渡兼容:探测式封装(`flock` 优先、mkdir 锁回退,shlock 弃用)不引入——直接统一 mkdir 锁,一处实现三环境同语义,避免探测分支的组合测试面。
- 验收基准:ubuntu runner 上以预期输入运行 `bash cron-dispatch.sh` 必须 exit=0,并同时断言日志、锁已释放且实际 dispatch 行为发生;缺少 `shlock` 的场景另设独立负例测试;macOS 本地链行为不变(互斥语义回归测试)。
- **本方案是 C 阶段(CI composite action)的前置条件,P1 PR #120 的 ubuntu 实测即其反例**。

### 2.7 条款清单(DIST-1..DIST-10,供后续 spec 化)

每条含验收手段;否定式条款(标注「否定式」)附缺席判据。后续 spec 化时测试代码按语言贴 `# spec:DIST-n` / `// spec:DIST-n` 标签,验收 = 条款到测试名反向核对矩阵。

| ID | 条款 | 类型 | 验收手段(雏形) |
|---|---|---|---|
| DIST-1 | 采纳分级:S(main 合入+锚点+链活跃)/A(main 合入+链活跃)/D(滞后:vs 锚 <80%)/E(未合 main),以 intent/adopt.md 自声明 + 中心巡检复核双源 | 行为 | downstream-check 输出分级表;抽验:10 仓分级与本报告 1.2 表一致(差异须有解释字段) |
| DIST-2 | 每仓 `.factory/intent/adopt.md` 必含 upstream_anchor/adopted_surface/platform_targets/schema_version 四字段 | 结构 | 结构校验器(缺字段红);否定式判据:无 intent/ 的仓,巡检标记「未声明」,不得进入 C 阶段自动化 |
| DIST-3 | 每次分发动作(追平/反哺/门禁变更)必须在 `.factory/REVIEW.md` 追加记录(from→to 锚点、diff 面、决策、反哺链接) | 行为 | git log 交叉核对:每个 `factory:` 分发 commit 对应 REVIEW.md 一节;缺节即红 |
| DIST-4 | CI gate 模板只注入纯 python3/POSIX bash 调用,禁止引用 cron-dispatch.sh/downstream-check.sh | 否定式 | 模板 lint:出现两脚本名即红;ubuntu runner 注入冒烟绿 |
| DIST-5 | 互斥锁统一 mkdir 原子锁,shlock 引用清零 | 行为+否定式 | grep `shlock` 在分发包零命中;ubuntu 冒烟 exit=0(§2.6 验收基准);macOS 互斥回归(并发双跑仅一实例执行)绿 |
| DIST-6 | factory-local.json 增 `schema_version`;下游启用上游未收编键(如 docstring_gate_cmd)必须先在 sync-debt.md 登记反哺去向 | 协商 | 校验器:未知键未登记即红;反哺 PR 合入后 sync-debt 条目自动/手工销账 |
| DIST-7 | 漂移红线:full 面与锚点差 >15 件或 tests/ 面缺失 → 中心自动开 issue + 降级 D 级 | 行为 | downstream-check --apply-commit 幂等重跑;红线触发冒烟(skills 仓现状即样本:>15 缺件必须触发) |
| DIST-8 | 死亡谷看护:移植完成(REVIEW.md 有移植节)但 3 日未合 main → 巡检列「移植未合」并通知 | 行为+否定式 | 否定式判据:E 级仓不得出现在 C 阶段自动化名单;web-tools 现状必须命中本条 |
| DIST-9 | 反哺优先:下游 full 面本地改在追平时不被静默覆盖——覆盖前必须 sync-debt 有「已反哺」或「永久本地化(移 skip)」记录 | 否定式 | sync-from-upstream 干跑模式:13 件本地改仓(php/gateway)干跑输出逐件处置单,无「静默覆盖」项 |
| DIST-10 | 分发真相源唯一:awesome-rules 仓为唯一 full 面来源,不建独立分发仓(实例 2<3 阈值纪律维持) | 否定式 | 仓拓扑断言:10 仓 upstream_repo 字段全部指向本仓;不新增 dist 仓(目录扫描判据) |

### 2.8 验收矩阵雏形(spec 化时展开为条款 × 测试名反向核对表)

| 条款 | 测试名占位 | 形态 |
|---|---|---|
| DIST-1 | test_intent_grading_table / test_grading_matches_survey | 单测 + 巡检快照 |
| DIST-2 | test_adopt_md_required_fields / test_missing_intent_blocks_automation | 结构校验单测(否定式) |
| DIST-3 | test_review_md_appended_per_dispatch | git log 交叉核对脚本 |
| DIST-4 | test_ci_templates_no_dispatch_scripts / test_ubuntu_inject_smoke | 模板 lint + CI 冒烟(否定式) |
| DIST-5 | test_no_shlock_refs / test_mkdir_lock_mutex / test_ubuntu_dispatch_smoke_exit0 / test_missing_shlock_negative | grep 断言 + 并发互斥 + 冒烟(exit=0 并断言日志/锁释放/dispatch 发生,见 §2.6 验收基准)+ 缺 shlock 独立负例(否定式) |
| DIST-6 | test_unknown_key_requires_debt_entry | 配置校验单测(否定式) |
| DIST-7 | test_drift_redline_triggers_issue / test_redline_idempotent | 巡检幂等单测 |
| DIST-8 | test_stale_port_branch_flagged / test_e_grade_excluded_from_automation | 巡检单测(否定式) |
| DIST-9 | test_sync_dryrun_no_silent_overwrite | sync 干跑断言(否定式) |
| DIST-10 | test_single_source_of_truth | 拓扑断言(否定式) |

### 2.9 实施顺序(建议,非本任务范围)

1. 跨平台锁(DIST-5,小而前置,P1 反例已证);2. intent/ + REVIEW.md 落到 go-sdk 试点(标杆仓零噪声);3. CI gate 模板注入 go-sdk→dotnet;4. 存量收敛专项:skills 追平 tests/ 15 件、web-tools 移植分支合 main、php/gateway 13 件反哺或 skip 化;5. 铺开 + C 阶段(依赖 DIST-5 完成)。

## 3. 决策记录

- **实施分发套件**(任务书条款 6 路径),设计见 §2,条款 DIST-1..DIST-10 待 spec 化。
- **否决「不实施」**:真消费 6/10 ≥ 半数(§1.7)。
- **否决「仅单仓试点」**:go-sdk 已是事实试点且全绿,风险已探明,缺的是收敛机制而非再验证。
- **暂不写分发代码**:本任务书明确只交付设计;代码化走后续 spec(条款清单即输入)。
- 同步在 `factory-harness-design.md` 新增 §11.5 记录本次数据结论与决策指针。

## 4. 附:证据索引

### 4.1 采集与复核口径

- 表 1 为 2026-09-03 上午采集快照;此后下游链持续运行(分支尖、运行时锁、local.json 键随追平演进而变动),故复核不依赖采集会话中间产物(`/tmp/p3_data.pkl`、`/tmp/p3_anchors.pkl` 已不存在,亦非唯一证据),而按**冻结 sha 钉扎**:对不可变提交执行下列只读命令,任何时点得到与采集时相同的数字。采集命令形态:`git ls-tree -r <ref> -- .factory`、`git grep -l -E 'shlock|osascript' HEAD -- .factory`、`git for-each-ref refs/remotes`、`stat`。
- 「vs 上游HEAD」基准:上游 `7d96941` `DISTRIBUTION.json` full 22 条目(含 `tests/`、`prompts/` 目录展开)恰为 **49 件 full 面**;vs 值 = 49 面 path+blob 等同件数(与 `08e1611` 基准等价,见 §4.3)。
- 常用复核命令(在各下游仓执行):件数 `git ls-tree -r <sha> -- .factory | wc -l`;锚点分解 `git ls-tree -r <锚sha> -- .factory` 逐一对比;追平 `git log [--all] --grep='上游同步追平'`(Codeup 仓用 `--grep='sync 上游'`;typescript 手工形态见 §4.2 判读基料);链活跃 `stat .factory/locks/dispatch.log` + `tail -1`、`wc -l < .factory/locks/ledger.jsonl`、`ls .factory/locks/leases/`;键数 `python3 -c "import json;print(len(json.load(open('.factory/factory-local.json'))))"`。
- `dispatch.log` 为追加式运行日志,行号随链运行增长:表列「1301 行」为采集时点文件长度,其第 1301 行即 `── 2026-09-03 10:58:26 dispatch 结束（exit=0）`,按内容 grep 可复核。
- `locks/upstream-lock.json`(锚点来源)为 skip 面运行时产物,后续链运行已清理;锚点 sha 改经追平提交文本与 `factory/sync-*` 分支名双源复核(04ee96d0/63301f27/2be9f99d 等,见 §4.2 各行)。

### 4.2 逐仓审计表(仓路径 × 冻结 sha × 输出摘要)

仓路径 = `downstream.json` 登记 `../open-platform/<仓名>`(本机 `~/sources/open-platform/`)。「平台期」= 连续多提交件数/vs 不变的区间,任取其一复核同数。追平列为**采集时点**计数,此后各仓脚本化追平仍在上链(当前 `--all` 口径:python 4、java 3、typescript 5、php 5、dotnet 3、skills 8、go 3、web-tools 0),故该列必须连同基据阅读;追平提交命名不统一(Codeup「sync 上游」、typescript 特性标题内嵌「追平」),任何单一 grep 必欠计——各行列明基据。

| 仓 | 冻结复核 sha(平台期) | 件数 | vs49 | 追平(采集时) | 关键输出摘要 |
|---|---|---|---|---|---|
| gtsp-xx-service | `7ed324e`(9-03 10:22,dev 分支尖端) | 50 | 27 | 0 | locks/ 已建(9-01)、无 dispatch.log;ledger 0 行;键 11 |
| gtsp-xx-gateway | `81a4d3c`(8-31,fix/sourcery-review 尖端未再动) | 56 | 23 | 1 [INFERENCE] | sync 链 `ad5f3f0`(8-26)@c798c943 → `d64d4f8`(8-28)`chore(factory): sync 上游 @04ee96d0（ADR-009 数据化）`;ledger 2 行;leases/ `issue:T-22.epoch`、`issue:T-26.epoch`;远端 factory 系分支 4(sync-2be9f99、issue-T-26、hotfix-review-s1-s2-p1、feat/factory-forge);键 11;*表列 1 与 grep 口径不符:`--grep='sync 上游'` 于 81a4d3c 历史内 = 2(ad5f3f0、d64d4f8,均 ≤采集截止)——疑按锚点 04ee96d0 对应的 ADR-009 数据化单次计,口径敏感 |
| xx-python-sdk | `8be4c9d`(9-02 13:05;平台期 9-01 23:06→9-03 01:41) | 55 | 23 | 0 | dispatch.log 第 1301 行 `── 2026-09-03 10:58:26 dispatch 结束（exit=0）`;键 11(现工作树 12,后续追平加 `port_point`) |
| xx-java-sdk | `5abc8df`(9-02 23:27;平台期 9-02 15:39→9-03 01:37) | 57 | 21 | 0 | dispatch.log 9-03 exit=0;键 12(+docstring_gate_cmd) |
| xx-typescript-sdk | `6885b89`(9-02 23:02;平台期 9-01 14:01→9-02 23:02) | 55 | 23 | 4(手工) | 追平判读基料(可复现):`git log 6885b89 --format='%h %ad %s' -- .factory` 枚举 8-31 八个触碰提交,「追平」语义内嵌于特性提交标题(4107d94 追平 Sourcery 回归闸、297c6e3 三方合并恢复+追平、9061d27 docstring 门含 .factory 追平等)而非独立 sync 消息——单一 grep 必欠计,故标「手工形态」;dispatch.log 9-03 exit=0;键 11(现 12) |
| xx-php-sdk | [INFERENCE] | 61* | 23* | 0* | *表列组合(61/23/0)在本地历史(采集截止及其后 120 提交)无同组合 sha:61 件状态均始于 9-03 12:47 且 vs=35;截止时 origin/main 尖端 `6a066a9`(9-03 10:48)`factory: 上游同步追平（298e5ca）` 实测 63 件/48 一致——表列疑为锚点世代(`20f6a632`,8-31,29/42+13 本地改)混合快照,采信其锚点分解列;可核:该追平提交、dispatch.log 9-03 exit=0、键 12→现 11(键面后续演进) |
| xx-dotnet-sdk | [INFERENCE] | 56* | 27* | 3 | *56/27 组合未能复现;可核邻点:`5bd39fc`(9-02 13:15)=56 件/21 一致、`fe2ee85`(9-03 01:56,≤截止)=61 件/35 一致;追平 3 与当前 `--all`=3 吻合(`8c60db7`(8-31)、`15557ab`(9-01)`factory: 上游同步追平（63301f27）`、合并流);分支 factory/sync-63301f27;键 13(+docstring_gate_cmd+port_point);dispatch.log 9-03 exit=0 |
| xx-skills | `f5b23e4`(9-02 22:00;平台期 9-01 14:01→9-02 22:00) | 36 | 14 | 0 | dispatch.log 9-03 exit=0;键 11;sync_debt 登记为锁文件运行时内容,采集后经链运行清理 |
| xx-web-tools | `ea301a7`(8-31 17:41,origin/main) | 0 | 0 | 0 | main `git ls-tree -r -- .factory` = 0;移植分支 `origin/factor/install-blackbox-factory` 树 50 件,尖端 `4721592`(8-31)`test(factory): 门灵敏度重证 — mutations 13/13 PASS，kill rate 11/11=100%`;工作树 `git status --porcelain -- .factory` = `?? .factory/`;dispatch.log 9-03 exit=0(本机未跟踪副本);键 13 |
| xx-go-sdk | `5e5ad92`(9-03 07:55;平台期 9-03 01:55→19:29) | 62 | 35 | 6 [INFERENCE] | *当前 `--all`=3(main 2),表列 6 疑含采集后已清理的分支侧提交,不可再按 grep 复现;可核:`17af40f`(9-02)`factory: 上游同步追平（2be9f99d4）`;键 13(+docstring_gate_cmd+port_point);dispatch.log 9-03 exit=0 |

### 4.3 其他引用(均已核验存在)

- 反哺闭环:上游 main `eb2246a fix(factory): feedback main 接线 files 字段(PR #86, xx-go-sdk 反哺)`、`c1d2788 Merge PR #88 feedback/multi-repo-20260831`(`git show -s` 可核)。
- install.sh 引用:gtsp 两仓 lefthook.yml 头注释 + commitmsg-check.sh:38(下游仓 `git grep` 可核);上游 install.sh 变更 8 commits(8-17→9-01,`git log --oneline -- tools/git/install.sh`)。
- 平台原语行号:上游 cron-dispatch.sh L3/16/32/36/71、downstream-check.sh L15/59/60/64/137-138——采集时点行号,上游演进后以 `git grep -n -E 'shlock|osascript' <上游sha>` 复核;CI runs-on 见 §1.6(8/8 ubuntu-latest)。
- 上游基准自洽:`7d96941` 与主仓 main `08e1611` 的 .factory 全树(64 件)path+blob 逐一相同——vs 值与基准选取无关。
