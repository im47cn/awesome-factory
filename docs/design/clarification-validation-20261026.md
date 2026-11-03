# 澄清方法与来源协议验证（2026-10-26）

本记录覆盖本次“合并两份澄清方法、补业务规则来源协议、建立历史需求评测集”的工作树增量。此前 shared-local-workflow-validation.md 是 workflow/2 公共化记录，不能替代本次 workflow/3 证明。三仓未提交、未推送，既有业务源码与历史任务保持原样。

## 交付范围

- awesome-rules：factory-clarification 统一交互主持和 Factory 节点方法；按需加载输出模板、交接、来源与场景参考；独立审查也核对冻结规范与实际语义。原 admin clarify-requirements 仅保留项目入口和文档位置。
- awesome-factory：business-source-policy/1 → business-sources/1 快照 → clarification/2 → Spec/独立审查全链路，workflow 升为 /3。登记资格、精确摘要、适用文件、输入保护、AC 映射及旧版本拒用由公共运行时控制。
- admin：配置登记现有回调模块 README 的原文 SHA256 和适用目录，旧 event-def-api 草案排除为历史；更新薄 bridge、内容锁和安装摘要。业务正文继续在现有 docs/模块文档；没有修改业务行为或覆盖率阈值。

来源配置是可信维护者登记，不是模型自签审批；project-rule 只表达保留约束，不能授权新增行为。未声明的语义冲突、引用是否真正支持结论仍依赖方法与独立审查。文件摘要与工具范围检查不提供 OS 隔离或独立身份签名。

## 历史回放

评测集在 awesome-rules/skills/factory-clarification/evals：8 条历史需求原话、2 条明确标注的合成冲突/注入用例。有限回放上下文、旁证提交与独立判据分离；不以修复后的实现作为答案。参考判据状态仍为 curated-proxy-pending-human-review。

| 轮次 | 输入与处理 | 实际结果 |
| --- | --- | --- |
| 首轮 | 独立生成者仅见 work 内原话、方法和冻结输入 | 10 例因 forbidden 被写成数组而结构拒绝；协议缺少类型说明，随后修正为字符串，未放宽运行器 |
| 第二轮 | 新的独立生成者，使用明确类型的协议；判读者独立于生成者 | 结构检查 10/10；代理判据通过 8/10；发现 H04/S09 的参考判据把未决选择/来源冲突强制当成已确认行为规则 |
| 判据修订复核 | case-pack 由 .1 升为 2026-10-26.2；按独立意见修正上述两例 | 复用第二轮盲候选，仅重绑新任务 ID 与输入摘要，不改规则、问题、假设、冲突；不称为新一轮模型生成 |

修订后工具 score 为 proxy-passed：10/10 案例、19/19 已确定规则判据、4/4 必要问题；独立判读的 16 条候选规则均有支持，0 多余问题、0 臆造决定、0 uncertain。企业切换、七天边界/超限处理、规范冲突仍正确保留待澄清状态；评测通过不等于这些业务问题已获答复。H04 仍将示例日期视作场景，不允许日期特化。

前后判据分母不同，不能当作准确率提高。没有人工正式审核标准答案，没有同输入 Superpowers 基准对照，也没有生产准确率结论。

三轮全部冻结输入、候选、原版与修订版 manifest/oracle、独立判断及失败结果保存在 admin 的 `artifacts/factory-local/clarification-upgrade-20261026/`。归档仅重定位 harness 的 workspace/pack 路径；原 run.json 另存 run.original.json，输入/候选/判据摘要不变，三轮均已在归档目录重评分。生成者工作区内无 oracle；归档不是防同权限进程访问的安全沙箱。

## 回归与项目质量

环境：Node 22.22.3、pnpm 10.28.1；共享回归由项目 Python 薄入口执行，旧 pytest 使用现有独立 Python venv。以下为最终冻结版本的实际结果，测试数量存在命令间重叠，不相加作为总用例数。

| 检查 | 结果 |
| --- | --- |
| admin `.factory/run.py --check-runtime` 与公共锁检查 | 通过 |
| 公共 Skill 及 admin 薄 Skill quick_validate | 通过 |
| factory skill routing pytest | 2 通过 |
| 公共 local 控制回归（经项目 test:factory） | 91 通过 |
| admin 适配/格式回归（同入口） | 3 通过 |
| 旧模板运行时、setup、外部七阶段收据 pytest | 168 通过 |
| 隔离项目 setup check / 变异样例 | 通过，无 SKIP |
| 隔离项目统一 quality-gate | 9/9 通过 |
| 其 test:ci 内前端测试 / 单测覆盖率测试 | 522 / 497 通过；不是互斥分组 |
| 普通80/核心90逐文件门禁、报告完整性与时效 | 111 个治理文件通过；行 98.28%（2343/2384） |

统一质量入口实际执行格式、ESLint、Stylelint、类型、test:ci、三项契约检查及 build:dev。build 产生的 components.d.ts 已按显式生成文件策略恢复，收据记录 restored=true；没有用生成结果污染原工作树。

原树 setup check 的 4 项变异因 MISSION/config 已有人工作树修改而 SKIP，汇总如实为 BLOCKED；不能把它写成原树全通过。为完整验收复制当前源到临时 Git 仓，使用原项目依赖与配置定位的公共仓；只在临时仓建立输入基线。原有无关需求文档 `docs/requirements/callback-batch-disable-real-20261026/requirement.md` 存在格式问题，仅在隔离副本格式化；原件未改。isolated-input.json 与 validation-only-variation.json 记录精确差异。

早期共享回归有一项错误文案断言不一致，已保持兼容措辞；旧 pytest 首轮因主会话并行编辑 runner.py 被运行时漂移检查拒绝。冻结代码后重跑的最终结果以上表为准，原失败日志仍保留。

独立代码审查发现工程假设可以伪填 confirmed_by 的 P2，已对所有假设统一校验 null/真实操作者答复 ID，新增负向回归并独立复审通过。工程自主决策无须业务审批，但不得冒称用户已确认。

## 证据与未验边界

admin `artifacts/factory-local/clarification-upgrade-20261026/` 含 result.json、quality/receipt.json 和各原始日志、原树/隔离 setup 记录、旧失败记录、三轮可复评分归档及范围审计。证据目录不入 Git；公共方法、数据集、工具、回归和本验证文档是可审阅交付物。

本次仅做本地控制回归、独立代理回放和隔离项目质量检查。未进行付费 OMP 真实节点调用、真实后端/权限/浏览器 UAT、远端流水线、提交推送 hook 实测、人工判据批准或 Superpowers 对照。这些状态不能被本地 passed 替代。旧 workflow/2 任务须保留 attempts 并从 classify 重新生成，不改旧通过收据。
