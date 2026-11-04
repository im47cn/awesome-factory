# 本地执行可靠性整改验证｜2026-11-04

本轮只修改 awesome-factory 与 awesome-rules，真实 admin/developer 业务仓未修改，两个公共仓未提交推送。演练目录的本地基线提交已获用户单独授权，无业务远端、无推送。运行使用已审核工作副本的固定字节，不是已发布提交重新 clone 的证明。

## 1. 职责与修改清单

| 能力 | Factory | Rules / 业务仓 |
| --- | --- | --- |
| 运行隔离 | 同工作树 runner/quality/setup 租约；Git 元数据内固定运行时代码副本；独立 worktree 创建入口 | 业务仓准备干净基线、固定依赖和项目命令 |
| 契约补全 | 从可信输入填 schema/任务/版本/摘要、选定引用的单文件sha和 NOT_RUN；冲突绑定拒绝，格式修复有限次 | Rules 编写有来源的业务内容，不代填人类决定 |
| 协议一致 | protocol.py 生成 Spec 机械说明并检查漂移；Spec 提前拒绝漏 browser、类型冒用、非法周界、失真引用 | Spec/Review 共用生成说明；需求方法约束事实/推断/未知项 |
| 文件角色 | 明确根目录公开配置 source→docs，记录纠正；不改内容、候选和业务状态 | 不把工程配置事实升级为业务真值；未知/治理/凭据不按白名单放行 |
| 自动浏览器 | 固定 Playwright 驱动、服务生命周期、断言/视口/截图及按 Spec ID 归档；计划编码前绑定且只读 | 业务仓预先审核断言、数据、入口；Rules 定义验证方法与边界 |
| 完整性 | source-integrity 证明 HEAD、范围及保护文件内容/模式/索引 | 不能据此证明业务语义或替代 Figma/接口验收 |
| 条件复用 | 同 attempt 精确 argv/输入/环境/依赖/日志绑定；质量和直接 Spec 命令共用任务环境 | 维护者登记其他实际依赖，无法绑定时重跑 |
| 自动复盘 | task/attempt/stage 事件、命令日志、退出码、耗时、恢复指引、execution-summary | 等待、阻断、失败区分统计，不用非零命令数算失败率 |

更新四个现有 Skill：factory-requirement-analysis、factory-spec-authoring、factory-spec-review、factory-acceptance。未新增重复 Skill；未改变普通80%/核心90%覆盖率策略。

详细配置和升级命令见 [本地执行可靠性](local-reliability.md)。

## 2. 独立审查及修复闭环

独立子代理多轮审查全部实现增量，实际复现并关闭以下问题：

- 编码前计划未冻结：增加内容/路径/模式/索引绑定，禁止计划进入 allowed_files。
- 初始及交互后跨 origin 可误报本地通过：目标、跳转、步骤和截图前后核对，监听主 frame/popup。
- 忽略依赖漂移仍复用：依赖实际内容参加指纹；外部链接/不可绑定时保守重跑。
- 质量/直接 Spec 控制环境不一致：共用当前 task/attempt/stage 注入函数，两条路径及 stale 负例实跑。
- 评测零案例假绿：强制 pack/run 案例全集、非空检查、唯一 ID、文件清单及冻结输入。
- worktree 半创建失败：add 前预检，post-checkout/配置失败保留私有日志及明确回收路径。
- 新测试 helper 与 Rules 夹具未锁：纳入 test_files/test_rules_files，改动后在套件启动前拒绝。
- 未校验需求额外 raw 副本、补全提前失败的隔离旁路：只保存原始摘要，已识别受限路径在补全前、terminal event、partial 归档前检查并统一隔离。
- 隔离写穿输出/固定临时 symlink、循环来源先解析：JSON/隔离输出安全原子替换，拒绝父链接，失败 snapshot 拒绝不安全链接；静态来源拒绝先于解析。

引用增量再由独立审查者运行两项测试通过（5.095秒）：缺历史拒绝、request-only可用、历史摘要保持、foreign拒绝及冲突不覆盖。最后专项复审确认无新的可操作阻断。独立测试为分批次定向验证，含重叠，不将其相加宣称覆盖率。最终文档与证据核对仍以收尾记录为准。

## 3. 验证结果与版本边界

| 检查 | 结果 | 外层命令耗时 |
| --- | --- | --- |
| 首轮冻结公共回归 | rc=0；Ran 151 tests in 622.859s | 623.350s |
| 来源/浏览器主干冻结回归 | rc=0；Ran 155 tests in 731.958s | 732.488s |
| 环境绑定冻结回归 | rc=0；Ran 157 tests in 848.878s | 849.370s |
| 来源/归档/环境绑定冻结回归 | rc=0；Ran 161 tests in 795.098s | 795.528s |
| 最终单文件引用补全冻结回归 | rc=0；Ran 163 tests in 767.482s | 767.847s |
| 初始化专项（pytest） | rc=0；10 passed in 45.27s | 46.425s |

- 独立静态方法评测：4个精选案例、10项判定一致，覆盖条件遗漏、失败选择保留、构建冒充业务验收、擅自定案与小文案任务。最终引用版本重新准备输入并由新的独立评审者生成10项答复，执行者不读取 oracle；评分检查绑定和逐字引用。oracle_status=maintainer-review-required，范围为 curated-proxy-evaluation，不是生产需求正确率。
- 四个相关 Skill 的 quick_validate 已用实际缓存 PyYAML 通过；纯格式/发现验证不代表方法语义通过。方法语义另有上述独立评测。
- Rules plugin-lock、文档新鲜度、机械协议同步、diff-check 已通过；最终新文件链接扫描以冻结目录 Git tracked 全集为准。
- 各轮回归分别绑定其版本，不把151/155/157/161等历史轮次替代最后版本结果，也不叠加成成功率。

## 4. 真实本地试点

工程从先前审核基线 bb2f8142ca7a034aea9b21b7613b5bd11fe4e17a 复用 Vue/TS/Vite、规范、Hooks 和健康单测；新目录重新安装固定依赖、初始化/升级 Factory，真实 OMP 重新分析、澄清、写 Spec、审查和编码。这不是再次由 AI 从零生成覆盖率基础设施。

需求是计数器：初始/刷新0、每次加一、重置状态、无存储/API、1280与375视口、4px圆角及治理策略不变。业务计划由原始需求预先审核并固定，实施者不修改验收预期。

- 前序完整样本 local-20261104-cfd51c58：7AC/7必验项、8张截图通过，耗时1019.041s（运行器），但不含最后环境/来源归档增量，不能给最后版本背书。
- 来源分类样本 local-20261104-ac01bed0：模型将 package.json 误标为业务 source，被正确拒绝，没有进入业务编码。此失败促成确定文件角色与补全前隔离整改；旧失败尝试保留，不改成 passed。
- 引用样本 local-20261104-fcc94485：第一次 Spec 错填浏览器计划摘要，普通 resume 后又将外部 Rules Skill 路径放入业务 references，两次均正确拦截且未编码。由此补齐“只选本仓路径、单文件摘要由 Factory 填、无历史不取当前源码”的职责；新版本不继承旧状态，从新任务 classify 完整重跑。

最终样本 task_id=`local-20261104-288b3d61`，attempt_id=`20261104T043318-fd3b39363aad`，当前 `verify/completed`。

| 已结束尝试 | 结果 | 耗时 |
| --- | --- | --- |
| 20261104T043318-fd3b39363aad | completed / rc=0 | 722.252s |

| 阶段 | 结果 | 耗时 |
| --- | --- | --- |
| classify | completed | 0.041s |
| skill-discovery | passed | 0.173s |
| requirement | completed | 166.925s |
| clarification | completed | 65.602s |
| spec | completed | 119.011s |
| spec-review | completed | 89.489s |
| preflight | completed | 3.478s |
| implement | completed | 72.563s |
| review | completed | 119.225s |
| verify | completed | 84.459s |

最终状态 completed/rc=0，6AC、7必验项通过；改动：src/App.vue；本次尝试耗时722.252s（不含其他样本和等待）。

验证计数：`{"reused_commands": 0, "executed_spec_commands": 2, "browser_status": "passed", "missing_required": []}`。未交手工收据、未手改模型输出；浏览器和完整性证据由 Factory 自动执行/校验/归档。

独立证据复核 `passed`：8张PNG（1280×900/375×900）、53个受保护文件、当前树/任务/Spec/收据/归档摘要逐项匹配，端口已清理。桌面和移动截图已人工查看；这是本地行为验收，不是Figma视觉还原。

机械补全实际结果：

```json
{
  "requirement": {
    "filled_fields": [
      "browser_plan_sha256"
    ],
    "format_repairs": 0
  },
  "clarification": {
    "filled_fields": [],
    "format_repairs": 0
  },
  "spec": {
    "filled_fields": [
      "schema",
      "task_id",
      "request_sha256",
      "requirement_sha256",
      "requirement_contract_sha256",
      "workflow_version",
      "clarification_sha256",
      "business_sources_sha256",
      "skill_selection_sha256",
      "source_sha256",
      "browser_plan_sha256",
      "references.0.sha256",
      "references.1.sha256"
    ],
    "format_repairs": 0
  }
}
```

普通 health.ts 单文件覆盖率结果不代表计数器 SFC 达到80%/90%；计数器行为由真实浏览器断言证明。本轮没有 Figma、真实业务接口或测试环境部署验收。

条件复用的正向命令计数和环境差异负例均实际运行。前序与最终真实样本均复用0次；本轮两个质量命令均rc=0，但执行前后总输入指纹不同，按协议拒绝复用。当前收据未细分变化分量，不断言具体缓存文件是原因。严格输入绑定不满足时保守重跑；不能据此宣称真实 Vue 项目已提速，后续性能优化不得忽略会影响结果的输入。

## 5. 失败与操作恢复记录

- Python 环境没有 PyYAML/pytest，PyPI/包注册表网络失败；使用已有实际缓存包运行开发验证，不修改全局环境。
- Playwright 注册表不可达；使用已有安装缓存1.63.0和core离线打包，记录tar摘要，项目依赖安装真实完成，不以工具存在代替浏览器证明。
- 一次误用 unittest 运行 pytest 函数，收集0项，已明确作废并用pytest真实跑10项；不将退出0当作验证完成。
- 新测试夹具把 evals/README 锁入方法 Skill，导致提前拒绝而非目标负例；改为与正式 lock 共用 skill_inputs 清单，重跑适配和专项。
- macOS /var→/private/var 别名造成测试路径断言失败；按真实 resolve 比较。
- 新增引用测试起初未建立需求历史快照，不能证明历史绑定；补真实冻结 fixture，并将生产路径缺历史时改为直接拒绝，专项重新通过。
- 升级后 tracked 治理配置尚未形成新基线时变异G04正确SKIP，检查未完成；仅在获授权的临时目录建立本地基线后重跑通过，未降低门禁。

## 6. 证据保管与能力边界

原始控制/日志目录：`/private/tmp/factory-reliability-20261104-468t80em/control`。持久归档目标：本仓忽略目录 `.factory/artifacts/local-reliability/20261104-468t80em`；归档包含 manifest.json、archive-verification.json 与 relocation.json，完成状态以最终校验收据为准；收据原绝对路径保留，按 relocation.json 的根目录映射定位归档文件。原始日志/截图不随公共代码提交，复制共享报告不等于取得全部本机证据。

冻结副本的 Git HEAD 是本地演练来源，不是公开发布版本；公共原始HEAD线索与逐文件摘要另存source-freeze文件。归档不含Git历史、依赖安装树和认证，不是可直接运行的发行包。

保证限于正常协作入口、被识别的类型化来源路径和这些机器绑定；不宣称OS沙箱、操作者签名、通用日志DLP或对任意外部输入的自动依赖跟踪。格式不可解析/其他字段/外部模型会话敏感输出仍须项目隐私规则处理。
