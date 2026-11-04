# 本地执行可靠性与自动验收

职责：Factory 调度、隔离、机械校验、执行与证据；Rules 提供分析/澄清/Spec/评审/验收方法及案例；业务仓保留 docs、正式契约、环境、测试数据和可执行业务断言。规则与执行能力分别验证。

## 隔离和版本

同一 Git 工作目录的 runner、独立 quality 和 setup 共用工作树租约。Runner 调起的质量子进程只加入当前租约；其他检查等待原任务结束后再运行。此机制约束配合的 Factory 入口，不锁编辑器、手工测试或任意外部程序。

公共运行时按 runtime-lock 文件摘要复制到本工作树的 Git 元数据目录，从固定字节导入；不放在 Vite 监听的源码或 artifacts 中。共享仓漂移仍会阻断，不自动刷新锁。规则内容同样锁定并在阶段前后复核。

需要并行任务时，先准备干净的、已审核 Git 基线：

```bash
python3 /path/to/awesome-factory/tools/factory-local-workspace.py /path/to/project /path/to/task-workspace
```

工具创建 detached worktree，保留源分支和文件，不提交、不推送、不安装依赖、不更新公共版本锁。有未提交内容时拒绝，不丢弃或自动转移 WIP。新工作树中的公共仓路径改为原已选仓的绝对路径；在新目录安装冻结依赖并执行 setup check 后运行任务。实现保留在输出工作目录供审核，工具不自动合回。

## 机械字段与格式修复

`runtime/local/protocol.py` 是机械协议定义。`python3 tools/sync-local-protocol.py --rules /path/to/awesome-rules --write` 生成两个 Spec Skill 的字段说明；不带 --write 检查一致性。

Factory 为 requirement/clarification/spec 补 schema、task_id、版本和输入绑定；Spec 缺失的验证状态补 NOT_RUN。保留原模型输出摘要、补全字段及格式修复次数。模型提供冲突绑定或自报已通过时直接阻断，不自动“改对”。业务内容、交叉引用 ID 和验证义务不能靠补默认值猜出。

JSON/容器/验证 ID 格式错误可有限修复：`format_repair_limit` 默认为 1，范围 0..2。诊断不是新增业务授权；业务缺口、来源错误、独立审查 gap 不进入格式修复。补全记录与诊断保留在 attempt 中；需求输出须先通过来源校验，不额外保存未校验的 raw 副本，受限来源失败时只归档隔离壳和原始摘要。

Spec 阶段提前拦截遗漏必需浏览器检查、command 冒充 browser、非法周界与失真的单文件引用。可选 references 只选原始 request 或本业务仓普通文件；模型可只给 `{path}`，Factory 从需求历史快照补齐单文件 sha256，原已提供冲突摘要仍拒绝。本仓文件缺少历史快照时须重跑 requirement，不改取当前实现的版本。Rules Skill 版本由方法锁单独管理，不列进业务 references；整树摘要和 browser_plan_sha256 的综合绑定均不能充当单文件摘要。

## 自动浏览器入口

业务仓先按原始需求/确认规则独立准备并审核计划，再进入 AI 编码；计划必须纳入 Git 可见源码基线，不能 ignored、经过符号链接或列入 allowed_files。计划路径、文件内容/模式/索引绑定需求与 Spec，执行前重新核对。下例是计数器示例，不是所有项目的通用业务预期。

在业务仓安装团队批准版本的 Playwright 及 Chromium；该依赖安装不由 Factory 静默执行：

```bash
pnpm add -D playwright@<团队批准版本>
pnpm exec playwright install chromium
```

在 `.factory/factory-local.json` 配置（在任务建立基线前完成并审核）：

```json
{
  "browser_executor": {
    "provider": "playwright",
    "plan": "tests/acceptance/browser-plan.json",
    "base_url": "http://127.0.0.1:6192/",
    "serve": {
      "argv": ["pnpm", "run", "dev", "--host", "127.0.0.1", "--port", "6192", "--strictPort"],
      "timeout_seconds": 30
    },
    "timeout_seconds": 120
  }
}
```

可用 `executable_path` 指定已安装浏览器的绝对路径；否则用 Playwright 已安装的 Chromium。无登录态的本地页面使用独立浏览器上下文，不借用用户个人账号。需登录或其他专用能力时继续使用对应连接器/手工收据，不能以匿名页替代登录后的验收。

计划示例：

```json
{
  "schema": "factory-local/browser-plan/1",
  "checks": [{
    "id": "V-BROWSER",
    "environment": "local",
    "path": "/",
    "viewports": [{"width": 1280, "height": 900}, {"width": 375, "height": 900}],
    "steps": [
      {"action": "assertText", "text": "当前计数：0"},
      {"action": "assertDisabled", "role": "button", "name": "重置", "expected": true},
      {"action": "click", "role": "button", "name": "加一"},
      {"action": "assertText", "text": "当前计数：1"},
      {"action": "assertNoOverflow"},
      {"action": "assertNoErrors"}
    ]
  }]
}
```

支持动作：click、press、reload；断言：assertText、assertDisabled、assertVisible、assertStyle、assertNoOverflow、assertNoStorage、assertNoApi、assertNoExternal、assertNoErrors。定位选 role/name、text 或 selector。每项必须有真实断言；空步骤、跨 origin 路径或跳转不能通过。local/mock 只能连接本机；真实环境需 Spec 的 test-environment 及既有环境预检。

Spec 由 Rules 将计划 ID 映射到 AC。Factory 在评审和命令完成后，启动自己拥有的服务，运行固定驱动，逐 ID 生成断言结果、浏览器版本、视口、截图及收据，再校验归档。端口已被占用会阻断，不接管或结束别人的服务；自身服务在结束或异常时清理。Playwright/浏览器缺失返回未完成检查，查看 browser-executor.log 和 browser/driver-error.txt，补齐依赖后按 recovery_command 恢复。

`kind=manual` 是当前兼容协议中的外部证据类别，并不表示只能由人点击。已有 `--verification-evidence` 继续有效。配置执行器不会取消未被支持的其他人工必验项。

明确要求源码范围/治理保持不变时，可在 manual/integrity/local 验证项声明 `executor=source-integrity`。Factory 自动对照原基线的 HEAD、修改集合及范围外内容/模式/索引并归档；该收据不证明业务语义。需要设计稿还原时，Figma 事实、截图和 visual guard 仍是独立义务。

## 质量命令复用与报告

仅同一 attempt 内，对 argv 完全一致的成功质量检查复用收据。核对 task/attempt、请求、源码/索引、配置、运行时、环境、可执行文件、实际依赖内容及原始日志摘要。失败收据不复用，质量门失败会阻断；已登记输入的完整性或绑定不满足时不沿用旧收据，按门禁和恢复指引处理。不跨 attempt 或版本复用，也不承诺检测任意外部副作用。

默认包含 node_modules 内容及符号链接；可用 `verification_dependency_paths` 登记其他受忽略的依赖目录。外部链接或无法绑定的依赖返回不可复用。维护者必须登记命令实际读取的额外外部输入；这不是对任意宿主文件的自动依赖追踪。严格内容指纹会增加扫描开销，先保证证据可信，再根据测量优化。

每次 attempt 自动输出 `execution-summary.json` 和 `.md`：阶段结果/耗时、命令退出码、首个命令失败日志、恢复路径、执行/复用验证项。pending、等待授权、环境阻断和失败分别展示；不把非零命令数当任务失败率，不猜测人工介入次数。任务根目录是最新摘要，旧 attempt 原始摘要保留。

## Rules 方法回归

机器契约测试不能代替业务分析方法评测。修改 Spec 编写/评审方法时，用 Rules 的精选案例准备独立评审输入：

```bash
python3 /path/to/awesome-factory/tools/evaluate-spec-quality.py \
  --pack /path/to/awesome-rules/skills/factory-spec-review/evals/behavior-cases.json \
  prepare /path/to/private-evaluation
```

输出目录须尚不存在。让独立评审者只读取逐案例目录中的输入、问题和 Skill，按以下结构写入该目录的 `response.json`，覆盖 `checks.json` 中全部 ID。`case_id` 和 `input_sha256` 逐字取自 `binding.json`，每项至少引用原始 request 的连续原文；verdict 可选 covered、gap、uncertain。

```json
{
  "case_id": "取自 binding.json",
  "input_sha256": "取自 binding.json",
  "checks": [{
    "id": "取自 checks.json",
    "verdict": "gap",
    "evidence": [{"path": "request", "quote": "原始需求中的连续原文"}]
  }]
}
```

不提供含预期答案的原始 pack 或根目录 run.json，也不让实施者根据实现修改答案。执行器不会自行调用模型。评审结束后评分：

```bash
python3 /path/to/awesome-factory/tools/evaluate-spec-quality.py \
  --pack /path/to/awesome-rules/skills/factory-spec-review/evals/behavior-cases.json \
  score /path/to/private-evaluation
```

评分同时核对案例全集、输入摘要和引用依据；空案例、漏答或输入漂移不能算通过。精选案例的预期答案仍需维护者复核；该结果仅证明这些案例的方法表现，不是任意需求的正确率，也不是业务运行验收。

## 升级与验证边界

现有项目须审核新的 runtime/Skill 和 bridge 差异，再复制新版 `templates/local/bridge.py` 到项目 `.factory/bridge.py`；保留项目配置、guard 和 MISSION。显式运行 factory-local-lock.py --write 后，installation.json 中 bridge 和 runtime-lock 的摘要也须更新；若审核后调整了公共仓路径，再更新 factory-local.json 的摘要。只更新本次明确审核的条目，不能重算全部安装文件来掩盖其他漂移。最后执行 setup check。普通 check 不安装、不覆盖、不刷新锁。

质量门和直接 Spec 命令使用同一个运行器函数注入当前 FACTORY_QUALITY_TASK_ID、FACTORY_QUALITY_ATTEMPT_ID、FACTORY_QUALITY_STAGE=verify；这三个控制字段另受收据绑定校验。直接执行和复用必须保持同一命令的实际环境语义一致。

升级后旧任务按运行时/Skill 绑定拒绝恢复，应从 classify 重跑或创建关联修订；不手改旧通过产物。新仓仍走 setup init。

实际修改、负例、独立评测和演练证据见 [本轮验证记录](local-reliability-validation-20261104.md)。
