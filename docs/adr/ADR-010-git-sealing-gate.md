# ADR-010 · 2026-08-27 · 测试 git 密封制度化（机械化门）+ final gate 双实现漂移锁

**背景**：同一根因（hook 注入 GIT_* 劫持仓库发现）两次事故（2026-08-22
真仓 389 文件删除；2026-08-27 PR #71 夹具提交落真仓 HEAD）。既有处置是
逐套件 conftest 打补丁 + steering §测试密封性规范 + 手工登记表——
规范靠人记住、登记靠人补行，PR #71 漏 .factory/tests 证明手工模式必然
漂移。另：pre-push 推送实测 exit=141（SIGPIPE）——hook 运行 ~45s 期间
git 已打开的 ssh.github.com:443 连接被中间设备空闲回收，钩子后复用死
连接即死；修复 = 仓本地 core.sshCommand keepalive（ServerAliveInterval=15,
CountMax=4），dry-run 带 hook 实测 141→0。

**决策**：
1. **机械化门 tools/check_git_sealing.py**（gauntlet 层 git-sealing，
   负控制 NC14）：R1 spawn git 的 test_*.py 所在套件 conftest 必须
   import 期密封；R2 调 git 的 test*.sh 必须 top-level unset（标记
   行首锚定，printf/heredoc 内嵌字符串不构成密封——NC14 自测实测的
   假满足形态）；R3 scripts/tests/test_hermetic_git.py GIT_FIXTURE_CASES
   覆盖全部 R1 检出套件（登记表所在套件豁免自跑：自登记 = 参数化
   自我 spawn 无限嵌套，实测 300s 超时）。规范事实源仍是 steering/
   testing-standards.md §测试密封性,门是机械执行者。
2. **密封补齐**：.factory/tests 与 skills/skill-evo conftest 接入
   import 期剥离；test-lease-sql.sh / test_gauntlet_checks.sh 顶层
   unset；登记表补 .factory 条目（TestStampRoundtrip 注入实跑）。
3. **final gate 双实现漂移锁（保留 python 实现的决策）**：shell 侧
   （final-gate 输出 + read -ra）与 python 侧（_final_gate_words +
   shlex）继续并存，一致性机械化：TestFinalGateDriftLock 锁三件事——
   同配置双侧拆词逐词相等（含 PATH 型）；活配置单一事实源
   （FINAL_GATE == final_gate_cmd().split()）；引号拒绝双侧互为镜像。
   PR #71 Sourcery S1 的 bash 前缀漂移即双实现无锁的产物。
4. **SIGPIPE 修复不入库**：core.sshCommand 是仓本地配置（git config
   --local，worktree 共享），非周界文件——不随 PR 走；本条目即交接
   记录（其他 clone 遇 141 同方处置）。

**验证**：check_git_sealing 真仓 R1/R2/R3 干净（6 套件全登记）；NC14
三态（R1+R3 拦 / R2 拦 / 中性零误报）；hermetic 5 案例注入实跑全绿；
drift-lock 5 例；带 hook push dry-run exit=0。

## ADR-010 附记 · 2026-08-27 · 反斜杠分叉收口

审查发现漂移锁遗留真实分歧点：反斜杠。`read -r -a` 字面（`a\ b` →
2 词 `a\` + `b`）vs `shlex.split` POSIX 转义（→ 1 词 `a b`）——词数
即不同，且旧校验（仅禁引号）双侧都放行，「过校验 ⇒ 两侧拆词一致」
不变量有洞。处置：两侧校验同禁反斜杠（fail-closed，与禁引号并列：
final_gate_cmd 与 _final_gate_words 互为镜像），TestFinalGateDriftLock
参数化三形态（`a\ b` 转义词 / `x\y` 词内 / `tail\` 尾随）钉死双侧同拒。
分叉点至此闭集：引号 + 反斜杠之外，两拆词器都只按空白切词、其余字符
全字面（shlex.split 的 comments 默认 False，`#` 亦字面）——纯空白分隔
下逐词相等，不变量闭环。
