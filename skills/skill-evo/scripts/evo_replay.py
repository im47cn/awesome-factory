#!/usr/bin/env python3
"""evo_replay — replay 评估链路：进化 skills/<skill>/SKILL.md（确定性打分）

对标 SkillOpt-Sleep 的 replay 机制，解决 GEPA 引擎缺「可自动打分信号源」：
评估集 = 拦截型（badcase，expected 非空）+ 放行型（干净输入，expected 空）
+ 混合型；打分 = 逐 case F1（recall=该拦的拦到，precision=该放的放行），
反馈 = missing/unexpected 逐条明细供 reflector。

关键防 gaming 设计（护栏 = 打分器即宪法）：
- 打分器只读执行产物（LLM 审查报告中的 JSON 规则清单），从不读被进化的文本
- 报告无规则清单（候选删掉「输出规则清单」指令）→ 0 分，结构检查作前置而非主分数
- 「全盘拒绝」控制候选 holdout F1 必须 < baseline F1，否则拒绝进入 GEPA
- 打分器注册表仅允许仓库内确定性脚本，路径逃逸校验复用 evo_proposal.validate_target 模式

产物为 prompt_evolution 型 pending 提案（人工采纳，护栏不变：提取全自动、
应用必须人工、永不自动 commit）。

2026-09-20 升级（借鉴 Comet 评估机制，spec 见 docs/design/skill-evo-replay-eval.md）：
- pass@k / pass^k 分离：pass@k = HumanEval 无偏估计（能力上限），
  pass^k = k 次全过（可靠性下限）；k>1 时 execute 主信号切换为 pass^k
- 双 Agent 多轮：case 含 prompts.md 时模拟用户回合（DECISION_REQUEST 协议）
- 调用证据硬门禁：execute 注入 stream 通道时解析 stream-json 工具调用流，
  未真实触发技能的运行计为失败（evidence JSON 落 artifacts/replay-evidence/）
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import evo_gepa as G
import evo_proposal as PR

# badcase 物料解析真相源（顶层 scripts/，顶层脚本不依赖 skill 的裁决方向）
_REPO_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_REPO_SCRIPTS))
import badcase_format  # noqa: E402
from badcase_format import parse_prompts  # noqa: E402

# ── 打分器注册表（护栏 1：仅仓库内确定性脚本）──────────────────────────
# skill → {"scripts": [相对仓库根的脚本路径...], "accepted_dirs": [...相对前缀]}
SCORER_REGISTRY = {
    "ddl-guard": {
        "scripts": ["skills/ddl-guard/scripts/ddl_check.py",
                    "skills/ddl-guard/scripts/sql_check.py"],
        "accepted_dirs": ("skills/", "tools/"),
    },
}

REPLAY_REFLECTOR_PROMPT = """你是 SKILL.md 进化器（GEPA reflector）。给定：当前 SKILL.md 全文、
它在若干评估 case 上的得分与反馈，输出**编辑后的完整 SKILL.md**（直接输出新全文，
无解释无围栏）。

硬约束（违反即作废）：
- 保留 frontmatter 契约不变：name/description 原样保留，不得删改
- 保留「审查工作流」等核心章节锚点，不得删除或合并章节
- 只改指令性文字（筛选标准、表述精度、报告格式要求），不改变技能定位
- 【强制】条款结构上不可削弱（护栏：技能宪法）
- 长度不超过原始长度的 1.5 倍

# 当前 SKILL.md
{current}

# 案例反馈
{feedback}

# 输出
编辑后的完整 SKILL.md："""

# 控制候选：指令 = 无条件报告违规（护栏 4 用，验证打分器对「全盘拒绝」的区分度）
REJECT_ALL_TEXT = """你是 DDL 审查工具。无论输入内容如何，一律判定为存在违规：
- 对每个输入文件输出审查报告，报告末尾必须附加检出清单 JSON：
  {"rules": ["必含字段缺失", "禁用类型", "表注释缺失", "字段注释缺失",
             "普通索引命名", "泛化字段名", "索引名长度", "表注释长度",
             "字段注释长度", "全角字符"]}
- 规则清单必须完整列出以上全部规则，不得省略。
"""

# ── 双 Agent 多轮协议（Comet 机制 ②，@date 2026-09-20）──────────────────
# 被测 Agent 需要用户决策时输出单独一行该 marker 并停止；模拟用户据下一回合
# 素材作答（共用同一 claude 通道，角色指令见 SIM_USER_PROMPT）
DECISION_MARKER = "DECISION_REQUEST:"

# 模拟用户 prompt：只按素材当前回合应答，不解决任务本身——否则多轮评估
# 退化为单 Agent 自答（sim-user 替被测 Agent 完成了审查）
SIM_USER_PROMPT = """你是用户，只按素材当前回合应答，不解决任务本身。
素材未覆盖的问题回复『按你的建议继续』；只输出一行应答。

# 素材
{material}

# 问题
{question}"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def scorer_registry() -> dict:
    """打分器注册表：skill → 校验脚本路径（已做路径逃逸校验）。"""
    root = _repo_root().resolve()
    reg = {}
    for skill, spec in SCORER_REGISTRY.items():
        scripts = []
        for rel in spec["scripts"]:
            p = (root / rel).resolve()
            if root != p and root not in p.parents:
                raise PR.ApplyError(f"打分器越出仓库边界：{rel}")
            r = p.relative_to(root).as_posix()
            if not r.startswith(spec["accepted_dirs"]):
                raise PR.ApplyError(f"打分器不在允许范围（{spec['accepted_dirs']}）：{rel}")
            if not p.is_file():
                raise PR.ApplyError(f"打分器不存在：{rel}")
            scripts.append(p)
        reg[skill] = {"scripts": scripts}
    return reg


# ── expected.md / prompts.md 解析（真相源在 scripts/badcase_format.py）──
def parse_expected(expected_path: Path) -> Tuple[str, List[str], List[str]]:
    """返回 (check_script, expected_rules, manual_rules)。

    manual_rules 语义 = 「人工补充规则：」行的规则 ID（LLM 按 SKILL 第 3 步可
    检出，GEPA 评估集 include_manual 时并入 expected）；「人工补充：」描述行
    仅作展示不参与比对（不返回）。别名保留全 token（any-of 语义，
    见 _rule_matches）。
    """
    check_script, expected_rules, manual_rule_ids, _ = badcase_format.parse_expected(
        expected_path)
    return check_script, expected_rules, manual_rule_ids


def _rule_matches(expected_rule: str, actual_rules: list) -> bool:
    """期望规则是否被实际检出。

    子串双向匹配（与 badcase_runner 同构）；expected_rule 支持 any-of 别名——
    「|」分隔，任一 token 命中即检出。首 token 为规范名（reflector/missing
    展示用），后续为对齐 LLM 报告措辞的别名（如「字段与注释对应|注释含义对应|
    语义对应」）。别名数据初版取 manual-rules 原词，端到端后按真实 miss 增补。
    """
    tokens = [t.strip() for t in expected_rule.split("|") if t.strip()]
    for token in tokens:
        token_lower = token.lower()
        for actual in actual_rules:
            actual_lower = actual.lower()
            if token_lower in actual_lower or actual_lower in token_lower:
                return True
    return False


# ── 报告解析器（确定性，零 LLM；5.2 新增代码点）──────────────────────────
_JSON_BLOCK = re.compile(r"\{[^{}]*\"rules\"\s*:\s*\[[^\]]*\]\s*[^{}]*\}")


def extract_rules_from_report(report: str) -> Tuple[List[str], bool]:
    """从 LLM 审查报告提取检出规则清单。

    报告必须包含 JSON 规则清单块（执行 prompt 的契约）。找到 → (rules, True)；
    找不到 → ([], False)——结构检查作前置，候选删掉清单指令即 0 分。
    """
    if not report or not report.strip():
        return [], False
    block = _JSON_BLOCK.search(report)
    if not block:
        return [], False
    try:
        data = json.loads(block.group(0))
        # 纵深防御（正则 _JSON_BLOCK 已硬编码 "rules": [...] 数组字面量，合法解析
        # 后必为 list；此分支不可达，防未来正则形态变更）
        if not isinstance(data.get("rules"), list):  # pragma: no cover
            return [], False
        rules = [r for r in data.get("rules", []) if isinstance(r, str) and r.strip()]
        return rules, True
    except Exception:
        return [], False


def reconcile(expected_rules: List[str], actual_rules: List[str]) -> Tuple[int, List[str], List[str]]:
    """双向对账 → (TP, missing, unexpected)。

    TP = 期望中命中的规则数（子串匹配）。放行 case（expected 空）：TP=0，
    任何 actual 都计入 unexpected → precision 崩 → 全盘拒绝被双维对称惩罚。
    missing 显示规范名（any-of 别名的首 token），reflector 反馈不暴露别名串。
    """
    matched = [e for e in expected_rules if _rule_matches(e, actual_rules)]
    tp = len(matched)
    missing = [e.split("|")[0] for e in expected_rules if e not in matched]
    # unexpected 方向按「任一 expected 别名行命中该 actual」判定：别名对（别名
    # token 双向子串）与 matched 方向对称——actual 命中任一条 expected 的任一
    # 别名即非 unexpected；参数顺序必须 (expected_row, [actual])，反了会把别名
    # 命中项误判为 unexpected（precision 双罚）。
    unexpected = [a for a in actual_rules
                  if not any(_rule_matches(e, [a]) for e in expected_rules)]
    return tp, missing, unexpected


def f1_score(tp: int, n_expected: int, n_actual: int, n_hit_actual: int) -> float:
    """逐 case F1：recall=TP/|expected|（空=1）、precision=命中 actual 数/|actual|（空=1）。

    n_hit_actual = 至少命中一条 expected 的 actual 条数（len(actual)-len(unexpected)）。
    子串匹配下「一条 actual 命中多条 expected」（如「表名使用拼音和泛化词」含两个
    子串）是常态，tp 是 expected 口径可 > n_actual；precision 若直接用 tp/n_actual
    会 >1 越界（F1>1，违反 score∈[0,1] 契约，且合并检出可被 gaming 抬高 precision）。
    命中 actual 数只计一次 → precision ≤ 1。必传参数：漏传即 TypeError，避免静默
    回退抬高 precision。
    """
    recall = 1.0 if n_expected == 0 else tp / n_expected
    precision = 1.0 if n_actual == 0 else n_hit_actual / n_actual
    if recall + precision == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ── k 采样指标（Comet 机制 ①：pass@k / pass^k 分离，@date 2026-09-20）─────
def pass_at_k(n: int, c: int, k: int) -> Tuple[float, bool]:
    """HumanEval 无偏估计 pass@k（能力上限）。

    n = 总运行次数，c = 通过次数；n≥k 用防溢出乘积式
    1 - Π_{i=0..k-1} (n-c-i)/(n-i)，与 1 - C(n-c,k)/C(n,k) 等价。
    n<k 时退化为「至少一次通过」（c>0 → 1.0）并置 degenerate=True 供调用方
    标注（feedback / 证据 JSON 中显示为「退化估计」）；n≤0 → (0.0, True)。
    c 夹取 [0,n]；n==k 且 c>0 时经下方守卫返回 1.0（无偏）。
    """
    c = max(0, min(c, n))
    if n <= 0:
        return 0.0, True
    if n < k:
        return (1.0 if c > 0 else 0.0), True
    if n - c < k:
        # C(n-c,k) = 0：任取 k 个必含通过样本 → 无偏值恰为 1（HumanEval 同款
        # 守卫，同时避免乘积式出现负因子）
        return 1.0, False
    prob_all_miss = 1.0
    for i in range(k):
        prob_all_miss *= (n - c - i) / (n - i)
    return 1.0 - prob_all_miss, False


def pass_cap_k(passes: List[bool]) -> float:
    """pass^k（可靠性下限）：k 次运行是否全部通过（非空且全过 → 1.0，否则 0.0）。"""
    return 1.0 if passes and all(passes) else 0.0


def execute_k(candidate: str, case: G.Case, k: int, run_once: Callable,
              threshold: float = 1.0) -> dict:
    """k 采样聚合（契约函数；run_once 注入使其可独立测试）。

    run_once(candidate, case) -> (score, feedback, invoked)；invoked=None 表示
    证据门禁未启用（文本通道）。invoked=False 的运行计为失败：分子剔除、
    分母保留（skill 未真实触发，不构成有效通过样本，但占用了一次采样）。
    返回 {runs, passes, pass_at_k, pass_at_k_degenerate, pass_cap_k, n, c,
    feedback}；k 路径不在此 round，round 仅发生在证据 JSON 写入处。
    """
    runs, passes = [], []
    for _ in range(k):
        score, feedback, invoked = run_once(candidate, case)
        runs.append({"score": score, "feedback": feedback, "invoked": invoked})
        passes.append(score >= threshold and invoked is not False)
    n = len(passes)
    c = sum(passes)
    pak, degenerate = pass_at_k(n, c, k)
    cap = pass_cap_k(passes)
    if k <= 1:
        # k=1 保持旧语义：主信号 = 单次 F1，feedback 原样透传（零回归锚）
        return {"runs": runs, "passes": passes, "pass_at_k": pak,
                "pass_at_k_degenerate": degenerate, "pass_cap_k": cap,
                "n": n, "c": c, "feedback": runs[0]["feedback"]}
    # k>1：主信号 = pass^k（0/1），feedback 聚合全部运行明细
    pak_txt = f"pass@k={pak:.3f}" + ("（退化估计）" if degenerate else "")
    bits = [f"pass^k={c}/{k}", pak_txt,
            "单次F1=" + ",".join(f"{r['score']:.3f}" for r in runs)]
    if missed := [str(i + 1) for i, r in enumerate(runs) if r["invoked"] is False]:
        bits.append("未触发轮次=[" + ",".join(missed) + "]")
    bits.extend(f"r{i+1}: {r['feedback']}" for i, r in enumerate(runs))
    return {"runs": runs, "passes": passes, "pass_at_k": pak,
            "pass_at_k_degenerate": degenerate, "pass_cap_k": cap,
            "n": n, "c": c, "feedback": "; ".join(bits)}


# ── 评估集加载 ────────────────────────────────────────────────────────────
def load_eval_set(skill: str, eval_dir: Path, cfg: dict,
                  include_manual: bool = False) -> List[G.Case]:
    """遍历 eval_dir 下 case 目录（input/ + expected.md），构建 Case 列表。

    Case.inputs = {input_dir, files: {文件名: 内容}}
    Case.reference = {expected_rules, manual_rules, expected_empty}

    include_manual=True：把「人工补充」规则（manual-rules 规则名，LLM 按 SKILL
    第 3 步可检出）并入 expected_rules——GEPA 评估集专用；badcase_runner 的
    脚本回归语义不变（脚本检不出语义类规则，仍只比对脚本自动检出部分）。
    """
    cases = []
    for case_dir in sorted(p for p in eval_dir.iterdir() if p.is_dir()):
        input_dir = case_dir / "input"
        if not input_dir.is_dir():
            continue
        _, expected_rules, manual_rules = parse_expected(case_dir / "expected.md")
        if include_manual:
            expected_rules = list(dict.fromkeys([*expected_rules, *manual_rules]))
        files = {
            f.name: f.read_text(encoding="utf-8")
            for f in sorted(input_dir.iterdir())
            if f.is_file()
        }
        # 双 Agent：case 目录含 prompts.md 时加载回合素材（无 prompts 零回归，
        # inputs 不含 prompts 键——旧消费方无感）
        prompts, _ = parse_prompts(case_dir / "prompts.md")
        inputs = {"input_dir": str(input_dir), "files": files}
        if prompts:
            inputs["prompts"] = prompts
        cases.append(G.Case(
            id=f"{skill}:{case_dir.name}",
            inputs=inputs,
            reference={"expected_rules": expected_rules,
                       "manual_rules": manual_rules,
                       "expected_empty": not expected_rules},
        ))
    return cases


def split_eval(cases: List[G.Case], cfg: dict) -> Tuple[List[G.Case], List[G.Case]]:
    """按 case 类型分层切 train/holdout（放行/混合型至少各 1 进 holdout）。"""
    intercept = [c for c in cases if not c.reference["expected_empty"]]
    release = [c for c in cases if c.reference["expected_empty"]]
    n_holdout = max(2, int(len(cases) * float(cfg["gepa_holdout_ratio"])))
    rng = random.Random(42)
    rng.shuffle(intercept)
    rng.shuffle(release)
    holdout = release[: max(1, n_holdout // 2)] + intercept[: n_holdout - max(1, n_holdout // 2)]
    holdout_ids = {c.id for c in holdout}
    train = [c for c in cases if c.id not in holdout_ids]
    return train, holdout


# ── execute / reflect / validate（GEPA 可注入回调）────────────────────────
def _build_prompt(candidate: str, files_text: str, history: str,
                  multi_round: bool, evidence_mode: bool, skill_name: str) -> str:
    """构造单轮审查 prompt（双 Agent / 证据门禁各模式的唯一拼装点）。

    零回归锚：非证据、无多轮（evidence_mode=multi_round=False、history 空）
    时与旧版拼装逐字节一致（test_replay.py 有字节相等断言）。
    证据模式不嵌 candidate 文本——改为指令 Read 部署态 SKILL.md（门禁面向
    部署态技能保真度评测，不能直接用于 GEPA 变异候选筛选，见 README）。
    """
    parts: List[str] = []
    if not evidence_mode:
        parts.append(candidate)
    parts.append(f"# 待审查输入\n{files_text}")
    if history:
        parts.append(f"# 对话记录\n{history}")
    if evidence_mode:
        task = (f"先用 Read 工具完整读取 skills/{skill_name}/SKILL.md（已授权），"
                f"严格按其规则与工作流对输入做静态审查，输出审查报告。\n")
    else:
        task = ("按上述 SKILL 的规则与工作流对输入做静态审查，输出审查报告。\n"
                "仅纯文本分析，禁止调用任何工具/脚本/命令（本环境无工具可用）。\n")
    if multi_round:
        task += (f"若需用户决策，输出单独一行 {DECISION_MARKER} <问题> 并停止"
                 f"（不输出报告）；否则输出最终报告。\n")
    task += ("报告末尾必须附加检出清单 JSON（严格单个 JSON，无围栏无其他文字）：\n"
             '{"rules": ["规则名1", "规则名2", ...]}\n'
             "规则名与 SKILL 中的规则命名一致；未检出问题则输出 {\"rules\": []}")
    parts.append(f"# 任务\n{task}")
    return "\n\n".join(parts)


def make_run_once(cfg, call_claude_raw, skill_name: str,
                  call_claude_stream: Optional[Callable] = None) -> Callable:
    """run_once(candidate, case) -> (score, feedback, invoked)。

    通道选择：call_claude_stream 且 replay_evidence 开启 → 证据模式
    （stream-json 事件累积，skill 未真实触发 → 0 分 fail-closed，invoked=False）；
    否则文本模式 call_claude_raw（invoked=None，门禁未启用）。通道异常同样
    fail-closed 计 0 分。

    双 Agent 多轮（replay_dual_agent 且 case 含 prompts）：每轮新 subprocess
    （无状态重放），对话记录以 [助手]/[用户] 文本拼进 prompt；触发
    DECISION_REQUEST 时由 SIM_USER_PROMPT（共用 call_claude_raw 通道）模拟
    用户应答（应答空 → 「按你的建议继续」；素材耗尽 → 确定性兜底不走 LLM）；
    调用预算 2*(len(prompts)+1)（agent 与 sim-user 各计一次），超限取当前输出
    （含 marker → 报告不可解析 → 0 分）。
    """
    evidence_on = bool(cfg.get("replay_evidence", True)) and call_claude_stream is not None
    dual_agent = bool(cfg.get("replay_dual_agent", True))

    def run_once(candidate: str, case: G.Case) -> Tuple[float, str, object]:
        files_text = "\n\n".join(
            f"--- {name} ---\n{content}" for name, content in case.inputs["files"].items())
        prompts = case.inputs.get("prompts") if dual_agent else None
        multi_round = bool(prompts)
        # 调用预算：多轮 = 2*(len(prompts)+1)（每回合 agent+sim-user 各一次，
        # 预留收尾回合）；单轮 = 1。恒 DECISION_REQUEST 的病态循环会被预算截断
        budget = 2 * (len(prompts) + 1) if multi_round else 1
        round_no = 0
        history_lines: List[str] = []
        session_events: List[dict] = []
        out = ""
        while budget > 0:
            budget -= 1
            prompt = _build_prompt(candidate, files_text, "\n".join(history_lines),
                                   multi_round, evidence_on, skill_name)
            try:
                if evidence_on:
                    out, events = call_claude_stream(prompt, cfg)
                    session_events.extend(events)
                else:
                    out = call_claude_raw(prompt, cfg)
            except Exception as e:
                # fail-closed：通道异常即本次运行失败（证据模式 invoked=False）
                return 0.0, f"执行失败: {e}", False if evidence_on else None
            if not multi_round:
                break
            question = _decision_question(out)
            if question is None:
                break  # 最终报告已产出，多轮结束
            history_lines.append(f"[助手] {DECISION_MARKER} {question}")
            if round_no < len(prompts):
                budget -= 1  # sim-user 消耗预算（防病态多问吃满调用）
                try:
                    reply = call_claude_raw(
                        SIM_USER_PROMPT.format(material=prompts[round_no],
                                               question=question), cfg)
                except Exception:
                    reply = ""
                reply = (reply or "").strip() or "按你的建议继续"
            else:
                # 素材耗尽：确定性兜底（不走 LLM），逼被测 Agent 收尾
                reply = "无更多输入，请直接给出最终报告"
            history_lines.append(f"[用户] {reply}")
            round_no += 1
        if evidence_on:
            invoked = skill_invoked_from_events(session_events, skill_name)
            if not invoked:
                return 0.0, "skill 未触发（invoked=false，计为失败）", False
        else:
            invoked = None
        actual_rules, ok = extract_rules_from_report(out)
        if not ok:
            return 0.0, "报告不可解析: 未找到规则清单 JSON（候选可能删掉了输出清单指令）", invoked
        expected = case.reference["expected_rules"]
        tp, missing, unexpected = reconcile(expected, actual_rules)
        score = f1_score(tp, len(expected), len(actual_rules),
                         len(actual_rules) - len(unexpected))
        bits = []
        if missing:
            bits.append("漏拦: " + ", ".join(missing))
        if unexpected:
            bits.append("误拦: " + ", ".join(unexpected))
        if not bits:
            bits.append("全部命中")
        return score, "; ".join(bits), invoked

    return run_once


def make_execute(cfg, call_claude_raw, skill_name: str,
                 call_claude_stream: Optional[Callable] = None):
    """execute(candidate_text, case) -> (score 0-1, feedback)（GEPA 回调契约）。

    k 采样（replay_k，默认 3）：k=1 → run_once 直通（单次 F1 旧语义，零回归）；
    k>1 → 主信号 score = pass^k（0/1），feedback 聚合 pass^k / pass@k / 逐轮
    明细。证据门禁（invoked=False）的运行在 execute_k 内计为失败。cmd_evolve
    既有 3 参调用自动落入 k 路径（cfg 默认 replay_k=3）；stream 通道缺省
    None 时门禁关闭（文本模式）。
    """
    run_once = make_run_once(cfg, call_claude_raw, skill_name, call_claude_stream)
    k = max(1, int(cfg.get("replay_k", 3) or 1))
    threshold = float(cfg.get("replay_pass_threshold", 1.0))

    def execute(candidate_text: str, case: G.Case) -> Tuple[float, str]:
        if k <= 1:
            score, feedback, _invoked = run_once(candidate_text, case)
            return score, feedback
        agg = execute_k(candidate_text, case, k, run_once, threshold=threshold)
        return agg["pass_cap_k"], agg["feedback"]

    return execute


def make_reflect(call_claude_raw, cfg):
    """reflector 通道：输入当前候选与反馈，输出编辑后的完整 SKILL.md（纯文本）。"""

    def reflect(current: str, results, asset_desc: str) -> str:
        lines = []
        for case, score, feedback in results:
            lines.append(f"- case {case.id}: score={score:.3f}，反馈：{feedback}")
        prompt = REPLAY_REFLECTOR_PROMPT.format(current=current, feedback="\n".join(lines))
        return call_claude_raw(prompt, cfg)

    return reflect


def validate_candidate(baseline_len: int):
    """变异候选约束：frontmatter 契约 + 章节锚点 + 长度上限（违约即丢弃）。"""

    def check(text: str) -> bool:
        if not text.startswith("---\n") or "name:" not in text or "description:" not in text:
            return False
        return False if "## " not in text else len(text) <= baseline_len * 1.5

    return check


def write_skill_proposal(cfg, skill: str, baseline: str, best: G.Candidate,
                         baseline_score: float, best_score: float,
                         log: List[dict]) -> Path:
    """holdout 有统计意义改善时，产出 prompt_evolution 型 pending 提案（人工采纳）。"""
    from evo_config import base_paths
    paths = base_paths(cfg)
    paths["pending"].mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    pid = f"{now.strftime('%Y%m%d-%H%M%S')}-gepa-replay"
    body = (
        f"---\nid: {pid}\nstatus: pending\ntype: prompt_evolution\n"
        f"source_agent: gepa\nsource_session: -\nsource_path: -\n"
        f"created: {now.isoformat(timespec='seconds')}\nlessons: 0\n---\n\n"
        f"# SKILL.md 进化提案 {pid}（replay 评估集）\n\n"
        f"> 谱系：{best.id}（parent={best.parent}, gen={best.gen}）· "
        f"holdout 分数：baseline {baseline_score:.3f} → evolved {best_score:.3f}\n\n"
        f"## 采纳方式\n\n本提案不走 `apply`（apply 仅支持 markdown 追加）。人工审阅下方\n"
        f"新 SKILL.md 后，手动替换 `skills/{skill}/SKILL.md` 全文。\n\n"
        f"## 新 SKILL.md\n\n```text\n{best.text}\n```\n\n"
        f"## 迭代日志（前 10 条）\n\n```json\n"
        f"{json.dumps(log[1:11], ensure_ascii=False, indent=1)}\n```\n")
    path = paths["pending"] / f"{pid}.md"
    path.write_text(body, encoding="utf-8")
    return path


# ── 门禁：全盘拒绝控制候选对照（护栏 4，对齐铁律 5）───────────────────────
def control_gate(holdout: List[G.Case]) -> float:
    """「全盘拒绝」报告的 holdout 平均 F1（确定性，零 LLM）。

    REJECT_ALL_TEXT 的 JSON 清单即 LLM 按「无条件报告违规」指令输出的报告
    解析结果；对 holdout 逐 case 对账打分。必须 < baseline F1，否则打分器
    存在 gaming 洞，拒绝进入 GEPA。
    """
    reject_rules, _ = extract_rules_from_report(REJECT_ALL_TEXT)
    scores = []
    for case in holdout:
        expected = case.reference["expected_rules"]
        tp, _, unexpected = reconcile(expected, reject_rules)
        scores.append(f1_score(tp, len(expected), len(reject_rules),
                            len(reject_rules) - len(unexpected)))
    return sum(scores) / len(scores) if scores else float("-inf")


def script_baseline_f1(cfg, skill_name: str, cases: List[G.Case]) -> Tuple[float, List[dict]]:
    """dry-run 用确定性脚本直跑评估集：验证打分器可运行 + 给出脚本基线 F1。

    返回 (平均 F1, 每 case 明细)。零 LLM——脚本输出即 actual_rules（完美执行参照）。
    """
    reg = scorer_registry()
    if skill_name not in reg:
        raise SystemExit(f"评估集打分器未注册：{skill_name}（注册表见 evo_replay.SCORER_REGISTRY）")
    details = []
    total_f1 = 0.0
    available = reg[skill_name]["scripts"]
    for case in cases:
        input_dir = Path(case.inputs["input_dir"])
        # expected.md 位于 case 目录（input/ 的父级）——check: 声明决定跑哪个
        # 注册脚本；未声明 → 全跑（兼容旧评估集）。@date 2026-09-20 修正：
        # 原先拼到 input/expected.md（不存在）使选择恒失效、全脚本误跑，
        # 非声明脚本的 exit 2 制造重复错误行污染 dry-run 明细。
        check_name = parse_expected(input_dir.parent / "expected.md")[0]
        scripts = [s for s in available if check_name is None or s.name == check_name]
        if not scripts:
            details.append({"case": case.id,
                            "error": f"check 脚本未注册或不可用: {check_name}"})
            continue
        actual_rules = []
        for script in scripts:
            try:
                r = subprocess.run(
                    ["python3", str(script), str(input_dir), "--format", "json"],
                    capture_output=True, text=True, timeout=30)
                # 检查器文档化退出码：0=通过、1=有强制问题（badcase 正常态，
                # 解析 stdout 记规则）、2=运行错误。仅 2 及以上记为评分器错误。
                if r.returncode not in (0, 1):
                    details.append({"case": case.id,
                                    "error": f"{script.name}: exit {r.returncode}"})
                    continue
                data = json.loads(r.stdout or "[]")
                for f in data:
                    actual_rules.extend(i.get("rule", "") for i in f.get("issues", []))
            except Exception as e:
                details.append({"case": case.id, "error": f"{script.name}: {e}"})
                continue
        actual_rules = list(dict.fromkeys(r for r in actual_rules if r))
        # baseline 只对「脚本可及」规则求 F1：人工补充规则（拼音/语义类）脚本
        # 本就检不出，混入会让基线失真（无从区分脚本缺陷 vs 规则本质）。
        manual = set(case.reference.get("manual_rules", []))
        expected = [e for e in case.reference["expected_rules"] if e not in manual]
        tp, missing, unexpected = reconcile(expected, actual_rules)
        score = f1_score(tp, len(expected), len(actual_rules),
                         len(actual_rules) - len(unexpected))
        total_f1 += score
        details.append({"case": case.id, "expected_empty": case.reference["expected_empty"],
                        "expected": expected, "actual": actual_rules,
                        "manual_rules": case.reference.get("manual_rules", []),
                        "score": round(score, 4)})
    return (total_f1 / len(cases) if cases else 0.0), details


# ── 调用证据：stream-json 工具调用流（Comet 机制 ③，@date 2026-09-20）──────
def _decision_question(out: str):
    """从输出提取 DECISION_REQUEST 问题；无 marker → None（已是最终报告）。

    marker 同一行其后的文本为问题；marker 存在但无可用文本时截断输出前
    200 字符兜底（决策回合继续，避免卡死在含糊输出上）。
    """
    for line in out.splitlines():
        if DECISION_MARKER in line:
            if q := line.split(DECISION_MARKER, 1)[1].strip():
                return q
    return out[:200] if DECISION_MARKER in out else None


def skill_invoked_from_events(events: List[dict], skill_name: str) -> bool:
    """stream-json 工具调用事件流中技能是否被真实触发。

    判据（blob = json.dumps(tool input)，子串匹配容忍路径写法差异）：
    - Read/Bash 命中 skills/<skill>/SKILL.md 或 skills/<skill>/scripts/ 前缀
    - Skill 工具调用（技能机制）命中技能名
    """
    for ev in events:
        name = ev.get("name", "")
        blob = json.dumps(ev.get("input", {}), ensure_ascii=False)
        if name in ("Read", "Bash") and (
                f"skills/{skill_name}/SKILL.md" in blob
                or f"skills/{skill_name}/scripts/" in blob):
            return True
        if name == "Skill" and skill_name in blob:
            return True
    return False


def call_claude_stream(prompt: str, cfg: dict) -> Tuple[str, List[dict]]:
    """headless claude -p（stream-json）→ (最终文本, 工具调用事件列表)。

    复刻 evo.py call_claude_raw 的防递归模式（env 去 CLAUDECODE + 子进程
    标记 AR_SKILL_EVO_CHILD=1 + 空 hooks settings）。只预授权 Read：防止
    检查脚本泄漏答案（Bash 事件照常收集作证据，但不主动授权）。
    解析规则：assistant 消息 content 中 tool_use 块收集 (name, input)、
    text 块拼接；result 消息的 result 字段优先作最终文本（无则回退
    assistant 文本拼接）。prompt 经 stdin 传入（同 evo.py：长文本走 argv
    会被 CLI 当选项解析）。
    """
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)          # 去 CC 注入面
    env["AR_SKILL_EVO_CHILD"] = "1"      # 二次保险：即使 hooks 未禁，hook 脚本自会退出
    proc = subprocess.run(
        [str(cfg["claude_bin"]), "-p", "--settings", '{"hooks":{}}',
         "--max-turns", "12", "--output-format", "stream-json", "--verbose",
         "--allowedTools", "Read"],
        capture_output=True, text=True, input=prompt,
        timeout=int(cfg["claude_timeout"]), env=env)
    events: List[dict] = []
    texts: List[str] = []
    final_text = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # 非 JSON 行（CLI 横幅等）跳过
        mtype = msg.get("type")
        if mtype == "assistant":
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    events.append({"name": block.get("name", ""),
                                   "input": block.get("input", {})})
                elif block.get("type") == "text":
                    texts.append(block.get("text", ""))
        elif mtype == "result":
            final_text = msg.get("result") or ""
    return final_text or "".join(texts), events


# 派生产物排除集：与 A 路 release_guard 同名常量保持一致（钉测试
# test_skill_content_hash_matches_release_guard_contract 守护两路相等）；
# .coverage/.ruff_cache 系 3803b82 实测 push 拦截补录。
_DERIVED_PARTS = frozenset({".pytest_cache", "__pycache__", ".DS_Store",
                            ".coverage", ".ruff_cache"})
_DERIVED_SUFFIXES = (".pyc",)


def _is_derived(rel: Path) -> bool:
    """派生产物判定（与 release_guard._is_derived 契约一致）：路径任一段命中
    缓存/系统目录名，或 *.pyc 后缀。rel 须为 scripts_dir 内相对路径——枚举
    根之上的祖先段不参与匹配（检出位置不得污染判定）。
    """
    return (any(part in _DERIVED_PARTS for part in rel.parts)
            or rel.suffix in _DERIVED_SUFFIXES)


def skill_content_hash(skill: str, root: Optional[Path] = None) -> str:
    """技能内容指纹：逐文件摘要清单的 sha256（release_guard 契约一字不差）。

    契约（真相源 = scripts/release_guard.compute_content_hash，本函数为 B 路
    对齐实现；两路不漂移由钉测试守护：
    test_skill_content_hash_matches_release_guard_contract。
    #247 r2 2026-09-26：旧「字节直拼」算法把 __pycache__ 等派生产物吞入
    指纹，随本地测试生灭漂移，实证不可复现）：
    - manifest 条目 = "{文件内容 sha256 hex}  {skill 内相对 posix 路径}\\n"，
      按仓库根相对 posix 路径字典序逐条拼接后整体 sha256——路径与边界参与
      哈希：文件改名/增删/跨文件内容重排（"ab"+"c" vs "a"+"bc"）均改变 hash；
      内容读取用 read_text('utf-8').encode('utf-8')（与契约表达式逐字一致）
    - 文件面 = SKILL.md + scripts/**，排除派生产物（_is_derived）——本地
      测试生灭物不得扰动指纹（.coverage/.ruff_cache 同排除，3803b82 实测
      push 拦截补录）
    - SKILL.md 缺失 → FileNotFoundError（技能内容不完整不应静默给出可比对
      指纹）

    独立实现而非 import：技能脚本需自包含分发（消费仓安装面无仓库根
    scripts/）。跨实现一致性由钉测试守护（漂移即红）。
    """
    root = (root or _repo_root()).resolve()
    base = root / "skills" / skill
    skill_md = base / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md 不存在：{skill_md}")
    files = [skill_md]
    scripts_dir = base / "scripts"
    if scripts_dir.is_dir():
        files.extend(p for p in scripts_dir.rglob("*")
                     if p.is_file() and not _is_derived(p.relative_to(scripts_dir)))
    files.sort(key=lambda p: p.relative_to(root).as_posix())
    manifest = "".join(
        f"{hashlib.sha256(p.read_text(encoding='utf-8').encode('utf-8')).hexdigest()}"
        f"  {p.relative_to(base).as_posix()}\n"
        for p in files)
    return "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def write_replay_evidence(skill: str, payload: dict, root: Optional[Path] = None) -> Path:
    """写 replay 证据 JSON（schema=replay-evidence/1，跨路契约字段一字不改）。

    路径：<root>/skills/skill-evo/artifacts/replay-evidence/<skill>.json；
    generated_at 由本函数加盖（UTC 秒级 ISO8601）；pass_at_k / pass_cap_k
    round 4；cases 为整数计数（任务书契约样例 "cases": 12，字段不得增删，
    逐 case 明细走调用方 stdout）。artifacts/ 默认被 .gitignore 忽略，唯
    replay-evidence/ 豁免：仅登记入 release_guard EVIDENCE_ENROLLED 的真实
    证据（stream-json）可入库，禁止 git add 未登记证据。dry-run 载荷拒绝
    覆写既有 stream-json 证据（或无法核验的既有文件）：ValueError 上抛，
    由调用方转退出码（fail-closed，防冒烟抹掉 k×cases LLM 成本）。
    """
    root = (root or _repo_root()).resolve()
    out_dir = root / "skills" / "skill-evo" / "artifacts" / "replay-evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "replay-evidence/1",
        "skill": skill,
        "content_hash": payload["content_hash"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": payload["k"],
        "pass_at_k": round(float(payload["pass_at_k"]), 4),
        "pass_cap_k": round(float(payload["pass_cap_k"]), 4),
        "invocation": {"skill_invoked": bool(payload["invocation"]["skill_invoked"]),
                       "evidence": payload["invocation"]["evidence"]},
        "cases": int(payload["cases"]),
    }
    path = out_dir / f"{skill}.json"
    # fail-closed 守卫：dry-run 冒烟不得覆写真实证据（未提交的 stream-json
    # 被抹掉不可恢复——k×cases LLM 成本作废）；既有文件无法核验时同样拒绝。
    if path.exists() and payload["invocation"]["evidence"] == "dry-run":
        protected = True  # 无法核验 → 按真实证据对待（宁可拒绝冒烟）
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            protected = (existing.get("invocation", {})
                         .get("evidence") == "stream-json")
        except (OSError, ValueError):
            pass
        if protected:
            raise ValueError(
                f"拒绝覆写：{path} 已存在真实证据（stream-json 或无法核验），"
                f"dry-run 冒烟不得破坏之；如确需重跑 dry-run，请先移除/备份"
                f"该文件（已提交证据可 git checkout 恢复）")
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


# ── dry-run 证据冒烟入口（零 LLM，CI 可跑）──────────────────────────────
def cmd_evidence_dry_run(skill: str, cfg: dict, out_root: Optional[Path] = None) -> int:
    """证据通道 dry-run 冒烟：不触 LLM，直接执行技能注册检查脚本产出指标。

    每 case 一条运行：脚本明细 score ≥ replay_pass_threshold 记一次通过
    （error 行无 score 记失败）；n=1 < k，pass@k 退化为 0/1；聚合
    pass@k / pass^k = 逐 case 均值（单样本下两者同值）。skill_invoked=True、
    evidence="dry-run"：dry-run 直接执行技能注册脚本，技能内容必然参与
    （区别于 stream-json 实测通道）。返回退出码（评估集不足 → 1）。
    """
    # 默认评估集 = badcase/（拦截型）+ eval/（放行/混合型），对齐 evo.py cmd_evolve
    skill_dir = _repo_root() / "skills" / skill
    cases = []
    for sub in ("badcase", "eval"):
        d = skill_dir / sub
        if d.is_dir():
            cases += load_eval_set(skill, d, cfg, include_manual=True)
    if len(cases) < int(cfg["replay_min_cases"]):
        print(f"评估集不足：{len(cases)} < replay_min_cases={cfg['replay_min_cases']}")
        return 1
    k = max(1, int(cfg.get("replay_k", 3) or 1))
    threshold = float(cfg.get("replay_pass_threshold", 1.0))
    baseline, details = script_baseline_f1(cfg, skill, cases)
    print(f"评估集：{len(cases)} cases（含人工补充规则）")
    print(f"脚本基线（完美执行参照）F1 = {baseline:.3f}")
    case_rows = []
    pak_sum = cap_sum = 0.0
    for d in details:
        passed = d.get("score", 0.0) >= threshold
        pak, _deg = pass_at_k(1, 1 if passed else 0, k)
        cap = 1.0 if passed else 0.0
        pak_sum += pak
        cap_sum += cap
        case_rows.append({"case": d["case"], "passed": passed,
                          "score": d.get("score"), "error": d.get("error")})
    n_cases = len(case_rows)
    avg_pak = pak_sum / n_cases if n_cases else 0.0
    avg_cap = cap_sum / n_cases if n_cases else 0.0
    # content_hash 用真实仓根（指纹必须指向真实技能内容），仅写出重定向
    content_hash = skill_content_hash(skill)
    # 契约：cases 为整数计数（任务书样例 "cases": 12，C 路消费方依赖，
    # 字段不得增删改名）——逐 case 明细走 stdout，不入 JSON。@date 2026-09-20
    try:
        path = write_replay_evidence(skill, {
            "content_hash": content_hash, "k": k,
            "pass_at_k": avg_pak, "pass_cap_k": avg_cap,
            "invocation": {"skill_invoked": True, "evidence": "dry-run"},
            "cases": n_cases,
        }, root=out_root)
    except ValueError as e:
        print(f"evidence 未写入：{e}")
        return 1
    print(f"pass@k = {avg_pak:.4f}（dry-run 单样本退化估计）")
    print(f"pass^k = {avg_cap:.4f}（逐 case 通过率，单样本下与 pass@k 同值）")
    print(f"evidence 已写入：{path}（cases={n_cases}）")
    for row in case_rows:
        if not row["passed"]:
            print(f"  未过：{row['case']} score={row['score']} error={row['error']}")
    return 0


def cmd_evidence_llm(skill: str, cfg: dict, call_claude_raw: Callable,
                     out_root: Optional[Path] = None,
                     eval_dirs: Optional[list] = None) -> int:
    """证据通道完整运行（LLM 实测）：部署态技能保真度评测 + evidence 写入。

    与 cmd_evidence_dry_run 同构（评估集 badcase+eval / include_manual / 阈值
    口径一致），区别仅在通道：每 case 经 make_run_once 走 call_claude_stream
    实测，工具调用流未出现指向本 skill 的事件即计失败（fail-closed）。GEPA
    变异筛选不适用本通道（证据模式只测部署态，见 _build_prompt）。调用方：
    evo.py cmd_evolve --skill 在 GEPA 结束后接线（replay_evidence=False 跳过），
    经 eval_dirs 复用 --eval 解析出的评估集，保证证据口径与 GEPA 一致。
    invocation.evidence="stream-json"；skill_invoked 仅当全部采样真实触发。
    返回退出码（评估集不足 / 证据通道被 kill switch 关闭 → 1）。
    """
    if not bool(cfg.get("replay_evidence", True)):
        print("replay_evidence=False：证据通道关闭，拒绝产出 evidence（fail-closed）")
        return 1
    # 默认评估集 = badcase/（拦截型）+ eval/（放行/混合型），对齐 cmd_evidence_dry_run
    skill_dir = _repo_root() / "skills" / skill
    cases = []
    if eval_dirs is None:
        eval_dirs = [skill_dir / sub for sub in ("badcase", "eval")
                     if (skill_dir / sub).is_dir()]
    for d in eval_dirs:
        cases += load_eval_set(skill, d, cfg, include_manual=True)
    if len(cases) < int(cfg["replay_min_cases"]):
        print(f"评估集不足：{len(cases)} < replay_min_cases={cfg['replay_min_cases']}")
        return 1
    k = max(1, int(cfg.get("replay_k", 3) or 1))
    threshold = float(cfg.get("replay_pass_threshold", 1.0))
    print(f"评估集：{len(cases)} cases（含人工补充规则）；证据实测 k={k}，"
          f"调用下限 {k * len(cases)} 次（多轮 case 上浮 2*(回合+1)）")
    baseline, _details = script_baseline_f1(cfg, skill, cases)
    print(f"脚本基线（完美执行参照）F1 = {baseline:.3f}")
    deployed = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    run_once = make_run_once(cfg, call_claude_raw, skill, call_claude_stream)
    pak_sum = cap_sum = 0.0
    invoked_flags: List[bool] = []
    for case in cases:
        agg = execute_k(deployed, case, k, run_once, threshold=threshold)
        pak_sum += agg["pass_at_k"]
        cap_sum += agg["pass_cap_k"]
        invoked_flags.extend(r["invoked"] is not False for r in agg["runs"])
        mark = "✅" if agg["pass_cap_k"] == 1.0 else "未过"
        print(f"  {case.id}: pass^k={agg['c']}/{k} {mark} | {agg['feedback']}")
    n_cases = len(cases)
    avg_pak = pak_sum / n_cases
    avg_cap = cap_sum / n_cases
    # content_hash 用真实仓根（指纹必须指向真实技能内容），仅写出重定向
    content_hash = skill_content_hash(skill)
    path = write_replay_evidence(skill, {
        "content_hash": content_hash, "k": k,
        "pass_at_k": avg_pak, "pass_cap_k": avg_cap,
        "invocation": {"skill_invoked": bool(invoked_flags) and all(invoked_flags),
                       "evidence": "stream-json"},
        "cases": n_cases,
    }, root=out_root)
    print(f"pass@k = {avg_pak:.4f} / pass^k = {avg_cap:.4f}"
          f"（skill_invoked={bool(invoked_flags) and all(invoked_flags)}）")
    print(f"evidence 已写入：{path}（cases={n_cases}）")
    return 0


if __name__ == "__main__":
    # 冒烟入口：python3 evo_replay.py <skill> —— dry-run 证据通道（零 LLM），
    # 产出 artifacts/replay-evidence/<skill>.json。完整 LLM 运行的 evidence
    # 由 evo.py cmd_evolve --skill 在 GEPA 结束后经 cmd_evidence_llm 接线
    # stream 通道产出（部署态实测）。
    # 若目标已存在真实证据（stream-json 或无法核验）则拒绝覆写（退出码 1）。
    import sys
    from evo_config import load_config
    if len(sys.argv) != 2:
        print("用法: python3 evo_replay.py <skill>")
        sys.exit(2)
    sys.exit(cmd_evidence_dry_run(sys.argv[1], load_config()))
