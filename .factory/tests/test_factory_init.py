"""test_factory_init —— tools/factory-init.py 接入 CLI 的 E2E 套件。

先行失败清单（2026-10-12，测试先于实现——本文件先红后绿）：

输入边界:
  F1  init 目标路径不存在 → exit 2，零写入
  F2  init 目标非 git 仓 → exit 2
  F3  目标已有 .factory/ 且异源（MISSION 无生成标记）→ exit 2 拒绝
  F4  目标已有非工厂生成的 MISSION.md → exit 2 拒绝覆盖（数据丢失路径）
  F5  --perimeter 含占位符 `<…>` → exit 2
  F6  --perimeter 路径在目标仓不存在 → exit 2 且列缺失清单
  F7  周界无目录条目 / 缺 G-02 载体（非 MISSION 周界文件）/ 缺 B-01 载体
      （周界外真实文档）→ exit 2，指明缺什么
  F8  未知子命令 / 缺参 → exit 2 + usage
  F9  第一段幂等：重跑重生成骨架，字节不变，exit 1
  F10 doctor：注册表含注释/空行/坏路径 → 逐项 ✗ 报告不崩溃（exit 1 非 2）

信任边界:
  F11 doctor 零凭据值：plist 凭据只探存在性，stdout 不含值子串
  F12 零越界写：除注册表（--no-register 关断）外不写目标仓与上游仓之外；
      上游仓工作树跑完保持 porcelain 干净
  F13 生成配置自洽：第二段完成后 guard.self_check 真跑 rc=0
      （MISSION 与 factory-local.json 周界同源生成，结构性一致）
  F14 .factory/ 与 MISSION.md 未在 --perimeter 中 → 自动补入并告知
      （治理不可自改的机械化前提）
  F20 Sourcery 阻断回归：可伪造 MARKER + 自带 .factory/guard.py →
      第二段执行前须对上游 full 面验源（字节一致），不一致 exit 2 零执行

并发/重复:
  F15 注册表注册幂等：第二段重跑（已完成态重证）不产生重复行

数据丢失:
  F16 目标仓既有文件字节不变（full 面只写 .factory/，骨架只写 MISSION.md）
  F17 skip 面零拷贝：上游 DISTRIBUTION.json / README.md / artifacts / locks /
      上游 defects.json 锚点（B-101 等）不出现在目标
  F18 验证失败（mutations 有缺陷注入失败）→ exit 2 且不注册、不建标签
      ——半成品不得宣告完成
  F19 骨架确定性：同输入两次生成字节一致（禁时间戳/随机）

E2E:
  E1  fixture 空仓两段式全流程真跑（拷贝 → 骨架 → 填充 → self_check →
      mutations kill-rate 真跑）→ 产物对 golden 字节比对
  E2  可重跑命令 = 本文件：python3 -m pytest .factory/tests/test_factory_init.py
      -o addopts= -q（重跑两次结果一致；产物内容确定性由 F19/E1 保证）

分发约束：tools/ 不在 DISTRIBUTION full 面——下游仓 running 本文件时
工具不存在，skipif（对齐 test_sourcery_gate 仓侧闸先例）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "factory-init.py"

if not TOOL.exists():
    # 模块级守卫：skipif 只护执行期，模块级 read_text 在收集期先炸（下游
    # 整目录收集即 error）——allow_module_level 整文件跳过
    pytest.skip("tools/factory-init.py 不在本仓（下游分发面）", allow_module_level=True)

REGISTRY_ENV = "FACTORY_REGISTRY"
PLIST_ENV = "FACTORY_HUB_PLIST"

LABELS = ("factory:accepted", "factory:rejected", "factory:needs-review", "factory:in-progress")

# 生成标记值从工具源码提取（不硬编码副本，防双真相源漂移）
MARKER_VAL = re.search(r'MARKER = "([^"]+)"', TOOL.read_text(encoding="utf-8"))[1]


def run_tool(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | (env_extra or {})
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, env=env, timeout=600,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )


def make_fixture(tmp_path: Path, name: str = "fixture-repo") -> Path:
    """空仓 + 最小业务文件（周界内外齐备：docs//src/ 在周界，CHANGELOG 在周界外）。"""
    repo = tmp_path / name
    (repo / "docs").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "README.md").write_text(f"# {name}\n\n业务自述文件（周界外文档）。\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(f"# {name} 变更日志\n\n- 0.1.0 初版。\n", encoding="utf-8")
    (repo / "docs" / "spec.md").write_text("# 规范\n\n正文。\n", encoding="utf-8")
    (repo / "src" / "main.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".factory/locks/\n*.pyc\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return repo


def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        REGISTRY_ENV: str(tmp_path / "registry" / "repos.conf"),
        PLIST_ENV: str(tmp_path / "empty.plist"),
    }


PERIMETER_ARG = "docs/,src/,MISSION.md,.gitignore"


class TestInputBoundaries:
    """F1-F9 输入边界。"""

    def test_f1_nonexistent_path(self, tmp_path):
        r = run_tool(["init", str(tmp_path / "nope")], base_env(tmp_path))
        assert r.returncode == 2
        assert not (tmp_path / "nope").exists()

    def test_f2_not_git_repo(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        r = run_tool(["init", str(plain)], base_env(tmp_path))
        assert r.returncode == 2
        assert not (plain / ".factory").exists()

    def test_f3_existing_foreign_factory(self, tmp_path):
        repo = make_fixture(tmp_path)
        (repo / ".factory").mkdir()
        (repo / ".factory" / "README.md").write_text("别人的安装\n", encoding="utf-8")
        r = run_tool(["init", str(repo)], base_env(tmp_path))
        assert r.returncode == 2
        assert (repo / ".factory" / "README.md").read_text(encoding="utf-8") == "别人的安装\n"

    def test_f4_foreign_mission_refused(self, tmp_path):
        repo = make_fixture(tmp_path)
        (repo / "MISSION.md").write_text("# 人类手写的宪法\n", encoding="utf-8")
        r = run_tool(["init", str(repo)], base_env(tmp_path))
        assert r.returncode == 2
        assert (repo / "MISSION.md").read_text(encoding="utf-8") == "# 人类手写的宪法\n"

    def test_f5_placeholder_perimeter(self, tmp_path):
        repo = make_fixture(tmp_path)
        r = run_tool(["init", str(repo), "--perimeter", "docs/,<待填写>"], base_env(tmp_path))
        assert r.returncode == 2

    def test_f6_perimeter_path_missing(self, tmp_path):
        repo = make_fixture(tmp_path)
        r = run_tool(["init", str(repo), "--perimeter", "docs/,ghost-dir/"], base_env(tmp_path))
        assert r.returncode == 2
        assert "ghost-dir" in r.stdout + r.stderr

    def test_f7_missing_carriers(self, tmp_path):
        # 无目录条目 → M-01/M-02 锚点不可派生
        repo = make_fixture(tmp_path)
        r = run_tool(["init", str(repo), "--perimeter", "README.md"], base_env(tmp_path))
        assert r.returncode == 2
        # 全部周界文件都是 MISSION.md → G-02 无载体
        repo2 = make_fixture(tmp_path, "fixture-repo2")
        r2 = run_tool(["init", str(repo2), "--perimeter", "MISSION.md"], base_env(tmp_path))
        assert r2.returncode == 2

    def test_f8_usage(self, tmp_path):
        assert run_tool([]).returncode == 2
        assert run_tool(["wat"]).returncode == 2
        assert run_tool(["init"]).returncode == 2

    def test_f9_phase1_idempotent(self, tmp_path):
        repo = make_fixture(tmp_path)
        r1 = run_tool(["init", str(repo), "--upstream", "/fake/upstream"], base_env(tmp_path))
        assert r1.returncode == 1
        m1 = (repo / "MISSION.md").read_bytes()
        f1 = (repo / ".factory" / "factory-local.json").read_bytes()
        r2 = run_tool(["init", str(repo), "--upstream", "/fake/upstream"], base_env(tmp_path))
        assert r2.returncode == 1
        assert (repo / "MISSION.md").read_bytes() == m1
        assert (repo / ".factory" / "factory-local.json").read_bytes() == f1

    def test_f10_doctor_bad_registry(self, tmp_path):
        reg = tmp_path / "registry" / "repos.conf"
        reg.parent.mkdir(parents=True)
        reg.write_text("# 注释\n\n/not/a/repo\n", encoding="utf-8")
        r = run_tool(["doctor"], {PLIST_ENV: str(tmp_path / "empty.plist"), REGISTRY_ENV: str(reg)})
        assert r.returncode == 1  # 有 ✗ 但自身未崩
        assert "/not/a/repo" in r.stdout


class TestTrustBoundaries:
    """F11-F14 信任边界。"""

    def test_f11_doctor_never_prints_secret_values(self, tmp_path):
        plist = tmp_path / "hub.plist"
        secret = "pt-super-secret-token-value-xyz"
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            f'<key>EnvironmentVariables</key><dict><key>YUNXIAO_ACCESS_TOKEN</key>'
            f'<string>{secret}</string></dict>'
            "</dict></plist>\n",
            encoding="utf-8",
        )
        r = run_tool(["doctor"], base_env(tmp_path) | {PLIST_ENV: str(plist)})
        assert secret not in r.stdout
        assert "YUNXIAO_ACCESS_TOKEN" in r.stdout  # 键名可见，值不可见

    def test_f12_no_write_outside_target(self, tmp_path):
        repo = make_fixture(tmp_path)
        before = git(REPO_ROOT, "status", "--porcelain").stdout
        run_tool(["init", str(repo), "--upstream", "/fake/upstream", "--no-register"],
                 base_env(tmp_path))
        run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                  "--upstream", "/fake/upstream", "--no-register"], base_env(tmp_path))
        after = git(REPO_ROOT, "status", "--porcelain").stdout
        assert after == before  # 上游仓零污染（本套件自身文件变更由 Git 层管辖）

    def test_f13_selfcheck_green_after_phase2(self, tmp_path):
        repo = make_fixture(tmp_path)
        run_tool(["init", str(repo), "--upstream", "/fake/upstream"], base_env(tmp_path))
        run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                  "--upstream", "/fake/upstream", "--no-register"], base_env(tmp_path))
        chk = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.factory'); import guard; guard.self_check(); print('OK')"],
            capture_output=True, text=True, cwd=str(repo),
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
        assert chk.returncode == 0, chk.stderr

    def test_f20_forgeable_marker_no_exec(self, tmp_path):
        """Sourcery 阻断回归：可伪造的 MARKER + 自带 .factory/guard.py 不得被执行。
        现有 .factory 与上游 full 面不一致（或缺失）→ exit 2，恶意代码零执行。"""
        repo = make_fixture(tmp_path)
        (repo / "MISSION.md").write_text(
            f"# 宪法\n\n<!-- {MARKER_VAL} -->\n", encoding="utf-8")
        attacker_factory = repo / ".factory" / "mutations"
        attacker_factory.mkdir(parents=True)
        # 拷真上游 guard.py 再注入恶意尾码——模拟攻击者在标记仓内预置执行体
        guard = repo / ".factory" / "guard.py"
        guard.write_text(
            (REPO_ROOT / ".factory" / "guard.py").read_text(encoding="utf-8")
            + "\nimport pathlib; pathlib.Path('PWNED').write_text('x')\n",
            encoding="utf-8")
        r = run_tool(["init", str(repo), "--perimeter", "docs/,docs/spec.md,MISSION.md",
                      "--no-labels", "--no-register", "--upstream", "/fake/upstream"],
                     base_env(tmp_path))
        assert r.returncode == 2, r.stdout + r.stderr
        assert not (repo / "PWNED").exists()

    def test_f14_auto_ensure_governance_perimeter(self, tmp_path):
        repo = make_fixture(tmp_path)
        run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                  "--upstream", "/fake/upstream", "--no-register"], base_env(tmp_path))
        mission = (repo / "MISSION.md").read_text(encoding="utf-8")
        assert "- `MISSION.md`" in mission
        assert "- `.factory/`" in mission


class TestConcurrencyAndDataLoss:
    """F15-F19 并发重复与数据丢失。"""

    def _complete(self, repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
        return run_tool(
            ["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
             "--upstream", "/fake/upstream", "--no-register"],
            {**base_env(tmp_path), REGISTRY_ENV: str(tmp_path / "reg" / "repos.conf")},
        )

    def test_f15_registry_idempotent(self, tmp_path):
        repo = make_fixture(tmp_path)
        env = {**base_env(tmp_path), REGISTRY_ENV: str(tmp_path / "reg" / "repos.conf")}
        run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                  "--upstream", "/fake/upstream"], env)
        run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                  "--upstream", "/fake/upstream"], env)
        lines = (tmp_path / "reg" / "repos.conf").read_text(encoding="utf-8").splitlines()
        assert len([ln for ln in lines if ln.strip()]) == 1

    def test_f16_existing_files_untouched(self, tmp_path):
        repo = make_fixture(tmp_path)
        before = {
            p: p.read_bytes()
            for p in (repo / "README.md", repo / "CHANGELOG.md", repo / "docs" / "spec.md",
                      repo / "src" / "main.py", repo / ".gitignore")
        }
        self._complete(repo, tmp_path)
        for p, content in before.items():
            assert p.read_bytes() == content, p

    def test_f17_skip_face_not_copied(self, tmp_path):
        repo = make_fixture(tmp_path)
        self._complete(repo, tmp_path)
        f = repo / ".factory"
        assert not (f / "DISTRIBUTION.json").exists()
        assert not (f / "README.md").exists()
        assert not (f / "artifacts").exists()
        assert not (f / "locks").exists()
        defects = json.loads((f / "mutations" / "defects.json").read_text(encoding="utf-8"))
        ids = {d["id"] for d in defects["defects"]}
        assert "B-101" not in ids  # 上游仓特定锚点不得随拷贝流入

    def test_f18_gate_fail_no_register(self, tmp_path):
        # 重复周界条目 → MISSION 行重复 → M-01/M-02 锚点 count≠1 →
        # mutations FAIL-config → exit 2 且不注册（锚点唯一性由门收口，
        # 锚点扫描修复后 B-01 侧不再有廉价触发器）
        repo = make_fixture(tmp_path)
        env = {**base_env(tmp_path), REGISTRY_ENV: str(tmp_path / "reg2" / "repos.conf")}
        r = run_tool(["init", str(repo), "--perimeter", "docs/,docs/,MISSION.md,.gitignore",
                      "--no-labels", "--upstream", "/fake/upstream"], env)
        assert r.returncode == 2
        assert not (tmp_path / "reg2").exists()

    def test_f19b_frontmatter_anchor(self, tmp_path):
        """真实仓试跑回归（archify DESIGN.md 首行 `---` ×2）：锚点派生须
        跳过非唯一行，取前 scan 行中恰好出现一次的行。"""
        repo = tmp_path / "fm-repo"
        (repo / "docs").mkdir(parents=True)
        (repo / "README.md").write_text("# fm-repo\n\n自述。\n", encoding="utf-8")
        (repo / "docs" / "spec.md").write_text(
            "---\ntitle: 规范\n---\n\n# 规范\n\n正文。\n", encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        r = run_tool(["init", str(repo), "--perimeter", "docs/,docs/spec.md,MISSION.md",
                      "--no-labels", "--no-register",
                      "--upstream", "/fake/upstream"], base_env(tmp_path))
        assert r.returncode == 0, r.stdout + r.stderr
        defects = json.loads(
            (repo / ".factory" / "mutations" / "defects.json").read_text(encoding="utf-8"))
        g02 = next(d for d in defects["defects"] if d["id"] == "G-02")
        assert g02["find"] != "---"  # front matter 分隔符（×2 非唯一）不得当选

    def test_f19_skeleton_deterministic(self, tmp_path):
        repo_a = make_fixture(tmp_path, "det-a")
        repo_b = make_fixture(tmp_path, "det-b")
        run_tool(["init", str(repo_a), "--upstream", "/fake/upstream"], base_env(tmp_path))
        run_tool(["init", str(repo_b), "--upstream", "/fake/upstream"], base_env(tmp_path))
        # 仓名不同属预期差异：改名后比对
        a = (repo_a / "MISSION.md").read_text(encoding="utf-8").replace("det-a", "X")
        b = (repo_b / "MISSION.md").read_text(encoding="utf-8").replace("det-b", "X")
        assert a == b
        fa = (repo_a / ".factory" / "factory-local.json").read_text(encoding="utf-8").replace("det-a", "X")
        fb = (repo_b / ".factory" / "factory-local.json").read_text(encoding="utf-8").replace("det-b", "X")
        assert fa == fb


class TestGoldenE2E:
    """E1 两段式全流程 + golden 字节比对（确定性产物锁存）。"""

    GOLDEN = Path(__file__).resolve().parent / "golden" / "factory-init"

    def test_e1_full_flow_golden(self, tmp_path):
        repo = make_fixture(tmp_path)
        r1 = run_tool(["init", str(repo), "--upstream", "/fake/upstream"], base_env(tmp_path))
        assert r1.returncode == 1
        phase1_mission = (repo / "MISSION.md").read_bytes()
        r2 = run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                       "--upstream", "/fake/upstream", "--no-register"], base_env(tmp_path))
        assert r2.returncode == 0, r2.stdout + r2.stderr

        pairs = [
            ("mission-phase1.md", phase1_mission),
            ("MISSION.md", (repo / "MISSION.md").read_bytes()),
            ("factory-local.json", (repo / ".factory" / "factory-local.json").read_bytes()),
            ("defects.json", (repo / ".factory" / "mutations" / "defects.json").read_bytes()),
        ]
        for golden_name, actual in pairs:
            golden = self.GOLDEN / golden_name
            if golden.exists():
                assert actual == golden.read_bytes(), f"golden 漂移: {golden_name}"
            else:
                golden.parent.mkdir(parents=True, exist_ok=True)
                golden.write_bytes(actual)
                pytest.fail(f"golden 缺失已生成待人工审核: {golden}")

        # kill rate 实证：evidence-stamp 绑定要求 factory-local.json 已跟踪
        # （perimeter_blob 走 git ls-files——未跟踪不写 stamp 是诚实语义）。
        # 落库后重跑第二段 = 已完成态全量重证（顺带覆盖幂等重证路径）。
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "s0")
        r3 = run_tool(["init", str(repo), "--perimeter", PERIMETER_ARG, "--no-labels",
                       "--upstream", "/fake/upstream", "--no-register"], base_env(tmp_path))
        assert r3.returncode == 0, r3.stdout + r3.stderr
        stamp = json.loads(
            (repo / ".factory" / "mutations" / "evidence-stamp.json").read_text(encoding="utf-8"))
        assert stamp.get("perimeter_blob")
