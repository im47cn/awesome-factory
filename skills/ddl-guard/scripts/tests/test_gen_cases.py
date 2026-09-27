#!/usr/bin/env python3
"""gen_cases.py 单元测试

覆盖生成器核心纯函数：模板变换、expected 渲染、编号分配、匿名化、
门禁验证（生成即验证：候选 case 实际检出 == 标注）。
运行: python3 -m pytest tests/test_gen_cases.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gen_cases as gc


class TestOps:
    def test_ops_sequential_replace(self):
        sql = "a b c"
        assert gc._ops(sql, [("a", "x"), ("c", "y")]) == "x b y"

    def test_ops_missing_fragment_raises(self):
        with pytest.raises(ValueError, match="模板片段未命中"):
            gc._ops("hello", [("world", "x")])

    def test_base_table_ops_all_hit(self):
        # 所有模板的替换片段必须命中 BASE_TABLE（防模板漂移）
        for _rule, _cid, _title, v_ops, b_ops, *_ in gc.TEMPLATES:
            gc._ops(gc.BASE_TABLE, v_ops)  # 不抛即命中
            if b_ops:
                gc._ops(gc.BASE_TABLE, b_ops)


class TestRenderExpected:
    def test_intercept_case(self):
        md = gc.render_expected("正·原子", "违规-禁用类型", ["禁用类型"], "规则模板")
        assert md.startswith("# ddl-guard badcase")
        assert "check: ddl_check.py" in md
        assert "## 预期检查输出" in md
        assert "- 脚本自动检出：禁用类型" in md

    def test_release_case(self):
        md = gc.render_expected("反·近边界", "近边界合规", [], "边界模板")
        assert "放行" in md
        assert "无脚本自动检出项" in md
        assert "脚本自动检出：" not in md

    def test_multi_rule_intercept(self):
        md = gc.render_expected("正·组合", "组合", ["禁用类型", "varchar长度"], "组合模板")
        assert "- 脚本自动检出：禁用类型、varchar长度" in md


class TestNextCaseNumber:
    def test_empty_dir_starts_at_10(self, tmp_path):
        assert gc.next_case_number(tmp_path) == 10

    def test_max_plus_one(self, tmp_path):
        (tmp_path / "005-x").mkdir()
        (tmp_path / "012-y").mkdir()
        assert gc.next_case_number(tmp_path) == 13

    def test_ignores_non_numeric(self, tmp_path):
        (tmp_path / "abc").mkdir()
        assert gc.next_case_number(tmp_path) == 10


class TestAnonymize:
    def test_table_and_field_renamed(self):
        sql = ("CREATE TABLE user_info (\n"
               "    id bigint(20) NOT NULL COMMENT '主键id',\n"
               "    user_name varchar(50) NOT NULL COMMENT '用户名'\n"
               ") COMMENT = '用户表';")
        out = gc.anonymize_sql(sql)
        assert "t_anon_1" in out
        assert "user_info" not in out
        assert "f_anon_1" in out
        assert "user_name" not in out

    def test_required_fields_kept(self):
        sql = ("CREATE TABLE t_a (\n"
               "    creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
               "    del_flag tinyint(4) NOT NULL COMMENT '删除标志[0-否,1-是]'\n"
               ") COMMENT = 'x';")
        out = gc.anonymize_sql(sql)
        assert "creator_id" in out
        assert "del_flag" in out

    def test_multiple_tables_distinct(self):
        sql = ("CREATE TABLE t_a (id bigint(20) NOT NULL COMMENT 'id');\n"
               "CREATE TABLE t_b (id bigint(20) NOT NULL COMMENT 'id');")
        out = gc.anonymize_sql(sql)
        assert "t_anon_1" in out and "t_anon_2" in out


class TestRunDdlCheck:
    def test_clean_base_no_issues(self, tmp_path):
        d = tmp_path / "input"
        d.mkdir()
        (d / "example.sql").write_text(gc.BASE_TABLE, encoding="utf-8")
        assert gc.run_ddl_check(d) == []

    def test_forbidden_type_detected(self, tmp_path):
        d = tmp_path / "input"
        d.mkdir()
        sql = gc.BASE_TABLE.replace(
            "order_status    varchar(10)", "data1           text")
        (d / "example.sql").write_text(sql, encoding="utf-8")
        assert gc.run_ddl_check(d) == ["禁用类型"]


class TestWriteCase:
    def test_writes_sql_and_expected(self, tmp_path):
        case_dir = gc.write_case(tmp_path, 10, "test-case", "标题",
                                 "CREATE TABLE t_a (id bigint(20));",
                                 ["禁用类型"], "正·原子", "来源")
        assert (case_dir / "input" / "example.sql").is_file()
        md = (case_dir / "expected.md").read_text(encoding="utf-8")
        assert "禁用类型" in md
        assert case_dir.name == "010-test-case"


class TestGenerateReject:
    """门禁拒绝路径：mock 检出失配 → 计数 + 清理。"""

    def test_dry_run_reject_counts(self, monkeypatch, tmp_path):
        def fake_check(d):
            return ["意外规则"]
        monkeypatch.setattr(gc, "run_ddl_check", fake_check)
        generated, rejected, detail = gc.generate(tmp_path, dry_run=True, verbose=False)
        assert rejected >= len(gc.TEMPLATES) + len(gc.COMBO_TEMPLATES)
        assert generated == 0

    def test_real_write_rejects_and_cleans(self, monkeypatch, tmp_path):
        def fake_check(d):
            return ["意外规则"]

        monkeypatch.setattr(gc, "run_ddl_check", fake_check)
        gc.generate(tmp_path, dry_run=False, verbose=False)
        # 全部拒绝 → 无 case 目录残留
        assert not list(tmp_path.iterdir())

    def test_real_write_succeeds(self, tmp_path):
        """非 dry-run 真实落盘：编号递增、四层全落。"""
        generated, rejected, detail = gc.generate(tmp_path, dry_run=False, verbose=False)
        assert rejected == 0
        assert generated == len(gc.TEMPLATES) + len(gc.CLEAN_TEMPLATES) + len(
            gc.COMBO_TEMPLATES) + sum(bool(b)
                                  for _r, _c, _t, _v, b, *_ in gc.TEMPLATES)
        dirs = sorted(p.name for p in tmp_path.iterdir())
        assert dirs[0].startswith("010-")
        assert dirs[-1] != dirs[0]

    def test_real_write_idempotent(self, tmp_path):
        """重复 generate 幂等：cid 复用编号覆盖重写，不翻倍追加。"""
        first, rej1, _ = gc.generate(tmp_path, dry_run=False, verbose=False)
        n1 = len(list(tmp_path.iterdir()))
        second, rej2, _ = gc.generate(tmp_path, dry_run=False, verbose=False)
        n2 = len(list(tmp_path.iterdir()))
        assert first == second == rej1 == rej2 == 0 or (first == second and rej1 == rej2 == 0)
        assert n1 == n2  # 目录数不变（无 010-072 之外的重复 case）
        # 每个 cid 仅一个目录
        cids = [p.name[4:] for p in tmp_path.iterdir()]
        assert len(cids) == len(set(cids))


class TestIngest:
    def _make_sql(self, d):
        d.mkdir(parents=True)
        (d / "real.sql").write_text(
            "CREATE TABLE yonghu (\n"
            "    id bigint(20) NOT NULL COMMENT '主键id',\n"
            "    status varchar(10) NOT NULL COMMENT '状态（启用）'\n"
            ") COMMENT = '表';", encoding="utf-8")
        return d / "real.sql"

    def test_dry_run(self, tmp_path):
        src = self._make_sql(tmp_path / "src")
        generated, rejected, detail = gc.ingest(
            tmp_path / "src", tmp_path / "out", dry_run=True, verbose=False)
        assert generated == 1 and rejected == 0

    def test_real_write(self, tmp_path):
        self._make_sql(tmp_path / "src")
        generated, rejected, _ = gc.ingest(
            tmp_path / "src", tmp_path / "out", dry_run=False, verbose=False)
        assert generated == 1 and rejected == 0
        case_dir = next((tmp_path / "out").iterdir())
        md = (case_dir / "expected.md").read_text(encoding="utf-8")
        assert "全角字符" in md  # （启用）全角括号检出
        assert "脚本自动检出：" in md


class TestRunDdlCheckErrors:
    def test_rc_error_raises(self, monkeypatch, tmp_path):
        class FakeProc:
            returncode = 2
            stdout = ""
            stderr = "boom"
        monkeypatch.setattr(gc.subprocess, "run",
                            lambda *a, **k: FakeProc())
        with pytest.raises(RuntimeError, match="ddl_check 失败"):
            gc.run_ddl_check(tmp_path)

    def test_non_json_raises(self, monkeypatch, tmp_path):
        class FakeProc:
            returncode = 0
            stdout = "not json"
            stderr = ""
        monkeypatch.setattr(gc.subprocess, "run",
                            lambda *a, **k: FakeProc())
        with pytest.raises(RuntimeError, match="非 JSON"):
            gc.run_ddl_check(tmp_path)


    def test_generate_verbose_reject_exit1(self, monkeypatch, capsys):
        monkeypatch.setattr(gc, "generate",
                            lambda out, dry, ver: (0, 3, ["✗ atomic-x: 期望 ['a'] 实际 ['b']"]))
        monkeypatch.setattr(sys, "argv", [
            "gen_cases.py", "generate", "--dry-run", "--verbose"])
        assert gc.main() == 1
        out = capsys.readouterr().out
        assert "拒绝明细" in out and "✗ atomic-x" in out

    def test_ingest_cmd_branch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gc, "ingest", lambda d, o, dry, ver: (1, 0, []))
        monkeypatch.setattr(sys, "argv", [
            "gen_cases.py", "ingest", "--dir", str(tmp_path)])
        assert gc.main() == 0


class TestVerboseAndErrorBranches:
    def test_generate_verbose_output(self, capsys):
        gc.generate(gc._SCRIPT_DIR.parent / "badcase", dry_run=True, verbose=True)
        out = capsys.readouterr().out
        assert "✓" in out

    def test_ingest_verbose_dry_run(self, tmp_path):
        d = tmp_path / "src"
        d.mkdir()
        (d / "a.sql").write_text(
            "CREATE TABLE t_a (id bigint(20) NOT NULL COMMENT '主键id');",
            encoding="utf-8")
        generated, rejected, _ = gc.ingest(d, tmp_path / "out",
                                           dry_run=True, verbose=True)
        assert generated == 1 and rejected == 0

    def test_ingest_verbose_real(self, tmp_path):
        d = tmp_path / "src"
        d.mkdir()
        (d / "a.sql").write_text(
            "CREATE TABLE t_a (id bigint(20) NOT NULL COMMENT '主键id');",
            encoding="utf-8")
        generated, rejected, _ = gc.ingest(d, tmp_path / "out",
                                           dry_run=False, verbose=True)
        assert generated == 1 and rejected == 0

    def test_ingest_check_error_skips(self, monkeypatch, tmp_path):
        d = tmp_path / "src"
        d.mkdir()
        (d / "a.sql").write_text("CREATE TABLE t_a (id bigint(20));",
                                 encoding="utf-8")
        def boom(p):
            raise RuntimeError("ddl_check 崩溃")
        monkeypatch.setattr(gc, "run_ddl_check", boom)
        generated, rejected, _ = gc.ingest(d, tmp_path / "out",
                                           dry_run=False, verbose=True)
        assert generated == 0 and rejected == 1
