# ADR-009 · 2026-08-27 · 拆分前置：本地化全量数据化 + 引擎单点 + portability 门

**背景**：工厂拆独立仓库的评估结论（S2 会话）——现在不拆（实例 2 < 3 阈值，
设计 §2.2 重估条件），但先消灭全部结构性耦合，使拆分当天只剩 git mv + 锚点锁。
审计发现四类残留：门命令三处硬编码（fix-issue/validate-pr/mutations）、
prompts 七文件含宿主专名、DISTRIBUTION 缺件（omp-isolated.yml、db/schema.sql
不在任何面）、local 面三项未清零（feedback-upstream/tests/test_state）、
omp CLI 七处直调无单点。

**决策**：
1. **门命令数据化**：`factory-local.json` 增 `final_gate_cmd`；
   `factory_lib.py final-gate` 子命令（fail-closed）为唯一取值口，
   fix-issue/validate-pr `read -ra` 拆词执行，mutations `FINAL_GATE` 拆词
   （首词解析为仓库根绝对路径）。
2. **prompts 参数化**：增 `repo_identity/reading_scopes/review_basis/
   pr_review_skills`；`repo-vars` 子命令渲染「仓库参数」段，由 run_node /
   pr-review / feedback-adapt 拼装时注入；triage/holdout 物理隔离不注入。
   prompts 正文零宿主专名（triage 判据 a 改为指向内联 MISSION 原文，
   真相源唯一化是顺带修正）。
3. **上游指针数据化**：`upstream_repo/upstream_path/feedback_branch_prefix`；
   feedback-upstream.sh 默认值改读配置（env 显式覆盖保留，镜像拓扑逃生口），
   PR 文案/分支前缀用 `SELF_ID`/`FB_PREFIX`，升 full。
4. **引擎单点**：`factory-lib.sh omp_node()`（omp CLI 唯一执行点，设计 §4
   runNode 的 bash 形态）；七处直调全部收口，dry-run 文案同步。
5. **分发补缺与归零**：full += omp-isolated.yml、db/schema.sql、
   feedback-upstream.sh、tests/（用例随源走；tests 内仓名是夹具样例数据，
   合法）；local = {}。test_state.py 随源入 tests/（去自带 path hack）。
6. **防回归门**：gauntlet 新层 `factory-portability`（checker
   tools/check_factory_portability.py，负控制 NC13）三规则——P1 full 面+
   prompts 零宿主专名（awesome-rules/im47cn/gtsp-/fss-/etf-radar/steering//
   scripts/run_tests；刻意不含 skills/——双布局识别是通用机制词）；P2
   `omp -p` 仅 factory-lib.sh；P3 full 面 .py 禁 sys.path.insert（tests/
   豁免：conftest 注入与跨目录被测 import 属测试布局语义）。
   `factory-local-validity` 层扩 final-gate/repo-vars 渲染断言。
7. **历史考证中性化**：注释中 etf-radar#NN → 源仓#NN（编号保留可溯）、
   hosting 实测项目名泛化；feedback.py record 上游 repo 改读配置。

**验收**：local 面归零；`factory-portability` P1/P2/P3 干净；omp 单点；
factory-local.json 变更后 mutations 重证全绿（指纹绑定强制）。

**边界**：tests/ 内夹具专名（slug 解析、作者名）不属 P1 管辖；M2 同步流
设计未动；拆分本身（独立仓 + 版本 tag + guard.self_check 跨仓核对）留待
第 3 个消费者出现，按设计 §2.2 纪律执行。

## ADR-009 附记 · 2026-08-27 · R2 审查回流（ralph 轮 1）

reviewer 审出 3 BLOCKER + 4 MAJOR + 4 MINOR，处置：
- **B1** feedback-upstream.sh 补 `source factory-lib.sh`（omp_node 此前未定义，
  反哺真跑必炸 rc=127 被 `|| NODE_RC=$?` 吞）——已修并单元验证（omp 真进程）。
- **B2** upstream_path `~/` 字面 tilde 不展开（git -C 回归）——消费端
  `${VAR/#\~/$HOME}` 前缀展开，配置保持 `~/` 人类形态。已验证。
- **B3** validate-pr `SKILLS_ARG=""` 初始化被误删（set -u 下首触守卫技能即
  unbound 崩溃）——恢复。已验证。
- **M4** mutations FINAL_GATE 首词不再绝对化（PATH 型命令 `uv run pytest`
  变 `<repo>/uv` rc=127）——词保持原样，cwd=REPO_ROOT 解析。
- **M5** sync-from-upstream 目录项（tests/）静默跳过 = 漂移盲区——清单生成
  时 `ls-tree -r` 递归展开为文件项。fixture 端到端：同版 0/漂移 1/apply
  覆盖/追平 0。
- **M6** P1 禁词放宽 bare `run_tests`（抓出 feedback-upstream PR 文案真残留，
  与实际门禁 gauntlet 不符一并修正）；fix-issue/validate-pr 注释与 dry-run
  文案同步中性化。
- **M7** prompts/ 由 skip 升 **full**——中性化后即引擎无关资产，上游 prompt
  修复必须可达下游；README 三态描述同步。
- **M8** final_gate_cmd 禁含引号（read -ra 与 shlex 两拆词器一致性），
  fail-closed。
- **M9** triage.md 判据编号 a/b/c → 1/2/3（对齐 MISSION 数字编号）。
- **M10** repo_vars_text 的 pr_review_skills：键存在即严格校验（与 local-list
  同规），键缺失 = 无守卫面仓合法省略；删除死 try/except。
- **遗留（M11 及 MINOR）**：feedback.py record 独立读 factory-local.json
  （纯函数层刻意不 import factory_lib；KeyError→非零=fail-closed 语义已达，
  记录不改）；feedback-upstream 在上游仓运行时的语义错位（f6835d15 下游
  SHA 不在上游对象库，main 预存，工具设计为下游运行）。

## ADR-009 附记二 · 2026-08-27 · R2 复审收口

复审判定 8✓/2部分,新发现 N1-N9,处置：
- **N1(MAJOR)** upstream-lock.json 夹具运行时锚点(bad object)误入库 → 移除
  (skip 面运行时产物,上游自身无上游)。
- **N4** state.py 污染恢复丢执行位 → chmod +x(否则经 sync 的 chmod 语义
  传播到全部下游)。
- **N3** 编号体系:M9 只改 prompt 流程文字造成与回执消费链(receipt 正则
  ^判据([abc])/guidance 键 a/b/c/夹具)割裂 → 回退字母并显式声明映射
  (a/b/c ↔ MISSION 1/2/3,输出契约=字母)。教训:跨文件契约改动必须
  全链同步,单点"对齐"制造新割裂。
- **N2** README 三态描述与 DISTRIBUTION 同步(prompts 入 full 枚举)。
- **N5/N6/N8** 陈旧注释、目录消失零告警、run.py 引号拒绝补齐。
- **N7** ~user 形态误展开:fail-closed 兜底,配置约定 ~/ 形态,记录不改。
- **N9** evidence-stamp.json 被跟踪 → mutation 重证后须提交 stamp,既有
  设计(证据可审计),非缺陷。

## ADR-009 附记三 · 2026-08-27 · Sourcery R3 三评论处置

Sourcery 三条审查评论全部成立,处置:
- **S1(bug)** mutations run_gate 硬编码 `bash` 前缀破坏 PATH 型
  final_gate_cmd(`uv run pytest` 变 `bash uv ...`,首词被当脚本文件名)
  → 改直执 `FINAL_GATE`,与 shell 侧 fix-issue/validate-pr 的
  `"${GATE_ARGS[@]}"` 同构——两侧消费方执行形态统一(单测锁行为:
  monkeypatch Popen 断言 argv 无 bash 前缀)。killpg 审计注释随
  「bash 直子」措辞一并校正(直子=门命令进程,进程组语义不变)。
- **S2(bug)** `_final_gate_words` 先 `str()` 再校验:数字/列表等非字符串
  值 py 侧放行、shell 侧(factory_lib._local_str)拒绝——两消费方行为
  割裂 → 加 `isinstance(raw_val, str)` 前置校验,参数化负例锁四类
  JSON 类型;函数签名增可选 cfg_path(可测性,默认调用方不变)。
- **S3(bypass)** portability P2 字面子串 `omp -p` 可被 `omp   -p`/
  `omp \↵-p` 绕过 → 词边界正则 `\bomp[\s\\]+-p\b` + 全文 finditer
  (续行跨行,逐行扫描结构性漏报);P3 `sys . path . insert`(合法
  Python 空白点号变体)同根因一并正则化。NC13 负控制扩 d/e/f 三
  变体(多空格/续行/点号空白)。
- 附带:run_gate 安全审计注释更新——tests 分支命令词源自
  factory-local.json(治理周界内,禁引号+shlex 拆词后纯 argv 元素),
  闭集语义随直执重述。mutations 周界内改动 → kill rate 重证。

## ADR-009 附记四 · 2026-08-27 · pre-push 钩子 GIT_* 泄漏夹具污染事故

推送实测:lefthook pre-push 的 git 钩子环境向测试子进程泄漏 GIT_DIR
等仓库发现变量,`git -C <tmp夹具仓>` 的目标被环境变量优先级覆盖——
TestStampRoundtrip 的夹具 init/commit 落进真实仓 HEAD(树仅含 2 个
夹具文件),plugin_lock 随 HEAD 树缺文件连锁失败,推送被误拦。
处置(环境密闭):
- tests/gitenv.py git_env():剥除 10 个仓库发现类变量;三处消费
  (test_factory_local._git / test_breaker_wiring._sandbox /
  test-lease-sql.sh)接入。
- mutations/run.py perimeter_blob/tracked_and_dirty 同根因加固
  (测试 monkeypatch REPO_ROOT 后同样可被劫持),模块级 _GIT_ENV。
- 回归锁 TestGitEnvSealing:受害者仓复现泄漏机制(无密闭 → 提交落
  受害者仓;密闭 → -C 语义恢复)。
教训:与附记二夹具污染事故同类——tmp git 仓夹具必须视为不可信环境
边界,git 子进程一律显式环境密闭,不依赖 ambient environ。
