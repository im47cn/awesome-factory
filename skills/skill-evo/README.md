# skill-evo

会话经验自动总结与规范/技能进化 — 参考 Hermes self-evolution 的闭环：会话结束后自动提炼「用户纠正 / 失败模式 / 成功模式」，生成对 `skills/*/SKILL.md` 与 `steering/*.md` 的进化提案，人工审核后应用。

> **状态**：✅ v2 已实现（CC SessionEnd hook + omp 原生 hook 双端自动触发；GEPA 进化引擎已落地 replay-eval 确定性评估集打分链路，见 `docs/design/skill-evo-replay-eval.md`）。测试：`pytest skills/skill-evo/scripts/tests -q`（LLM 调用全 mock）。

## 架构

```
Claude Code                                    omp（oh-my-pi）
SessionEnd hook（hooks.json）                  session_shutdown hook（hooks/omp/skill-evo.ts）
  nohup 秒退，不阻塞会话                        Bun.spawn detached
        └──────────────┬──────────────────────────────┘
                       ▼
              scripts/evo.py run
                ├─ 会话定位：hook stdin / --cwd（首行 cwd 匹配 + 内容哈希增量去重）
                ├─ 脱敏切片（密钥/长 token/ANSI）+ 目标资产锚点索引
                ├─ headless claude -p 提炼 lessons（防递归三层）
                ├─ 搭车插件哑故障巡检（evo_patrol，节流 + 台账 patrol.json）
                └─ 提案落盘 ~/.config/ar/skill-evo/proposals/pending/
                       ▼
         人工审核：evo.py list / approve / apply / reject
           ├─ apply：锚点级追加写入（两阶段校验，失配不盲写）
           └─ rejected + reason → GEPA 负样本
                       ▼
         GEPA 进化引擎（evo.py evolve，手动低频）
           applied/rejected 提案 = 真实标注 → 进化自身总结 SYSTEM_PROMPT
```

核心护栏（对齐 Hermes「一切进化走人工评审」）：**提取全自动，应用必须人工**；只追加不改写（steering【强制】条款结构上不可被削弱）；永不自动 `git commit`。

## 快速使用

零配置即用（默认只总结 `~/sources` 下的会话）。CC 侧随插件安装自动生效；omp 侧需装一次 hook：

```bash
cp hooks/omp/skill-evo.ts ~/.omp/agent/hooks/pre/
```

- omp 自动发现用户级 hook；`session_shutdown` fire-and-forget 调用
  `evo.py run --agent omp --cwd <cwd>`；脚本路径可用 `AR_SKILL_EVO_SCRIPT` 覆盖
- 与 CC 搭车扫描并存无害（state.json 增量去重收敛）；防递归链：
  omp → evo.py（`AR_SKILL_EVO_CHILD=1`）→ claude -p（继承标记）→ CC hook 见标记即退
- 验证：安装后结束一个 omp 会话，`~/.config/ar/skill-evo/logs/evo.log` 应有记录

审核流程（在任意 AI 会话中说「查看 skill-evo 提案」即可触发技能引导）：

```bash
python3 skills/skill-evo/scripts/evo.py list            # 列 pending 提案（含护栏警告）
python3 skills/skill-evo/scripts/evo.py apply <id> --dry-run   # 预演
python3 skills/skill-evo/scripts/evo.py approve <id>   # 人工批准（apply 前置门禁）
python3 skills/skill-evo/scripts/evo.py apply <id>      # 应用（须先 approve；锚点失配整体失败）
python3 skills/skill-evo/scripts/evo.py reject <id> --reason "证据不足"   # 驳回 → GEPA 负样本
python3 skills/skill-evo/scripts/evo.py patrol [--force]          # 插件哑故障巡检复查
```

GEPA 进化（冷启动保护：标注 ≥10 cases 且 ≥8 sessions 才可运行）：

```bash
python3 skills/skill-evo/scripts/evo.py evolve --dry-run  # 查看标注积累进度
python3 skills/skill-evo/scripts/evo.py evolve            # 进化总结 prompt（预算默认 16 rollouts）
python3 skills/skill-evo/scripts/evo.py evolve --skill ddl-guard  # replay-eval 评估集链路

```

replay-eval 链路（详见 `docs/design/skill-evo-replay-eval.md`）：以 `skills/<skill>/badcase/`（拦截）+ `eval/`（放行/混合）确定性评估集为打分信号源，GEPA 候选经 headless claude 审查、确定性解析器提取检出清单、逐 case F1 打分；holdout 集独立于 rollout 预算必评（baseline c0 作改善锚），门禁拒绝「全盘拒绝」型退化，改善 > 0.2 才生成 prompt_evolution 型 pending 提案（人工采纳，不自动 apply）。

### 评估协议（Comet 三机制升级，@date 2026-09-20）

指标（每 case 独立 k 次采样，`replay_k` 默认 3）：

- **pass@k**：HumanEval 无偏估计 `1 - C(n-c,k)/C(n,k)`（c = 通过次数）——能力上限；
  `n<k` 时退化为「至少一次通过」并标记 degenerate
- **pass^k**（`pass_cap_k`）：k 次全过（0/1）——可靠性下限，GEPA 主信号
  （holdout 优化目标，改善锚不变）
- 单次通过判定 = 该次 F1 ≥ `replay_pass_threshold`（默认 1.0）；调用轮次未过
  证据门禁的采样计失败（分子剔除、分母保留）

调用证据硬门禁（`replay_evidence`，默认开）：headless claude 加 `--output-format
stream-json`，工具调用流中未出现指向本 skill 的 Read/Bash/Skill 事件的轮次记
0 分——prompt-only 污染（Comet 实测 ~70% 触发率虚高）不计分。证据模式下
prompt 不嵌候选文本，改为指令 Agent 用 Read 工具完整读取部署态
`skills/<skill>/SKILL.md`（评估部署态技能保真度，不能直接用于 GEPA 变异
候选筛选）。

双 Agent 多轮（case 目录含 `prompts.md` 即激活，`replay_dual_agent` 默认开）：
被测 Agent 在 `DECISION_REQUEST:` 决策点暂停，模拟用户 Agent 消费 prompts
素材逐回合应答；素材耗尽 / 空应答走确定性兜底（不走 LLM），调用预算
`2*(回合数+1)` 封顶。

证据产物（跨路契约，schema `replay-evidence/1`；`artifacts/` 默认 gitignore，
唯 `replay-evidence/` 豁免——登记后的真实证据可入库）：
`skills/skill-evo/artifacts/replay-evidence/<skill>.json`，字段顺序固定
`schema/skill/content_hash（SKILL.md+scripts 逐文件摘要清单指纹，排除派生
产物，与 release_guard 同一算法、双算对拍锚定）/generated_at/k/
pass_at_k/pass_cap_k/invocation/cases（整数计数）`；逐 case 明细走 CLI stdout。
写入方两个：dry-run 冒烟入口（零 LLM）与 `evo.py evolve --skill` GEPA 结束后
的部署态证据 pass（stream-json 实测 `cmd_evidence_llm`；`replay_evidence=False`
跳过）。dry-run 载荷拒绝覆写既有 stream-json 证据（或无法核验的既有文件），
fail-closed——防本地冒烟静默抹掉 k×cases LLM 成本（已提交证据可 checkout
恢复）。证据 JSON 落地 ≠ 技能合格：登记与消费由 release_guard
`EVIDENCE_ENROLLED` 驱动（见 ../../scripts/release_guard.py）。

```bash
python3 skills/skill-evo/scripts/evo_replay.py ddl-guard   # 证据 dry-run 冒烟（零 LLM，CI 可跑）
```

成本：完整评估 = k × cases 次 LLM 调用为下限——含 `prompts.md` 的多轮 case
每次采样上浮至 `2*(回合+1)` 次调用封顶（GEPA 预算按 execute 调用次数计，
每次内部含上述 k 次采样）；dry-run 入口零 LLM。

## 提案格式

置信度三级（High = 明确纠正 / Medium = 可行模式 / Low = 待观察），每条 lesson 必须含可追溯 evidence（审核第一步即核对原文，防幻觉）。护栏命中需 `--force`：

- new_text 含【强制】标记（强制级别是人工评审决策）
- 置信度 Low
- new_text 与目标文件既有段落语义相似（difflib >= `idempotent_threshold`，默认 0.8；
  逐字/归档命中为重复沉淀另两分支）——疑似换皮重提，需人工 diff 后改写合并
- `knowledge_type=instance`（随环境实例变化的 ID/路径/字段名等）→ 不入技能文档，
  指向代码或 ADR
- `prompt_evolution` 型提案不走 apply，人工编辑 `evo_prompt.py` 的 `SYSTEM_PROMPT` 采纳

- evidence 未命中来源会话（`✗` 可疑编造）或经作者改写（`✎` 与 .orig 快照不一致，
  机器核验不背书）——两者均须人工必审后 `--force`

## 插件哑故障巡检（evo_patrol）

CC 插件 `failed to load` 不弹通知，hook 静默失效无感知（曾发生：awesome-rules
插件因 manifest 重复 hooks 声明静默失效多日）。`evo.py run` 搭车巡检
`claude plugin list`（`patrol_interval_hours` 节流，默认 6h）：

- 加载失败落盘 `~/.config/ar/skill-evo/patrol.json` 台账（含 first_seen），
  新故障/错误变化记 `logs/evo.log` 告警，恢复记恢复日志
- `evo.py list` 置顶展示未决故障；`evo.py patrol [--force]` 手动复查（有故障 exit 1）
- 巡检自身永不抛栈（哑故障检测器不能自己成为哑故障）

## 设计边界（v2）

- apply 的人工审核不可降级为 AI 链自审（AI 初审 + AI 终审批量落盘）：2026-08-28 一次批量 apply 30 条 lessons 的计划被硬护栏拦截后撤销——AI 审 AI 会自我强化偏差，批量产出正是提案机制要消除的垃圾信息
- 扩展 SYSTEM_PROMPT 契约（如锚点层级从 `##` 扩到 `##`/`###`）时，须同步更新 `validate_candidate` 契约关键词与既有契约测试：reflector 重写掉新指引的候选要能被拒绝，防历史 ## 样本混训导致契约回退

- 排查「会话被跳过/被重复处理」时先查 state.json 的内容哈希记账（omp 退出 flush 会碰 mtime，mtime 不可作为处理依据）
- 进化目标：`skills/**/*.md`、`steering/**/*.md`、根 `README.md`（索引表，表格感知追加）、
  根 `CLAUDE.md`（AI 操作指引）；只做**追加**，不做改写/删除，不做「新增 skill」级提案
- README 索引另有确定性兜底：`scripts/md_link_check.py`（链接有效性 + README 索引
  零漂移统一门禁，已接入 `run_tests.sh`），磁盘新增技能/规范/设计文档而 README
  未登记即红
- 处理过的会话不再重提（state.json 内容哈希去重 + 单会话单提案守卫）
- omp 触发优先用原生 hook（未安装时 CC 搭车扫描兜底）；`evo.py scan-omp` 可手动查看
- GEPA 进化对象 v2 仅 `SYSTEM_PROMPT`；guard skill 触发词进化待数据积累后立项

## 配置

`~/.config/ar/skill-evo/config.toml`（可选），模板见 [`config.example.toml`](config.example.toml)。常用项：`scope_dirs`（会话范围）、`min_messages`（跳过短会话）、`gepa_budget`、
`patrol_interval_hours`（插件巡检节流间隔）。总开关：环境变量 `AR_SKILL_EVO_ENABLED=0`。

## 相关文件

- 技能定义：[`SKILL.md`](SKILL.md)（AI 审核操作指引）
- 核心脚本：[`scripts/evo.py`](scripts/evo.py)（CLI）、[`scripts/evo_patrol.py`](scripts/evo_patrol.py)（插件巡检）等
- 技术设计（含 GEPA 算法保真点与竞态修复记录）：[`../../docs/design/skill-evo-design.md`](../../docs/design/skill-evo-design.md)
- CC hook：[`../../hooks/on-session-end.sh`](../../hooks/on-session-end.sh)
- omp hook 模板：[`../../hooks/omp/skill-evo.ts`](../../hooks/omp/skill-evo.ts)
