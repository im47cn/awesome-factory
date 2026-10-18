"""账本主树锚定回归（源仓#73）：--ledger 覆盖默认跟随路径。

失效模式在「账本路径跟随被执行副本」——链 worktree 中 __file__ 落链树，
账本随链分支删除即丢，collect_pending 回潮重复开 PR。CLI 路径真跑
（gitenv 密闭），生产布局 = feedback.py 随仓分发进夹具；锚定表达式与
§9 接线沿用 backstop 测试哲学：正则提取分发脚本真实块（改脚本不改
测试即红），不做逻辑复制。运行：python3 -m pytest .factory/tests -q
"""
import json
import re
import subprocess
from pathlib import Path

from gitenv import git_env

FACTORY = Path(__file__).resolve().parents[1]

# feedback-upstream.sh 头部锚定三连赋值整体块（rev-parse 求值 + 判空拼
# 后缀 + 判空回退；值内无双引号，跨行匹配到收尾引号）
_ANCHOR = re.search(
    r'MAIN_FACTORY="\$\(git rev-parse[^"]+"\nMAIN_FACTORY=[^\n]+\nMAIN_FACTORY=[^\n]+',
    (FACTORY / "feedback-upstream.sh").read_text(encoding="utf-8"),
)[0]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=git_env(),
                          check=True, capture_output=True, text=True).stdout


def _mk_repo(tmp: Path) -> Path:
    """生产布局夹具：feedback.py + factory-local.json 随仓提交。import 期
    fail-closed 读 port_point（移植首提 SHA，反哺扫描下界）——先种子提交
    再记其 SHA，两步消解「首提内记录自身」自指；移植提交即待反哺候选
    （trailer 标记，HEAD）。"""
    repo = tmp / "repo"
    (repo / ".factory").mkdir(parents=True)
    (repo / ".factory" / "feedback.py").write_text(
        (FACTORY / "feedback.py").read_text(encoding="utf-8"), encoding="utf-8")
    env = git_env()
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t")):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                       capture_output=True)
    for path, text in ((repo / "seed.txt", "s\n"),):
        path.write_text(text, encoding="utf-8")
    for args in (("add", "-A"), ("commit", "-q", "-m", "seed")):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                       capture_output=True)
    (repo / ".factory" / "factory-local.json").write_text(json.dumps(
        {"upstream_repo": "o/r", "port_point": _sha(repo)}), encoding="utf-8")
    for args in (("add", "-A"),
                 ("commit", "-q", "-m", "port factory\n\nUpstream-Feedback: yes")):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                       capture_output=True)
    return repo


def _sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _py(cwd: Path, *args: str) -> str:
    return subprocess.run(["python3", ".factory/feedback.py", *args],
                          cwd=cwd, env=git_env(), check=True,
                          capture_output=True, text=True).stdout


def test_record_from_chain_wtree_writes_main_tree_ledger(tmp_path):
    repo = _mk_repo(tmp_path)
    sha = _sha(repo)
    assert sha in _py(repo, "pending")  # 前提：未记账时候选在列
    _git(repo, "worktree", "add", "-q", "-b", "chain", str(tmp_path / "chain"))
    chain = tmp_path / "chain"
    main_ledger = repo / ".factory" / "feedback-log.jsonl"
    _py(chain, "record", "https://x/p/1", f"{sha}:subj",
        "--ledger", str(main_ledger))
    # 账本落主树、链树无副本 → 删链分支不丢账本
    assert main_ledger.exists()
    assert not (chain / ".factory" / "feedback-log.jsonl").exists()
    _git(repo, "worktree", "remove", "--force", str(chain))
    _git(repo, "branch", "-qD", "chain")
    # 链分支已删仍不回潮：账本在主树生效（issue 验收 1）
    assert sha not in _py(repo, "pending")


def test_record_twice_idempotent_for_pending(tmp_path):
    repo = _mk_repo(tmp_path)
    sha = _sha(repo)
    main_ledger = repo / ".factory" / "feedback-log.jsonl"
    for _ in range(2):
        _py(repo, "record", "https://x/p/1", f"{sha}:subj",
            "--ledger", str(main_ledger))
    # 双记账按 SHA 集合去重，无双计数回潮（issue 验收 2）
    assert sha not in _py(repo, "pending", "--ledger", str(main_ledger))


def test_anchor_expression_resolves_main_tree(tmp_path):
    # 提取分发脚本真实锚定块（含判空拼接/回退两行），在链 worktree cwd
    # 求值 → 主树 .factory
    repo = _mk_repo(tmp_path)
    _git(repo, "worktree", "add", "-q", "-b", "chain", str(tmp_path / "chain"))
    out = subprocess.run(["bash", "-c", f'{_ANCHOR}; printf %s "$MAIN_FACTORY"'],
                         cwd=tmp_path / "chain", env=git_env(), check=True,
                         capture_output=True, text=True).stdout
    assert out == str(repo / ".factory")


def test_s9_wiring_anchored_commit_with_guards():
    """接线钉（grep 级）：六 py 调用点全锚定（F3：删一处 --ledger 全套
    仍绿即病态绿）、§9 提交锚 MAIN_WT + 双守卫 + pathspec + add -f
    （F1：ignored 裸 add 退出 1）+ 判空回退接线（F2）。"""
    src = (FACTORY / "feedback-upstream.sh").read_text(encoding="utf-8")
    assert src.count('--ledger "$LEDGER"') == 6  # 含 adapt-prep 惰性路径（F4）
    assert "add -f .factory/feedback-log.jsonl" in src
    assert 'git -C "$MAIN_WT" commit' in src
    assert "-- .factory/feedback-log.jsonl" in src
    assert "symbolic-ref --short HEAD" in src
    assert "locks/dispatcher" in src
    assert "MAIN_FACTORY:-$FACTORY" in src
