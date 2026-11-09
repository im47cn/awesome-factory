#!/usr/bin/env bash
#
# awesome-factory —— Git 自动化工具一键安装 / 更新
# 工具全局（commitlint/commit-and-tag-version/lefthook）+ 配置入库（commitlint.config.cjs/.versionrc.cjs/lefthook.yml）+ hook 仓库（lefthook 托管）
#
# 用法:
#   bash install.sh [目标项目根目录]            # 首次安装（默认当前目录）
#   bash install.sh --update [目标项目根目录]   # 刷新 awesome-factory 最新配置到已装项目
#   bash install.sh --check [目标项目根目录]    # 巡检已装项目分发件缺失/漂移（零副作用，可挂 CI）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 参数解析：--update 刷新 / --check 巡检（同给时后出现者优先）；其余非选项参数为目标目录
MODE=install
TARGET_ARG=""
for arg in "$@"; do
  case "$arg" in
    --update|-u) MODE=update;;
    --check)     MODE=check;;
    -h|--help)
      echo "用法: bash install.sh [--update | --check] [目标项目根目录]"
      echo "  首次安装：交互式确认覆盖已存在文件"
      echo "  --update：刷新本仓库最新配置到目标项目，不碰非本工具的 hook"
      echo "  --check：巡检目标项目分发件缺失/漂移，非交互零副作用，差异 exit 1 可挂 CI"
      echo "  --update 与 --check 同给时，后出现者优先"
      exit 0;;
    *) TARGET_ARG="$arg";;
  esac
done
TARGET="$(cd "${TARGET_ARG:-$PWD}" && pwd)"

# 分发清单（单一来源）：src 相对 SCRIPT_DIR → dst 相对 TARGET，install/update/check 三模式共用
# ../spec_check.py 是 spec 反向核对脚本（与 spec-check.sh 配套），随 hook 分发到项目 .lefthook/
DIST=(
  "commitlint.config.cjs:commitlint.config.cjs"
  ".versionrc.cjs:.versionrc.cjs"
  "lefthook.yml:lefthook.yml"
  "lefthook/coverage.sh:.lefthook/coverage.sh"
  "lefthook/commitmsg-check.sh:.lefthook/commitmsg-check.sh"
  "lefthook/run-tests.sh:.lefthook/run-tests.sh"
  "lefthook/spec-check.sh:.lefthook/spec-check.sh"
  "lefthook/sourcery-gate.sh:.lefthook/sourcery-gate.sh"
  "lefthook/coderabbit-gate.sh:.lefthook/coderabbit-gate.sh"
  "lefthook/pre-push-delete-guard.sh:.lefthook/pre-push-delete-guard.sh"
  "../spec_check.py:.lefthook/spec_check.py"
)

# 变量后紧跟全角字符须用 ${} 界定，否则 bash 在部分 locale 下会把多字节字符误并入变量名
echo "▸ 目标项目: ${TARGET}（模式: ${MODE}）"

# ── 0. 巡检模式：按分发清单逐件比对（缺失/漂移），非交互零副作用 ──
# 不 mkdir、不碰 ~/.gitmessage/git config/npm/package.json；cmp 字节比对与 hash 判定等价
# （cmp 置于 if 条件位，避免 set -e 中断）；~/.gitmessage 是机器级全局文件，不在比对集
if [ "$MODE" = "check" ]; then
  missing=0
  drift=0
  for pair in "${DIST[@]}"; do
    src="${pair%%:*}"
    dst="${pair#*:}"
    if [ ! -f "$TARGET/$dst" ]; then
      echo "缺失  $dst"
      missing=$((missing + 1))
    elif ! cmp -s "$SCRIPT_DIR/$src" "$TARGET/$dst"; then
      echo "漂移  ${dst}（与上游 awesome-factory 不一致）"
      drift=$((drift + 1))
    fi
  done
  # 旧版分发名（issue #131 前 .js 双件）：检出即漂移——ESM 项目会崩，须 --update 迁移
  for legacy in commitlint.config.js .versionrc.js; do
    if [ -f "$TARGET/$legacy" ]; then
      echo "遗留  ${legacy}（旧版分发名，重跑 --update 迁移 .cjs）"
      drift=$((drift + 1))
    fi
  done
  if [ "$missing" -eq 0 ] && [ "$drift" -eq 0 ]; then
    echo "✔ ${#DIST[@]}/${#DIST[@]} 分发件一致，无缺失无漂移"
    exit 0
  fi
  echo "✘ 巡检发现：缺失 ${missing} 件、漂移 ${drift} 件（应装 ${#DIST[@]} 件）"
  exit 1
fi

# ── 1. 检测 node / npm ──────────────────────────────────────────
command -v node >/dev/null 2>&1 || { echo "✘ 未检测到 node，请先安装 node ≥ 16"; exit 1; }
command -v npm  >/dev/null 2>&1 || { echo "✘ 未检测到 npm"; exit 1; }
echo "✔ node $(node -v)"

# ── 2. 拷贝配置（install：已存在则确认；update：直接覆盖）─────────
# lefthook.yml/.lefthook/coverage.sh 是 hook 配置，入库后随 clone 共享给全团队
copy_one() { # $1=源文件名(相对 SCRIPT_DIR) $2=目标路径
  if [ "$MODE" = "install" ] && [ -f "$2" ]; then
    read -rp "⚠ $2 已存在，覆盖？[y/N] " ans || true
    [ "${ans:-N}" = "y" ] || { echo "  跳过 $2"; return; }
  fi
  cp "$SCRIPT_DIR/$1" "$2"
}
mkdir -p "$TARGET/.lefthook"
for pair in "${DIST[@]}"; do
  copy_one "${pair%%:*}" "$TARGET/${pair#*:}"
done

# 旧版分发名迁移清理（issue #131：.js → .cjs）：旧 .js 与新拷贝的 .cjs 字节
# 相同 → 删（未改写的旧安装）；被业务改写 → 保留点名，不覆盖业务决策
for legacy in commitlint.config.js .versionrc.js; do
  if [ -f "$TARGET/$legacy" ]; then
    if cmp -s "$TARGET/${legacy%.js}.cjs" "$TARGET/$legacy"; then
      rm "$TARGET/$legacy"
      echo "✔ 已迁移 ${legacy} → ${legacy%.js}.cjs（旧件未改写，删除）"
    else
      echo "⚠ 保留 ${legacy}（与上游 .cjs 不同——业务已改写，请人工确认后删除）"
    fi
  fi
done


# commit 模板 → 用户主目录 ~/.gitmessage（全局，所有仓库/IDEA 一次识别）
skip_tmpl=0
if [ "$MODE" = "install" ] && [ -f "$HOME/.gitmessage" ]; then
  read -rp "⚠ ~/.gitmessage 已存在，覆盖？[y/N] " ans || true
  [ "${ans:-N}" = "y" ] || { echo "  跳过 ~/.gitmessage"; skip_tmpl=1; }
fi
if [ "$skip_tmpl" != "1" ]; then
  cp "$SCRIPT_DIR/commit-template.txt" "$HOME/.gitmessage"
  git config --global commit.template "$HOME/.gitmessage"
  echo "✔ 全局 commit 模板已配置（~/.gitmessage，所有仓库生效）"
fi
echo "✔ 配置已就位"

# ── 3. 全局安装工具（一次，所有项目共享；检测已装则跳过）─────────
# commitlint / lefthook / commit-and-tag-version 装全局，避免每项目重复 npm install
GLOBAL_PKGS=("@commitlint/cli" "@commitlint/config-conventional" "commit-and-tag-version" "lefthook")
# 用 npm JSON 输出精确判断（grep "$pkg@" 在包名互为子串时会误判）
NEED=()
while IFS= read -r missing; do
  [ -n "$missing" ] && NEED+=("$missing")
done < <({ npm ls -g --depth=0 --json 2>/dev/null || echo '{}'; } | node -e '
  let s = "";
  process.stdin.on("data", d => (s += d));
  process.stdin.on("end", () => {
    const deps = (JSON.parse(s).dependencies) || {};
    process.argv.slice(1).forEach(p => { if (!deps[p]) console.log(p); });
  });
' "${GLOBAL_PKGS[@]}")
if [ ${#NEED[@]} -gt 0 ]; then
  npm install -g "${NEED[@]}"
  echo "✔ 全局工具已安装: ${NEED[*]}"
else
  echo "✔ 全局工具已就位（跳过安装）"
fi
cd "$TARGET"

# ── 4. 安装 lefthook hook（shim 由 lefthook 写 .git/hooks/*，配置已入库）──
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null || true)"
if [ -n "$GIT_DIR" ]; then
  HOOK="$GIT_DIR/hooks/commit-msg"
  # hooksPath 被其他方案（husky 等）接管时，git 不读 .git/hooks，写了也无效
  HOOKS_PATH="$(git config core.hooksPath || true)"
  if [ -n "$HOOKS_PATH" ]; then
    echo "⚠ core.hooksPath 已指向 ${HOOKS_PATH}（husky 等已接管），跳过 lefthook 安装"
  else
    install_hook=1
    # 本工具旧版直写的 hook → 清理，交由 lefthook 接管
    if [ -f "$HOOK" ] && grep -q "awesome-factory tools/git/install.sh" "$HOOK"; then
      rm "$HOOK"
      echo "✔ 已移除旧版直写 hook，交由 lefthook 接管"
    elif [ -f "$HOOK" ] && ! grep -q "lefthook" "$HOOK"; then
      # 非本工具、非 lefthook 生成的 → install 询问，update 一律跳过
      if [ "$MODE" = "install" ]; then
        read -rp "⚠ $HOOK 已存在且非本工具/lefthook 生成，覆盖？[y/N] " ans || true
        [ "${ans:-N}" = "y" ] || { echo "  跳过 lefthook hook 安装"; install_hook=0; }
      else
        echo "⚠ $HOOK 非本工具/lefthook 生成，跳过"
        install_hook=0
      fi
    fi
    if [ "$install_hook" = "1" ]; then
      if lefthook install; then
        echo "✔ lefthook hooks 已安装（.git/hooks/*，配置见项目内 lefthook.yml）"
      else
        echo "✘ lefthook install 失败"
      fi
    fi
  fi
else
  echo "⚠ 非 git 仓库，跳过 lefthook hook 安装"
fi

# ── 5. 注入 package.json scripts ────────────────────────────────
if [ -f "$TARGET/package.json" ]; then
  node -e '
    const fs = require("fs");
    const f = "package.json";
    const j = JSON.parse(fs.readFileSync(f, "utf8"));
    j.scripts = j.scripts || {};
    j.scripts.release = "commit-and-tag-version";
    j.scripts["release:dry"] = "commit-and-tag-version --dry-run";
    fs.writeFileSync(f, JSON.stringify(j, null, 2) + "\n");
  '
  echo "✔ package.json scripts 已注入（release / release:dry）"
fi

echo ""
if [ "$MODE" = "update" ]; then
  echo "✅ 更新完成（已刷新至 awesome-factory 当前版本）"
else
  echo "✅ 安装完成"
fi
echo "   提交校验  → git commit 时自动触发"
echo "   发版预览  → npm run release:dry"
echo "   正式发版  → npm run release"
