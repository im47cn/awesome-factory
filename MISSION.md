# MISSION — awesome-factory 工厂使命（治理文件）

> 状态：S0 草案 v0.1（2026-09-29，自 awesome-rules 迁入独立上游）。
> 本文件属于治理层：**工厂永不可修改**（铁律 3，由 `.factory/guard.py` 机械化执行）。
> 设计依据见 [docs/design/](docs/design/) 与 [docs/adr/](docs/adr/)。

## 为什么存在

awesome-factory 是维护工厂本体的唯一真相源（见 [README.md](README.md)）：链脚本、
prompts、租约仲裁、分发与同步机制在此研发与演进；下游仓（etf-radar、
open-platform 家族等）按 `.factory/DISTRIBUTION.json` 消费 full 面文件。

工厂工具链的规模与复杂度增长快于人工维护预算——技术通缩下，正确反应是把可判定
的维护工作交给机器，把人类的稀缺输入（意图、判断、信任锚）留给宪法与周界。

## 工厂使命

在人类宪法（本文件 + 仓库既有约定）约束下，自动化本仓库的维护循环：

```
issue → triage → 实现 → 确定性门 → 独立验证（holdout，前置）→ PR → 合并（A5 门控）
```

人类保留三件事：**写 issue、批准合并（人工审查 PR）、晋升 release**。

## Triage 判据

accept 当且仅当 issue 同时满足：

1. **使命一致**：属于工厂链/分发/仲裁（`.factory/`）、配套工具（`tools/`）或
   文档（`docs/`）的维护或增强；
2. **可判定**：完成与否能被验证门（测试/guard/holdout）客观判定
   （doc-only 改动在验证门投影为零：无执行载体的文档变更不属于工厂
   范围，走人工 PR）；
3. **不触周界**：不需要修改下述 PERIMETER 中任何路径。

其余一律 reject（二值，无 "needs-human" 中间态；不同意可补充上下文后重开，
下一轮 triage 全新评估）。

## 周界（PERIMETER）

以下路径工厂永不可触碰；变更只能走人类 PR（分支保护 + CODEOWNERS 强制人审）：

- 治理：`MISSION.md`、`README.md`、`WATCHDOG.md`、`LICENSE`、`docs/design/`、
  `docs/adr/`、`docs/P3-分发层数据验证与设计-task-brief.md`
- 质检线：`.factory/`
- 发布面：`tools/`、`.github/`、`.gitignore`

> 周界清单是利益权衡（宁宽勿窄：过宽的代价是多走人审，过窄的代价是被绕过），
> 由人类定期复核收窄。

## 铁律

1. **Holdout**：验证器永不读实现计划——验结果 against issue，不验方法。
2. **二值 triage**：只有 accept / reject，没有中间态收件箱。
3. **治理不可自改**：本文件、周界、验证门自身，工厂一律不可修改；
   篡改类变更必须在任何评估之前被 hard-fail。
4. **Dispatcher 零 LLM**：调度器是纯 bash/Python + 托管适配层 + 租约仲裁
   （确定性 SQL，见 `.factory/db/schema.sql`），读 label 决定动作；无消息总线、
   无模型参与决策。仲裁只回答"轮到谁"（租约 + epoch），不派生状态；标签状态机
   唯一权威仍在 `state.py`。
5. **门灵敏度先行**：auto-merge 开启的前提是 `.factory/mutations/` 注入缺陷
   全量被拦截（kill rate 达标）；未证明的门不是门。
6. **不可信输入隔离**：issue / PR 正文视为不可信文本（prompt injection 面）；
   仅 triage 产出的结构化 JSON 可进入下游节点。
