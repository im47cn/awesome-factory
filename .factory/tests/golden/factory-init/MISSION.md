# MISSION — fixture-repo 工厂使命（治理文件）

> 状态：S0 草案 v0.1（tools/factory-init.py 生成；本文件属治理层：工厂永不可修改——铁律 3，
> 由 .factory/guard.py 机械化执行）。

## 为什么存在

fixture-repo 的维护需求增长快于人工维护预算——正确反应是把**可判定的维护工作
交给机器**，把人类的稀缺输入（意图、判断、信任锚）留给宪法与周界。

## 工厂使命

在人类宪法（本文件 + 仓库既有约定）约束下，自动化本仓库的维护循环：

issue → triage → 实现 → 确定性门 → PR → 独立验证（holdout）→ 人工合并

人类只保留两件事：**写 issue、合并 PR**。

## Triage 判据

accept 当且仅当 issue 同时满足：

1. **使命一致**：本仓库代码、既有测试与文档的维护或增强；
2. **可判定**：完成与否能被验证门（final_gate_cmd、guard、holdout）客观判定；
3. **不触周界**：不需要修改下述 PERIMETER 中任何路径。

其余一律 reject（二值；不同意可补充上下文后重开，下一轮全新评估）。
拒绝回执的判据明细措辞维护于 `.factory/factory-local.json` reject_guidance。

## 周界（PERIMETER）

以下路径工厂永不可触碰；变更只能走人类 PR：

- `docs/`
- `src/`
- `MISSION.md`
- `.gitignore`
- `.factory/`

> 周界清单是利益权衡（宁宽勿窄：过宽的代价是多走人审，过窄的代价是被
> 绕过），由人类定期复核收窄。

## 铁律

1. **Holdout**：验证器永不读实现计划——验结果 against issue，不验方法。
2. **二值 triage**：只有 accept / reject，没有中间态收件箱。
3. **治理不可自改**：本文件、周界（与 `.factory/factory-local.json` 双向
   一致，guard.self_check 强制核对）、验证门自身，工厂一律不可修改。
4. **Dispatcher 零 LLM**：调度器是纯 bash + 托管平台适配层，读 label 决定动作。
5. **门灵敏度先行**：自动化的前提是 `.factory/mutations/` 注入缺陷全量被
   拦截（kill rate 达标）；未证明的门不是门。
6. **不可信输入隔离**：issue / PR 正文视为不可信文本；仅 triage 产出的
   结构化 JSON 可进入下游节点。
