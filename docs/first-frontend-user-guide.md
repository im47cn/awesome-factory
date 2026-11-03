# 首个前端用户接入指南：用 Factory 完成一个项目从 0 到 1

本文面向第一次使用本地 Factory 的前端开发者。目标不是把 Factory 当成脚手架，而是把一个真实前端需求从「需求输入」推进到「Spec、AI 编码、本地验证」，并且让每个阶段都留下可以恢复、复核和交接的证据。

适用版本：`factory-local/workflow/4`。适用仓库：Vue/React/TypeScript 等包含 `package.json` 的前端仓。本文默认不提交、不推送、不创建 MR；提交和发布由人确认后单独完成。

## 1. 先理解边界：Factory 负责什么，不负责什么

Factory 是编排和验收运行时，不是业务仓脚手架，也不是自动发布机器人。

| 范围 | Factory 负责 | 业务仓/操作者负责 |
|---|---|---|
| 项目初始化 | 接入 `.factory` 薄适配、治理门、运行器和版本锁 | 选择技术栈、创建 package.json、安装依赖、准备 README |
| 需求分析 | 分类、技能路由、设计/接口/业务来源分析、需求和澄清产物 | 提供真实需求、回答影响行为的业务问题 |
| Spec | 生成带范围、验收条件、验证计划的 Spec，并做结构校验 | 审阅目标、非目标、文件范围和验收条件 |
| AI 编码 | 在冻结 Spec 和授权上限内调用模型、记录改动范围 | 授权实施，处理需要人工决定的业务取舍 |
| 验证 | 执行仓库质量命令、校验验证收据、保留日志和恢复命令 | 提供 Mock/测试环境、账号权限、浏览器或 Figma 证据 |
| 交付 | 生成交接和远端意图记录，不自动产生远端副作用 | 人工审查 diff 后提交、推送、创建 MR |

重要边界：`build`、单测、静态检查只能证明对应命令通过，不能单独证明浏览器交互、Figma 视觉还原、真实接口或生产环境可用。

## 2. 一条最短可行路径

完整链路如下：

```text
创建前端壳 → 准备 docs / Mock → Factory init
→ check / runtime 体检 → request
→ classify → skill-discovery → design-analysis → requirement
→ clarification → spec → 独立 spec-review（先停下来人工审）
→ 显式授权 implement → 独立 review → verify
→ 浏览器/测试环境验收 → 人工审 diff → 再决定提交推送
```

每次运行都自动生成稳定 `task_id`。同一任务的 request、需求分析、Spec、代码变更、验证和恢复状态必须绑定同一个 `task_id`；不要复制任务目录或手工修改通过状态来“续跑”。

## 3. 前置条件

### 3.1 工具与版本

- Git
- Python `>=3.10`
- Node `>=24.19.0`
- 项目要求的 pnpm 版本（Factory 预检默认要求 `>=10.28.1`）
- `omp` CLI，并在交互终端完成登录
- 本地可读的 `awesome-factory` 和 `awesome-rules`

Factory 不会自动下载依赖、不保存模型凭证、不在共享配置中写 token。若使用 Figma，先在浏览器登录 Figma，并确认当前账号能打开目标文件；未登录时设计分析/视觉验收应停在待处理状态，不得把页面能打开当成设计稿通过。

### 3.2 从 Git 创建一个最小前端仓

下面以 Vite Vue 为例；React 或其他脚手架只需替换第一条命令。

```bash
mkdir -p ~/work/first-factory-app
cd ~/work/first-factory-app
pnpm create vite . --template vue-ts
pnpm install
git init
git add package.json pnpm-lock.yaml src public index.html vite.config.* tsconfig* README.md .gitignore
git commit -m "chore: bootstrap frontend app"
```

如果仓库已经存在，先在业务仓根目录确认：

```bash
git status --short --branch
test -f package.json && test -f README.md
node --version
pnpm --version
```

初始化前不要清理或覆盖已有 `.factory`、`MISSION.md`、未提交改动。已有治理目录会被拒绝覆盖。

## 4. 准备业务上下文

### 4.1 推荐目录

```text
project/
├── docs/
│   ├── README.md                 # 文档索引和当前版本
│   ├── requirements/             # 需求原文、已确认决策
│   ├── api/                      # 接口文档、契约样例、版本
│   ├── design/                   # Figma 链接、截图、尺寸标注
│   └── history/                  # 已废弃或历史资料，只作追溯
├── mock/                         # 本地 Mock 数据/适配器
├── src/
└── README.md
```

`docs` 是业务事实来源；`artifacts/factory-local/` 是 Factory 的任务证据缓存，两者不能混用。文档变更后，恢复任务会比较引用文件和版本摘要；检测到变化时会阻断并要求从 `requirement` 或更早阶段重跑，不会静默沿用旧 Spec。

### 4.2 新建需求文件

例如 `docs/requirements/first-page.md`：

```md
# 首个页面试点

## 背景
用户要解决什么问题，当前页面/流程有什么缺陷。

## 目标
本次只交付哪些用户可观察行为。

## 设计证据
- Figma：<完整 URL，包含 node-id>
- 页面尺寸、响应式范围、状态截图：见 docs/design/...

## 接口证据
- 接口文档：docs/api/...
- 契约版本：v1
- 本地阶段：使用 mock；测试环境阶段：使用真实地址

## 可观察验收条件
- AC-001：未填写必填字段时，提交按钮保持禁用并展示字段提示。
- AC-002：选择有效数据后，提交发送约定字段和记录 ID，重复点击不会重复提交。
- AC-003：接口失败时保留用户输入并展示可定位的失败原因。
- AC-004：成功后列表状态更新，已完成记录不再允许重复操作。
- AC-005：在目标浏览器宽度范围内，结构、间距、字体、颜色和交互状态与 Figma 一致。

## 非目标
- 不改后端接口协议。
- 不重构无关模块。
- 不在本任务内提交或发布。
```

验收条件必须写成可观察行为，而不是“功能可用”“样式还原良好”这类无法判定的句子。每条 AC 最终都要映射到命令、Mock、浏览器、Figma 或测试环境证据。

## 5. 初始化 Factory

### 5.1 推荐的一站式初始化

在 `awesome-factory` 仓执行，目标是业务项目路径：

```bash
python3 ~/sources/awesome-factory/tools/factory-local-setup.py init ~/work/first-factory-app
```

初始化脚本会：

1. 记录初始化前分支、HEAD 和工作区基线。
2. 调用 `factory-init` 安装前端本地模式的最小治理面。
3. 生成 `MISSION.md`、`.factory/factory-local.json`、`runtime-lock.json` 和 `installation.json`。
4. 复制本地 runner、质量门、测试适配器和 Figma 采集入口。
5. 自动把 `artifacts/factory-local/` 加入本机 Git exclude。
6. 写入初始化节点日志、退出码、快照和 `summary.json`。

初始化只改目标仓的 Factory 配置和治理文件，不安装业务依赖、不调用模型、不登录 Figma、不提交、不推送。

### 5.2 初始化结果检查

```bash
cd ~/work/first-factory-app
python3 .factory/setup.py check .
python3 .factory/run.py --check-runtime
python3 .factory/test.py
python3 .factory/quality-gate.py
```

初始化证据位于：

```text
artifacts/factory-local/onboarding/<run-id>/
├── nodes.jsonl       # 每个初始化节点一行
├── *.log             # 原始命令输出
└── summary.json      # 初始化总状态和未验范围
```

接入成功只代表运行器、配置、规则来源和治理门可用；`model_auth` 与 `business_acceptance` 初始仍是 `NOT_RUN`，这是预期行为。

## 6. 按阶段运行第一个任务

### 6.1 先完成 Spec 独立审查，不直接让 AI 改代码

```bash
cd ~/work/first-factory-app
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --only-stage spec-review
```

分析模式默认只授权到编码前 `spec-review`。完成后先检查：

```text
artifacts/factory-local/first-page-pilot/
├── task-identity.json
├── authorization.json
├── run-state.json
├── state-events.jsonl
├── requirement.md
├── requirement-input.json
├── requirement-contract.json
├── requirement-validation.json
├── clarification-contract.json
├── spec.md
├── spec-contract.json
├── spec-validation.json
├── spec-review-input.json
├── spec-review-result.json
└── attempts/<attempt-id>/
    ├── *.log
    ├── receipts.json
    └── manifest.json
```

人工重点审阅：

- 目标和非目标是否准确；
- 需求事实、行为候选与待澄清问题是否分开，证据是否命中原文；
- `scope.allowed_files` 是否是最小文件集合；
- 是否包含所有真实设计/接口约束；
- 每条 `AC-*` 是否是可观察行为；
- 每条 AC 是否映射到至少一个必验方法；
- Mock、真实接口、身份权限和浏览器证据是否分开；
- Spec 是否仍为 `NOT_RUN`，没有把计划写成测试通过。
- 独立 Spec Review 是否逐项覆盖需求候选、澄清规则和 AC，并引用 Spec 上游证据。

### 6.2 需要业务决定时，补充澄清答复

如果 `clarification` 阶段出现阻塞问题，准备一个不含凭证的 JSON，例如 `docs/requirements/first-page-answers.json`，然后恢复：

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --clarification-answers docs/requirements/first-page-answers.json
```

操作者答复必须逐条绑定问题 ID。模型不能替用户确认业务规则，也不能从当前源码反推“已经确认”。

### 6.3 人工确认后授权 AI 编码

确认 Spec 后，才显式扩大授权：

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --authorize-through implement
```

Factory 只允许模型使用既定的本地工具集合：`read/write/edit/grep/lsp`。它不会自动提交、推送、创建 MR 或发布。编码完成后先查看：

```bash
git diff --stat
git diff -- src/ docs/ mock/
cat artifacts/factory-local/first-page-pilot/implemented-files.txt
```

### 6.4 独立审查与本地验证

审查不是实现模型的自评。继续到验证阶段：

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --authorize-through verify
```

如果 Spec 要求浏览器或测试环境证据，Runner 会生成待填写的请求文件；不能用“命令退出 0”替代这些证据。典型命令如下：

```bash
# 质量门（由 factory-local.json 的真实命令决定）
python3 .factory/quality-gate.py

# 工厂及项目适配回归
python3 .factory/test.py

# 按 Spec 提供绑定后的验证收据
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --verification-evidence /path/to/verification-evidence.json \
  --browser-evidence /path/to/browser-evidence.json
```

验证收据必须绑定当前 `task_id`、request 摘要、Spec 摘要、契约摘要和实际证据文件摘要。Mock 只证明本地数据态和前端行为；若 Spec 标记测试环境为必验，Mock 通过后仍不能关闭该要求。

## 7. 设计稿、接口文档和 Mock 的正确接法

### 7.1 有 Figma 设计稿

在 request 和 `docs/design/` 中同时记录：完整链接、node-id、目标页面/状态、关键尺寸、响应式范围和截图版本。Figma 设计分析是独立阶段；视觉验收至少需要：

- 设计节点/截图证据；
- 运行中的页面截图；
- 关键尺寸、字体、颜色、间距、状态和交互对照；
- 目标浏览器宽度下的结果。

“页面能加载”“DOM 结构存在”“构建通过”都不能单独产生 `visual pass`。Figma 未登录、链接无权访问或截图缺失时，任务必须标记待处理并给出恢复路径。

### 7.2 有接口文档

在 `docs/api/` 保存接口版本、请求/响应示例、错误码、字段是否必填和 Mock 映射。Mock 信封需要逐项对照契约：HTTP 方法和路径、状态码、响应 envelope、字段类型/可空性、分页/排序、错误响应和业务 ID。建议把对照结果写进 Spec 的验证计划，而不是只凭运行成功判断。

### 7.3 只有 Mock 环境

可以先用 `--acceptance-scope local-mock` 推进本地分析和验证：

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --acceptance-scope local-mock \
  --authorize-through verify
```

该选项只延后测试环境准备，不会删除 Spec 中的真实环境必验项。进入测试环境前，重新提供不含密码/token 的地址、契约版本和身份权限证据。

## 8. 恢复、失败和文档变化

### 8.1 普通恢复

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume
```

恢复会校验前置阶段收据、task/request 摘要、源码基线、规则锁和 docs 引用快照。恢复不会自动提高授权上限；若进入 implement/verify 被阻断，必须重新使用明确的 `--authorize-through <stage>`。

### 8.2 需求或文档变了

不要直接编辑旧的 `passed` 产物。需求、接口契约、Figma 引用或受控业务规则变化时，从 `classify` 或 `requirement` 重新跑；旧 attempt 保留作历史。若只是修订任务，应使用 `--revision-of` 和 `--revision-reason`，新任务不会继承旧任务的通过状态。

### 8.3 常见阻断

| 现象 | 原因 | 处理 |
|---|---|---|
| `检测到现有 .factory 或 MISSION.md；拒绝覆盖` | 仓库已接入或有治理文件 | 不重复 init，运行 `.factory/setup.py check .` |
| `缺少 README.md 良性负例载体` | 缺少 B-01 负例文档 | 新增真实 README，再重新初始化 |
| `rules-source BLOCKED` | `rules_path` 不存在或锁不匹配 | 准备 `awesome-rules`，审核路径和锁，不自动下载 |
| `project-dependencies BLOCKED` | 没有 `node_modules` | 按项目锁文件执行 `pnpm install --frozen-lockfile` |
| `OMP` 未找到/未登录 | 模型节点不可用 | 交互终端安装并登录，不把凭证写入仓库 |
| `docs 引用资料已变化` | 已引用文档摘要发生变化 | 查看差异，从 `requirement` 或更早阶段重跑 |
| `--only-stage 超出授权上限` | 运行选项不能扩大授权 | 用户明确授权后使用 `--authorize-through` |
| `pending_acceptance` | 缺少必需浏览器/接口/测试环境证据 | 按请求文件补证据并恢复，不伪造通过 |
| 两个任务同时写同一目录 | 任务锁冲突 | 等待前一个进程退出，再恢复 |

## 9. 完成判定和交接清单

一个可交接的首个前端任务至少满足：

- [ ] `setup.py check`、Factory 回归和项目质量门通过；
- [ ] 所有阶段都有对应收据、日志、退出码和恢复命令；
- [ ] 同一个 `task_id` 贯穿 request、Spec、实现和验证；
- [ ] Spec 的 `allowed_files` 与实际改动一致，没有越界文件；
- [ ] 每条 AC 都有独立的验证方法和证据；
- [ ] Mock/静态检查/构建与浏览器/Figma/测试环境证据分开记录；
- [ ] 失败时保留失败原因、证据路径和下一步，不把失败改写成通过；
- [ ] 人工审阅 `git diff`，确认是否提交或推送；
- [ ] 提交时只提交共享配置、业务代码和明确需要交付的文档，不提交 `artifacts/factory-local/`、token、cookie 或本机缓存。

交接时优先提供以下信息：

```text
项目路径：...
分支：...
task_id：local-YYYYMMDD-xxxxxxxx
当前阶段/状态：...
Spec：artifacts/factory-local/<task>/spec.md
最终结果：artifacts/factory-local/<task>/result.json
未完成验收：...
恢复命令：...
```

## 10. 给首个用户的建议顺序

第一次不要从“让 AI 一次完成全部页面”开始。建议选一个页面、一个接口、一个明确的用户动作，先完成：

1. 初始化和 `check`；
2. 只跑到 Spec，人工审阅验收条件；
3. 用 Mock 完成一次本地实现；
4. 做一次独立审查和浏览器验证；
5. 再补测试环境证据；
6. 最后才决定提交、推送和是否扩大到下一个任务。

这样首个用户得到的不是一段无法解释的 AI 代码，而是一条有输入、有授权、有阶段产物、有失败恢复路径的可重复研发链路。
