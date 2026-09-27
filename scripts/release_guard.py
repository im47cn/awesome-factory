#!/usr/bin/env python3
"""release 防呆：拦截 commit-and-tag-version 的 0.x preMajor 语义与仓库惯例冲突。

根因（2026-08-26 v0.4.1 事故实证）：
  catv 13.x bumpVersion 对 currentVersion < 1.0.0 强制 presetOptions.preMajor=true
  （无 CLI 开关），preMajor 规则 level<2 → level++，即 0.x 阶段 feat 降为 patch、
  breaking 降为 minor。本仓惯例是 pre-1.0 仍按常规语义发布（v0.3.0/v0.4.0/v0.5.0
  均为 feat 集合的 minor 发布）。实测证据：区间 23 个 feat，catv reason 正确计数
  却返回 level=2 → 0.4.1。

行为：
  0. 评测证据门禁（先行，fail-closed）：登记集 EVIDENCE_ENROLLED 内的 skill 必须
     携带与最新内容 hash 绑定的 replay-eval 证据（skills/skill-evo/artifacts/
     replay-evidence/<skill>.json）；缺失 / 内容漂移 / schema 不识别 /
     字段损坏一律拦截（exit 2）。登记集初始为空（政策见常量定义处）；
     --verify-evidence 子命令仍按全量 scope 审计。唯一逃逸
     RELEASE_EVIDENCE_SKIP=1 仅限测试注入（allow_skip=True 参数 +
     环境变量同时成立才生效；decide()/--verify-evidence 生产入口
     一律不传 allow_skip，环境变量在发布路径不产生任何放行效果）。
  1. 按仓库惯例独立计算期望 bump（常规语义，无 preMajor 降级）
  2. catv --dry-run 取工具目标版本
  3. 一致 → 原生执行；不一致 → 打印原因并 --release-as <期望> 纠偏执行
  区间无可发布提交 → 拒绝发布退出（防误发空版本）。

用法：
  npm run release              # 防呆发布（唯一发布入口，显式 --release）
  python3 scripts/release_guard.py          # 裸调用=只报告决策，不执行（防误触）
  python3 scripts/release_guard.py --check   # 同上，显式只读
  python3 scripts/release_guard.py --release  # 直接执行发布（等价 npm run release）
  python3 scripts/release_guard.py --verify-evidence   # 单跑证据门禁
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_HEADER_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s+\S")
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def sh(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def latest_stable_tag() -> str | None:
    """HEAD 可达的最新 v* semver tag（与 catv skipUnstable 语义对齐）。"""
    tags = [t for t in sh("git", "tag", "-l", "v*").splitlines() if _SEMVER_RE.match(t)]
    if reachable := [
        t
        for t in tags
        if subprocess.run(
            ["git", "merge-base", "--is-ancestor", t, "HEAD"],
            cwd=REPO,
            capture_output=True,
        ).returncode == 0
    ]:
        return sorted(
            reachable, key=lambda t: tuple(int(x) for x in t[1:].split("."))
        )[-1]
    else:
        return None


def current_version() -> str:
    import json
    return json.loads((REPO / "package.json").read_text())["version"]


def expected_bump(commits: list[dict]) -> str | None:
    """仓库惯例（常规语义，0.x 不降级）：breaking→major, feat→minor, fix/perf→patch。

    commits 项: {"header": str, "body": str}
    """
    level = None  # 0=major 1=minor 2=patch
    for c in commits:
        m = _HEADER_RE.match(c["header"])
        if not m:
            continue
        breaking = bool(m.group("bang")) or "BREAKING CHANGE" in (c["body"] or "") \
            or "BREAKING-CHANGE" in (c["body"] or "")
        if breaking:
            level = 0
        elif level in (None, 2) and m.group("type") in ("feat", "feature"):
            level = 1
        elif level is None and m.group("type") in ("fix", "perf"):
            level = 2
    return None if level is None else ("major", "minor", "patch")[level]


def bump_version(base: str, bump: str) -> str:
    major, minor, patch = (int(x) for x in base.lstrip("v").split("."))
    return {
        "major": f"{major + 1}.0.0",
        "minor": f"{major}.{minor + 1}.0",
        "patch": f"{major}.{minor}.{patch + 1}",
    }[bump]


def parse_catv_target(dry_output: str) -> str | None:
    """从 catv dry-run 输出抓目标版本：`bumping version in package.json from A to B`。"""
    m = re.search(r"bumping version in package\.json from \S+ to (\S+)", dry_output)
    return m[1] if m else None


def interval_commits(base: str | None) -> list[dict]:
    rng = f"{base}..HEAD" if base else "HEAD"
    raw = sh("git", "log", rng, "--no-merges", "--format=%s%x01%b%x01")
    commits = []
    for entry in raw.split("\x01\n"):
        if not entry.strip():
            continue
        parts = entry.split("\x01", 1)
        commits.append({"header": parts[0], "body": parts[1] if len(parts) > 1 else ""})
    return commits


# ---------------------------------------------------------------------------
# 评测证据门禁（Comet 借鉴点 #4，@date 2026-09-20）
# 发布物 = 技能内容；技能内容变更必须携带对应 replay-eval 证据才能过发布门。
# 契约（与 B 路 skill-evo 产出统一，一字不差，冲突必须上报）：
#   content_hash = sha256( 逐文件摘要清单；条目 = "<文件内容 sha256 hex>␣␣<skill 内相对 posix 路径>\n"，
#                          按路径字典序排列后整体拼接——路径与边界参与哈希，
#                          文件改名/增删/跨文件内容重排（"ab"+"c" vs "a"+"bc"）均改变 hash )
#   文件面       = SKILL.md + scripts/**，排除派生产物（.pytest_cache/、__pycache__、
#                  *.pyc、.DS_Store、.coverage、.ruff_cache/）——本地测试生灭物
#                  不得扰动指纹（2026-09-21 事故；2026-09-24 PR #237 增补后两项）
#   evidence     = skills/skill-evo/artifacts/replay-evidence/<skill>.json
# ---------------------------------------------------------------------------

EVIDENCE_SCHEMA = "replay-evidence/1"
EVIDENCE_DIR = Path("skills") / "skill-evo" / "artifacts" / "replay-evidence"
_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SKIP_ENV = "RELEASE_EVIDENCE_SKIP"


_DERIVED_PARTS = frozenset({".pytest_cache", "__pycache__", ".DS_Store",
                            ".coverage", ".ruff_cache"})
_DERIVED_SUFFIXES = (".pyc",)


def _is_derived(path: Path) -> bool:
    """派生产物判定：任一路径段命中缓存/系统目录名，或 *.pyc 后缀。

    这些文件随本地测试运行生灭（pytest 写 .pytest_cache/、解释器写
    __pycache__/*.pyc、Finder 写 .DS_Store、coverage 写 .coverage、
    ruff 写 .ruff_cache/），不属于 skill 源文件面，
    计入哈希会让指纹随工作区卫生状况漂移（2026-09-21 plugin_lock
    全红事故：重锁时缓存被静默吞入锁定值）。其余未知二进制不在排除
    之列，仍由读取期 UTF-8 校验干净拦截。

    path 须为枚举根（scripts_dir）内的相对路径：检出位置的祖先段不
    参与匹配——仓库检出到名为 __pycache__ 等派生产物目录下时，
    scripts/** 不得被整体误判排除（Sourcery #229 评审，2026-09-21）。
    """
    return (any(part in _DERIVED_PARTS for part in path.parts)
            or path.suffix in _DERIVED_SUFFIXES)


def skill_source_files(repo_root: Path, skill: str) -> list[Path]:
    """参与 content_hash 的文件面：SKILL.md + scripts/**（路径字典序）。

    scripts/** 排除派生产物（_is_derived，含嵌套路径段）：指纹只反映
    源文件，跨机器、跨工作区卫生状况确定。

    排序键为仓库相对 POSIX 路径（同前缀下 SKILL.md 因大写 S 先于 scripts/）。
    """
    base = repo_root / "skills" / skill
    files = [base / "SKILL.md"]
    scripts_dir = base / "scripts"
    if scripts_dir.is_dir():
        files.extend(
            p for p in scripts_dir.rglob("*")
            if p.is_file() and not _is_derived(p.relative_to(scripts_dir)))
    return sorted(
        (p for p in files if p.is_file()),
        key=lambda p: p.relative_to(repo_root).as_posix())


def compute_content_hash(repo_root: Path, skill: str) -> str:
    """契约 content_hash：逐文件摘要清单（sha256sum 风格）整体 sha256。

    manifest 条目 = "{文件内容 sha256 hex}  {skill 内相对 posix 路径}\\n"，
    按 skill_source_files 的路径字典序逐条拼接后整体取 sha256。路径与
    边界参与哈希：文件改名、增删、跨文件内容重排（如 "ab"+"c" vs
    "a"+"bc"）都会改变 hash。非 UTF-8 文件在读取期直接抛错（由调用方
    归入拦截），不静默降级。
    """
    base = repo_root / "skills" / skill
    manifest = "".join(
        f"{hashlib.sha256(p.read_text(encoding='utf-8').encode('utf-8')).hexdigest()}"
        f"  {p.relative_to(base).as_posix()}\n"
        for p in skill_source_files(repo_root, skill))
    return "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def discover_evidence_skills(repo_root: Path) -> list[str]:
    """需携带评测证据的 skill 集：skills/ 下含 scripts/ 子目录者（字典序）。"""
    root = repo_root / "skills"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and (d / "scripts").is_dir())


def evidence_path(repo_root: Path, skill: str) -> Path:
    return repo_root / EVIDENCE_DIR / f"{skill}.json"


def _check_iso8601(ts: str) -> bool:
    # Python 3.9 fromisoformat 不认 Z 后缀，先归一化为 +00:00
    try:
        datetime.datetime.fromisoformat(f"{ts[:-1]}+00:00" if ts.endswith("Z") else ts)
        return True
    except ValueError:
        return False


def _is_prob(v: object) -> bool:
    """概率字段：数值（排除 bool 这一 int 子类）且落在 [0, 1]。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1


def _validate_evidence(ev: object, skill: str, expect_hash: str) -> list[str]:
    """单份证据的字段/类型/绑定校验，返回中文错误清单（空 = 通过）。

    schema 不识别即失败、不猜测；未知额外字段放行（版本演进由 schema 把关）。
    """
    if not isinstance(ev, dict):
        return [f"{skill}: 证据不是 JSON 对象"]
    errs = []
    if ev.get("schema") != EVIDENCE_SCHEMA:
        errs.append(f"{skill}: schema 版本不识别（期望 {EVIDENCE_SCHEMA!r}，"
                    f"实际 {ev.get('schema')!r}）——不识别即失败，不猜测")
    if ev.get("skill") != skill:
        errs.append(f"{skill}: skill 字段与证据文件名不符（实际 {ev.get('skill')!r}）")
    ch = ev.get("content_hash")
    if not isinstance(ch, str) or not _CONTENT_HASH_RE.match(ch):
        errs.append(f"{skill}: content_hash 格式非法（期望 sha256:<64hex>，"
                    f"实际 {ch!r}）")
    elif ch != expect_hash:
        errs.append(f"{skill}: 内容已漂移，证据过期（现算 {expect_hash[:19]}… "
                    f"证据 {ch[:19]}…）——需对最新内容重跑 replay-eval")
    ga = ev.get("generated_at")
    if not isinstance(ga, str) or not _check_iso8601(ga):
        errs.append(f"{skill}: generated_at 非法 ISO8601（实际 {ga!r}）")
    k = ev.get("k")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        errs.append(f"{skill}: k 字段缺失或类型不符（期望正整数，实际 {k!r}）")
    errs.extend(
        f"{skill}: {f} 字段缺失或类型不符（期望 [0,1] 数值，实际 {ev.get(f)!r}）"
        for f in ("pass_at_k", "pass_cap_k")
        if not _is_prob(ev.get(f))
    )
    inv = ev.get("invocation")
    if (not isinstance(inv, dict)
            or not isinstance(inv.get("skill_invoked"), bool)
            or not isinstance(inv.get("evidence"), str)):
        errs.append(f"{skill}: invocation 字段缺失或类型不符"
                    "（期望 {skill_invoked: bool, evidence: str}）")
    cases = ev.get("cases")
    if not isinstance(cases, int) or isinstance(cases, bool) or cases < 0:
        errs.append(f"{skill}: cases 字段缺失或类型不符（期望非负整数，"
                    f"实际 {cases!r}）")
    return errs


# 发布门禁的证据登记集：decide() 只对登记在册的 skill 执行证据校验。
# 登记政策：某 skill 首次提交「真实 LLM replay 证据」后（skills/skill-evo/
# artifacts/replay-evidence/<skill>.json；dry-run 冒烟证据只证脚本基线，
# 不算数），把该 skill 名加入本元组。
# 初始为空的裁决（2026-09-21 集成期）：verify_skill_evidence 默认 scope 是
# skills/ 下全部含 scripts/ 的 skill（现仓 8 个），但评估基础设施仅 ddl-guard
# 有 case 集——按默认面会让下一次发布必 exit 2 且唯一逃逸是被禁的 skip 开关。
# 故发布路径按登记集驱动（门已就位，随首个真实证据登记而生效）；
# --verify-evidence 诊断入口保持全量 scope 不变。
EVIDENCE_ENROLLED: tuple[str, ...] = ()

def verify_skill_evidence(repo_root: Path = REPO,
                          skills: list[str] | None = None,
                          *, allow_skip: bool = False) -> int:
    """发布门禁：技能内容 hash ↔ 评测证据绑定校验（fail-closed）。

    校验面：默认 skills/ 下所有含 scripts/ 的 skill，或参数/锁清单指定集。
    返回码：0 = 全部通过（输出 skill → hash 前 12 位 → pass_cap_k 核对清单）；
            2 = 拦截（证据缺失 / 内容漂移 / schema 不识别 / JSON·字段损坏）。
    唯一逃逸：allow_skip=True（仅限测试注入）且 RELEASE_EVIDENCE_SKIP=1
    ——stderr 醒目警告；decide()/--verify-evidence 生产入口一律不传
    allow_skip，环境变量在发布路径不产生任何放行效果——除此之外无任何
    静默放行路径。
    """
    if allow_skip and os.environ.get(_SKIP_ENV) == "1":
        print(f"⚠⚠⚠ 警告：{_SKIP_ENV}=1 —— 已跳过评测证据门禁！"
              "该开关仅限测试注入（allow_skip=True + 环境变量同时成立），"
              "生产入口不受其影响。", file=sys.stderr)
        return 0

    scope = skills if skills is not None else discover_evidence_skills(repo_root)
    if not scope:
        print("（无可校验对象：skills/ 下没有含 scripts/ 的 skill）")
        return 0

    errors, checklist = [], []
    for skill in scope:
        ev_file = evidence_path(repo_root, skill)
        if not ev_file.is_file():
            errors.append(
                f"{skill}: 评测证据缺失（"
                f"{ev_file.relative_to(repo_root).as_posix()} 不存在）"
                "——内容变更必须先跑 replay-eval 生成证据")
            continue
        try:
            ev = json.loads(ev_file.read_text(encoding="utf-8"))
        except OSError as e:
            # 证据文件存在但不可读（权限等）：归入干净拦截而非 traceback
            errors.append(f"{skill}: 证据文件不可读（{type(e).__name__}: {e}）")
            continue
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append(f"{skill}: 证据 JSON 损坏（{e}）")
            continue
        try:
            expect = compute_content_hash(repo_root, skill)
        except (UnicodeDecodeError, OSError) as e:
            # scripts/** 混入未知二进制（派生产物已在 skill_source_files
            # 排除，此处剩如误提交的 .bin）必须归入干净拦截，而非
            # traceback 崩溃（exit 码语义不混淆）
            errors.append(f"{skill}: 内容不可读或非 UTF-8，无法计算 "
                          f"content_hash（{type(e).__name__}）——排查 "
                          "scripts/ 下的二进制/缓存文件后重试")
            continue
        if errs := _validate_evidence(ev, skill, expect):
            errors.extend(errs)
        else:
            checklist.append((skill, expect, ev.get("pass_cap_k")))

    if errors:
        print(f"❌ 评测证据校验失败（{len(errors)} 处）——"
              "证据先行于版本语义，发布终止:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    print(f"✅ 评测证据门禁通过：{len(checklist)} 个 skill 的内容 hash"
          " 与最新证据绑定一致")
    for skill, h, cap in checklist:
        print(f"  - {skill}  {h[:19]}  pass_cap_k={cap}")
    return 0


def decide(check_only: bool = False) -> int:
    if EVIDENCE_ENROLLED:
        rc = verify_skill_evidence(skills=sorted(EVIDENCE_ENROLLED))
    else:
        print("ℹ 评测证据门：登记集 EVIDENCE_ENROLLED 为空——门已就位，"
              "首个真实 replay 证据提交后登记 skill 名即生效"
              "（--verify-evidence 可全量审计）")
        rc = 0
    if rc != 0:
        return rc
    base = latest_stable_tag()
    commits = interval_commits(base)
    bump = expected_bump(commits)
    cur = current_version()

    if bump is None:
        n = len(commits)
        print(f"⛔ 区间 {base or '起点'}..HEAD 含 {n} 个非 merge 提交，无 feat/fix/perf/breaking——无可发布内容，拒绝发布。")
        return 1

    expected = bump_version(base or f"v{cur}", bump)
    dry = subprocess.run(
        ["npx", "--no-install", "commit-and-tag-version", "--dry-run"],
        cwd=REPO, capture_output=True, text=True,
    )
    target = parse_catv_target(dry.stdout + dry.stderr)

    print(f"基线 tag: {base}  当前版本: {cur}")
    print(f"惯例期望: {expected}（{bump}）   catv 目标: {target}")

    if target == expected:
        print("✅ 一致，原生执行 release")
        cmd = ["npx", "--no-install", "commit-and-tag-version"]
    else:
        reason = (
            "catv 0.x preMajor 语义（feat→patch）与仓库惯例（feat→minor）冲突"
            if cur.startswith("0.") and bump == "minor"
            else "catv 判定与仓库惯例不一致"
        )
        print(f"⚠ 拦截：{reason}，按惯例纠偏 --release-as {expected}")
        cmd = ["npx", "--no-install", "commit-and-tag-version", "--release-as", expected]

    if check_only:
        print("（--check：不执行）")
        return 0
    subprocess.run(cmd, cwd=REPO, check=False)
    return 0


def _main(argv: "list[str]") -> int:
    if "--verify-evidence" in argv:
        print("（--verify-evidence：全量审计口径，独立于发布门禁——"
              f"decide() 仅校验登记集 EVIDENCE_ENROLLED，当前登记 "
              f"{len(EVIDENCE_ENROLLED)} 个）")
        return verify_skill_evidence()
    if "--release" not in argv and "--check" not in argv:
        # 防误触（2026-09 两次实证：裸调用/帮助探针直接跑完 catv 提交版本 bump）：
        # 无 --release 一律只报告决策，catv 执行仅经 npm run release 显式发起
        print("（未传 --release：只报告决策不执行。发布请用 npm run release，"
              "或 python3 scripts/release_guard.py --release）")
        return decide(check_only=True)
    return decide(check_only="--check" in argv)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
