# 黑盒工厂接入指南（新电脑 / 新项目）

> 本文是接入动作的**单一入口**：按场景给出步骤序列与验证点，细节以各权威
> 文档为准（文内标注出处）。分发面语义见
> [.factory/DISTRIBUTION.json](../.factory/DISTRIBUTION.json)；
> 移植权威 = [.factory/README.md §移植到其他仓库](../.factory/README.md)；
> 调度权威 = factory-hub 仓（`im47cn/factory-hub` → `~/.config/factory`）。
>
> 维护纪律：本文只写「步骤序列 + 出处指针」，不复述权威文档细节——
> 权威变更时只需改源头，本文无需追改（防双真相源漂移）。

## 前端项目：先在本地跑通（推荐入口）

适用于尚无 `.factory` 的 Vue/TypeScript 项目；先检查现有工作树和包管理器，不切分支、不提交或推送，也不安装远程派发器。当前一站式入口支持前端仓，后端仍使用下文的 factory-init profile。

```bash
# 新仓库第一次接入（在任意目录执行，路径可包含空格）
python3 /path/to/awesome-factory/tools/factory-local-setup.py init /path/to/frontend-repo

# 团队 clone 已接入仓后：项目根执行，不重复 init
python3 .factory/setup.py check .

# 先生成需求和 Spec；task 是目录名，task_id 自动生成
python3 .factory/run.py --task first-pilot --request-file /path/to/request.md --only-stage spec

# 用户明确授权实施后，扩大上限并从下一阶段继续
python3 .factory/run.py --task first-pilot --request-file /path/to/request.md --resume --authorize-through verify
```

接入入口复用现有治理初始化，而非重新实现 guard/mutation。自动按 package.json 选择已有质量命令，安装公共本地运行器模板和前端 Figma 采集适配器；配置中禁止自动提交和远端副作用。现有 `.factory`/MISSION 会被拒绝覆盖，升级须由维护者审核。

每次接入/体检生成 `artifacts/factory-local/onboarding/<run-id>/nodes.jsonl`、逐节点原始日志和 `summary.json`。记录分支、基线、节点结果、命令退出码、安装文件摘要和上游快照；运行产物由 Git 本机 exclude 排除，团队成员执行 check 时自动配置。共享 `.factory` 文件和 MISSION 则应纳入版本控制。

接入检查仅证明程序、治理门和工具存在，不会自动调用模型、登录 Figma 或访问测试环境。模型认证、实际编码、UI/API 和业务验收保持 NOT_RUN，待真实试点需求验证。质量基线有问题须单独记录，不作为接入成功或业务完成的证明。

若提示缺少规则库：clone 到项目兄弟目录 awesome-rules，或设置 `AWESOME_RULES_ROOT`。若提示缺项目依赖：使用满足 package.json engines 的 Node/pnpm，执行 `pnpm install --frozen-lockfile`。若缺 OMP：安装并在交互终端登录；不要把凭证放入共享配置。

## 场景观别

| 你要做什么 | 场景 | 走哪节 |
|---|---|---|
| 换电脑 / 新机器恢复整套工厂 | **A. 新电脑** | [§1](#1-场景-a新电脑) |
| 让一个新仓库被工厂维护（issue 进 PR 出） | **B. 新项目（下游移植）** | [§2](#2-场景-b新项目下游移植) |
| 已移植仓追增量 / 追平上游 | 既非 A 也非 B | [§3](#3-增量维护已移植仓) |

工厂由两个 git 仓组成，两者都要在机上：

| 仓 | 位置 | 职责 |
|---|---|---|
| **awesome-factory**（本仓） | `~/sources/awesome-factory` | 工具链唯一上游真相源（`.factory/` full 面） |
| **factory-hub** | `~/.config/factory`（git clone 自 `im47cn/factory-hub`） | 中央调度（LaunchAgent → dispatch-all.sh）+ 新仓接入模板 + factory-init |

## 1. 场景 A：新电脑

### 1.1 前置条件

- `omp` CLI（AI 节点引擎）
- `python3`
- 托管平台凭据（ADR-008，二选一）：GitHub = `gh` 已认证；Codeup = `YUNXIAO_ACCESS_TOKEN` + `CODEUP_ORG_ID`（可经 git remote 自动推导，见 factory-hub README「Codeup 7 仓」节）
- （可选）`SUPABASE_DB` 租约仲裁 PG 连接串；不设 = 单写者本地锁降级
- SSH：若维护 wop-platform 仓，`~/.ssh/config` 需 `github-wop-bot` Host 别名（`FACTORY_SLUG_EXTRA_HOSTS` 白名单依赖，见 dispatch-all.sh 头注释）

### 1.2 步骤

```bash
# ① clone 两个仓
git clone git@github.com:im47cn/awesome-factory ~/sources/awesome-factory
git clone git@github.com:im47cn/factory-hub ~/.config/factory

# ② hub 安装（幂等）：写 com.im47cn.factory.hub.plist + launchctl 注册
sh ~/.config/factory/install.sh

# ③ 注册表核对：repos.conf 每行一个仓绝对路径，缺失的补上
cat ~/.config/factory/repos.conf

# ④ 日回归 LaunchAgent（独立于 hub plist，install.sh 不覆盖）
launchctl load ~/Library/LaunchAgents/com.im47cn.factory.daily.plist
#   前提：.factory/artifacts/regression/ 已存在（任意一次手动/dry-run 会建好）
bash ~/sources/awesome-factory/.factory/regression/daily-regression.sh --dry-run
```

### 1.3 凭据恢复清单（git clone 恢复不了的）

| 凭据 | 位置 | 说明 |
|---|---|---|
| Codeup token 等 | hub plist `EnvironmentVariables` | install.sh 写的 plist **不含** token——从旧机 plist 或密码管理器补录（注意：plist 内是明文） |
| 飞书告警凭证 | `~/.omp/agent/feishu/config.json` | dispatch-all.sh 告警推送依赖 |
| 飞书 open_id | dispatch-all.sh 内 `ALERT_OPEN_ID` | 已入库，通常免改 |
| Codeup spaceId 映射 | `~/.config/factory/codeup-spaces.conf` | factory-hub 仓 tracked，clone 即到 |
| `gh` / `git` 凭据 | 各自标准登录流程 | `gh auth login` |

### 1.4 验证

```bash
python3 -m pytest ~/sources/awesome-factory/.factory/tests -o addopts= -q   # 工厂测试套件
sh ~/.config/factory/install.sh                                             # 幂等重跑应全「· 已存在」
launchctl list | grep factory                                               # hub + daily 两个 job
cd <某注册仓> && ./.factory/cron-dispatch.sh; echo $?                       # 0=正常 2=环境/配置 3=熔断
```

## 2. 场景 B：新项目（下游移植）

> 权威出处：[.factory/README.md §移植到其他仓库](../.factory/README.md)。

**正道 = 上游侧 CLI**（`tools/factory-init.py`，零 LLM、两段式；与
`DISTRIBUTION.json` 同仓同演进，鸡生蛋结构性不存在）：

```bash
# 第一段：拷贝 full 面 + 生成 MISSION.md / factory-local.json 骨架（占位周界）→ exit 1
python3 ~/sources/awesome-factory/tools/factory-init.py init /path/to/new-repo \
    --scope "使命范围一句话" --upstream ~/sources/awesome-factory

# 人工填写周界（MISSION.md 是唯一真相源），然后第二段：
python3 ~/sources/awesome-factory/tools/factory-init.py init /path/to/new-repo \
    --perimeter 'docs/,src/,MISSION.md,.gitignore'

# 本地 AI 编码仓推荐：自动识别前端/后端/全栈，只安装本地链路需要的最小面
python3 ~/sources/awesome-factory/tools/factory-init.py init /path/to/new-repo \
    --mode local --project-type auto \
    --perimeter 'docs/,src/,.gitignore' --no-register --no-labels
# 第二段自动完成：配置落定 → 结构缺陷集生成 → guard self_check →
# mutations kill-rate 全量重证（强制，无 skip）→ 注册 repos.conf →
# gh 标签预建。不 commit——落库内容（周界划界）正是人要审的。

# 机况体检（新电脑 / 排障）：非交互，✓/✗/⚠ + 修复指针
python3 ~/sources/awesome-factory/tools/factory-init.py doctor
```

`--mode local` 只安装 `guard.py`、`factory_lib.py` 和 `mutations/run.py`，
并在现有 `factory-local.json` 中记录 `project_type`、`execution_mode`、
推荐的 `final_gate_cmd` 和本地 runner。项目类型可选 `frontend`、`backend`、
`fullstack`，默认 `auto` 按仓库文件判断。前端仓不会生成远程调度、托管平台、
验收和下游巡检脚本；后端也不会因为只是本地编码流程而生成这些远程副作用面。
需要云效/GitHub 调度时再使用默认的 `--mode full`。

手工四步（CLI 的等价展开，权威细节见
[.factory/README.md §移植到其他仓库](../.factory/README.md)）：

1. **拷贝**：上游 `DISTRIBUTION.json` 的 full 面 + `prompts/` + `tests/` →
   目标仓 `.factory/`（排除 artifacts/locks/worktrees 等运行时产物）。
2. **写目标仓 `factory-local.json`**（skip 分发，每仓一份）：`perimeter` /
   `reject_guidance` / `repo_identity` / `reading_scopes` / `review_basis` /
   `final_gate_cmd`（目标仓真实测试命令）/ `pr_review_skills` /
   `upstream_repo` / `upstream_path` / `feedback_branch_prefix`。
3. **写目标仓 `MISSION.md`**（使命与 triage 判据），重跑
   `python3 .factory/mutations/run.py` 重证 kill rate——改过周界未重证的门
   不算门（evidence-stamp 指纹绑定强制）。
4. **平台适配**：GitHub 仓零配置；Codeup 仓设 `FACTORY_HOSTING=codeup` +
   相关环境变量（ADR-008）。

移植后按 [.factory/README.md §下游采纳 M2/M4 checklist](../.factory/README.md)
（**顺序不可倒**）启用自动追平。CLI 第二段已覆盖注册表登记与标签预建；
手工路径需自行补：

```bash
# 注册中央调度（零系统安装）：repos.conf 加一行绝对路径
echo "/path/to/new-repo" >> ~/.config/factory/repos.conf
# 完整性检查：.factory/ 需 cron-dispatch.sh + breaker.sh + triage-batch.sh + dispatch.sh 四件套
# （2026-09-01 etf-radar 缺 breaker.sh → exit 127 事故，见 factory-hub README）
```

### 接入路径现状（2026-10-12 盘点）

| 路径 | 状态 | 说明 |
|---|---|---|
| `tools/factory-init.py`（上游侧 CLI） | **正道** | 两段式零 LLM；mutations 强制重证；E2E 见 `.factory/tests/test_factory_init.py`（先行失败清单 + golden 字节比对） |
| 四步手工移植 | 等价展开 | CLI 不可用时的手工路径，权威出处同上 |
| factory-hub 的 `factory-init.sh`（模板一键接入） | **已废弃** | ADR-009 前旧架构（MISSION 派生 guard、无 factory-local.json、缺现行 full 面文件）——勿用；其「一键 + 两段式」设计已由上游 CLI 继承并追平 |

## 3. 增量维护（已移植仓）

```bash
# 逐仓：漂移检查 / 追平（单提交落库不推送）
.factory/sync-from-upstream.sh ~/sources/awesome-factory --check
.factory/sync-from-upstream.sh ~/sources/awesome-factory --apply --commit

# 中心巡检全舰队：downstream.local.json（gitignored）配清单
bash .factory/downstream-check.sh --apply-commit
```

漂移闭环：下游热修 → `feedback-upstream.sh` 反哺 PR → 上游合并 → 下游
`--apply` 追平 → `--check` 归零。详见
[.factory/README.md §上游同步](../.factory/README.md)。

公共本地十阶段工作流、三项 Skill 分层、项目薄适配与升级说明见 [shared-local-workflow](design/shared-local-workflow.md)。旧七阶段外部验收协议单独保留，不与原生流程快照混用。
