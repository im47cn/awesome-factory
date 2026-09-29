# ADR-011 · 2026-09-01 · 下游追平自动化 B 阶段（--repo/--commit + 中心巡检 + blame-ignore）

**背景**：`.factory` 工具链分发 N 仓下游后的两项运营痛点：① sync
追平提交持续污染下游 blame/变更历史（追平是机械动作，blame 眼里却与
人工改动无异）；② 同步与反哺须逐仓手工操作，漂移发现滞后。曾评估
symlink 整目录方案，三硬伤否决：skip 面 per-repo 状态
（factory-local.json/upstream-lock.json）串台；下游 CI 无上游路径时
断链；blob 锚点版本边界消失（live-at-HEAD，回滚与审计失效）。
拍板「先 B 后 C」：B 阶段落地 blame-ignore 自动化（B1）+ 中心仓集中
巡检（B2）；C 阶段（中心经 worktree 直接操作下游仓 + CI composite
action）缓行约一周。

**决策**：
1. **B1 `sync-from-upstream.sh --repo <path> --commit`**：中心驱动，
   任意第三方 cwd 操作目标仓（`--repo` 解析+规范化+git 校验，非 git
   目录 exit 2）；`--commit` 仅与 --apply 组合，追平产物+锚点+
   blame-ignore 以单提交落库（`factory: 上游同步追平（<sha9>）`，
   10 仓主流惯例），落当前分支**不推送**——push/CI 属 C 阶段。
   `--commit` 前置脏检查：目标仓 .factory 有未提交 tracked 改动即
   拒绝（exit 1）——自动提交会淹没热修，热修正道是先落库或反哺。
   暂存精确化：逐 dst `git add`（STAGE_FILE 清单），绝不
   `git add -A -- .factory`（防扫进下游未 gitignore 的运行时产物）。
2. **blame-ignore 滞后一条**：提交无法含自身 SHA（自指无解），每次
   真追平时把「上一个触碰 upstream-lock.json 的提交」append 进
   `.git-blame-ignore-revs`，并 `git config blame.ignoreRevsFile` 指
   绝对路径（pathspec 按 cwd 解析，相对路径在子目录 blame 下 rc128）；
   首跑只建带 `#` 头的空文件。**空转链双修复**（会话内自查发现）：
   ① blame-ignore 追加最初在 staged 检查之前——无漂移重跑会生成只含
   一行追加的空提交并无限链式，已移入 `git diff --cached --quiet`
   为假的分支；② 锁点改条件重写（APPLIED>0 或锚点变更才写），否则
   synced_at 每轮必变 → 永远「有可提交内容」。存量历史追平提交一次
   性回填：`git log --grep='追平' --format=%H | sort -u >>
   .git-blame-ignore-revs`（40-hex 全 SHA 一行，GitHub 要求）。
3. **B2 `downstream-check.sh` + `downstream.json`**：10 仓清单
   （path 相对中心根）**skip 分发**——中心专属状态，下游不携带，
   缺失 fail-closed rc2 即正确边界；巡检脚本 **full 分发**。
   etf-radar 排除（旧代工厂无 factory-local.json，迁移走 README 移植
   四步）。巡检一律 `--anchor main`（中心发布线）且执行**中心版**
   sync——下游副本可能滞后，鸡生蛋。shlock 互斥照抄 cron-dispatch；
   子进程一律 `</dev/null` 防吞读循环 stdin；单仓失败记 `[错误]`
   继续；漂移即 osascript 通知（静默 cron 教训；`FACTORY_NO_NOTIFY=1`
   关断，测试静默）。退出码 0=全干净 1=漂移/单仓失败 2=清单/用法错误。
4. **lock 补写 `upstream` 字段**：apply 落锁时记录上游路径原样值，
   巡检可溯源。
5. **Sourcery 闸 cd 修复**（随本 ADR 落地）：闸的相对路径清单按
   $REPO 生成，CLI 却在脚本 cwd 执行——旧用法 cwd 恰为目标仓掩盖；
   `--repo` 巡检场景（cwd=中心仓）会检查错仓。修复 = CLI 一并 cd 进
   $REPO（foreign-cwd 测试暴露）。
6. **bash 3.2 全角变量名坑**（同轮暴露）：UTF-8 locale 下 `$VAR` 后
   紧跟多字节字符会被并入变量名（`$MODE）` → `MODE\xef…` unbound
   崩溃，且错误消息含非法 UTF-8 字节令 Python strict decode 二次炸）；
   规约：变量后接非 ASCII 一律 `${VAR}` 花括号。

**验证**：test_sync_from_upstream.py 10 测（--repo 三态/脏守卫/空转链
回归/滞后一条/首跑引导）+ test_downstream_check.py 6 测（退出码契约/
汇总串/坏清单 rc2/舰队级空转链/单仓容错）全绿；gauntlet 全绿。
