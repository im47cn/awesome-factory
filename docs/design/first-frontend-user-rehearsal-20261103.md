# 首个前端用户接入修订与隔离演练（2026-11-03）

## 1. 范围与版本

按首用户指南，从全新目录创建 Vue 3 + TypeScript + Vite 工程，安装工程规范与 Hook，接入本地 Factory，以计数器页面演练需求分析、Spec、AI 编码和本地浏览器验收。演练启动时 Factory/Rules 不提交、不推送；后续用户另行授权公共仓提交推送，发布情况与历史执行来源分别记录。隔离业务仓仅建立本地工程与治理基线，不配置远端。

- 隔离目录：`/private/tmp/factory-first-user-20261103-iTbmCR`。
- 公共仓来源 HEAD：Factory `557c50494bac2231d62eedfa508a919d43ac0626`，Rules `22c6b94b020b3d5fef4f13f0b58e32382568d32c`，叠加本次未提交补丁。
- 实际执行以文件摘要锁定；HEAD 仅记录来源，不能将工作树补丁称为已发布版本。
- 环境：Python 3.11.9、Node 24.22.1、项目 pnpm 10.28.1、OMP 18.10.3；真实模型为 `openai-codex/gpt-5.6-sol`。
- 任务链：`first-page-pilot` / `local-20261103-613a17e2` → `first-page-pilot-final` / `local-20261103-56853139` → `first-page-pilot-closure` / `local-20261103-ddf56748` → `first-page-pilot-complete` / `local-20261103-4a06fbd9` → `first-page-pilot-verified` / `local-20261103-4ec147c4`。工程与反馈能力升级后分别创建关联修订；父子关系和完整历史保留，未继承通过状态。最终结果以 verified 任务为准。
- 最终工程基线 `c6ee9f4d8e789d9c0ab24c1163bef9373e58e0f5`；执行版本为 `reviewed-history/` 冻结快照，运行时锁 SHA-256 `a9eaffd00dde40cc0febaf481702b4abd8db07eb9427b10dc214b5b85fa17ef3`。本地工程提交结束、退出 0 后才启动任务。

这是无 Figma、无后端、无接口、无登录依赖的最小前端闭环。测试环境部署、远端 CI、推送、MR 和 Figma 视觉验收不在本次演练范围。

## 2. 修订内容与原因

| 问题 | 改动 | 证明方式 |
|---|---|---|
| P1：指南说自动追踪 docs，但默认初始化未启用 | init 新增 `--context-file`，安装前验证登记文件、现行规范摘要；生成配置、版本锁前应用；审计明确启用状态 | 合法上下文真实 init/check、错误摘要安装前阻断、任务引用快照与恢复漂移反例 |
| P1：团队业务修改 App.vue 后模板体检误报 | profile 1.0.2 区分 scaffold 和治理文件；`check --check-mode project` 允许业务壳演进，保留治理校验 | fresh/project 双模式、治理漂移反例、旧版本升级保留已修改/已删除 scaffold |
| P2：首次使用的操作者、模型与环境准备说明不足 | 补 OMP 安装/登录/真实探测、配套固定版本、技术选型、项目 pnpm、Git 身份、docs 接入、preflight 与 verification 收据填写、团队接手路径 | 按指南逐节点执行，保留命令/时间/退出码/原始日志 |
| 演练发现：coverage 忽略规则误伤校验脚本 | `.gitignore`、`.prettierignore` 使用根锚定 `/coverage/`、`/coverage-unit/`；升级仅迁移工具生成区的两条旧规则 | fresh 与 upgrade 均执行真实 `git check-ignore` 正反向验证 |
| 演练发现：ESLint 扫描 Factory 审查快照 | 模板仅排除 `.factory/**`、`artifacts/factory-local/**`，保留 src 规则 | 有快照时 lint=0；真实 src var/any 反例=1；恢复=0；正常 Hook 重试通过 |
| 演练发现：未建立 HEAD 时输出 traceback | 安装前生成 `workspace-baseline: BLOCKED` 和恢复指引，不半装 `.factory` | 无 HEAD 隔离仓退出 1，有失败审计，无 `.factory` |
| 演练发现：“没有设计稿、没有服务端”触发 Figma/全栈 | 领域与设计分类复用带否定范围的意图判断，输出可检查的分类证据 | 同一 request 重跑为 frontend-no-design；正向全栈/Figma 与否定场景回归 |
| 演练发现：Skill 未声明澄清覆盖关系的必填 id | Rules clarification 协议明确唯一 id 并给出关系示例；不放宽校验器 | 真实模型首轮缺 id 被拒，更新协议后由模型重新生成 |
| 演练发现：模型引用拼接/格式改写及 Spec 自证 | 明确连续原文、保留空白、上下文相对路径，且三个审查数组每个 covered 条目均各自含上游证据 | 原失败保留；重新锁定 Rules 后从 classify 重跑，禁止手工改通过状态 |
| 演练发现：可选 references 把源码摘要误作需求文件摘要 | Spec Skill 明确区分源码快照与单文件摘要，未知单文件摘要不补写 | 顶层运行器绑定始终正确；旧可选字段保留在历史 attempt，不作为可信版本依据 |
| 演练发现：升级治理并提交新 HEAD 后，旧任务基线仍不可变 | 指南明确区分同输入重试与工程/需求修订，补 `--revision-of` 的登记与恢复命令 | 实际 `HEAD changed` 拦截后归档父任务代码，创建新 task_id，重新分析与验收，不重写旧基线 |
| 演练发现：独立 Spec 审查的有效缺口没有返给 Spec 节点 | Factory 提供经完整复验的只读技术反馈，Rules 负责按当前需求修验证映射；不继承批准或业务授权 | 9 项真实 validator 夹具回归，含完整归档/上下文绑定、白名单投影、错请求/父任务/越界、篡改恢复与损坏报告拒绝；真实模型验证另列 |
| 演练发现：本地 manual 未计入浏览器汇总；简单纳入又把治理检查误作浏览器证据 | 明确 manual 的 browser/integrity/other 类型，浏览器只认显式 browser；无 browser 保持 pending，失败优先；修订保留必验 ID/类型/环境并全部重跑 | 被拒的一行补丁红端出现 2 项断言失败；最终 8/8 回归通过，独立复审无 P0/P1；真实浏览器结果另列 |
| 演练发现：编码后需求引用仍对照 live 源码，导致正常修改被判引用失效 | requirement 阶段独立保存源码观察的原始内容和 manifest，绑定分析收据；编码后读历史快照，当前代码仍按实施摘要与范围验证；重复 implement 先核对 Spec 基线与范围 | 实际 OBS-008 失败日志为红端；4 项回归覆盖真实恢复、重复 implement、模型前范围拦截及快照篡改/越界；独立复审通过 |
| 演练发现：编码后恢复仍用 live 源码核对编码前 observation 引用 | 在需求分析通过时独立保存 source 引用文件，按分析收据、版本和内容摘要验证历史引用；实施重试仍检查 Spec 基线与允许范围 | complete 任务真实编码后恢复 OBS-008 失败；首个依赖实施临时产物的修补未通过重试复审，改为分析阶段独立快照，正常恢复、再编码、篡改和范围外反例另留证 |

以上修订保持职责：Rules 提供初始化/规范/技能；Factory 校验输入、编排阶段、绑定证据；业务仓保留可执行质量命令与项目 docs。

## 3. 实际获取和工程接入

初次从父目录读取 Codeup 失败，退出 128（publickey）。原 Rules 仓的仓库级 SSH 配置不会被新 clone 自动继承。用操作者已有、明确选择的授权配置仅注入本次进程后，真实网络 clone Rules/Factory 均成功。没有修改全局 Git 身份，没有复制密钥正文；本地快照 clone 与网络 clone 分别留证。

工程接入包括真实 bootstrap plan/apply/install/check、冻结锁安装、格式/Lint/Stylelint/类型/单测/构建、覆盖率门禁与真实 Hook。覆盖率接入通过 OMP 执行 Rules 的 `frontend-unit-test-setup`，耗时 528.513 秒、退出 0。首次提交及首次 push stdin 的 Hook 正向验证在临时仓执行；部分暂存低覆盖源码、补测仅在工作区的反例被实际 pre-commit 阻断。

覆盖率策略登记普通文件 `src/lib/health.ts`（80%），核心 0（有职责依据），SFC/装配/声明排除项逐一记录；不能将其解释为组件行为已受 80%/90% 门禁覆盖。组件与页面行为另按 Spec 做测试和浏览器验收。

第一次工程试跑保留在 `trial-engineering`。最终全新 `first-factory-app` 重新执行 bootstrap/install/check，复用前次真实 AI 生成并审阅的覆盖率基础设施，`cmp` 证明入口字节一致；这属于基础设施复用，没有冒充第二次 AI 生成。最终工程重新运行覆盖率和 Hook，没有复用旧报告作为新结果。

## 4. 失败与恢复记录

| 节点/退出码 | 首次原因 | 处理 |
|---|---|---|
| remote-rules-read / 128 | 新目录缺少已有仓的 SSH 选择配置 | 仅当前进程显式选择已有授权配置，真实网络 clone 成功 |
| final-bootstrap-check / 1 | 覆盖率接入修正了模板过宽的 prettierignore，模板原始字节检查因此失败 | 修正 Rules 模板与升级，另建最终干净工程重演，不覆盖第一次证据 |
| factory-init / 2 | 演练驱动在前序失败后错误继续，仓库无 HEAD | 修复安装前审计；后续先完成本地基线再初始化 |
| factory-analysis-spec / 2 | 分类器将否定描述识别成设计与全栈需求 | 修正分类并锁定新版本，从 classify 重跑 |
| shared-runtime-regression / -15 | 公共运行时修复后停止旧快照回归 | 明确为中止，修复版整套重跑，不计作通过 |
| factory-analysis-spec-retry / 2 | 演练驱动在本地基线提交未结束时启动模型分析，完整性门发现阶段输入变化 | 严格等待前序提交退出 0、工作区干净后，从 classify 重跑；保留失败 attempt，不改通过状态 |
| factory-analysis-spec-clean / 2 | 澄清 Skill 的 requirement_coverage 未写明校验器要求的关系 id，真实模型照协议输出后被拒 | 补齐 Rules 协议，重新锁定后模型重跑；不手工给原产物补 id |
| factory-targeted-regression / 1 | 回归执行期间又编辑公共 runner，完整性保护阻断 1 项；另 73 项通过 | 冻结源码后整组重跑；这一次不计全绿 |
| rules-document-lock-check / 1 | 演练驱动误用 Python 执行 Bash 脚本 | 保留调用错误；改为逐项运行正确的 Python 锁/文档检查入口，不冒充整库全测通过 |
| final-analysis-spec / 2 | 模型生成了必填关系 id，但把需求第 1、4 条非连续拼接成一条 quote，来源校验拒绝 | 保持门禁；只重跑 clarification 及后续，不手工改引用或通过状态 |
| clarification-evidence-retry / 2 | 单纯重试仍拼接 quote | 强化 Skill 的连续引用说明；审核并重新锁定后从 classify 重跑，后续来源校验通过 |
| quote-analysis-spec / 2 | 独立 Spec 审查把 Markdown 表格空格重排，部分证据 quote 无法逐字命中 | 保留来源门禁和原日志，只重跑独立审查；不把模型 verdict=passed 当成门禁通过 |
| spec-review-anchor-retry / 2 | 部分 acceptance covered 仅引用 Spec 自身 | 把逐项上游证据写清到 Rules，审核后从 classify 重跑；没有放宽“不能 Spec 自证”的门禁 |
| review-evidence-baseline / 1 | 新模板把 Factory 复制的 commitlint 配置当作业务源码扫描，ESLint 阻断正常 Hook | 修模板输出范围、实测 src 仍受控；工程更正与交接记录纳入隔离基线，Hook 重试通过 |
| factory-preflight-implement / 2 | AI 已生成两处代码，但旧任务初始 HEAD=cefb23b，工程修复后的 HEAD 已改变；实际报错 HEAD changed during Factory execution | 保存父任务源码与 diff，只还原明确属于该任务的两处源码，使用关联修订从当前工程基线重跑；没有刷新/篡改旧基线 |
| final-revision-analysis-spec / pending_spec_review | 审查发现 AC-006 的治理/范围不可变约束缺少专属验证方法；只列质量与覆盖率命令不足 | 保留 high finding；补受验证返修反馈，新关联修订重新分析和映射，不反复重跑审查碰绿 |
| closure-analysis-spec / 2 | 模型把 NOT_RUN 写进 verification_hint，但漏写 acceptance_candidates.status 必填字段 | 结构门拒绝；同基线从 requirement 重试，不人工给产物补字段 |
| final-feedback/revision/acceptance-regression / 1 | 运行定向回归时继续编辑公共 runner，完整性门发现运行时代码变化 | 不计为全绿；最终冻结 reviewed-local 后运行整套回归，独立于后续规范文档编辑 |
| browser-type-red / 0（预期 1） | 红端误选尚未包含 local 一行修补的旧冻结版本，未复现被复审发现的问题 | 保留失败收据；准确重建被拒的一行改动后，2 项正常断言确实失败，不用测试错误冒充缺陷复现 |
| final-review-verify-await-browser / 2 | complete 的 AI 实现成功后，review 仍用 live App.vue 验证编码前 OBS-008 引用 | 原实现与 diff 归档，只恢复该任务唯一改动 App.vue；把源码观察冻结前移至需求阶段，新关联任务从当前基线重跑，不改写历史产物 |
| requirement-history-independent-green / 1 | 4 项测试执行中仍有 runner 修改，完整性门在 1 项中拦截 | 冻结 reviewed-history 后 4/4 重新通过，另由独立复审再跑 4/4；原失败保留 |
| final-review-verify-await-browser / 2 | 编码成功后，旧源码 observation OBS-008 的原文不再存在于 live App.vue；恢复仍按现行源码校验历史 | 保留实际红端和生成代码；不改需求或通过状态，补独立分析快照，归档代码后建立关联修订重跑 |

运行时更新仅在人工审阅差异后刷新 runtime-lock 和 installation 摘要，另建隔离仓本地治理基线。没有编辑旧阶段产物来绕过失败。

## 5. 阶段结果

同一冻结基线的重试和负例保持原 task_id；基线变化后按修订协议创建新 task_id 并关联父任务。完整命令收据见归档的 `evidence/nodes.jsonl`；Factory 每次 attempt、阶段输入输出摘要、恢复命令和错误上下文单独保留。运行中的阶段不视为通过。下表先记录父任务中已实际通过的分析与反例，最终修订的阶段结果另列。

| 节点 | 实际结果 | 证据 |
|---|---|---|
| classify | frontend-no-design，本地执行，需浏览器验收，远端交付否定生效 | classification.json，含逐项领域/设计/远端决策 |
| skill-discovery | 只选择 factory-acceptance，背景中的测试/API等词未扩散命中 | skill-selection.json + 路由策略快照 |
| design-analysis | not_required；不制造 Figma 通过状态 | design-orchestration.json |
| requirement / clarification | 结构、来源和输入绑定通过，6 个行为候选对应已确认规则，无待决问题 | requirement-validation.json / clarification-result.json |
| spec / spec-review | 已通过，6 条 AC、2 个命令检查、2 个本地浏览器检查；无外部服务前置项 | spec-validation.json / spec-review-result.json；分析 attempt 20261103T065045-254bbf689fdf |
| 恢复授权负例 | 不带新授权恢复，退出 2，授权上限仍为 spec-review | authorization-ceiling-negative 节点 |
| docs 漂移负例 | 编码规范加入无业务含义的注释，退出 2，明确指出 content-changed 与前后摘要 | docs-drift-negative；attempt 20261103T065704-e5ebee0a04d1 |

漂移反例结束后撤除探针，实际 SHA 恢复为 `20c67971725c876fdd978235463376177434f35f1e6cae6bc67e4a097c6aebf2`，并核对 src/docs 无差异。随后显式授权进入 implement，不让普通 resume 自动提权。

父任务 AI 生成的 `src/App.vue`、`src/styles.css` 已归档至其 `implementation-source-snapshot/`（含文件摘要与 source.diff）。恢复前逐项确认这两处都是本次 AI 改动，恢复后 src 与当前 HEAD 无差异。新任务引用这份证据时角色为 historical-only，`inherited_passes=[]`；任务状态通过 `superseded_by`、`parent_task_id` 双向关联。

### 5.1 真实 Spec 返修结果

第二个任务审查指出 FND-001：AC-006 要求治理和覆盖率策略保持不变，但 V-QUALITY/V-COVERAGE 命令本身不能证明这一点。closure 任务由运行器完整复验历史报告、上下文文件和证据引用后，将诊断交给 Spec 节点；真实模型新增 V-MANUAL-INTEGRITY，逐项要求源码快照/范围、治理文件内容及命令结果证据。独立 Spec 审查重新通过，未删除 AC-006、放宽阈值或继承旧通过状态。证据位于 closure 的 attempt `20261103T073209-972a84cdfb62`，含 `spec-review-feedback.json` 及独立审查输出。

### 5.2 独立复审与后续加固

增量复审已关闭本次发现的 P0/P1；已明确 browser 与 integrity 类型，禁止 command 冒充人工证据，失败状态优先，修订 ID 限制为 1–64 位安全标识且不继承状态。源码历史校验的最新专项 9/9 通过（157.661 秒），增量经独立只读复审；覆盖完整基线和逐文件摘要、协同篡改、Review→Verify、重复实施、范围与 HEAD 阻断、敏感引用在读取前拒绝以及隔离后的实际归档。独立审查者实际另跑的是前序 4 项历史恢复和 8 项验收汇总，不将其表述为另跑最新 9 项。专项夹具证明协议逻辑，不能替代真实浏览器。

非阻断后续项：新 Spec 未声明 required browser 时，目前 Rules 要求补齐、最终 verify 会保持 pending；尚未把这项检查前移为 Spec 的强制机器门。command 类型限制已实现，但负例暂直接覆盖修订投影，未单独构造完整 Spec 钉死同一分支。技术反馈回归未穷举每个字段和路径/符号链接组合；输入摘要与模型改写保护不等于操作系统级沙箱，外部进程恶意替换 inode 的竞争不在本次保证范围。

可选 `references` 的单文件版本正确性目前由协议和独立审查约束，不能称为每个可选引用都受机器校验；可信版本依据仍是顶层输入绑定和已登记的 docs 快照。

来源保护的保证范围是运行器在 quote/body 读取前拒绝已识别的敏感、治理和非基线路径，并将明确违规的需求契约与文档替换为无引用正文的隔离壳；不宣称可以拦截恶意模型自行读取任意宿主文件、在其他字段/外部 OMP 会话输出密钥或外发数据。那需要独立的宿主权限与输出治理。

本次明确登记的来源保护 P2：自定义 perimeter 尚未严格拒绝绝对路径、`.`、`..`；敏感文件名列表不穷尽嵌套 AWS/Docker 配置和 `credentials.json` 等名称；retained 主要比较字节，同字节的普通文件替换成 symlink 不会在当前模型阶段立即识别，后续历史路径校验会拒绝。当前演练采用规范化相对周界、无凭据业务工程；不将其外推为任意仓库的保密保证。

### 5.3 最终本地闭环

任务 `local-20261103-4ec147c4` 从 2026-11-03 08:03:24 UTC 开始，最终于 08:15:59 UTC（北京时间 16:15:59）完成，`status=completed`、`rc=0`、`recovery_command=null`。6/6 AC、5/5 必验检查通过，`browser_status=passed`、`integration_status=not_required`。这是本地闭环，不是测试环境、Figma 或远端流水线验收。

| 节点 | 结果与耗时 | 主要证据 |
|---|---|---|
| classify → requirement → clarification → spec → spec-review | 367.841 秒、退出 0；frontend-no-design，仅选中 factory-acceptance，无未决澄清，6 条 AC | attempt `20261103T080324-ddef56362c25`；分类、技能选择、分析及 Spec 校验、独立审查输出 |
| preflight → implement | 74.429 秒、退出 0；真实环境检查通过，唯一实际修改 `src/App.vue` | attempt `20261103T080955-55f1e38dcb95`；环境请求/结果、实施摘要与范围收据 |
| review → 首次 verify | 176.962 秒、退出 3；独立评审及质量命令通过，仍明确缺三项人工证据 | attempt `20261103T081114-344152bddc5a`；review-result、质量日志、verification-result 的 pending_acceptance |
| 真实浏览器 | 44.509 秒、退出 0；Chrome 156，1280×900、375×900 均通过 | browser-evidence/interaction.json、两宽度初始/点击后/键盘焦点截图；截图已实际核看 |
| 治理完整性 | 0.719 秒、退出 0；仅 App.vue 变化，50 个范围外文件的内容/路径/模式/索引保持一致，HEAD 未变 | browser-evidence/scope-integrity.json，含两命令真实退出码及日志摘要 |
| 收据封装 → 恢复 verify | 封装退出 0；最终恢复 96.121 秒、退出 0，质量及 Spec 命令重新执行 | browser-evidence/verification-evidence.json；最终 attempt `20261103T081423-ba88dee7c9fc` 的收据归档、verification-result 与 final/result |

浏览器逐项断言初始 0/禁用重置、三次加一得到 1/2/3、正数可重置、刷新归零、无 localStorage 写入、无 XHR/fetch/外部请求、无运行错误、Tab 可见焦点和 Enter/Space 操作。两宽度 html/body scrollWidth 均等于 viewport，按钮计算圆角均为 4px。完整性与浏览器证据绑定同一 task/request/source/Spec/contract 摘要后才提交，命令通过未代替手工检查。

覆盖率命令实际运行现有 health 单测：`src/lib/health.ts` 行覆盖率 1/1、100%，满足其普通文件 80% 门槛。实际业务修改是 SFC，未新增独立 TS 逻辑或组件单测；这个覆盖率结果不代表计数器组件达到 80%/90%，计数器行为的本次证明来自独立浏览器断言。

### 5.4 提交前增量与验证版本差异

完整业务演练使用 `reviewed-history` 冻结字节。最终待提交运行器另外补充了来源隔离防护：禁止把未纳入基线、治理或凭据路径作为业务源码来源；快照保存分析整树并核对原始文件摘要；拒绝归档符号链接；保护归档 manifest/body；受限需求产物隔离后不作为正式证据继续归档。该增量由单独 9 项定向回归和只读复审覆盖，不能把冻结版完整演练结果解释为增量字节也已重跑完整模型链路。

隔离只约束被拒的需求正文、契约及来源快照，不是对所有日志、模型 stdout/stderr 或会话的通用脱敏保证。不要输入真实密钥；不能用此防护宣称模型会话不会接触或输出凭据。

## 6. 回归与交付边界

- Bootstrap：24/24 单测通过，覆盖新仓、项目检查模式、治理漂移和升级迁移。
- Factory 定向回归：74/74（setup 10 项 + docs、否定远端与技能路由相关用例）；分类另覆盖 25 组正反场景。
- reviewed-local 冻结版：119/119 标准库回归（570.429 秒），项目薄适配 1/1；reviewed-history 冻结版：123/123 标准库回归（926.142 秒），项目薄适配 1/1。完整业务闭环采用后者；提交前来源隔离增量单列，不混称同一版全量回归。
- Rules Bootstrap 真实 ESLint 产物/业务源码正反例 1/1 通过；plugin-lock、文档新鲜度、273 个文档链接和 diff-check 通过。
- 隔离工程仅本地基线提交，无 remote、无 push；公共仓后续提交推送使用独立授权，不回写演练时来源 HEAD。
- 不用模型工具存在、静态检查或截图文件存在替代真实交互/环境验收。

公共仓配套版本发布后仍需在团队标准环境复核；旧参考 SHA 不含新能力，不能与新版命令混用。本次演练按冻结文件摘要执行，发布不会将其变成对新 commit 的重新演练。

## 7. 发布记录与证据保管

用户后续明确授权“提交推送改动”。Rules 首批修复提交 `749d276d32a8659007d7932f8e33a6c593457692`，另补来源隔离配套协议 `a52770e`，均推送至 `feature/figma-visual-guard-final`；Factory 在本轮验证与报告核对完成后提交至 `feature/local-init-acceptance`，实际提交身份由 Git 历史记录，不伪造自包含 commit SHA。

逐节点原始日志、截图、任务收据和冻结快照保留在本机 `.factory/artifacts/onboarding-rehearsal/20261103-iTbmCR/`，该路径明确受 Git ignore 保护，不随公共仓推送。归档含可核验文件摘要，原绝对路径仅用于来源追溯；它不是携带 Git 历史、依赖和认证的可直接恢复发行包。团队可以取得共享报告与永久回归测试；完整原始证据需要另外按权限交接，不能声称 clone 公共仓就能获得本机日志。
