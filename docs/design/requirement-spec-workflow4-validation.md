# workflow/4 需求与 Spec 链路整改记录

本轮只改 `awesome-factory` 公共本地运行时与 `awesome-rules` 方法 Skill，不改业务仓代码，不提交、不推送。

## 职责调整

1. `factory-requirement-analysis` 负责证据、行为候选、待澄清项和验收候选；运行器只提供绑定并校验结构、引用和 Skill 使用。
2. `factory-clarification` 升级为候选决策中心：每个需求候选必须一对一解析为确定规则或 `blocking=true` 的真实问题。
3. `factory-spec-authoring` 负责把已确认规则写成 Spec；每条 AC 同时映射 requirement、clarification 与 verification。
4. 新增编码前 `spec-review`：全新无会话、read/grep、只读快照，覆盖需求候选、澄清规则和 AC；gap/uncertain、critical/high 阻断。
5. Factory 内嵌提示只保留输入、输出、绑定、可信命令和禁止副作用；方法正文由 Rules Skill 提供。

领域 Skill 目录内全部受控常规文件由 skill-discovery 记录逐文件摘要，包括正文、规则、references、scripts、templates 与测试夹具；缓存和运行产物除外。后续阶段和恢复前复核。Spec Review 快照包含完整命中 Skill、编码前源码和上游契约，不能由 Spec 自证需求真值。

## 独立审查修复

独立复核发现 `needs-clarification` 候选可映射到非阻塞问题的绕过，以及领域 Skill 摘要遗漏同级规则、脚本和模板。现已强制 question 解析绑定 `blocking=true`，并将 Skill 摘要扩展到目录内全部受控常规文件；两项均增加负控。另补 Spec Review gap/绑定/上下文篡改负控，以及分析授权默认截止 `spec-review` 的测试。

## 验证边界

- `awesome-rules`：7 组技能套件、incident regression、79 个 badcase 和 doc-freshness 通过；3 个新 Skill 与升级后的澄清 Skill 通过 skill-creator 校验。
- `awesome-factory`：workflow/4 针对性 15 项回归通过，覆盖完整新链路、修订任务、澄清回放、非阻塞问题绕过、领域 Skill 漂移和 Spec Review 负控。
- 语义审查仍是静态独立模型判断，不是人工批准；命令、Mock、浏览器、Figma 和测试环境验收仍须各自证据。
- `awesome-rules` 完整 `npm test` 的 pytest 套件可在临时依赖环境通过；默认本机 Python 缺 pytest，且历史回放快照内有 8 条既存相对链接被 md-link 层报告，本轮未改写历史输入。
