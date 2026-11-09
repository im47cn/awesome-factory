#!/usr/bin/env bash
# commit message 规范校验（awesome-factory tools/git 分发，由 lefthook 调用）
# 用法: bash .lefthook/commitmsg-check.sh <msg-file>
# 单一规则源: commitlint + 项目内 commitlint.config.{cjs,js,mjs,ts}（.mjs/.ts 非分发名、静默承认；旧版 .js 仅 ESM 项目提示迁移，issue #131）；缺 commitlint 时自动安装（一次性）。
# 无 node/npm 时提示后放行 —— commit 钩子不阻塞环境问题，只拦规范违规。
set -u
MSG_FILE="${1:-}"

# git 特殊提交放行（merge/revert/squash 自动生成，不适用规范）
sed -n '1p' "$MSG_FILE" | grep -qE '^(Merge |Revert |Auto-Merged )' && exit 0
grep -vE '^\s*(#|$)' "$MSG_FILE" | grep -q . || exit 0

# 无 commitlint → 有 npm 则自动安装；装完优先重新 command -v（Windows/非标
# prefix 下 npm prefix -g 路径可能不存在，exec 失败会阻断提交，违背本脚本
# "环境问题放行"原则），再回退 npm prefix 绝对路径（受限 PATH 下 command -v 找不到）
CL="$(command -v commitlint || true)"
if [ -z "$CL" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[commitmsg] 首次使用: 自动安装 commitlint（一次性，约几十秒）"
    npm install -g @commitlint/cli @commitlint/config-conventional >/dev/null 2>&1 || {
      echo "⚠ [commitmsg] 安装失败，本次放行；手动执行: npm install -g @commitlint/cli @commitlint/config-conventional"
      exit 0
    }
    CL="$(command -v commitlint || true)"
    [ -n "$CL" ] || CL="$(npm prefix -g)/bin/commitlint"
    [ -x "$CL" ] || {
      echo "⚠ [commitmsg] 已安装但未定位到可执行文件（${CL}），本次放行"
      exit 0
    }
  else
    echo "⚠ [commitmsg] 无 node/npm，暂无法校验；装 node 后首次提交自动安装 commitlint"
    exit 0
  fi
fi

# 规则配置是分发物之一，缺失说明未跑 install.sh。2026-09-04（issue #131）
# 分发名 .js → .cjs：ESM 项目（package.json "type":"module"）下 require(.js)
# 被当 ESM 解析、module.exports 导出即崩——现双认 .cjs/.js；旧 .js 仅 ESM 项目警告（CJS 合法静默）
ROOT="$(git rev-parse --show-toplevel)"
if [ -f "$ROOT/commitlint.config.cjs" ]; then
  :
elif [ -f "$ROOT/commitlint.config.js" ]; then
  # 旧版 .js：CJS/无 package.json 的项目本就合法——静默双认；仅 ESM 警告
  # （require 崩，提示才有诊断价值）。ESM 裁决按 Node 解析语义（PR #142
  # 评论）：从 ROOT 向上取最近 package.json 的 type 字段（无 = CJS），
  # JSON 解析容 tab/换行空白与父作用域继承；node 缺失回退 grep 近似
  # （剥全部空白后匹配，仍只查根、不识父作用域——仅诊断增强不改变 rc）
  _esm=0
  _node="$(command -v node 2>/dev/null || true)"
  if [ -n "$_node" ]; then
    _type="$("$_node" -e '
      const fs = require("fs"), path = require("path");
      let d = process.argv[1];
      for (;;) {
        const f = path.join(d, "package.json");
        if (fs.existsSync(f)) {
          try {
            const j = JSON.parse(fs.readFileSync(f, "utf8"));
            if (j.type === "module") process.stdout.write("module");
          } catch (e) {}
          break;
        }
        const up = path.dirname(d);
        if (up === d) break;
        d = up;
      }' "$ROOT" 2>/dev/null || true)"
    [ "$_type" = "module" ] && _esm=1
  elif [ -f "$ROOT/package.json" ] \
    && tr -d '[:space:]' <"$ROOT/package.json" | grep -q '"type":"module"'; then
    _esm=1
  fi
  [ "$_esm" -eq 1 ] && echo "⚠ [commitmsg] 检出旧版 commitlint.config.js（issue #131：ESM 项目会崩溃）；重跑 awesome-factory tools/git/install.sh --update 迁移 .cjs"
elif [ -f "$ROOT/commitlint.config.mjs" ] || [ -f "$ROOT/commitlint.config.ts" ]; then
  # 非分发名但合法（2026-09-14 增补）：commitlint ≥18 原生解析 .mjs（ESM）/ .ts（jiti），
  # 守卫只验存在性，加载成败由 commitlint 自行裁决
  :
else
  echo "⚠ [commitmsg] 缺 commitlint.config.cjs，请跑 awesome-factory tools/git/install.sh；本次放行"
  exit 0
fi

exec "$CL" --edit "$MSG_FILE"
