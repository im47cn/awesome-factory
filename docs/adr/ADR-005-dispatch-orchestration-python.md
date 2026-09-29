# ADR-005 · 2026-08-24 · dispatch 进程编排下沉 factory_lib.py

- **背景**：ADR-002 证据类的主体（jobs 表/wait 落空 0d947f60、管道吞码 61c119c2、管道早退 a4d81930、trap 吞错 c749ac5e、锁跨树 39b6b8ec）全部是 bash 进程原语的边角语义，且集中于 dispatch 侧。
- **决策**：dispatch.sh 退为入口 shim（`exec python3 factory_lib.py dispatch`），进程编排（ChainPool 并发槽+收割、mkdir+PID 硬锁、watch 循环、TERM/HUP 放锁）以 Python 重写并入 factory_lib.py；CLI/env/退出码契约逐项等价。bash 形态判断不变：dispatch 仍零 LLM（A3 未破）——下沉消灭的不是「智能在壳」，是 shell 进程原语类缺陷的表达面：Popen 句柄即作业表，jobs 表清点竞态与 grep -m1 SIGPIPE 在 Python 形态下结构性不可表达。
- **现状更新**：dispatch.sh 246→19 行，factory_lib.py 342→752 行，factory-lib.sh 103 不变（合计 691→874）；组件数不变（18）；ADR-002 触发器语义不变——进程管理类缺陷此后按新形态继续记账（Python 侧再发同样计 1 条）。
- **验证**：tests/test_breaker_wiring 三路沙箱（熔断门口/干跑/全轮哨兵计数）+ 新增 tests/test_dispatch.py（时间戳区间重叠测真并发峰值、陈锁/垃圾 pid 接管、slug/priority 纯函数）+ 全套 162 测试全绿；真仓 `--dry-run` 冒烟输出格式与 bash 版逐行一致，干跑退出码修正为 0（bash 尾部 `[ $DRY = 0 ] && echo` 的 rc=1 怪癖，测试注释既已点名）。
