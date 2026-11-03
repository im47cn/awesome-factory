# 首个前端用户接入指南：用 Factory 完成一个项目从 0 到 1

本文面向第一次使用本地 Factory 的前端开发者。目标不是把 Factory 当成脚手架，而是把一个真实前端需求从「需求输入」推进到「Spec、AI 编码、本地验证」，并且让每个阶段都留下可以恢复、复核和交接的证据。

适用版本：`factory-local/workflow/4`。空仓工程基线当前只支持 Vue 3 + TypeScript + Vite。React/其他前端仓只有在已经使用 pnpm，提供覆盖格式、Lint、类型、单测和构建的 `quality:check`（或 Factory 可识别的等价脚本），并提供面向当前工作树的正式 `coverage:gate` 时，才能从 Factory 接入步骤开始；否则先做项目专属适配，不能按本文宣称已接入。Factory 不会自动提交、推送或创建 MR；为了让空仓具有可比较的 Git 基线，本文包含两次由操作者审阅后执行的**本地提交**，默认不推送。

本指南配套版本包含 `--context-file`、Bootstrap `check --check-mode project`、profile `1.0.2` 及恢复校验修复。旧参考基线 Factory `557c504`、Rules `22c6b94` 不包含这些能力，不能只 clone 旧提交再照抄新命令。使用维护者本次发布的两仓配套完整 SHA，clone 后固定版本并保存 `git rev-parse HEAD` 与运行时文件摘要；本次演练使用旧 HEAD 叠加冻结补丁的历史记录另见文末报告，不冒充已发布版本的重新演练。

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

操作者在终端运行本文命令、准备账号与答复；外层 AI 在 Factory 接入前执行 bootstrap/覆盖率 Skill；Factory 接入后才调用 OMP 编排业务阶段。需要编辑文件或执行 Skill 时，应使用能访问这三个本地目录的编码 Agent。纯终端不会因为下载了 `SKILL.md` 就自行完成覆盖率接入。

首任务前确定最小技术选型：是否需要 Vue Router、状态管理、UI 组件库、Mock 服务和浏览器测试工具。当前 profile 只有最小 Vue 壳和 Vitest，不包含这些业务依赖。需要扩展时先由操作者确认并安装，再建立工程基线；Factory 接入后 `package.json`、锁文件和治理脚本受周界保护，不让业务实现临时绕过。

## 2. 第一个用户应该走哪条路径

| 当前仓库状态 | 起点 | 禁止事项 |
|---|---|---|
| 只有 `.git`、README、LICENSE 或 `.gitignore` | 先执行 `frontend-repo-bootstrap` | 不先接 `.factory`，不手工拼配置 |
| Vue 3 + TypeScript + Vite 空壳，尚无业务源码 | 先对 bootstrap 执行 `plan`；无冲突再 `apply` | 不覆盖不同内容的 ESLint、Vite、Hook 配置 |
| 已有业务源码 | 跳过 bootstrap，先审计并逐项接入工程基线 | 不使用 bootstrap 强行覆盖已有项目 |
| 工程基线、README、初始提交都已存在 | 从 Factory init 开始 | 不重复初始化已有 `.factory`/`MISSION.md` |

完整链路如下：

```text
创建空 Git 仓 → frontend-repo-bootstrap plan/apply/check
→ frontend-unit-test-setup 覆盖率与 Hook 正反向验收
→ 人工审 diff 并建立工程基线提交
→ 准备 docs / Mock → Factory init → 人工审 Factory 配置并建立本地提交
→ check / runtime 体检 → request
→ classify → skill-discovery → design-analysis → requirement
→ clarification → spec → 独立 spec-review（先停下来人工审）
→ 显式授权 implement → 独立 review → verify
→ 浏览器/测试环境验收 → 人工审 diff → 再决定提交推送
```

每次运行都自动生成稳定 `task_id`。同一任务的 request、需求分析、Spec、代码变更、验证和恢复状态必须绑定同一个 `task_id`；不要复制任务目录或手工修改通过状态来“续跑”。

为什么工程基线必须在 Factory 前完成：空仓此时还没有可执行的本地 Factory；而 Factory init 需要真实 README、`package.json`、至少一次 Git 提交和可运行质量命令。bootstrap 负责“把仓库变成合格的前端工程”，Factory 负责“让后续业务需求按阶段可恢复地执行”，两者不能倒置。

## 3. 前置条件

### 3.1 工具与版本

- Git
- Python `>=3.10`
- Node `>=24.19.0`
- 项目要求的 pnpm 版本（Factory 预检默认要求 `>=10.28.1`）
- `omp` CLI，并在交互终端完成登录
- 本地可读的 `awesome-factory` 和 `awesome-rules`
- macOS/Linux；Windows 原生环境使用 WSL

OMP 的安装入口以[上游安装说明](https://github.com/can1357/oh-my-pi#install)为准。本次本机可运行版本为 `omp/18.10.3`；新机可下载并审阅安装器后按固定版本安装：

```bash
curl -fsSL https://omp.sh/install -o /tmp/factory-omp-install.sh
# 先审阅安装器，再安装已验证版本；已有 OMP 不重复安装
sh /tmp/factory-omp-install.sh --binary --ref v18.10.3
omp --version
omp login openai-codex
omp models openai-codex
```

登录会打开授权页面，按提示选择实际授权的账号。若使用其他 provider，按其登录方式配置，不能把 ChatGPT/Codex 登录当成任意 API Key。不要运行会导出凭证的命令来收集诊断信息。

从本机模型清单中选择可用模型，再做一次真实、无文件工具的探测。下列型号是本次演练选择，不要求所有账号具备同一型号：

```bash
export FACTORY_LOCAL_MODEL=openai-codex/gpt-5.6-sol
omp --print --no-session --no-tools --no-skills --no-rules --no-extensions \
  --model "$FACTORY_LOCAL_MODEL" --thinking low -- '只输出 FACTORY_MODEL_OK'
```

成功条件是命令退出 0 且实际返回 `FACTORY_MODEL_OK`；列表中有型号、`omp --version` 通过都不证明认证可用。失败先按终端提示登录、处理权限或模型不可用，不启动业务任务。模型调用可能消耗账号额度；探测无需读写业务仓。

Factory 不会自动下载依赖、不保存模型凭证、不在共享配置中写 token。若使用 Figma，需要同时满足：浏览器账号能打开目标文件；在 Figma 的头像菜单 → Settings → Security → Personal access tokens 创建 Token；仅在当前终端执行 `export FIGMA_TOKEN='<token>'`。Token 不得写入仓库、共享 `.env`、需求文件或日志，任务结束后可执行 `unset FIGMA_TOKEN`。缺少 Token 或文件权限时，设计分析/视觉验收会停在待处理状态，不得把浏览器页面能打开当成连接器已认证或设计稿通过。

### 3.2 准备三个并列仓库

推荐让 Factory、Rules 和业务仓位于同一父目录。这样初始化生成的 `upstream_path`、`rules_path` 是可读、可迁移的相对路径：

```text
~/work/factory-pilot/
├── awesome-factory/
├── awesome-rules/
└── first-factory-app/
```

当前试点分支的获取方式：

```bash
mkdir -p ~/work/factory-pilot
cd ~/work/factory-pilot
git clone --branch feature/local-init-acceptance \
  git@github.com:im47cn/awesome-factory.git
git clone --branch feature/figma-visual-guard-final \
  git@codeup.aliyun.com:610b3c9d86508f8da8b08436/awesome-rules.git
mkdir first-factory-app
cd first-factory-app
git init
```

clone 前用 `git ls-remote --heads <上方仓库地址> <分支名>` 验证两个托管端的读取权限；出现 `Permission denied` 或 `Repository not found` 时向仓库维护者申请权限、配置 SSH，不能把公司凭证转交给 AI。拿到维护者公布的配套 commit 后，分别 `git -C <公共仓> checkout --detach <对应完整SHA>`，回读 `git rev-parse HEAD`。分支名用于找到仓库，固定 commit 用于复现。

尤其检查“已有仓能访问、新目录不能访问”的情况：原仓 `.git/config` 中的 `core.sshCommand` 不会被新 clone 继承。先在新终端/父目录验证读取；需要指定密钥时，由操作者选择已授权密钥，通过 SSH Host 配置或仅本次进程的 `GIT_SSH_COMMAND='ssh -i /absolute/path/to/approved-key -o IdentitiesOnly=yes'` 配合 `git ls-remote` / `git clone` 使用。不要复制别人的密钥、改全局身份或把密钥正文写入文档。权限未解决就停止远端获取；本地已有快照的演练只能报告“使用本地快照”，不能冒充新机网络 clone 成功。

如果试点分支已经合入稳定分支，使用维护者公布的稳定分支或固定 commit，不盲目沿用本文的试点分支名。两个公共仓升级后不能直接续跑旧任务：先审核 diff 和新锁；如果因此改动业务仓治理文件或建立了新 HEAD，按第 8.2 节创建关联修订，从新基线重新分析。

先确认真实环境，不要只修改版本声明：

```bash
git status --short --branch
node --version
pnpm --version
omp --version
```

初建仓前还应检查 `git config user.name`、`git config user.email`。只在当前新仓按团队约定补齐缺项，不改全局 Git 身份。pnpm 安装后在业务仓再次执行 `pnpm --version`，应解析为 `packageManager` 固定的 `10.28.1`；不能用父目录里的全局版本代替项目版本。

初始化前不要清理或覆盖已有 `.factory`、`MISSION.md`、未提交改动。已有治理目录会被拒绝覆盖。

### 3.3 用 frontend-repo-bootstrap 建立工程基线

不要再额外运行 `pnpm create vite`；`vue-admin` profile 已包含最小 Vue/Vite 壳、编码规范、统一 UI/表单规范、ESLint、Stylelint、Prettier、Commitlint、Vitest 和 Lefthook 骨架。

```bash
cd ~/work/factory-pilot
BOOTSTRAP=awesome-rules/skills/frontend-repo-bootstrap/scripts/bootstrap.py
APP=first-factory-app

python3 "$BOOTSTRAP" plan \
  --target "$APP" --profile vue-admin --package-name first-factory-app

# 只有 plan.status=ready 且 conflicts=[] 时执行；同时安装依赖和 Lefthook
python3 "$BOOTSTRAP" apply \
  --target "$APP" --profile vue-admin --package-name first-factory-app --install

python3 "$BOOTSTRAP" check --target "$APP" --profile vue-admin
```

`plan` 被阻断时先处理 `environment_blockers` 或 `conflicts`，不要增加 force、删除用户文件或改写冲突配置。`apply --install` 失败时，文件可能已应用但状态仍是 `files_applied`；查看 Git 元数据目录里的脱敏失败收据，修复网络/依赖/Hook 后重跑，不能把它描述为安装完成。

为项目编写真实 README（至少包含唯一项目标题、用途、启动和质量命令）。最小结构如下，项目名称和说明必须换成真实内容：

```md
# first-factory-app

首个 Factory 前端试点，用于验证一个明确页面和用户动作的本地闭环。

## 本地启动

- 安装：`pnpm install --frozen-lockfile`
- 开发：`pnpm run dev`
- 质量：`pnpm run quality:check`
- 单测：`pnpm run test:unit`

## 文档

需求、接口、设计和历史资料统一从 `docs/README.md` 索引。
```

然后逐项验证：

```bash
cd ~/work/factory-pilot/first-factory-app
pnpm install --frozen-lockfile
pnpm run format:check
pnpm run lint:ci
pnpm run stylelint:ci
pnpm run typecheck
pnpm run test:unit
pnpm run build
FRONTEND_BOOTSTRAP_PROJECT="$PWD" node --test \
  ../awesome-rules/skills/frontend-repo-bootstrap/tests/eslint-scope.test.mjs
git config --get core.hooksPath || true
git status --short
```

### 3.4 补齐逐文件覆盖率和真实 Hook 门禁

bootstrap 只提供 Vitest、覆盖率采集入口和 Lefthook 骨架；它的 `check` 会持续提示覆盖率须由 `frontend-unit-test-setup` 独立检查，不会替该 Skill 回写“已完成”。在接入 Factory 前，让具备本仓 Rules 的 AI 明确执行：

```text
请从业务仓读取并执行 ../awesome-rules/skills/frontend-unit-test-setup/SKILL.md，
为当前 Vue/TypeScript 空壳仓完成全量源码分类、普通文件 80%/核心文件 90%
逐文件行覆盖率门禁，以及 pre-commit/pre-push 的正反向隔离验证。
只接入基础设施，不创建远端、不推送，不把配置存在冒充 Hook 已生效；
记录接入起点、分类依据、命令退出码、失败样例和恢复后通过证据。
```

必须得到以下结果后再继续：覆盖率 Skill 自身的机器清单、接入起点、命令收据和 Hook 正反向证据齐全；候选源码全部分类或说明排除理由；空分类不能冒充完成；覆盖率不足的增量文件真实返回非零；恢复后通过；pre-commit 检查暂存快照，pre-push 检查实际推送提交；本地 Hook 与远端 CI 状态分开记录。

覆盖率 Skill 还必须在 `package.json` 写入固定入口 `coverage:gate`。它针对当前未提交工作树执行同一份普通80%/核心90%逐文件策略，供 Factory verify 直接调用；不能只在 Git Hook 里存在。至少执行一次正反向验证：

```bash
pnpm run coverage:gate    # 当前达标源码应返回 0
# 在隔离快照制造一个低于阈值的适用文件，命令必须非零；恢复后再次为 0
```

人工审阅 `git diff` 和敏感信息后，建立第一个本地基线提交。该提交会真实触发刚安装的 Hook：

```bash
git add -A
git diff --cached --check
git commit -m "chore: initialize frontend engineering baseline"
git status --short --branch
```

此处提交失败就是门禁结果，应修复后重试；不要使用 `--no-verify`。默认不推送。

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

`docs` 是业务事实来源；`artifacts/factory-local/` 是 Factory 的任务证据缓存。只有完成下文 `docs_context` 显式登记后，运行器才会自动选择相关文档、保存版本摘要并在恢复时检查变化；只创建目录或索引不会启用这项能力。变更已引用文档会阻断恢复，按指引从 `requirement` 或更早阶段重跑。

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

### 4.3 在初始化前登记文档与有效规范

先编写 `docs/README.md`，以真实 Markdown 链接登记编码规范、UI 规范、需求及本次接口文档。再复制维护者提供的配置示例并按项目修改：

```bash
cd ~/work/factory-pilot/first-factory-app
cp ../awesome-factory/templates/local/context-policy.example.json docs/factory-context.json
```

示例启用 `docs_context`，使用 `docs/README.md` 作为索引；编码/UI 指南按前端领域总是读取，其他文档按 `triggers` 或 request 中的显式路径选择。逐项核对 `documents`：所有路径必须已在索引登记、文件真实非空；历史/执行/决策资料只允许显式引用，不能配置自动命中。未登记的文件不会因为放在 docs 下而自动进入上下文。

`business_sources` 与阅读策略分开。示例为空列表，表示尚未登记可作为既有业务约束的现行规范。若确有经过维护者确认的规则，先核对原文、适用范围并用 `shasum -a 256 <文件>` 取得摘要，再加入 `business_sources.sources`：

```json
{
  "id": "project-rules",
  "path": "docs/engineering/project-rules.md",
  "kind": "normative",
  "status": "active",
  "authority": "project-maintained",
  "reviewed_sha256": "填写实际文件的64位SHA256",
  "scope_paths": ["src/"]
}
```

不要直接复制摘要占位值；不把草案、历史记录或 AI 建议登记为已确认规范。维护者确认是人工动作，内容摘要只负责固定字节。具体协议见[业务来源说明](design/clarification-business-sources.md)。

`docs/factory-context.json` 顶层使用 `schema=factory-local/context-input/1`，仅包含 `docs_context` 和 `business_sources`。初始化会在安装治理文件前验证，再把策略写入受控配置并生成安装摘要。已有 `.factory` 的仓不能重复 init；配置变更须由维护者审核，更新运行时锁及 installation 摘要，普通成员只运行 check，不自行刷新摘要放行。

## 5. 初始化 Factory

### 5.1 推荐的一站式初始化

在公共仓的父目录执行，目标是已经具有工程基线提交的业务仓：

```bash
cd ~/work/factory-pilot
python3 awesome-factory/tools/factory-local-setup.py init first-factory-app \
  --context-file first-factory-app/docs/factory-context.json
```

初始化脚本会：

1. 记录初始化前分支、HEAD 和工作区基线。
2. 调用 `factory-init` 安装前端本地模式的最小治理面。
3. 生成 `MISSION.md`、`.factory/factory-local.json`、`runtime-lock.json` 和 `installation.json`。
4. 复制本地 runner、质量门、测试适配器和 Figma 采集入口。
5. 自动把 `artifacts/factory-local/` 加入本机 Git exclude。
6. 写入初始化节点日志、退出码、快照和 `summary.json`。

确认收据中 `context-input-validation.docs_context_enabled=true`，体检节点 `project-context` 通过。文档未登记、规范摘要不匹配、路径越界或空文件时，初始化必须在写入 `.factory` / `MISSION.md` 前阻断。省略 `--context-file` 可接入最小运行器，但会明确记录上下文自动治理未启用，不能声称 docs 接线完成。

初始化只改目标仓的 Factory 配置和治理文件，不安装业务依赖、不调用模型、不登录 Figma、不提交、不推送。

bootstrap 基线中的 `.prettierignore` 已排除 `.factory/` 和 `artifacts/factory-local/`：前者由安装摘要保护，后者是本机运行证据。不要用 `prettier --write` 改写受锁定的 `.factory` 来消除格式提示；如果旧 profile 缺少这些忽略项，应先升级并重跑 bootstrap 检查。

本指南对应的 bootstrap profile 为 `1.0.2`。已有 `1.0.0` / `1.0.1` 安装必须使用受控升级模式；普通 `plan/apply` 会把业务仓判为冲突，不能用于升级：

```bash
python3 "$BOOTSTRAP" plan --upgrade --target "$APP" --profile vue-admin
python3 "$BOOTSTRAP" apply --upgrade --target "$APP" --profile vue-admin --install
python3 "$BOOTSTRAP" check --target "$APP" --profile vue-admin --check-mode project
```

人工确认升级计划只把 `.prettierignore` 等预期受控文件列入 `replacements`，业务源码、docs、覆盖率脚本和合法扩展列为保留或不在受控范围。受控文件在安装后被修改、安装锁不合法或 package 字段冲突时会 fail-closed；不要改用普通 `apply`、force 或手改锁摘要。

`--check-mode project` 不比较 `scaffold_files`，保留已开发的 Vue 入口/页面和允许删除的示例；其他工程配置仍受检。未知或未来 profile 版本会阻断，同版本升级只返回 noop。

如果两个公共仓不与业务仓并列，初始化时显式提供 `AWESOME_RULES_ROOT=/absolute/path/to/awesome-rules`。初始化会把它换算为业务仓中的相对 `rules_path`；团队接入优先保持并列目录布局，避免每台机器反复改共享配置和重锁。

### 5.2 初始化结果检查

```bash
cd ~/work/factory-pilot/first-factory-app
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

检查共享配置和本次准备的 docs/Mock 不包含 token、cookie、绝对用户目录和运行产物后，建立第二个本地提交，不与后续 AI 业务代码混在一起：

```bash
git status --short
git add -A
git diff --cached --name-status
git diff --cached --check
git commit -m "chore: initialize local factory workflow"
git status --short --branch
```

只在暂存清单符合预期时提交；如果出现业务实现、凭证或无关文件，先取消对应暂存并查明来源。`artifacts/factory-local/` 应由本机 Git exclude 排除，不能进入提交。此处仍默认不推送。

### 5.3 首任务启动前总检查

```bash
cd ~/work/factory-pilot/first-factory-app
test -z "$(git status --porcelain)"          # 首任务从可解释的干净基线开始
python3 .factory/setup.py check .
python3 .factory/run.py --check-runtime
python3 .factory/quality-gate.py
```

只要任一命令非零，就先查看 `artifacts/factory-local/onboarding/<run-id>/summary.json` 和对应日志，不进入业务任务。`check` 只探测 OMP 工具存在，不代表账号已登录；真正的模型调用仍以首任务结果为准。

体检后再看一次 `git status --short`：mutation 可能生成 `.factory/mutations/evidence-stamp.json`（周界绑定与执行时间）。将它明确记为体检产物，审核其内容和治理策略；不要混入业务提交，也不要为了清空状态删除未知文件。后续任务必须从可解释的基线开始，已有业务 WIP 与体检产物分别记录；运行期间不再提交或改写这些输入。

## 6. 按阶段运行第一个任务

### 6.1 先完成 Spec 独立审查，不直接让 AI 改代码

```bash
cd ~/work/factory-pilot/first-factory-app
# 首用户先检查分类，不调用模型、不修改业务源码
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --only-stage classify

# 核对 classification.json 的 domain/has_design/execution_route，再继续
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --only-stage spec-review
```

第二条命令从已完成的分类继续顺序执行到 `spec-review`，不是跳过前置阶段。分类属于可解释的规则路由，不能保证理解任意自然语言；首次接入时核对其证据与原始需求，发现歧义先修订需求或路由规则，从 classify 重跑。默认分析授权上限就是 `spec-review`，因此不会修改业务代码。首次真实模型调用会同时验证 OMP 登录状态。

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
    ├── inputs/ outputs/ partial/     # 本次输入、通过产物和失败现场快照
    ├── state.json
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
- `skill-selection.json` 是否至少包含固有的 `factory-acceptance`，并只命中与本需求真正相关的领域 Skill；普通业务需求不应再次选择空仓 bootstrap。

### 6.2 需要业务决定时，补充澄清答复

如果 `clarification` 阶段出现阻塞问题，先打开 `clarification-contract.json`，逐条回答其中 `blocking=true` 的问题。准备一个不含凭证的 JSON，例如 `docs/requirements/first-page-answers.json`：

```json
{
  "schema": "factory-local/clarification-answers/1",
  "workflow_version": "factory-local/workflow/4",
  "task_id": "从 clarification-contract.json 原样复制",
  "request_sha256": "从 clarification-contract.json 原样复制",
  "executor": "实际答复人的姓名或角色",
  "answers": [
    {
      "id": "Q-1",
      "question": "从 open_questions 原样复制问题",
      "decision": "明确、可执行的业务决定"
    }
  ]
}
```

`task_id`、`request_sha256`、问题 ID 和问题原文必须与当前任务一致；不要使用另一个任务的答复，也不要把建议、代码现状或模型推断写成用户确认。然后恢复：

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

如果 Spec 要求浏览器或测试环境证据，Runner 会生成待填写的请求文件；不能用“命令退出 0”替代这些证据。人工执行项通过 `evidence_type=browser|integrity|other` 区分浏览器、源码与治理完整性、其他证据。前端浏览器验收必须有 required 的 browser 项；只有完整性检查通过或旧 Spec 未声明证据类型时，仍会停在待验收。典型命令如下：

若先停在 `pending_environment`，打开任务目录的 `preflight-request.json`、`preflight-guide.json` 和 `preflight-result.json`。把请求复制成新的输入收据，保留绑定字段、check ID 和 environment；实际检查环境后再填写 `executor`、带时区的 `checked_at`、状态及证据：

```bash
cp artifacts/factory-local/first-page-pilot/preflight-request.json \
  artifacts/factory-local/first-page-pilot/readiness-evidence.input.json
# 按真实观察填写新输入文件，随后恢复
python3 .factory/run.py --task first-page-pilot \
  --request-file docs/requirements/first-page.md --resume \
  --readiness-evidence artifacts/factory-local/first-page-pilot/readiness-evidence.input.json
```

endpoint 项的 `details` 需要 `url` 和 `observation`；contract 项需要 `version` 和 `source`；identity 项需要无凭证的测试身份 `alias` 与实际 `permissions`。每项执行证据使用相对收据目录的 `artifacts[].path` 和该文件实际 `sha256`；默认 24 小时过期，verify 会复核。纯本地且无外部前置项的 Spec 不要求伪造环境收据。

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
  --verification-evidence /path/to/verification-evidence.json
```

使用一份统一验证收据覆盖 `verification.request.json` 中的全部 checks；浏览器截图、录屏或交互日志作为对应 check 的 artifacts 引用。不要同时传 `--verification-evidence` 与 `--browser-evidence`，运行器会明确拒绝，避免其中一份证据被静默忽略。验证收据必须绑定当前 `task_id`、request 摘要、Spec 摘要、契约摘要和实际证据文件摘要。Mock 只证明本地数据态和前端行为；若 Spec 标记测试环境为必验，Mock 通过后仍不能关闭该要求。

填写步骤是：复制 `verification.request.json` 为新输入文件 → 启动项目开发服务 → 逐条执行 AC 对应操作 → 保存截图、控制台/交互日志 → 计算证据摘要 → 按实际结果填写状态 → 传给 `--verification-evidence`。Spec 的命令项由运行器执行；`verification.request.json` 的 `checks` 只列需要实际执行者补证的 manual 项。证据路径必须是收据目录内的非空真实文件，不能使用占位截图、另一个任务的结果或只改状态。失败项保留 `failed` 和原因；尚未验证保留 `NOT_RUN`，不要为关闭任务填 `passed`。

只有进入 verify 后才会出现 `quality-gate-receipt.json`；它绑定当前 `task_id/attempt_id`。对应 attempt 的 `quality/` 保存覆盖率等质量子命令原始收据和日志。即使质量门失败，这些文件也会进入该 attempt 的 `partial/`/manifest，不能把 Spec Review 阶段尚未生成它们误判为缺失。

## 7. 设计稿、接口文档和 Mock 的正确接法

### 7.1 有 Figma 设计稿

在 request 和 `docs/design/` 中同时记录：完整链接、node-id、目标页面/状态、关键尺寸、响应式范围和截图版本。运行前按 3.1 节设置仅当前终端可见的 `FIGMA_TOKEN`；浏览器登录只用于确认人能访问文件，连接器认证实际读取 Token。Figma 设计分析是独立阶段；视觉验收至少需要：

- 设计节点/截图证据；
- 运行中的页面截图；
- 关键尺寸、字体、颜色、间距、状态和交互对照；
- 目标浏览器宽度下的结果。

“页面能加载”“DOM 结构存在”“构建通过”都不能单独产生 `visual pass`。Figma 未登录、链接无权访问或截图缺失时，任务必须标记待处理并给出恢复路径。

### 7.2 有接口文档

在 `docs/api/` 保存接口版本、请求/响应示例、错误码、字段是否必填和 Mock 映射。Mock 信封需要逐项对照契约：HTTP 方法和路径、状态码、响应 envelope、字段类型/可空性、分页/排序、错误响应和业务 ID。建议把对照结果写进 Spec 的验证计划，而不是只凭运行成功判断。

### 7.3 只有 Mock 环境

可以先用 `--acceptance-scope local-mock` 推进本地分析和验证。应在第一次运行就指定，或者只在任务的恢复节点仍为 `preflight`（或更早）时随 `--resume` 指定：

```bash
# 尚未创建任务：分析阶段即固定为 local-mock
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --only-stage spec-review \
  --acceptance-scope local-mock

# 已完成 spec-review，当前 recovery/resume_from 为 preflight
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --resume \
  --acceptance-scope local-mock \
  --authorize-through verify
```

该选项只延后测试环境准备，不会删除 Spec 中的真实环境必验项。进入测试环境前，重新提供不含密码/token 的地址、契约版本和身份权限证据。

如果任务已经越过 preflight 才决定改变 scope，不要带 `--resume` 强改原收据；显式从 preflight 重跑：

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --from-stage preflight \
  --acceptance-scope local-mock \
  --authorize-through verify
```

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

不要编辑旧的 `passed` 产物、worktree-baseline 或摘要来接纳变化。普通技术重试适用于请求、源码基线、HEAD、治理与 Rules 锁均未变化的情况。需求、文档、契约、Figma、治理文件或工程 HEAD 已变化时，先审阅新输入与必要的工程基线，再创建关联修订；旧 task_id 和 attempts 保留，新 task_id 通过 parent_task_id 关联，不继承通过状态。

```bash
# 仅登记新任务，不与 --resume / --only-stage 混用
python3 .factory/run.py \
  --task first-page-pilot-r2 \
  --request-file docs/requirements/first-page.md \
  --revision-of first-page-pilot \
  --revision-reason '工程或需求输入已更新，从审阅后的新基线重新分析' \
  --authorize-through spec-review

# 登记会复制 request；后续使用新任务内的不可变请求路径
python3 .factory/run.py \
  --task first-page-pilot-r2 \
  --request-file artifacts/factory-local/first-page-pilot-r2/request.md \
  --resume --only-stage spec-review
```

登记会自动保存父任务快照；额外使用 `--revision-evidence` 时引用不可变的 attempt 或独立归档文件，避免引用会随状态推进更新的顶层 result.json/run-state.json。父任务已有后续修订时，跟随 `superseded_by` 和恢复命令找到当前任务，不再次覆盖旧父任务。Spec 审阅后再按新任务显式授权 implement/verify。原任务已有 AI 代码时先审阅和保存其证据；只恢复明确属于该任务且可还原的改动，不清理其他人的 WIP，也不把旧通过状态转给新任务。

### 8.3 常见阻断

若 `pending_spec_review` 指出了已有需求的覆盖或验证缺口，先阅读 `spec-review-result.json`；在请求、源码、HEAD 和版本锁均未变化时执行：

```bash
python3 .factory/run.py \
  --task first-page-pilot \
  --request-file docs/requirements/first-page.md \
  --from-stage spec --only-stage spec-review
```

Factory 将上一份审查中的 gap/findings 作为只读技术反馈交回 Spec 节点，再执行新的独立审查；反馈不授权新增业务、不继承通过状态。若问题需要变更业务约定，先取得真实答复，按第 8.2 节建立关联修订。关联修订可用 `--revision-evidence` 引用已归档的 `spec-review-result.json` 供诊断，同请求摘要和证据摘要均需匹配；仍必须重新分析和验收。

编码后恢复时，需求中的源码现状引用应对照需求分析阶段保存的内容快照及阶段收据验证；当前业务代码则对照编码后摘要和实际修改范围验证。分析快照独立于可重跑的实施阶段，正常实现会改变旧源码原文，不应因此把历史引用判成失效；历史快照缺失或被改写仍会阻断，不能手工重写需求引用来迁就新代码。

| 现象 | 原因 | 处理 |
|---|---|---|
| `检测到现有 .factory 或 MISSION.md；拒绝覆盖` | 仓库已接入或有治理文件 | 不重复 init，运行 `.factory/setup.py check .` |
| `缺少 README.md 良性负例载体` | 缺少 B-01 负例文档 | 新增真实 README，再重新初始化 |
| `缺少 Git 基线提交` | 新仓还没有 HEAD，无法绑定源码版本 | 审阅工程差异并完成第一笔本地基线提交，再 init；失败审计保留，不会半装 `.factory` |
| `rules-source BLOCKED` | `rules_path` 不存在或锁不匹配 | 准备 `awesome-rules`，审核路径和锁，不自动下载 |
| `project-dependencies BLOCKED` | 没有 `node_modules` | 按项目锁文件执行 `pnpm install --frozen-lockfile` |
| `缺少 package.json scripts.coverage:gate` | 覆盖率只接在 Hook 或尚未完成 | 执行 `frontend-unit-test-setup`，补当前工作树正式入口并完成正反向验证 |
| 覆盖率脚本不出现在 `git status` | 旧 `.gitignore` 的 `coverage/` 误匹配 `scripts/coverage/` | 用 `git check-ignore -v <脚本>` 定位；经维护者审核，将产物规则改为根锚定 `/coverage/`、`/coverage-unit/`，不忽略校验程序 |
| 已明确“没有设计稿”却要求 Figma 认证 | 分类版本仍使用关键词存在性判断 | 查看 `classification.json` 的路由和否定意图证据；升级并审核新锁，已有冻结任务按第 8.2 节创建关联修订，不伪造设计证据 |
| `HEAD changed during Factory execution` | 当前任务的不可变基线与当前工程 HEAD 不同 | 核对中途提交/切分支/治理升级；保存历史，按第 8.2 节建关联修订，不重写旧基线 |
| `OMP` 未找到/未登录 | 模型节点不可用 | 交互终端安装并登录，不把凭证写入仓库 |
| `docs 引用资料已变化` | 已引用文档摘要发生变化 | 查看差异，从 `requirement` 或更早阶段重跑 |
| `--only-stage 超出授权上限` | 运行选项不能扩大授权 | 用户明确授权后使用 `--authorize-through` |
| `pending_acceptance` | 缺少必需浏览器/接口/测试环境证据 | 按请求文件补证据并恢复，不伪造通过 |
| 两个任务同时写同一目录 | 任务锁冲突 | 等待前一个进程退出，再恢复 |

## 9. 完成判定和交接清单

一个可交接的首个前端任务至少满足：

- [ ] 首次安装的 Bootstrap `check` 状态为 `passed`；业务开发后与团队接手使用 `check --check-mode project`，同时通过项目质量与覆盖率命令；`files_applied` / `pending_install` 均不算安装完成；
- [ ] 覆盖率 Skill 自身的机器清单、逐文件阈值收据和 Hook 正反向证据齐全，`pnpm run coverage:gate` 已对当前工作树完成正反向验证并被 Factory 质量命令登记；bootstrap 的 `coverage_gate` 提示只负责指向该独立验收，不作为完成状态；
- [ ] 工程基线和 Factory 配置分别经过人工审阅并建立本地提交；首任务无未审阅业务改动，体检产物或其他既有 WIP 已明确记录，运行期间不改写基线；
- [ ] `setup.py check`、Factory 回归和项目质量门通过；
- [ ] 所有阶段都有对应收据、日志、退出码和恢复命令；
- [ ] verify 的 `quality-gate-receipt.json` 绑定当前 `task_id/attempt_id`，失败时仍归档在对应 attempt，覆盖率子命令的原始退出码可追溯；
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

1. 完成工程 bootstrap、覆盖率门禁、Factory init 和 `check`；
2. 只跑到 Spec，人工审阅验收条件；
3. 用 Mock 完成一次本地实现；
4. 做一次独立审查和浏览器验证；
5. 再补测试环境证据；
6. 最后才决定提交、推送和是否扩大到下一个任务。

这样首个用户得到的不是一段无法解释的 AI 代码，而是一条有输入、有授权、有阶段产物、有失败恢复路径的可重复研发链路。

## 11. 团队成员 clone 后怎么继续

初始化动作只由首位接入者执行一次。团队成员拿到已经提交 `.factory`、`MISSION.md` 和工程基线的业务仓后，不再运行 bootstrap `apply` 或 Factory `init`：

```bash
# 三个仓仍建议放在同一父目录，并切到团队约定的固定分支/commit
cd ~/work/factory-pilot/first-factory-app
pnpm install --frozen-lockfile

python3 ../awesome-rules/skills/frontend-repo-bootstrap/scripts/bootstrap.py \
  check --target . --profile vue-admin --check-mode project
python3 .factory/setup.py check .
python3 .factory/run.py --check-runtime
python3 .factory/quality-gate.py
```

如果公共 Factory 或 Rules 版本变化，先看差异，不直接刷新锁。由维护者审核后运行公共锁定工具并提交共享锁文件；普通成员不得通过手改摘要绕过漂移阻断。

团队接手不再校验初始示例页面的逐字内容。`project` 检查只验证工程基线，仍要执行 `quality:check`、`coverage:gate` 和本次 Spec 的业务验收；不能以此忽略测试失败或任意修改治理配置。

## 12. 首用户遇到问题时需要提供什么

不要只发送“Factory 失败了”。提供以下脱敏信息即可定位，同时不要发送 token、cookie、`.env.local` 或账号密码：

```text
操作系统及 Node/pnpm/Python/OMP 版本：...
三个仓的分支与 HEAD：...
执行命令：...
退出码：...
task_id（尚未创建任务则写 NOT_CREATED）：...
当前 stage/status/resume_from：...
onboarding summary 或 attempt 证据目录：...
日志最后一个明确错误：...
git status --short：...
是否使用 Figma/Mock/测试环境：...
已经尝试过的恢复动作：...
```

Factory 已生成 `recovery_command` 时优先原样附上，但先确认需求、规则、文档和源码没有在失败后发生变化。不要删除任务目录重新制造“干净成功”。

## 13. 维护者的隔离演练记录

参见 [2026-11-03 接入修订与演练记录](design/first-frontend-user-rehearsal-20261103.md)。记录区分网络获取、工程初始化、真实模型阶段、门禁反例与浏览器验收；失败和恢复历史也保留。它提供本次环境的复核依据，不能替代使用者在自己账号、操作系统与项目上的验收。
