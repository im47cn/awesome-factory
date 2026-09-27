"""release_guard 防呆逻辑单测——catv 0.x preMajor 纠偏 + 评测证据门禁（fail-closed）。"""

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import release_guard
from release_guard import (bump_version, compute_content_hash,
                           discover_evidence_skills, expected_bump,
                           parse_catv_target, verify_skill_evidence)


def C(header, body=""):
    return {"header": header, "body": body}


class TestExpectedBump:
    def test_feat_minor(self):
        assert expected_bump([C("feat(code-review): 转默认")]) == "minor"

    def test_fix_patch(self):
        assert expected_bump([C("fix: x"), C("docs: y")]) == "patch"

    def test_breaking_body_major(self):
        assert expected_bump([C("fix: x", "BREAKING CHANGE: api")]) == "major"

    def test_breaking_bang_major(self):
        assert expected_bump([C("feat!: x")]) == "major"

    def test_major_wins_over_feat(self):
        # feat 在前、breaking 在后，取最高级
        assert expected_bump([C("feat: a"), C("feat: b", "BREAKING CHANGE: c")]) == "major"

    def test_none_when_no_releasable(self):
        assert expected_bump([C("docs: x"), C("chore: y"), C("Merge pull request #1")]) is None

    def test_merge_and_nonconventional_ignored(self):
        assert expected_bump([C("Merge branch x"), C("随机中文标题")]) is None

    def test_feat_wins_over_fix(self):
        assert expected_bump([C("fix: a"), C("feat: b"), C("fix: c")]) == "minor"

    def test_revert_not_releasable(self):
        assert expected_bump([C("revert: feat xyz")]) is None


class TestBumpVersion:
    def test_from_0_4_0(self):
        assert bump_version("v0.4.0", "minor") == "0.5.0"
        assert bump_version("v0.4.0", "patch") == "0.4.1"
        assert bump_version("0.4.0", "major") == "1.0.0"

    def test_carry(self):
        assert bump_version("v0.9.9", "patch") == "0.9.10"


class TestParseCatvTarget:
    def test_normal(self):
        out = "✔ bumping version in package.json from 0.4.0 to 0.4.1\nother"
        assert parse_catv_target(out) == "0.4.1"

    def test_none(self):
        assert parse_catv_target("no match") is None


class TestRootCauseScenario:
    """2026-08-26 事故场景：区间 23 feat，catv 判 patch，期望必须 minor。"""

    def test_incident_expectation(self):
        commits = [C("feat(code-review): 可视化输出转默认")] + [C("fix: t%d" % i) for i in range(5)]
        bump = expected_bump(commits)
        assert bump == "minor"
        assert bump_version("v0.4.0", bump) == "0.5.0"
        # catv 实际目标（0.x preMajor 降级）
        assert parse_catv_target(
            "✔ bumping version in package.json from 0.4.0 to 0.4.1"
        ) == "0.4.1"


# ---------------------------------------------------------------------------
# 评测证据门禁（@date 2026-09-20）：fixture 为 tmp 仓（conftest 已剥 GIT_*），
# evidence 文件按 B 路 replay-evidence/1 契约自造——B 的实际产物不在本 worktree。
# ---------------------------------------------------------------------------

SKILL_BODY = "# 用法\n示例技能正文\n"
SCRIPT_BODY = "print('hi')\n"


def make_skill(repo, name, body=SKILL_BODY, script=SCRIPT_BODY):
    d = repo / "skills" / name
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    (d / "scripts" / "run.py").write_text(script, encoding="utf-8")


def write_evidence(repo, name, **over):
    """B 路 replay-evidence/1 契约格式的 fixture；over 值 None 表示删除该键。

    参数名刻意用 name：契约字段 skill 需能经 **over 覆盖（同名参数会截胡）。
    """
    ev = {
        "schema": "replay-evidence/1",
        "skill": name,
        "content_hash": compute_content_hash(repo, name),
        "generated_at": "2026-09-20T10:00:00+08:00",
        "k": 3,
        "pass_at_k": 0.83,
        "pass_cap_k": 0.67,
        "invocation": {"skill_invoked": True, "evidence": "stream-json"},
        "cases": 12,
    }
    for key, val in over.items():
        if val is None:
            ev.pop(key, None)
        else:
            ev[key] = val
    p = (repo / "skills" / "skill-evo" / "artifacts"
         / "replay-evidence" / f"{name}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
    return p


class TestContentHashContract:
    """跨路契约钉死：content_hash 算法漂移（本路或 B 路）必须在此红。"""

    def test_algorithm_pinned(self, tmp_path):
        make_skill(tmp_path, "demo", body="# 标题\n", script="x = 1\n")
        sub = tmp_path / "skills" / "demo" / "scripts" / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("y = 2\n", encoding="utf-8")
        # manifest 契约：条目 "{sha256(文件内容)hex}  {skill 内相对 posix 路径}\n"
        # 按 skill_source_files 的路径字典序逐条拼接后整体 sha256
        # （SKILL.md 大写 S 先于 scripts/**）
        def _entry(content, rel):
            return (hashlib.sha256(content.encode("utf-8")).hexdigest()
                    + "  " + rel + "\n")
        manifest = (_entry("# 标题\n", "SKILL.md")
                    + _entry("x = 1\n", "scripts/run.py")
                    + _entry("y = 2\n", "scripts/sub/b.py"))
        expect = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        assert compute_content_hash(tmp_path, "demo") == f"sha256:{expect}"

    def test_concat_boundary_collision_impossible(self, tmp_path):
        # 跨文件拼接歧义回归（CodeRabbit 2026-09-21）：旧算法 "ab"+"c" 与
        # "a"+"bc" 内容拼接相同 → hash 相同；manifest 化后必须区分
        make_skill(tmp_path, "one", body="ab", script="c")
        make_skill(tmp_path, "two", body="a", script="bc")
        assert (compute_content_hash(tmp_path, "one")
                != compute_content_hash(tmp_path, "two"))

    def test_rename_changes_hash(self, tmp_path):
        # 路径参与哈希回归：同内容改名（scripts/run.py → scripts/other.py）
        # 必须漂移（旧算法对单脚本改名不敏感）
        make_skill(tmp_path, "demo")
        before = compute_content_hash(tmp_path, "demo")
        run = tmp_path / "skills" / "demo" / "scripts" / "run.py"
        run.rename(run.with_name("other.py"))
        assert compute_content_hash(tmp_path, "demo") != before

    def test_derived_artifacts_do_not_disturb_hash(self, tmp_path):
        # 2026-09-21 plugin_lock 全红事故回归：重锁时工作区里 pytest 留下的
        # .pytest_cache（纯文本，静默入哈希）污染了 6 个 skill 的锁定指纹，
        # fresh clone 上 check 必红。派生产物必须完全排除在文件面之外。
        make_skill(tmp_path, "demo")
        before = compute_content_hash(tmp_path, "demo")
        scripts = tmp_path / "skills" / "demo" / "scripts"
        (scripts / ".pytest_cache").mkdir()
        (scripts / ".pytest_cache" / "lastfailed").write_text(
            '{"tests/test_run.py::test_x": true}', encoding="utf-8")
        (scripts / "__pycache__").mkdir()
        (scripts / "__pycache__" / "run.cpython-314.pyc").write_bytes(
            b"\xcb\x0d\x0d\x0a")
        (scripts / ".DS_Store").write_bytes(b"\x00\x00\x00Bud1")
        # 2026-09-24 PR #237 push 被拦实证：coverage 写 .coverage（SQLite
        # 二进制）、ruff 写 .ruff_cache/<ver>/<hash>，同属本地生灭物
        (scripts / ".coverage").write_bytes(b"SQLite format 3\x00\xbb\xa1")
        ruff = scripts / ".ruff_cache" / "0.16.8"
        ruff.mkdir(parents=True)
        (ruff / "11728694205254252146").write_bytes(b"\x00\x01\x02cache")
        nested = scripts / "sub" / "__pycache__"
        nested.mkdir(parents=True)
        (nested / "x.cpython-314.pyc").write_bytes(b"\xff\xfe")
        assert compute_content_hash(tmp_path, "demo") == before
        # 排除不得误伤合法文件：新增正常脚本仍必须改变哈希
        (scripts / "sub" / "real.py").write_text("z = 3\n", encoding="utf-8")
        assert compute_content_hash(tmp_path, "demo") != before

    def test_checkout_under_derived_dir_not_blanket_excluded(self, tmp_path):
        # Sourcery 评审回归（#229，2026-09-21）：_is_derived 只看 scripts_dir
        # 内相对段——仓库检出到名为 __pycache__ 的目录下时，scripts/** 不
        # 得因绝对路径祖先段命中被整体排除（否则脚本变更对锁与证据门不可见）。
        hostile = tmp_path / "__pycache__" / "repo"
        hostile.mkdir(parents=True)
        make_skill(hostile, "demo")
        make_skill(tmp_path, "demo")
        assert (compute_content_hash(hostile, "demo")
                == compute_content_hash(tmp_path, "demo"))
        # 恶意检出路径下排除不得误伤：脚本变更仍必须改变哈希
        run = hostile / "skills" / "demo" / "scripts" / "run.py"
        run.write_text("print('changed')\n", encoding="utf-8")
        assert (compute_content_hash(hostile, "demo")
                != compute_content_hash(tmp_path, "demo"))

    def test_b_path_evo_replay_hash_identical(self):
        # A/B 双算对拍：B 路 evo_replay.skill_content_hash（自包含分发故独立
        # 实现）必须与本路 compute_content_hash 对同一技能内容产出完全相同
        # 的指纹。2026-09-24 前两路算法漂移（字节拼接 vs manifest）无锚点
        # 拦截——本测试是唯一跨实现一致性守卫。
        import importlib.util
        repo = Path(__file__).resolve().parents[2]
        evo_scripts = repo / "skills" / "skill-evo" / "scripts"
        sys.path.insert(0, str(evo_scripts))
        try:
            spec = importlib.util.spec_from_file_location(
                "evo_replay_crosscheck", evo_scripts / "evo_replay.py")
            assert spec and spec.loader
            evo_replay = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(evo_replay)
        finally:
            sys.path.remove(str(evo_scripts))
        for skill in discover_evidence_skills(repo):
            assert (evo_replay.skill_content_hash(skill, root=repo)
                    == compute_content_hash(repo, skill)), skill

    def test_discovery_only_skills_with_scripts(self, tmp_path):
        make_skill(tmp_path, "alpha")
        make_skill(tmp_path, "beta")
        (tmp_path / "skills" / "plain").mkdir()  # 无 scripts/ → 不在校验面
        (tmp_path / "skills" / "loose.md").write_text("x")  # 散文件 → 忽略
        assert discover_evidence_skills(tmp_path) == ["alpha", "beta"]


class TestEvidenceGateQuadrants:
    """fail-closed 四象限：匹配放行 / hash 不匹配拦 / 证据缺失拦 / schema 不识别拦。"""

    def test_match_passes_with_checklist(self, tmp_path, capsys):
        make_skill(tmp_path, "api-guard")
        write_evidence(tmp_path, "api-guard")
        assert verify_skill_evidence(tmp_path) == 0
        out = capsys.readouterr().out
        # 契约清单格式钉死：skill → hash 前 12 位（sha256:+12hex）→ pass_cap_k
        assert re.search(r"api-guard\s+sha256:[0-9a-f]{12}\s+pass_cap_k=0\.67", out)

    def test_hash_mismatch_blocked(self, tmp_path, capsys):
        make_skill(tmp_path, "api-guard")
        write_evidence(tmp_path, "api-guard", content_hash="sha256:" + "0" * 64)
        assert verify_skill_evidence(tmp_path) == 2
        err = capsys.readouterr().err
        assert "api-guard" in err and "漂移" in err

    def test_content_drift_blocked(self, tmp_path, capsys):
        make_skill(tmp_path, "arch-guard")
        write_evidence(tmp_path, "arch-guard")
        (tmp_path / "skills" / "arch-guard" / "SKILL.md").write_text("# 改动\n")
        assert verify_skill_evidence(tmp_path) == 2
        assert "漂移" in capsys.readouterr().err

    def test_missing_evidence_blocked(self, tmp_path, capsys):
        make_skill(tmp_path, "ddl-guard")
        assert verify_skill_evidence(tmp_path) == 2
        err = capsys.readouterr().err
        assert "ddl-guard" in err and "缺失" in err
        assert "replay-evidence/ddl-guard.json" in err

    def test_unknown_schema_blocked(self, tmp_path, capsys):
        make_skill(tmp_path, "api-guard")
        write_evidence(tmp_path, "api-guard", schema="replay-evidence/2")
        assert verify_skill_evidence(tmp_path) == 2
        assert "schema" in capsys.readouterr().err


class TestEvidenceGateFailClosed:
    """契约其余拦截语义：JSON 损坏 / 字段缺失 / 类型不符 / 时间戳 / 指定集。"""

    def test_broken_json_blocked(self, tmp_path, capsys):
        make_skill(tmp_path, "doc-gen")
        p = (tmp_path / "skills" / "skill-evo" / "artifacts"
             / "replay-evidence" / "doc-gen.json")
        p.parent.mkdir(parents=True)
        p.write_text("{not json", encoding="utf-8")
        assert verify_skill_evidence(tmp_path) == 2
        assert "损坏" in capsys.readouterr().err

    def test_missing_field_blocked(self, tmp_path):
        make_skill(tmp_path, "impact-guard")
        write_evidence(tmp_path, "impact-guard", k=None)
        assert verify_skill_evidence(tmp_path) == 2

    def test_wrong_type_blocked(self, tmp_path):
        make_skill(tmp_path, "impact-guard")
        write_evidence(tmp_path, "impact-guard", pass_cap_k="0.67")
        assert verify_skill_evidence(tmp_path) == 2

    def test_bool_not_int_blocked(self, tmp_path):
        # bool 是 int 子类，字段校验必须排除
        make_skill(tmp_path, "impact-guard")
        write_evidence(tmp_path, "impact-guard", cases=True)
        assert verify_skill_evidence(tmp_path) == 2

    def test_bad_iso8601_blocked(self, tmp_path):
        make_skill(tmp_path, "impact-guard")
        write_evidence(tmp_path, "impact-guard", generated_at="2026-13-45 99:99")
        assert verify_skill_evidence(tmp_path) == 2

    def test_z_suffix_iso8601_accepted(self, tmp_path):
        make_skill(tmp_path, "impact-guard")
        write_evidence(tmp_path, "impact-guard", generated_at="2026-09-20T02:00:00Z")
        assert verify_skill_evidence(tmp_path) == 0

    def test_skill_field_mismatch_blocked(self, tmp_path):
        make_skill(tmp_path, "impact-guard")
        write_evidence(tmp_path, "impact-guard", skill="other-skill")
        assert verify_skill_evidence(tmp_path) == 2

    def test_non_utf8_content_blocked_cleanly(self, tmp_path, capsys):
        # 未知二进制（不在派生产物排除清单内，如误提交的 .bin）混入
        # scripts/** → 干净拦截（非 traceback）
        make_skill(tmp_path, "sourcery-autofix")
        write_evidence(tmp_path, "sourcery-autofix")
        (tmp_path / "skills" / "sourcery-autofix" / "scripts"
         / "blob.bin").write_bytes(b"\xff\xfe\x00")
        assert verify_skill_evidence(tmp_path) == 2
        err = capsys.readouterr().err
        assert "sourcery-autofix" in err and "UTF-8" in err
    def test_unreadable_evidence_blocked_cleanly(self, tmp_path, capsys):
        # 证据文件存在但不可读（权限）：归入错误清单而非 traceback（exit 码语义不混淆）
        make_skill(tmp_path, "api-guard")
        p = write_evidence(tmp_path, "api-guard")
        p.chmod(0o000)
        assert verify_skill_evidence(tmp_path) == 2
        err = capsys.readouterr().err
        assert "api-guard" in err and "不可读" in err

    def test_explicit_scope_partial_failure(self, tmp_path, capsys):
        make_skill(tmp_path, "api-guard")
        make_skill(tmp_path, "arch-guard")
        write_evidence(tmp_path, "api-guard")  # arch-guard 无证据
        assert verify_skill_evidence(tmp_path, skills=["arch-guard"]) == 2
        err = capsys.readouterr().err
        assert "arch-guard" in err and "api-guard" not in err

    def test_empty_scope_passes_with_note(self, tmp_path, capsys):
        assert verify_skill_evidence(tmp_path) == 0
        assert "无可校验对象" in capsys.readouterr().out


class TestEscapeHatch:
    def test_skip_env_passes_with_loud_warning(self, tmp_path, capsys, monkeypatch):
        make_skill(tmp_path, "api-guard")  # 无证据本应拦
        monkeypatch.setenv("RELEASE_EVIDENCE_SKIP", "1")
        assert verify_skill_evidence(tmp_path, allow_skip=True) == 0
        err = capsys.readouterr().err
        assert "RELEASE_EVIDENCE_SKIP" in err and "警告" in err

    def test_skip_env_powerless_without_test_injection(self, tmp_path, capsys,
                                                       monkeypatch):
        # 生产语义（CodeRabbit 2026-09-21）：不传 allow_skip（即 decide()/
        # --verify-evidence 的调用形状）时，环境变量不产生任何放行效果
        make_skill(tmp_path, "api-guard")  # 无证据
        monkeypatch.setenv("RELEASE_EVIDENCE_SKIP", "1")
        assert verify_skill_evidence(tmp_path) == 2
        assert "缺失" in capsys.readouterr().err


class TestDecideIntegration:
    """接入点：证据校验先于版本语义（先于 git/tag/catv），按登记集驱动。"""

    def test_evidence_failure_short_circuits_before_git(self, monkeypatch):
        monkeypatch.setattr(release_guard, "EVIDENCE_ENROLLED", ("api-guard",))

        def _boom():
            raise AssertionError("版本语义（git/tag）不应在证据门禁失败后执行")

        def _fail(**kw):
            # 发布路径只校验登记集内的 skill（显式 scope 传入）
            assert kw == {"skills": ["api-guard"]}
            return 2

        monkeypatch.setattr(release_guard, "latest_stable_tag", _boom)
        monkeypatch.setattr(release_guard, "verify_skill_evidence", _fail)
        assert release_guard.decide() == 2

    def test_decide_proceeds_to_existing_semantics_on_pass(self, monkeypatch):
        monkeypatch.setattr(release_guard, "EVIDENCE_ENROLLED", ("api-guard",))
        monkeypatch.setattr(release_guard, "verify_skill_evidence",
                            lambda **kw: 0)
        monkeypatch.setattr(release_guard, "latest_stable_tag", lambda: None)
        monkeypatch.setattr(release_guard, "interval_commits", lambda base: [])
        assert release_guard.decide() == 1  # 既有行为：空区间无可发布内容 → 拒绝

    def test_empty_enrollment_skips_gate_with_note(self, monkeypatch, capsys):
        # 登记集为空（初始态）：门已就位但不强制不可满足的证据要求，直接进版本语义
        monkeypatch.setattr(release_guard, "EVIDENCE_ENROLLED", ())
        monkeypatch.setattr(release_guard, "latest_stable_tag", lambda: None)
        monkeypatch.setattr(release_guard, "interval_commits", lambda base: [])
        assert release_guard.decide() == 1
        assert "EVIDENCE_ENROLLED" in capsys.readouterr().out


class TestMainDispatchReleaseIntent:
    """防误触门禁：catv 执行仅经 --release（npm run release）显式发起。

    负控制=首个用例：改动前裸调用直达 decide(check_only=False)，
    本组断言 check_only=True，旧代码必然失败（2026-09 两次实证：
    裸调用/帮助探针直接跑完 catv 提交版本 bump）。
    """

    def _spy_decide(self, monkeypatch):
        seen = []
        monkeypatch.setattr(release_guard, "decide",
                            lambda check_only: seen.append(check_only) or 0)
        return seen

    def test_bare_invocation_forced_check_only(self, monkeypatch, capsys):
        seen = self._spy_decide(monkeypatch)
        assert release_guard._main([]) == 0
        assert seen == [True]  # 门禁生效：未显式 --release 不给执行
        assert "--release" in capsys.readouterr().out

    def test_explicit_release_executes(self, monkeypatch, capsys):
        seen = self._spy_decide(monkeypatch)
        assert release_guard._main(["--release"]) == 0
        assert seen == [False]
        assert "--release" not in capsys.readouterr().out

    def test_check_flag_unchanged(self, monkeypatch, capsys):
        seen = self._spy_decide(monkeypatch)
        assert release_guard._main(["--check"]) == 0
        assert seen == [True]
        out = capsys.readouterr().out
        assert "未传 --release" not in out  # 显式只读不弹防误触提示

    def test_verify_evidence_route_bypasses_release_gate(self, monkeypatch):
        monkeypatch.setattr(release_guard, "verify_skill_evidence", lambda: 0)
        seen = self._spy_decide(monkeypatch)
        assert release_guard._main(["--verify-evidence"]) == 0
        assert seen == []  # 证据审计独立于发布门禁，不触发 decide
