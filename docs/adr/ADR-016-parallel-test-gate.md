# ADR-016 · 2026-09-20 · 并行测试门工厂化（段 fan-out 下沉 factory_lib，随 full 面分发）

**背景**：PR #212 把宿主仓全量门禁 43s→14s（#139 段间 fan-out 先把串行
64s 压到 43s，#212 再叠加 pytest-xdist 长段白名单至 14s），但能力以 bash
原语困在 scripts/run_tests.sh：DISTRIBUTION
full 面的 ~10 个下游仓照旧串行门禁，且 ADR-002 的证据类（shell 进程原语
边角语义）正是并行编排的高发缺陷区。诉求：并行测试门赋能到每个使用工厂
的项目，并支持多语言栈（Java 等）的段内并行。

**决策**：
1. **编排器下沉 factory_lib.py**（ADR-005 同型先例——消灭的是缺陷表达
   面，不是零 LLM 形态）：`parallel-gate` 子命令 `run_parallel_gate`。
   契约全集：配置序稳定回放（并发只影响起跑时序，不改变裁决顺序）；
   workers 0=不限、>0=并发上限；每段日志独立 mkdtemp 目录——失败保留
   （回放至 stdout 供证据链）、成功整体删除；失败 tag 清单按行 `tag\n`
   写出（宿主 `while IFS= read -r` 无损回收）；argv 的 `$PY` 词替换为
   解释器（env PYTHON 或 python3）；shell 段以 `bash -o pipefail -c
   <cmd> _ <解释器>` 执行（ADR-002 管道吞码类缺陷的构造级免疫）；cwd
   预检 fail-closed（目录缺失=配置错误，rc 2 零产出）。CLI 退出码
   0=全绿 1=有失败段 2=门自身错误。
2. **配置 = factory-local.json 可选键 parallel_gate**（ADR-012 载体
   纪律）：缺键=None，未采用仓零行为变化；存在但损坏=RuntimeError——
   门是裁决路径，坏配置必须炸，不许静默退回串行或漏段；schema 拒未知
   键——本键的失效形态是「静默慢」，拼错键名时能力没开，宁可误伤不可
   漏拦。段键闭集 {tag,name,argv,shell,cwd,intra,stack}，argv XOR
   shell 互斥，tag 全局唯一且禁换行（按行回收契约）。
3. **段内并行只进保守档、缺省 off**：intra:"auto" 逐段显式 opt-in。
   适配表：pytest→`-n auto`（未装 xdist 注记降级串行，不硬注）；
   maven→`-T 1C -DforkCount=1C -DreuseForks=true`（模块级+fork 级测试
   类分发，不含 `-Dparallel=methods` 类线程交错放大器）；
   gradle→`--parallel`（任务级；maxParallelForks 属构建脚本面不硬注）；
   jest→`--maxWorkers=50%`；vitest/go/cargo 框架默认已并行，零附加
   参数+注记（不虚计功）；phpunit/dotnet 无 CLI 保守参数，注记串行
   （paratest/xunit.runner.json 属仓面配置）。顺序敏感段逃生舱=不设
   intra（缺省 off）。
4. **宿主迁移为组合方**：run_tests.sh 卸下并行原语——调 `parallel-gate`
   取回失败 tag 清单，串行尾段照跑、末尾一次统一裁决（与旧版 parity：
   失败清单一次全量暴露，不因并行提前中止而漏段）。

**验证**：test_parallel_gate.py 77 例——配置校验矩阵（缺键/损坏/未知键/
argv-shell 互斥/tag 换行）、9 栈探测、保守档适配表、编排契约（配置序
回放、worker 有界性与真并发时间戳差分、失败日志保留与成功清理、`$PY`
替换、shell 段 pipefail 真码传播、CLI 面）；迁移后全量门禁 10/10 段绿、
壁钟 14.7s（对齐 #212 基线 14.2s）；maven 微项目实证（Maven 3.9.16/
Java 17/surefire 3.5.4/junit-jupiter 5.12.2，2 个 test class 各
Thread.sleep 2500ms）：串行 5.68s vs 适配器参数 3.19s（Wall Clock）——
测试类跨 fork 分发生效、类内方法保持串行；factory-local.json 变更后
mutations 全绿重证（树干净态）。

**引用**：steering/testing-standards.md「并行测试门」；.factory/README.md
「并行测试门（ADR-016）」；scripts/run_tests.sh 头注（组合方模式）。
