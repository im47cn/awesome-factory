"""evo_replay 单测：评估集加载、报告解析器、F1 对账、门禁、CLI 链路（LLM 全 mock）。"""
import types
from pathlib import Path

import evo_gepa as G
import evo_replay as R


def _case(cid="c1", expected=None, files=None):
    return G.Case(
        id=cid,
        inputs={"input_dir": "/tmp/x", "files": files or {"a.sql": "CREATE TABLE t (id int);"}},
        reference={"expected_rules": expected or [], "manual_rules": [],
                   "expected_empty": not expected})


# ── 报告解析器（确定性，零 LLM）───────────────────────────────────────────
def test_extract_rules_from_report_happy():
    rules, ok = R.extract_rules_from_report('审查报告…\n{"rules": ["禁用类型", "表注释缺失"]}')
    assert ok and rules == ["禁用类型", "表注释缺失"]


def test_extract_rules_from_report_no_list_is_unparsable():
    # 候选删掉「输出清单」指令 → 报告无 JSON → 不可解析（0 分前置）
    assert R.extract_rules_from_report("五段式报告，无清单") == ([], False)
    assert R.extract_rules_from_report("") == ([], False)
    assert R.extract_rules_from_report('{"rules": broken') == ([], False)


def test_extract_rules_empty_list_is_parsable():
    # 合法空清单 = 放行（该拦的没拦 → recall 惩罚留给对账层）
    rules, ok = R.extract_rules_from_report('{"rules": []}')
    assert ok and rules == []


def test_extract_rules_filters_non_string_and_bad_json():
    # 非 str 规则剔除；json 解析异常 → 不可解析
    rules, ok = R.extract_rules_from_report('{"rules": ["禁用类型", 42, "", null]}')
    assert ok and rules == ["禁用类型"]
    assert R.extract_rules_from_report('{"rules": [1,2,}') == ([], False)
    # 正则命中但 json.loads 抛异常 → except 分支（[nope] 无引号非法 JSON）
    assert R.extract_rules_from_report('{"rules": [nope]}') == ([], False)


# ── F1 对账（双维对称惩罚）────────────────────────────────────────────────
def test_f1_recall_and_precision_dimensions():
    # 拦截型：漏拦一半 → recall 0.5（tp=1；2 actual 中 1 条命中 → n_hit_actual=1）
    assert R.f1_score(1, 2, 2, 1) == 2 * (0.5 * 0.5) / (0.5 + 0.5) == 0.5
    # 放行型：expected 空 + actual 非空 → precision 0 → F1 0（全盘拒绝被对称惩罚）
    assert R.f1_score(tp=0, n_expected=0, n_actual=3, n_hit_actual=0) == 0.0
    # 完美：2 actual 全命中 → n_hit_actual=2
    assert R.f1_score(tp=2, n_expected=2, n_actual=2, n_hit_actual=2) == 1.0
    # 双零 = 空 expected + 空 actual = 干净放行 → F1 1（与实现语义一致）
    assert R.f1_score(tp=0, n_expected=0, n_actual=0, n_hit_actual=0) == 1.0


def test_reconcile_missing_and_unexpected():
    tp, missing, unexpected = R.reconcile(["表注释缺失", "禁用类型"], ["表注释缺失", "全角字符"])
    assert tp == 1
    assert missing == ["禁用类型"]
    assert unexpected == ["全角字符"]


def test_reconcile_empty_expected_any_actual_is_unexpected():
    # 放行 case：任何检出都算误拦（precision 侧核心）
    tp, missing, unexpected = R.reconcile([], ["禁用类型"])
    assert tp == 0 and missing == [] and unexpected == ["禁用类型"]


# ── 评估集加载 ─────────────────────────────────────────────────────────────
def test_load_eval_set_parses_expected_and_empty(tmp_path):
    (tmp_path / "001-bad" / "input").mkdir(parents=True)
    (tmp_path / "001-bad" / "input" / "t.sql").write_text("x", encoding="utf-8")
    (tmp_path / "001-bad" / "expected.md").write_text(
        "# c\n\n## 预期检查输出\n\n- 脚本自动检出：禁用类型、表注释缺失\n- 人工补充：语义\n",
        encoding="utf-8")
    (tmp_path / "007-clean" / "input").mkdir(parents=True)
    (tmp_path / "007-clean" / "input" / "c.sql").write_text("y", encoding="utf-8")
    (tmp_path / "007-clean" / "expected.md").write_text(
        "# c\n\n## 预期检查输出\n\n（本 case 无脚本自动检出项）\n", encoding="utf-8")
    (tmp_path / "002-mix" / "input").mkdir(parents=True)
    (tmp_path / "002-mix" / "input" / "t.sql").write_text("z", encoding="utf-8")
    (tmp_path / "002-mix" / "expected.md").write_text(
        "# c\n\n## 预期检查输出\n\n- 脚本自动检出：禁用类型\n- 独立规则行\n", encoding="utf-8")

    cases = R.load_eval_set("s", tmp_path, {})
    by_id = {c.id: c for c in cases}
    assert by_id["s:001-bad"].reference["expected_rules"] == ["禁用类型", "表注释缺失"]
    assert by_id["s:002-mix"].reference["expected_rules"] == ["禁用类型", "独立规则行"]
    assert by_id["s:001-bad"].reference["expected_empty"] is False
    assert by_id["s:007-clean"].reference["expected_empty"] is True
    assert by_id["s:001-bad"].inputs["files"] == {"t.sql": "x"}


def test_load_eval_set_skips_dir_without_input(tmp_path):
    (tmp_path / "no-input").mkdir()          # 无 input/ → 跳过
    (tmp_path / "ok" / "input").mkdir(parents=True)
    (tmp_path / "ok" / "input" / "f.sql").write_text("x", encoding="utf-8")
    cases = R.load_eval_set("s", tmp_path, {})
    assert [c.id for c in cases] == ["s:ok"]


def test_parse_expected_manual_rule_ids_vs_desc(tmp_path):
    """「人工补充规则：」行 → manual_rules 精确规则 ID；「人工补充：」描述行
    不进 manual_rules（不参与对账）。"""
    f = tmp_path / "e.md"
    f.write_text(
        "# c\n\n## 预期检查输出\n\n"
        "- 脚本自动检出：禁用类型\n"
        "- 人工补充：命名语义（拼音、泛化词）\n"
        "- 人工补充规则：拼音、泛化词、复数\n",
        encoding="utf-8")
    script, rules, manual = R.parse_expected(f)
    assert rules == ["禁用类型"]
    assert manual == ["拼音", "泛化词", "复数"]   # 描述行不混入


def test_load_eval_set_include_manual_merges(tmp_path):
    """include_manual=True：manual_rules 并入 expected_rules（GEPA 专用），
    放行型（无人工规则）expected_empty 不受影响。"""
    for name, content in {
        "001-bad": "- 脚本自动检出：禁用类型\n- 人工补充规则：拼音、泛化词",
        "007-clean": "（无脚本自动检出项）",
    }.items():
        (tmp_path / name / "input").mkdir(parents=True)
        (tmp_path / name / "input" / "f.sql").write_text("x", encoding="utf-8")
        (tmp_path / name / "expected.md").write_text(
            f"# c\n\n## 预期检查输出\n\n{content}\n", encoding="utf-8")
    by_id = {c.id: c for c in R.load_eval_set("s", tmp_path, {}, include_manual=True)}
    assert by_id["s:001-bad"].reference["expected_rules"] == ["禁用类型", "拼音", "泛化词"]
    assert by_id["s:001-bad"].reference["expected_empty"] is False
    assert by_id["s:007-clean"].reference["expected_rules"] == []
    assert by_id["s:007-clean"].reference["expected_empty"] is True


def test_rule_matches_anyof_alias(tmp_path):
    """any-of 别名：| 分隔任一 token 命中即检出；首 token 规范名用于 missing 展示。"""
    assert R._rule_matches("字段与注释对应|注释含义对应", ["字段名与注释含义对应"])
    # 否定插入：短别名命中「字段与注释不对应」？——「注释含义对应」不中，规范名不中，
    # 但别名「对应」类需要真实现（此处验证规范名优先不误报）
    assert not R._rule_matches("字段与注释对应|注释含义对应", ["字段与注释不对应"])
    # 无别名规则兼容（单 token 行为不变）
    assert R._rule_matches("禁用类型", ["使用禁用类型 text"])
    assert not R._rule_matches("禁用类型", ["注释缺失"])
    # 空 token（如「规范名|」）不崩
    assert R._rule_matches("复数形式|", ["表名使用复数形式"])


def test_reconcile_missing_shows_canonical_name():
    """reconcile：别名规则 missing 只显示规范名（reflector 反馈不暴露别名串）。"""
    tp, missing, unexpected = R.reconcile(
        ["表名主体|核心主体", "禁用类型"], ["使用禁用类型 bigint"])
    assert tp == 1                      # 禁用类型命中
    assert missing == ["表名主体"]       # 规范名，非「表名主体|核心主体」
    assert unexpected == []
    # 别名命中 → 不 missing
    tp2, missing2, _ = R.reconcile(
        ["表名主体|核心主体"], ["表名未围绕核心主体"])
    assert tp2 == 1 and missing2 == []


def test_reconcile_unexpected_respects_alias():
    """unexpected 方向与 matched 对称：actual 命中任一 expected 别名行即非 unexpected。

    回归：参数顺序曾颠倒为 _rule_matches(a, expected_rules)——别名 token
    （「核心主体」等非首 token）对 actual 的匹配在反参数下失配，别名命中项被
    误判 unexpected → precision 双罚（005 冒烟 0.635 → 修复后 0.833）。
    """
    tp, missing, unexpected = R.reconcile(
        ["表名主体|核心主体"], ["表名未围绕核心主体"])
    assert tp == 1 and missing == [] and unexpected == []
    # 完全无关的 actual 仍进 unexpected
    tp2, missing2, unexpected2 = R.reconcile(
        ["表名主体|核心主体"], ["表注释缺失"])
    assert tp2 == 0 and missing2 == ["表名主体"] and unexpected2 == ["表注释缺失"]


def test_reconcile_merged_hit_not_unexpected():
    """合并检出（一条 actual 命中两条别名 expected）不产生 unexpected。

    006 冒烟实证：「逻辑删除字段不规范」同时命中「逻辑删除字段名」「逻辑删除
    字段注释」→ tp=2 但该 actual 非 unexpected（precision 不涨、recall 如实涨）。
    """
    tp, missing, unexpected = R.reconcile(
        ["逻辑删除字段名|逻辑删除字段不规范", "逻辑删除字段注释|逻辑删除字段不规范"],
        ["逻辑删除字段不规范"])
    assert tp == 2 and missing == [] and unexpected == []


def test_f1_score_merged_hit_bounded():
    """一条 actual 命中多条 expected（合并检出）→ precision 用命中 actual 数，F1 ≤ 1。

    子串匹配下「表名使用拼音和泛化词」同时命中 [拼音, 泛化词] → tp=2 但 actual=1；
    precision 若直接 tp/n_actual 会 2.0 → F1=1.33 越界（score 契约 [0,1]，且合并
    报告可被 gaming 抬高 precision）。命中 actual 数 = len(actual)-len(unexpected)。
    """
    # 合并检出：单条 actual 含两个 expected 子串 → tp=2, unexpected=0, 命中 actual=1
    tp, _, unexpected = R.reconcile(["拼音", "泛化词"], ["表名使用拼音和泛化词"])
    assert tp == 2 and unexpected == []
    s = R.f1_score(tp, 2, 1, 1 - len(unexpected))
    assert s == 1.0                      # 全检出，F1=1 不越界
    # 全 miss：命中 actual=0 → precision=0 → F1=0
    tp2, _, unexpected2 = R.reconcile(["拼音"], ["无关评论"])
    assert R.f1_score(tp2, 1, 1, 1 - len(unexpected2)) == 0.0
    # 混合：1 命中 + 1 无关 → precision=(2-1)/2=0.5
    tp3, _, unexpected3 = R.reconcile(["拼音", "泛化词"], ["表名使用拼音", "无关"])
    assert abs(R.f1_score(tp3, 2, 2, 2 - len(unexpected3)) - 0.5) < 1e-9


def test_parse_expected_missing_file_and_head_fallback(tmp_path):
    assert R.parse_expected(tmp_path / "absent.md") == (None, [], [])
    # 无「## 预期检查输出」段 → head 兜底解析；check_script 行提取
    f = tmp_path / "e.md"
    f.write_text("check: scripts/ddl_check.py\n\n- 禁用类型\n- 表注释缺失\n", encoding="utf-8")
    script, rules, manual = R.parse_expected(f)
    assert script == "scripts/ddl_check.py"
    assert rules == ["禁用类型", "表注释缺失"] and manual == []
    # head 分支内：以 # 开头行跳过（非规则）
    f2 = tmp_path / "e2.md"
    f2.write_text("# 标题\n\n- 规则A\n- 规则B\n", encoding="utf-8")
    assert R.parse_expected(f2)[1] == ["规则A", "规则B"]
    # 列表项内嵌 # 注释 → 跳过（elif not startswith("#") 分支）
    f3 = tmp_path / "e3.md"
    f3.write_text("- # 内部注释\n- 规则C\n", encoding="utf-8")
    assert R.parse_expected(f3)[1] == ["规则C"]


# ── execute（候选 → 报告 → 解析 → 对账）──────────────────────────────────
def test_execute_scores_and_feedback(monkeypatch):
    seen = {}

    def fake_claude_raw(prompt, cfg):
        seen["prompt"] = prompt
        return '报告…\n{"rules": ["禁用类型"]}'

    case = _case(expected=["禁用类型", "表注释缺失"])
    execute = R.make_execute({"replay_k": 1}, fake_claude_raw, "s")
    score, fb = execute("候选 SKILL.md", case)
    assert score == 2 * 0.5 * 1 / 1.5        # recall=1/2, precision=1/1 → F1=2*0.5*1/1.5
    assert "漏拦: 表注释缺失" in fb
    assert '{"rules"' in seen["prompt"]       # 执行 prompt 强制输出清单契约


def test_execute_full_hit_and_unexpected(monkeypatch):
    def fake(prompt, cfg):
        return '{"rules": ["禁用类型", "全角字符"]}'
    execute = R.make_execute({"replay_k": 1}, fake, "s")
    score, fb = execute("x", _case(expected=["禁用类型"]))
    assert score == 2 * 1.0 * 0.5 / 1.5       # recall=1/1, precision=1/2 → 0.667
    assert "误拦: 全角字符" in fb

    def fake_clean(prompt, cfg):
        return '{"rules": ["禁用类型"]}'
    execute2 = R.make_execute({"replay_k": 1}, fake_clean, "s")
    score2, fb2 = execute2("x", _case(expected=["禁用类型"]))
    assert score2 == 1.0 and "全部命中" in fb2


def test_execute_unparsable_report_scores_zero(monkeypatch):
    def fake_claude_raw(prompt, cfg):
        return "报告没有清单 JSON"
    execute = R.make_execute({}, fake_claude_raw, "s")
    score, fb = execute("x", _case(expected=["禁用类型"]))
    assert score == 0.0 and "报告不可解析" in fb


def test_execute_llm_failure_scores_zero(monkeypatch):
    def boom(prompt, cfg):
        raise RuntimeError("claude 超时")
    execute = R.make_execute({}, boom, "s")
    score, fb = execute("x", _case(expected=["禁用类型"]))
    assert score == 0.0 and "执行失败" in fb


# ── make_reflect（reflector 通道）──────────────────────────────────────────
def test_make_reflect_builds_prompt_with_feedback(monkeypatch):
    seen = {}

    def fake(prompt, cfg):
        seen["p"] = prompt
        return "编辑后的 SKILL.md"
    reflect = R.make_reflect(fake, {})
    out = reflect("当前 SKILL", [(_case(cid="c1", expected=["禁用类型"]), 0.5, "漏拦: 表注释缺失")], "x")
    assert out == "编辑后的 SKILL.md"
    assert "score=0.500" in seen["p"] and "漏拦: 表注释缺失" in seen["p"]
    assert "当前 SKILL" in seen["p"]


# ── 门禁：全盘拒绝控制候选（护栏 4，确定性）───────────────────────────────
def test_control_gate_reject_all_below_clean():
    # 放行 case：全盘拒绝报告 → precision 0 → F1 0
    release = _case(cid="r1", expected=[], files={"c.sql": "clean"})
    # 拦截 case：expected 不在全盘拒绝清单 → recall 0 → F1 0
    intercept = _case(cid="i1", expected=["WHERE避免函数转换"])
    gate = R.control_gate([release, intercept])
    assert gate == 0.0
    # 干净执行参照：逐 case F1=1 → 门禁 F1 显著更低
    clean = sum(R.f1_score(len(c.reference["expected_rules"]),
                           len(c.reference["expected_rules"]),
                           len(c.reference["expected_rules"]),
                           len(c.reference["expected_rules"])) for c in [release, intercept]) / 2
    assert gate < clean


def test_control_gate_empty_holdout():
    assert R.control_gate([]) == float("-inf")


def test_split_eval_keeps_release_in_holdout():
    cases = [_case(cid=f"i{i}") for i in range(6)] + [_case(cid="r1", expected=[])]
    train, holdout = R.split_eval(cases, {"gepa_holdout_ratio": 0.2})
    assert any(c.reference["expected_empty"] for c in holdout)   # 放行型至少 1 进 holdout
    assert len(train) + len(holdout) == 7


# ── validate_candidate（frontmatter 契约 + 锚点 + 长度）───────────────────
def test_validate_candidate_rejects_frontmatter_drop_and_oversize():
    v = R.validate_candidate(100)
    good = "---\nname: ddl-guard\ndescription: x\n\n## 审查工作流\n" + "x" * 50
    assert v(good) is True
    assert v("## 审查工作流\n" + "x" * 50) is False         # 删 frontmatter → 拒
    assert v(good + "y" * 120) is False                     # 超长 1.5× → 拒
    assert v("---\nname: x\ndescription: y\n\n" + "x" * 50) is False  # 无 ## 锚点 → 拒


# ── 注册表路径逃逸（护栏 1）───────────────────────────────────────────────
def test_scorer_registry_rejects_escape_and_missing():
    import evo_proposal as PR
    saved = R.SCORER_REGISTRY["ddl-guard"]
    try:
        R.SCORER_REGISTRY["ddl-guard"] = {"scripts": ["../../etc/passwd"], "accepted_dirs": ("skills/",)}
        try:
            R.scorer_registry()
            assert False, "应拒绝越界路径"
        except PR.ApplyError as e:
            assert "越出仓库" in str(e) or "不在允许范围" in str(e)
        # resolve 后越出仓库边界（四层 .. 逃到仓库外）
        R.SCORER_REGISTRY["ddl-guard"] = {"scripts": ["skills/ddl-guard/scripts/../../../.."],
                                          "accepted_dirs": ("skills/",)}
        try:
            R.scorer_registry()
            assert False, "应拒绝 resolve 后越界"
        except PR.ApplyError as e:
            assert "越出仓库" in str(e)

        # 仓库内但前缀不在允许范围（合法 resolve → startswith 检查）
        R.SCORER_REGISTRY["ddl-guard"] = {"scripts": ["docs/design/x.py"],
                                          "accepted_dirs": ("skills/",)}
        try:
            R.scorer_registry()
            assert False, "应拒绝范围外前缀"
        except PR.ApplyError as e:
            assert "不在允许范围" in str(e)

        R.SCORER_REGISTRY["ddl-guard"] = {"scripts": ["skills/ddl-guard/scripts/nope.py"],
                                          "accepted_dirs": ("skills/",)}
        try:
            R.scorer_registry()
            assert False, "应拒绝不存在脚本"
        except PR.ApplyError as e:
            assert "不存在" in str(e)
    finally:
        R.SCORER_REGISTRY["ddl-guard"] = saved


def test_scorer_registry_happy_path():
    reg = R.scorer_registry()
    assert "ddl-guard" in reg and len(reg["ddl-guard"]["scripts"]) == 2
    assert all(p.is_file() for p in reg["ddl-guard"]["scripts"])


# ── script_baseline_f1（dry-run 确定性打分）──────────────────────────────
def test_script_baseline_f1_unknown_skill():
    import pytest
    with pytest.raises(SystemExit):
        R.script_baseline_f1({}, "nope", [])


def test_script_baseline_f1_script_failure(monkeypatch, tmp_path):
    """脚本异常 → 该 case 记 error 不崩溃，F1 降级计算。"""
    import json
    case = _case(cid="c1", expected=["禁用类型"], files={"a.sql": "x"})
    case = G.Case(id="c1", inputs={"input_dir": str(tmp_path), "files": {"a.sql": "x"}},
                  reference={"expected_rules": ["禁用类型"], "manual_rules": [],
                             "expected_empty": False})

    def boom(cmd, capture_output=True, text=True, timeout=None):
        raise subprocess_exc

    import evo_replay
    import subprocess as sp
    global subprocess_exc
    subprocess_exc = sp.TimeoutExpired(cmd="x", timeout=30)
    monkeypatch.setattr(R.subprocess, "run", boom)
    avg, details = R.script_baseline_f1({}, "ddl-guard", [case])
    assert details[0]["error"] and avg == 0.0     # 无 actual → recall 0 → F1 0


# ── write_skill_proposal（产物格式）───────────────────────────────────────
def test_write_skill_proposal_lands_pending_prompt_evolution(tmp_path, monkeypatch):
    import evo_config as C
    monkeypatch.setattr(C, "base_paths", lambda cfg: {
        "pending": tmp_path / "pending", "base": tmp_path})
    best = G.Candidate(id="c3", parent="c1", gen=2, text="新 SKILL 全文")
    path = R.write_skill_proposal({}, "ddl-guard", "旧 SKILL", best, 0.6, 0.9,
                                  [{"c0": 0.6}, {"c3": 0.9}])
    body = path.read_text(encoding="utf-8")
    assert "type: prompt_evolution" in body and "status: pending" in body
    assert "gepa-replay" in path.name and "手动替换" in body
    assert "新 SKILL 全文" in body and "0.600 → evolved 0.900" in body
    assert path.parent == tmp_path / "pending"


# ── CLI 链路（--dry-run，零 LLM）──────────────────────────────────────────
def test_cmd_evolve_replay_dry_run(tmp_path, monkeypatch):
    import subprocess
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _fake_run)
    repo = Path(__file__).resolve().parents[3]
    import evo
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="", budget=None, seed=0, dry_run=True)
    assert evo.cmd_evolve(sys) == 0


def test_cmd_evolve_replay_unknown_skill():
    import evo
    sys = types.SimpleNamespace(
        skill="no-such-skill", eval="", budget=None, seed=0, dry_run=True)
    assert evo.cmd_evolve(sys) == 1


def test_cmd_evolve_replay_gate_fail(tmp_path, monkeypatch):
    # 门禁失败（全盘拒绝控制候选 F1 >= baseline）→ return 1，不进入 GEPA
    import subprocess
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _fake_run)
    monkeypatch.setattr(evo_replay, "control_gate", lambda holdout: 1.0)
    import evo
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="", budget=None, seed=0, dry_run=False)
    assert evo.cmd_evolve(sys) == 1


def test_cmd_evolve_replay_full_run(tmp_path, monkeypatch):
    # 门禁通过 + GEPA 进化 → 提案落盘 base_paths/pending，不自动 apply/commit
    import subprocess
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _fake_run)
    monkeypatch.setattr(evo_replay, "control_gate", lambda holdout: 0.0)
    ev_calls = []

    def _ev_spy(skill, cfg, raw, out_root=None, eval_dirs=None):
        ev_calls.append((skill, eval_dirs))
        return 0

    monkeypatch.setattr(evo_replay, "cmd_evidence_llm", _ev_spy)
    import evo
    import evo_gepa as G
    # fake run_gepa：返回 best(c1 候选) + log 含 holdout 分数，模拟改善 > 0.2
    class FakeCandidate:
        id, parent, gen, text = "c1", "c0", 1, "---\nname: ddl-guard\ndescription: d\n\n## 审查工作流\n新内容"
    def _fake_run_gepa(baseline, train, holdout, execute, reflect, budget,
                       batch_size, rng_seed, validate, asset_desc):
        assert len(train) >= 1 and len(holdout) >= 1
        return (FakeCandidate(),
                {},
                [{"holdout": {"c0": 0.5, "c1": 0.9}}, {"holdout": {"c1": 0.95}}])
    monkeypatch.setattr(evo_replay.G, "run_gepa", _fake_run_gepa)
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="", budget=None, seed=0, dry_run=False)
    import evo_config as C
    pending = C.base_paths(C.load_config())["pending"]
    before = {f.name for f in pending.glob("*gepa-replay.md")}
    assert evo.cmd_evolve(sys) == 0
    # eval_dirs 透传：证据阶段与 GEPA 同源（防回归：--eval 不得在证据阶段被丢弃）
    assert all(ev[0] == "ddl-guard" and ev[1] for ev in ev_calls)
    assert len(ev_calls) == 1            # 完整运行后接线部署态证据 pass
    # 本次调用新增 1 个提案（差集）；不自动 apply/commit
    after = {f.name for f in pending.glob("*gepa-replay.md")}
    props = list(after - before)
    assert len(props) == 1
    body = (pending / props[0]).read_text(encoding="utf-8")
    assert "type: prompt_evolution" in body
    assert "手动替换" in body


def test_cmd_evolve_replay_with_budget(monkeypatch):
    # --budget 设置 cfg（356 行）；dry-run 到此返回 0
    import subprocess
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _fake_run)
    import evo
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="", budget=8, seed=0, dry_run=True)
    assert evo.cmd_evolve(sys) == 0


def test_cmd_evolve_replay_custom_eval_dir_missing():
    # --eval 指向不存在目录 → return 1（367 行）
    import evo
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="no-such-eval-dir", budget=None, seed=0, dry_run=False)
    assert evo.cmd_evolve(sys) == 1


def test_cmd_evolve_replay_custom_eval_dir_ok(tmp_path, monkeypatch):
    # --eval 指向自定义目录（含 1 case）→ 不足 replay_min_cases → return 1（386 行）
    import evo
    d = tmp_path / "my-eval"
    (d / "101-x" / "input").mkdir(parents=True)
    (d / "101-x" / "input" / "t.sql").write_text("x", encoding="utf-8")
    (d / "101-x" / "expected.md").write_text(
        "# c\n\n## 预期检查输出\n\n- 脚本自动检出：禁用类型\n", encoding="utf-8")
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval=str(d), budget=None, seed=0, dry_run=True)
    assert evo.cmd_evolve(sys) == 1


def test_cmd_evolve_replay_no_improvement(tmp_path, monkeypatch):
    # 进化无改善（best_score - base_score ≤ 0.2）→ 仅存报告，无提案（429 行）
    import subprocess
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _fake_run)
    monkeypatch.setattr(evo_replay, "control_gate", lambda holdout: 0.0)
    ev_calls = []

    def _ev_spy(skill, cfg, raw, out_root=None, eval_dirs=None):
        ev_calls.append((skill, eval_dirs))
        return 0

    monkeypatch.setattr(evo_replay, "cmd_evidence_llm", _ev_spy)
    import evo
    import evo_config as C
    pending = C.base_paths(C.load_config())["pending"]
    before = {f.name for f in pending.glob("*gepa-replay.md")}
    class FakeCandidate:
        id, parent, gen, text = "c1", "c0", 1, "---\nname: ddl-guard\ndescription: d\n\n## 审查工作流\n新内容"
    def _fake_run_gepa(baseline, train, holdout, execute, reflect, budget,
                       batch_size, rng_seed, validate, asset_desc):
        return (FakeCandidate(), {},
                [{"holdout": {"c0": 0.8, "c1": 0.85}}, {"holdout": {"c1": 0.9}}])
    monkeypatch.setattr(evo_replay.G, "run_gepa", _fake_run_gepa)
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="", budget=None, seed=0, dry_run=False)
    assert evo.cmd_evolve(sys) == 0
    assert len(ev_calls) == 1 and ev_calls[0][0] == "ddl-guard"  # 无提案路径同样跑证据 pass
    after = {f.name for f in pending.glob("*gepa-replay.md")}
    assert after - before == set()   # 无新提案


def _fake_run(cmd, capture_output=True, text=True, timeout=None):
    import json
    return types.SimpleNamespace(stdout=json.dumps(
        [{"file": "x", "summary": {"total": 0, "mandatory": 0, "recommended": 0},
          "issues": []}]))


def test_extract_rules_rules_string_rejected():
    """rules 为字符串（非 list）→ 不可解析：正则层挡非 list 语法，isinstance 纵深防御。"""
    assert R.extract_rules_from_report('{"rules": "字符串"}') == ([], False)
    # 数字列表仍为 list → 放行后过滤为空（契约：结构合法即 ok=True）
    rules, ok = R.extract_rules_from_report('{"rules": [1, 2]}')
    assert ok and rules == []


def test_script_baseline_f1_exit_1_is_badcase_parsed(monkeypatch, tmp_path):
    """rc=1 = 有强制问题（badcase 正常态）：解析 stdout 记规则，不记为 error。"""
    import json
    case = _case(cid="c1", expected=["禁用类型"], files={"a.sql": "x"})
    case = G.Case(id="c1", inputs={"input_dir": str(tmp_path), "files": {"a.sql": "x"}},
                  reference={"expected_rules": ["禁用类型"], "manual_rules": [],
                             "expected_empty": False})
    calls = []
    out = json.dumps([{"issues": [{"rule": "禁用类型"}]}])

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=out, returncode=1)

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    avg, details = R.script_baseline_f1({}, "ddl-guard", [case])
    assert len(calls) == 2 and details[0]["score"] == 1.0  # 命中 → F1 1
    assert all("error" not in d for d in details)


def test_script_baseline_f1_exit_2_recorded(monkeypatch, tmp_path):
    """rc=2 = 运行错误：记 error，不得当作空结果计入基线。"""
    import types
    case = _case(cid="c1", expected=["禁用类型"], files={"a.sql": "x"})
    case = G.Case(id="c1", inputs={"input_dir": str(tmp_path), "files": {"a.sql": "x"}},
                  reference={"expected_rules": ["禁用类型"], "manual_rules": [],
                             "expected_empty": False})
    calls = []

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        calls.append(cmd)
        return types.SimpleNamespace(stdout="[]", returncode=2)

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    avg, details = R.script_baseline_f1({}, "ddl-guard", [case])
    assert len(calls) == 2 and "exit 2" in details[0]["error"]
    assert avg == 0.0


def test_script_baseline_f1_check_script_selects_matching(monkeypatch, tmp_path):
    """expected.md 声明 check: → 只跑匹配的注册脚本，无关检查器不混入。"""
    import types
    # 真实布局：expected.md 在 case 目录（input/ 的父级），@date 2026-09-20
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (tmp_path / "expected.md").write_text(
        "## 预期检查输出\n- check: sql_check.py\n- 脚本自动检出：禁用类型\n",
        encoding="utf-8")
    case = G.Case(id="c1", inputs={"input_dir": str(input_dir), "files": {"a.sql": "x"}},
                  reference={"expected_rules": ["禁用类型"], "manual_rules": [],
                             "expected_empty": False})
    calls = []

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        calls.append(cmd)
        return types.SimpleNamespace(stdout="[]", returncode=0)

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    avg, details = R.script_baseline_f1({}, "ddl-guard", [case])
    assert len(calls) == 1 and calls[0][1].endswith("sql_check.py")
    assert details[0]["score"] == 0.0  # 0 actual vs 1 expected → recall 0


def test_script_baseline_f1_check_script_unregistered_fails_closed(monkeypatch, tmp_path):
    """expected.md 声明未注册脚本 → fail-closed 记 error，不静默空跑。"""
    # 真实布局：expected.md 在 case 目录（input/ 的父级），@date 2026-09-20
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (tmp_path / "expected.md").write_text(
        "## 预期检查输出\n- check: ghost.py\n- 脚本自动检出：禁用类型\n",
        encoding="utf-8")
    case = G.Case(id="c1", inputs={"input_dir": str(input_dir), "files": {"a.sql": "x"}},
                  reference={"expected_rules": ["禁用类型"], "manual_rules": [],
                             "expected_empty": False})
    avg, details = R.script_baseline_f1({}, "ddl-guard", [case])
    assert "未注册" in details[0]["error"] and avg == 0.0


# ── spec:replay-eval-6 打分器确定性（设计稿 §7.6）────────────────────────
def test_execute_deterministic_same_report_same_score(monkeypatch):
    """打分器确定性：同一候选同一 case（同一报告文本）两次打分，分数与反馈
    完全一致——打分器（提取+对账+F1）是纯函数，随机性只在 LLM 执行层
    （GEPA rng_seed 固定；evo.py evolve --seed 默认 0）。"""
    def fake(prompt, cfg):
        return '五段式审查报告……\n{"rules": ["禁用类型", "全角字符"]}'

    ex = R.make_execute({}, fake, "ddl-guard")
    case = _case(cid="d1", expected=["禁用类型"], files={"a.sql": "CREATE TABLE t;"})
    assert ex("candidate-text", case) == ex("candidate-text", case)


# ── spec:replay-eval-7 提取层对照抽验（设计稿 §5.2/§7.7）──────────────────
def test_extract_rules_against_real_report():
    """对真实人工审查报告跑提取：
    ① 原报告早于输出契约（无 JSON 规则清单）→ fail-closed ([], False)，
       不从叙述性正文臆造规则；
    ② 报告按输出契约补 JSON 清单（008-real expected 声明的 6 类脚本规则，
       源自 ddl_check 对同一 DDL 的实测检出）→ 提取结果与清单逐条一致。"""
    import json
    skill_dir = Path(__file__).resolve().parents[3] / "ddl-guard"
    report = (skill_dir / "test" / "ddl-202607071777审查报告.md").read_text(
        encoding="utf-8")
    # ① 无清单 → 不可解析（报告含表格/代码块/中文标题等真实噪音，不误匹配）
    assert R.extract_rules_from_report(report) == ([], False)
    # ② 真实报告实体 + 契约清单 → 逐条保真（无增删无乱序）
    _, expected_rules, _ = R.parse_expected(
        skill_dir / "eval" / "008-real" / "expected.md")
    tail = json.dumps({"rules": expected_rules}, ensure_ascii=False)
    assert R.extract_rules_from_report(report + "\n" + tail) == (expected_rules, True)


# ── k 采样指标（pass@k / pass^k，Comet 机制 ①，@date 2026-09-20）──────────
def test_pass_at_k_unbiased_formula():
    # n=10,c=2,k=5：1 - C(8,5)/C(10,5) = 1 - 56/252 = 7/9 ≈ 0.7778（非退化）
    pak, deg = R.pass_at_k(10, 2, 5)
    assert abs(pak - 7 / 9) < 1e-12 and deg is False
    assert R.pass_at_k(10, 0, 5) == (0.0, False)
    assert R.pass_at_k(10, 10, 5) == (1.0, False)
    # n-c < k → C(n-c,k)=0 → 恰 1.0（HumanEval 同款守卫，含 n==k 边界）
    assert R.pass_at_k(3, 1, 3) == (1.0, False)
    assert R.pass_at_k(4, 3, 3) == (1.0, False)


def test_pass_at_k_degenerate_and_bounds():
    # n<k：退化「至少一次通过」且 degenerate=True
    assert R.pass_at_k(1, 1, 3) == (1.0, True)
    assert R.pass_at_k(1, 0, 3) == (0.0, True)
    assert R.pass_at_k(0, 5, 3) == (0.0, True)          # n≤0
    # c 越界夹取 [0,n]
    assert R.pass_at_k(5, 99, 3) == R.pass_at_k(5, 5, 3)


def test_pass_cap_k_semantics():
    assert R.pass_cap_k([True, True]) == 1.0
    assert R.pass_cap_k([True, False, True]) == 0.0
    assert R.pass_cap_k([]) == 0.0                      # 空采样不是全过


def test_execute_k_all_pass_and_partial():
    agg = R.execute_k("c", _case(), 3, lambda c, cs: (1.0, "全部命中", None))
    assert agg["pass_cap_k"] == 1.0 and agg["c"] == 3 and agg["n"] == 3
    assert "pass^k=3/3" in agg["feedback"] and agg["pass_at_k"] == 1.0

    seq = iter([(1.0, "全部命中", None), (1.0, "全部命中", False), (1.0, "全部命中", None)])
    agg2 = R.execute_k("c", _case(), 3, lambda c, cs: next(seq))
    # invoked=False 计失败：分子剔除（c=2）、分母保留（n=3）→ pass^k=0
    assert agg2["pass_cap_k"] == 0.0 and agg2["c"] == 2 and agg2["n"] == 3
    assert "未触发轮次=[2]" in agg2["feedback"] and "pass@k=" in agg2["feedback"]


def test_execute_k_threshold_and_k1_passthrough():
    def run(c, cs):
        return (0.9, "部分命中", None)

    # 阈值语义：0.9 ≥ 0.9 记通过；threshold=1.0 记失败
    assert R.execute_k("c", _case(), 1, run, threshold=0.9)["c"] == 1
    assert R.execute_k("c", _case(), 3, run, threshold=1.0)["c"] == 0
    # k=1：feedback 原样透传（旧语义锚）
    assert R.execute_k("c", _case(), 1, run, threshold=0.5)["feedback"] == "部分命中"


def test_execute_k_aggregated_via_make_execute():
    # k>1 时 execute 主信号 = pass^k：全过 → 1.0；任一失败 → 0.0
    outs = iter(['{"rules": ["禁用类型"]}', '{"rules": ["禁用类型"]}',
                 '{"rules": ["禁用类型"]}'])
    execute = R.make_execute({"replay_k": 3}, lambda p, c: next(outs), "s")
    score, fb = execute("x", _case(expected=["禁用类型"]))
    assert score == 1.0 and "pass^k=3/3" in fb

    outs2 = iter(['{"rules": ["禁用类型"]}', '{"rules": []}', '{"rules": ["禁用类型"]}'])
    execute2 = R.make_execute({"replay_k": 3}, lambda p, c: next(outs2), "s")
    score2, fb2 = execute2("x", _case(expected=["禁用类型"]))
    assert score2 == 0.0 and "r2: 漏拦: 禁用类型" in fb2


# ── prompt 拼装零回归锚 ───────────────────────────────────────────────────
def test_build_prompt_zero_regression_bytes():
    candidate = "---\nname: x\ndescription: d\n\n## 审查规则\nR1"
    files_text = "--- a.sql ---\nCREATE TABLE t (id int);"
    # 旧版拼装的硬拷贝（非证据、无多轮必须逐字节一致）
    expected = (
        candidate + "\n\n# 待审查输入\n" + files_text + "\n\n# 任务\n"
        "按上述 SKILL 的规则与工作流对输入做静态审查，输出审查报告。\n"
        "仅纯文本分析，禁止调用任何工具/脚本/命令（本环境无工具可用）。\n"
        "报告末尾必须附加检出清单 JSON（严格单个 JSON，无围栏无其他文字）：\n"
        '{"rules": ["规则名1", "规则名2", ...]}\n'
        '规则名与 SKILL 中的规则命名一致；未检出问题则输出 {"rules": []}')
    assert R._build_prompt(candidate, files_text, "", False, False, "ddl-guard") == expected

    # 集成层：文本模式 execute 实发 prompt 与硬拷贝逐字节一致
    seen = {}

    def fake(prompt, cfg):
        seen["p"] = prompt
        return '{"rules": []}'

    R.make_execute({"replay_k": 1}, fake, "ddl-guard")(
        candidate, _case(files={"a.sql": "CREATE TABLE t (id int);"}))
    assert seen["p"] == expected


# ── 调用证据门禁（stream-json 事件流，Comet 机制 ③）───────────────────────
def test_skill_invoked_from_events_branches():
    assert R.skill_invoked_from_events(
        [{"name": "Read", "input": {"file_path": "/r/skills/ddl-guard/SKILL.md"}}],
        "ddl-guard") is True
    assert R.skill_invoked_from_events(
        [{"name": "Bash", "input": {"command": "python3 skills/ddl-guard/scripts/c.py"}}],
        "ddl-guard") is True
    assert R.skill_invoked_from_events(
        [{"name": "Skill", "input": {"skill": "ddl-guard"}}], "ddl-guard") is True
    # 无事件 / 他技能路径 / 无关工具名 → False（fail-closed）
    assert R.skill_invoked_from_events([], "ddl-guard") is False
    assert R.skill_invoked_from_events(
        [{"name": "Read", "input": {"file_path": "/r/skills/api-guard/SKILL.md"}}],
        "ddl-guard") is False
    assert R.skill_invoked_from_events(
        [{"name": "Grep", "input": {"pattern": "skills/ddl-guard/SKILL.md"}}],
        "ddl-guard") is False


def test_execute_evidence_gate_blocks_uninvoked():
    def fake_raw(prompt, cfg):
        raise AssertionError("证据模式单轮 case 不应走 raw 通道（仅 sim-user 备用）")

    def fake_stream(prompt, cfg):
        return ('{"rules": ["禁用类型"]}', [])   # 报告可解析但无工具调用事件

    execute = R.make_execute({"replay_k": 1}, fake_raw, "ddl-guard", fake_stream)
    score, fb = execute("x", _case(expected=["禁用类型"]))
    assert score == 0.0 and "skill 未触发" in fb


def test_execute_evidence_mode_reads_deployed_skill():
    seen = []

    def fake_stream(prompt, cfg):
        seen.append(prompt)
        return ('{"rules": ["禁用类型"]}',
                [{"name": "Read",
                  "input": {"file_path": "/r/skills/ddl-guard/SKILL.md"}}])

    execute = R.make_execute({"replay_k": 1}, lambda p, c: "", "ddl-guard", fake_stream)
    score, fb = execute("CANDIDATE-SENTINEL", _case(expected=["禁用类型"]))
    assert score == 1.0 and "全部命中" in fb
    # 证据模式：指令 Read 部署态 SKILL.md；不嵌候选文本；无「禁止工具」行
    assert "Read 工具完整读取 skills/ddl-guard/SKILL.md" in seen[0]
    assert "CANDIDATE-SENTINEL" not in seen[0]
    assert "禁止调用任何工具" not in seen[0]


# ── 双 Agent 多轮（Comet 机制 ②）──────────────────────────────────────────
def _da_case(expected=None):
    return G.Case(
        id="c1",
        inputs={"input_dir": "/tmp/x",
                "files": {"a.sql": "CREATE TABLE t (id int);"},
                "prompts": ["素材1：表 t 无外键，id 为主键"]},
        reference={"expected_rules": expected or [], "manual_rules": [],
                   "expected_empty": not expected})


def test_dual_agent_sim_user_and_history_replay():
    calls = []

    def fake_raw(prompt, cfg):
        calls.append(prompt)
        if len(calls) == 1:
            return "审查中。\nDECISION_REQUEST: 表 t 是否有外键？"
        elif len(calls) == 2:
            return "没有外键"
        else:
            return '{"rules": ["禁用类型"]}'

    run_once = R.make_run_once({"replay_dual_agent": True}, fake_raw, "ddl-guard")
    score, fb, invoked = run_once("候选", _da_case(expected=["禁用类型"]))
    # 调用序列：agent r1 / sim-user / agent r2（终局报告）
    assert len(calls) == 3
    assert "DECISION_REQUEST:" in calls[0]              # 多轮协议进首轮任务段
    assert "你是用户" in calls[1] and "素材1" in calls[1] and "外键" in calls[1]
    # 终局回合无状态重放：对话记录以 [助手]/[用户] 文本拼进 prompt
    assert "# 对话记录" in calls[2]
    assert "[助手] DECISION_REQUEST: 表 t 是否有外键？" in calls[2]
    assert "[用户] 没有外键" in calls[2]
    assert score == 1.0 and "全部命中" in fb and invoked is None


def test_dual_agent_round_cap_and_deterministic_fallback():
    calls = []

    def fake_raw(prompt, cfg):
        calls.append(prompt)
        return "DECISION_REQUEST: 还需要什么？"       # 病态：恒提问

    run_once = R.make_run_once({"replay_dual_agent": True}, fake_raw, "ddl-guard")
    score, fb, invoked = run_once("候选", _da_case())
    # 调用预算 2*(1+1)=4：agent / sim-user / agent / agent，超限截断
    assert len(calls) == 4
    # 素材耗尽后的确定性兜底进第 4 次 prompt（不走 LLM）
    assert "[用户] 无更多输入，请直接给出最终报告" in calls[3]
    assert score == 0.0 and "报告不可解析" in fb and invoked is None


def test_dual_agent_sim_user_empty_reply_fallback():
    calls = []

    def fake_raw(prompt, cfg):
        calls.append(prompt)
        if len(calls) == 1:
            return "DECISION_REQUEST: 用哪种命名规范？"
        elif len(calls) == 2:
            return "   "                               # sim-user 空应答
        else:
            return '{"rules": []}'

    run_once = R.make_run_once({"replay_dual_agent": True}, fake_raw, "ddl-guard")
    score, fb, _inv = run_once("候选", _da_case())
    assert len(calls) == 3
    assert "[用户] 按你的建议继续" in calls[2]         # 空应答 → 固定兜底
    assert score == 1.0                                # 放行型（expected 空 + 空 actual）


# ── 证据 JSON（content_hash / 契约字段 / dry-run 入口）─────────────────────
def test_skill_content_hash_deterministic_order(tmp_path):
    import hashlib
    skill = tmp_path / "skills" / "demo"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"A")
    (skill / "scripts" / "a.py").write_bytes(b"C")
    (skill / "scripts" / "b.py").write_bytes(b"B")
    # 契约算法：逐文件摘要清单（内容 sha256 + 两空格 + skill 内相对 posix
    # 路径 + 换行），按仓库根相对 posix 路径字典序（SKILL.md < scripts/a.py
    # < scripts/b.py）拼接后整体 sha256——路径与边界参与哈希
    manifest = (
        f"{hashlib.sha256(b'A').hexdigest()}  SKILL.md\n"
        f"{hashlib.sha256(b'C').hexdigest()}  scripts/a.py\n"
        f"{hashlib.sha256(b'B').hexdigest()}  scripts/b.py\n")
    want = f"sha256:{hashlib.sha256(manifest.encode('utf-8')).hexdigest()}"
    assert R.skill_content_hash("demo", root=tmp_path) == want
    # 派生产物不扰动指纹（A 路同口径：coverage/ruff/pytest/解释器生灭物）
    (skill / "scripts" / ".coverage").write_bytes(b"SQLite format 3\x00")
    (skill / "scripts" / ".ruff_cache" / "0.16.8").mkdir(parents=True)
    (skill / "scripts" / ".ruff_cache" / "0.16.8" / "abc").write_bytes(b"\x00")
    (skill / "scripts" / "__pycache__").mkdir()
    (skill / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"\xff\xfe")
    (skill / "scripts" / ".pytest_cache").mkdir()
    (skill / "scripts" / ".pytest_cache" / "c").write_bytes(b"\x00")
    (skill / "scripts" / ".DS_Store").write_bytes(b"\x00\x00Bud1")
    assert R.skill_content_hash("demo", root=tmp_path) == want
    # 真实源文件增删 → hash 变化（路径与内容均参与）
    (skill / "scripts" / "c.py").write_text("x", encoding="utf-8")
    assert R.skill_content_hash("demo", root=tmp_path) != want
    # SKILL.md 缺失 → fail-closed
    (skill / "SKILL.md").unlink()
    try:
        R.skill_content_hash("demo", root=tmp_path)
        assert False, "应抛 FileNotFoundError"
    except FileNotFoundError:
        pass


def test_skill_content_hash_excludes_derived_artifacts(tmp_path):
    # 派生产物（__pycache__/.pytest_cache/.DS_Store/*.pyc）生灭不得扰动指纹
    # ——2026-09-21 plugin_lock 事故与 #247 r2 replay 证据漂移同根因
    skill = tmp_path / "skills" / "demo"
    (skill / "scripts" / "__pycache__").mkdir(parents=True)
    (skill / "scripts" / ".pytest_cache").mkdir()
    (skill / "SKILL.md").write_text("demo", encoding="utf-8")
    (skill / "scripts" / "a.py").write_text("print()", encoding="utf-8")
    base = R.skill_content_hash("demo", root=tmp_path)
    (skill / "scripts" / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"\x00junk")
    (skill / "scripts" / ".pytest_cache" / "lastfailed.json").write_text("{}", encoding="utf-8")
    (skill / "scripts" / ".DS_Store").write_bytes(b"\x00")
    assert R.skill_content_hash("demo", root=tmp_path) == base


def test_skill_content_hash_matches_release_guard_contract():
    # 跨路契约钉测试（plugin_lock 单一真相源纪律）：replay 证据 content_hash
    # 必须与 scripts/release_guard.compute_content_hash 逐字相等——任一侧算法
    # 漂移此测试先红（#247 r2 回流：此前字节直拼实现随 __pycache__ 生灭漂移，
    # 同源码状态下四种哈希值互不相等）
    import sys
    repo_root = Path(R.__file__).resolve().parents[3]
    scripts_dir = repo_root / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import release_guard
    finally:
        sys.path.remove(str(scripts_dir))
    assert R.skill_content_hash("ddl-guard") == \
        release_guard.compute_content_hash(repo_root, "ddl-guard")


def test_execute_evidence_kill_switch_uses_raw_channel():
    # replay_evidence=False 杀开关：即便提供 stream 通道也不启用证据门禁，
    # 文本模式原样走 raw 通道（门禁关闭时不阻断、不降级计分）
    def fake_stream(prompt, cfg):
        raise AssertionError("杀开关下不应调用 stream 通道")

    execute = R.make_execute({"replay_k": 1, "replay_evidence": False},
                             lambda p, c: '{"rules": ["禁用类型"]}',
                             "ddl-guard", fake_stream)
    score, fb = execute("CAND", _case(expected=["禁用类型"]))
    assert score == 1.0 and "全部命中" in fb


def test_write_replay_evidence_contract(tmp_path):
    import json as _json
    from datetime import datetime
    path = R.write_replay_evidence("demo", {
        "content_hash": "sha256:" + "0" * 64, "k": 3,
        "pass_at_k": 0.77777777, "pass_cap_k": 0.0,
        "invocation": {"skill_invoked": False, "evidence": "disabled"},
        "cases": 12,
    }, root=tmp_path)
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("}\n")                          # 尾换行
    doc = _json.loads(raw)
    # 字段与顺序一字不差（跨路契约）
    assert list(doc.keys()) == ["schema", "skill", "content_hash", "generated_at",
                                "k", "pass_at_k", "pass_cap_k", "invocation", "cases"]
    assert doc["schema"] == "replay-evidence/1" and doc["skill"] == "demo"
    assert doc["pass_at_k"] == 0.7778                   # round 4
    assert doc["pass_cap_k"] == 0.0
    assert doc["invocation"] == {"skill_invoked": False, "evidence": "disabled"}
    datetime.fromisoformat(doc["generated_at"])         # ISO8601 可解析
    assert doc["cases"] == 12 and isinstance(doc["cases"], int)  # 契约：整数计数
    assert path == (tmp_path / "skills" / "skill-evo" / "artifacts"
                    / "replay-evidence" / "demo.json")


def test_parse_prompts_bullet_and_fenced(tmp_path):
    # 旧式纯 bullet（零回归）+ 已知问题剥离
    f1 = tmp_path / "p1.md"
    f1.write_text("# 提示词集\n\n- 素材A\n- 素材B\n\n## 已知问题\n\n- 噪音\n",
                  encoding="utf-8")
    assert R.parse_prompts(f1) == (["素材A", "素材B"], ["噪音"])
    assert R.parse_prompts(tmp_path / "absent.md") == ([], [])
    # `---` 围栏：块内有 bullet → 逐 bullet；无 bullet → 剥标题整块压缩
    f2 = tmp_path / "p2.md"
    f2.write_text("# 提示词集\n\n---\n\n## 回合1\n\n素材一\n继续行\n\n---\n\n"
                  "- 素材二a\n- 素材二b\n", encoding="utf-8")
    prompts2, known2 = R.parse_prompts(f2)
    assert prompts2 == ["素材一 继续行", "素材二a", "素材二b"] and known2 == []


def test_load_eval_set_prompts_key(tmp_path):
    d = tmp_path / "eval"
    for name, with_prompts in (("101-x", True), ("102-y", False)):
        (d / name / "input").mkdir(parents=True)
        (d / name / "input" / "t.sql").write_text("x", encoding="utf-8")
        (d / name / "expected.md").write_text(
            "# c\n\n## 预期检查输出\n\n- 脚本自动检出：禁用类型\n", encoding="utf-8")
        if with_prompts:
            (d / name / "prompts.md").write_text(
                "# 提示词集\n\n- 素材1：无外键\n- 素材2：含外键\n", encoding="utf-8")
    cases = R.load_eval_set("s", d, {})
    assert cases[0].inputs["prompts"] == ["素材1：无外键", "素材2：含外键"]
    assert "prompts" not in cases[1].inputs             # 无 prompts.md 零回归


def test_cmd_evidence_dry_run_writes_evidence(tmp_path, monkeypatch, capsys):
    import json
    import evo_replay

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        return types.SimpleNamespace(stdout=json.dumps([{"issues": []}]),
                                     returncode=0)

    monkeypatch.setattr(evo_replay.subprocess, "run", fake_run)
    cfg = {"replay_min_cases": 8, "replay_k": 3, "replay_pass_threshold": 1.0}
    assert evo_replay.cmd_evidence_dry_run("ddl-guard", cfg, out_root=tmp_path) == 0
    path = (tmp_path / "skills" / "skill-evo" / "artifacts"
            / "replay-evidence" / "ddl-guard.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == "replay-evidence/1"
    assert doc["invocation"] == {"skill_invoked": True, "evidence": "dry-run"}
    assert doc["k"] == 3 and doc["content_hash"].startswith("sha256:")
    # 契约：cases 为整数计数（任务书样例 "cases": 12），真实评估集 73 cases
    assert isinstance(doc["cases"], int) and doc["cases"] >= 8
    # @date 2026-09-20 回归锚：check: 选择生效 → 无脚本误跑的 exit-2 错误明细
    # （原 input/expected.md 路径 bug 使全脚本误跑、exit 2 重复行）
    assert "exit 2" not in capsys.readouterr().out


# ── 部署态证据 pass（cmd_evidence_llm + evolve 接线，LLM 通道 mock）────────
def _ev_stream_fake(report='{"rules": []}', events=None):
    """证据实测通道 fake：报告恒定 + 可注入工具调用事件（默认含本 skill Read）。"""

    def fake_stream(prompt, cfg):
        return (report, events if events is not None else
                [{"name": "Read",
                  "input": {"file_path": "/r/skills/ddl-guard/SKILL.md"}}])

    return fake_stream


def _ev_fake_run(cmd, capture_output=True, text=True, timeout=None):
    return types.SimpleNamespace(stdout='[{"issues": []}]', returncode=0)


def test_cmd_evidence_llm_writes_stream_evidence(tmp_path, monkeypatch):
    import json
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _ev_fake_run)
    monkeypatch.setattr(evo_replay, "call_claude_stream", _ev_stream_fake())
    cfg = {"replay_min_cases": 8, "replay_k": 1, "replay_pass_threshold": 1.0}
    assert evo_replay.cmd_evidence_llm(
        "ddl-guard", cfg, lambda p, c: "", out_root=tmp_path) == 0
    path = (tmp_path / "skills" / "skill-evo" / "artifacts"
            / "replay-evidence" / "ddl-guard.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["invocation"] == {"skill_invoked": True, "evidence": "stream-json"}
    assert doc["k"] == 1 and doc["content_hash"].startswith("sha256:")
    assert isinstance(doc["cases"], int) and doc["cases"] >= 8
    # k=1 不变量：逐 case pass@1 == pass^1（均 0/1），均值必相等
    assert doc["pass_at_k"] == doc["pass_cap_k"]
    assert 0.0 <= doc["pass_at_k"] <= 1.0


def test_cmd_evidence_llm_gate_fail_records_uninvoked(tmp_path, monkeypatch, capsys):
    """门禁 fail-closed：stream 无本 skill 调用事件 → 全采样计失败，evidence
    如实记录 skill_invoked=False / 双 0（不为跑通而放水）。"""
    import json
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _ev_fake_run)
    monkeypatch.setattr(evo_replay, "call_claude_stream",
                        _ev_stream_fake(events=[]))
    cfg = {"replay_min_cases": 8, "replay_k": 1, "replay_pass_threshold": 1.0}
    assert evo_replay.cmd_evidence_llm(
        "ddl-guard", cfg, lambda p, c: "", out_root=tmp_path) == 0
    path = (tmp_path / "skills" / "skill-evo" / "artifacts"
            / "replay-evidence" / "ddl-guard.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["invocation"] == {"skill_invoked": False, "evidence": "stream-json"}
    assert doc["pass_at_k"] == 0.0 and doc["pass_cap_k"] == 0.0
    assert "skill 未触发" in capsys.readouterr().out


def test_cmd_evidence_llm_insufficient_cases(tmp_path, monkeypatch):
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _ev_fake_run)
    monkeypatch.setattr(evo_replay, "call_claude_stream", _ev_stream_fake())
    cfg = {"replay_min_cases": 999, "replay_k": 1, "replay_pass_threshold": 1.0}
    assert evo_replay.cmd_evidence_llm(
        "ddl-guard", cfg, lambda p, c: "", out_root=tmp_path) == 1
    assert not (tmp_path / "skills" / "skill-evo" / "artifacts"
                / "replay-evidence" / "ddl-guard.json").exists()


def test_cmd_evidence_llm_kill_switch_refuses(tmp_path, monkeypatch):
    """replay_evidence=False：拒绝产出 evidence（fail-closed，不写误导性 JSON）。"""
    import evo_replay
    monkeypatch.setattr(evo_replay, "call_claude_stream", _ev_stream_fake())
    cfg = {"replay_evidence": False, "replay_min_cases": 8,
           "replay_k": 1, "replay_pass_threshold": 1.0}
    assert evo_replay.cmd_evidence_llm(
        "ddl-guard", cfg, lambda p, c: "", out_root=tmp_path) == 1
    assert not (tmp_path / "skills" / "skill-evo" / "artifacts"
                / "replay-evidence" / "ddl-guard.json").exists()


def test_cmd_evolve_replay_evidence_kill_switch(tmp_path, monkeypatch):
    # replay_evidence=False → GEPA 照跑，但部署态证据 pass 被跳过（不调
    # cmd_evidence_llm），cmd_evolve 返回 0
    import evo_replay
    import evo_config as C
    monkeypatch.setattr(evo_replay.subprocess, "run", _fake_run)
    monkeypatch.setattr(evo_replay, "control_gate", lambda holdout: 0.0)
    real = C.load_config()
    real["replay_evidence"] = False
    monkeypatch.setattr(C, "load_config", lambda: real)

    def _boom(skill, cfg, raw, out_root=None):
        raise AssertionError("kill switch 下不应调用证据 pass")

    monkeypatch.setattr(evo_replay, "cmd_evidence_llm", _boom)
    import evo

    class FakeCandidate:
        id, parent, gen, text = "c1", "c0", 1, "---\nname: ddl-guard\ndescription: d\n\n## 审查工作流\n新内容"

    monkeypatch.setattr(evo_replay.G, "run_gepa",
                        lambda *a, **kw: (FakeCandidate(), {},
                                          [{"holdout": {"c0": 0.5, "c1": 0.9}}]))
    sys = types.SimpleNamespace(
        skill="ddl-guard", eval="", budget=None, seed=0, dry_run=False)
    assert evo.cmd_evolve(sys) == 0


def test_dry_run_refuses_overwrite_stream_evidence(tmp_path, monkeypatch, capsys):
    """真实 stream-json 证据落盘后，dry-run 冒烟拒绝覆写（fail-closed：
    未提交的 k×cases LLM 成本被抹掉不可恢复）；dry-run→dry-run 幂等不受影响。"""
    import json
    import evo_replay
    monkeypatch.setattr(evo_replay.subprocess, "run", _ev_fake_run)
    cfg = {"replay_min_cases": 8, "replay_k": 1, "replay_pass_threshold": 1.0}
    # 先落一份真实证据（write_replay_evidence 直写最小合法 payload，
    # 形态等价 cmd_evidence_llm 产物）
    path = evo_replay.write_replay_evidence("ddl-guard", {
        "content_hash": "sha256:deadbeef", "k": 1,
        "pass_at_k": 1.0, "pass_cap_k": 1.0,
        "invocation": {"skill_invoked": True, "evidence": "stream-json"},
        "cases": 12,
    }, root=tmp_path)
    # dry-run 冒烟 → 拒绝覆写，原文件原封不动
    assert evo_replay.cmd_evidence_dry_run("ddl-guard", cfg, out_root=tmp_path) == 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["invocation"] == {"skill_invoked": True, "evidence": "stream-json"}
    assert doc["content_hash"] == "sha256:deadbeef"
    assert "拒绝覆写" in capsys.readouterr().out
    # 移除真实证据（模拟人工确认）后，dry-run→dry-run 幂等冒烟恢复可用
    path.unlink()
    assert evo_replay.cmd_evidence_dry_run("ddl-guard", cfg, out_root=tmp_path) == 0
    assert evo_replay.cmd_evidence_dry_run("ddl-guard", cfg, out_root=tmp_path) == 0
