# 公共本地 Factory 运行时

方法在 awesome-rules 的 factory-requirement-analysis / factory-clarification / factory-spec-authoring / factory-spec-review / factory-environment-preflight / factory-independent-review；编排、状态、证据校验、恢复和回归在本仓 runtime/local 与 tests/local。项目 .factory 只放配置和薄适配，以及项目原有治理 guard/MISSION/缺陷样例。业务知识继续保存在项目 docs，任务目录保存来源与证据快照。

阶段：classify → skill-discovery → design-analysis → requirement → clarification → spec → spec-review → preflight → implement → review → verify。requirement 提取证据和行为候选，clarification 只负责把候选确认成规则或问题，Spec 独立审查通过后才允许进入实施。未知业务规则、环境缺口和审查阻断有各自 pending 状态。只做 local-mock 不取消冻结 Spec 的真实环境必验项。

## 接入与升级

新项目：先准备 Git、Python 3.10+、Node >=24.19.0、项目固定 pnpm/依赖、OMP 及 awesome-rules，执行：

```bash
python3 /path/to/awesome-factory/tools/factory-local-setup.py init /path/to/project
```

只初始化前端 package.json 项目，不联网、不安装依赖、不调用付费模型或提交推送。已存在 .factory/MISSION 时拒绝覆盖。模型可调用性与业务验收留待真实任务。

项目根目录常用入口：

```bash
python3 .factory/setup.py check .
python3 .factory/run.py --check-runtime
python3 .factory/run.py --task pilot --request-file /path/to/request.md --only-stage spec-review
python3 .factory/run.py --task pilot --request-file /path/to/request.md --resume --authorize-through verify
python3 .factory/quality-gate.py
python3 .factory/test.py
```

扩大授权只在用户已明确允许实施后执行；普通 resume 不自动扩权。`test.py` 运行公共回归和项目适配回归，项目 test:ci 应继续包含此入口。

upstream_path 与 rules_path 可为相对或部署机上的明确路径，没有公共仓时 fail closed；不自动下载，也不回落到项目旧实现。部署时须同时提供锁定版本的两个公共仓。不默认兄弟目录名必须匹配，只使用配置路径。runtime-lock.json 锁定公共代码和六个方法 Skill 正文及全部 references 字节；Git HEAD 只作来源线索，不替代内容摘要。

升级须先核对公共仓 diff，在项目配置显式选择六项 workflow_skills，核对固定十一阶段和项目命令/版本，再由维护者运行：

```bash
python3 /path/to/awesome-factory/tools/factory-local-lock.py /path/to/project --write
python3 .factory/setup.py check .
```

`--write` 是显式接受已审查字节，不是自动升级机制。普通执行/check 永不刷新锁。项目配置/适配调整后也要审核并更新 installation.json 的本地文件摘要；不自动覆盖现有接入或任务。新公共摘要/旧流程收据不兼容时从 classify 重跑，旧 attempts 原字节保留。不要手改旧 passed 来恢复。

## 门禁与证据

- 需求分析：原始输入、已选 Skill 和最小必要证据冻结为 requirement-contract；事实、推断、待澄清行为和验收候选分离，引用必须命中原文。
- 领域 Skill：skill-discovery 为命中 Skill 目录内全部受控常规文件记录逐文件摘要（含正文、规则、references、scripts、templates 与测试夹具，排除缓存/运行产物）；需求分析、Spec 和恢复前均复核，Spec Review 使用冻结副本，任一依据漂移须从 skill-discovery 重跑。
- 澄清：逐项覆盖需求候选，解析为可观察规则或真实问题；原始需求/操作者答复精确引用、稳定问题 ID、行为假设确认。未答复不可被模型删除或降级。
- Spec 与编码前审查：所有 AC 映射需求候选、澄清规则和验证方法；全新只读会话从上游真值独立核对，不能由 Spec 自证。
- 预检：按 Spec 推导环境义务，执行配置命令；环境地址、契约和身份权限分别提供有时效的无凭证证据。默认 24 小时，verify 复核。
- 审查：新进程、read/grep、独立只读快照；原始请求/答复/AC/准确 task.diff/current source，不给实现者自评。critical/high、gap/uncertain 阻断。
- 验证：逐项 required 控制完成，所有实际改动检查范围；配置质量命令统一入口，生成文件仅按显式支持清单恢复，其余源/索引变化保留现场并拒绝。

每次运行记录 task_id、attempt_id、运行时/Skill 摘要、输入/输出、原始命令日志、退出码和恢复指引。artifacts/factory-local 与缓存不入 Git，不重格式化旧证据。不保存密码、token、cookie。

当前为原生 stage-snapshot/1 + workflow/4，旧 factory-acceptance-manifest/1 外部七阶段协议独立维护，未声称已完成格式桥接。工具白名单和事后完整性校验不是 OS 沙箱或身份签名。真实接口/权限验收须提供相应环境证据。

本次迁移的实际命令结果、隔离快照差异与未验范围见 [验证记录](shared-local-workflow-validation.md)。

澄清方法已合并交互与节点模式，新增受控规范来源、范围和版本绑定。配置与迁移见 [业务来源协议](clarification-business-sources.md)，历史需求回放评测见 awesome-rules 中 Skill 的 evals/README.md。上列验证记录为此前 workflow/2 公共化记录，不代表本次 workflow/3 结果。

本次 workflow/3 的实际回放、回归及隔离质量结果见 [澄清升级验证](clarification-validation-20261026.md)。

workflow/4 的需求分析、澄清决策、Spec 编写和编码前独立审查整改及验证见 [workflow/4 验证记录](requirement-spec-workflow4-validation.md)。

本地运行隔离、机械字段补全、自动浏览器执行、质量收据复用与阶段复盘见 [执行可靠性与自动验收](local-reliability.md)。已有项目升级需要一并审核 bridge 文件清单，不只刷新旧 lock。
