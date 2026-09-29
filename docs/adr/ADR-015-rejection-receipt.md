# ADR-015 · 2026-09-20 · #207 拒绝回执缺失：标签假阴性和解 + 回执存在性断言

**背景**：#207 拒绝裁决 11:01:01Z factory:rejected 已落 GitHub，gh 客户端
却报非零（mutation landed + client reported failure 假阴性）；
`issue_label_swap … || return 1` 早退致判据回执永不执行——只落标不发
评论的静默拒绝，违反 steering/review-report-standards.md:19（自动化
拒绝必须附回执）；ADR-013 宽限检测按 updatedAt 计，无回执态被推迟暴露。

**决策**：
1. **动作层（A/B）**：issue_reject 落标失败先复核远端标签态——
   factory:rejected 在列即按已落定继续回执（swap stderr 落档回显），
   确证缺失才中止。
2. **传输层（C）**：hosting.py issue/pr_set_labels 失败路径读回复核，
   目标集已达成（增集在列 ∧ 删集缺席）即判成功。
3. **解析兜底（D）**：parse_agent_json 改逐 `{` 偏移 raw_decode——
   重复 JSON 块/尾文花括号不再崩（#207 triage 双块崩溃根因）。
4. **存在性断言（E）**：rejected_reconcile 增 has_receipt、无回执时
   不计人工评论；dispatch_liveness 无回执即时 FAIL 不进宽限——
   ADR-013「回执刷新 updatedAt」前提对无回执态不成立。
5. **marker 裁定**：幂等键 r${ROUND:-batch}，r 前缀恒在 → 批次键
   rbatch（docstring 曾误写 batch，以代码为准，改格式破坏既有查重）。
   #207 回执按 rbatch 补发，timeline 验证单条，批次重放查重收敛。
6. **判据收紧（PR #211 Sourcery 二轮）**：parse_agent_json 顶层
   verdict 契约——对象一旦完整解析，内部偏移全部丧失资格（外层
   verdict 非法时嵌套 evidence 携带的合法 verdict 不代表裁决，
   skip 至对象末尾继续扫后续顶层）；has_receipt/无回执 FAIL 判据
   从回执标题子串改为幂等 marker（人工复述标题不得冒充回执、屏蔽
   完整性违规——线上 3 个 open rejected 回执全带 marker，无存量
   误伤），rejected_reconcile 与 dispatch_liveness 同步换用。
7. **解析缺口补封（PR #211 CodeRabbit 三轮）**：fence 优先级穷尽
   兑现——`re.search` 只看首个 fence，首 fence 裁决非法时更早的
   合法裸对象会抢先后位合法 fence，改 `finditer` 全量 fence 预检
   后才落裸对象扫描；解码失败的对象（未闭合外层）不得落到嵌套
   `{` 接受其 verdict——原 `continue` 不推进 skip，改字符串感知
   平衡范围扫描（`_balanced_end`）：坏对象整体跳过其平衡区间，
   未闭合则跳到输入末尾（其后视为对象内部，fail-closed）。

**验证**：全套 463 绿（stall 10→13：无回执即时 FAIL/提交评论不算回执/
人工复述标题非回执；沙箱 test_issue_reject_receipt 3 例正则提取真函数
三态；hosting 和解 4 例；parse 3→8：嵌套 verdict 不具顶层资格/坏外层
后兄弟对象恢复/全量 fence 优先/未闭合外层嵌套拒绝/散文花括号平衡跳过
后恢复；rejected_reconcile +2：复述标题不冒充回执/回执后复述
计人工）+ bash -n + check_hosting_exit 干净。

**引用**：steering/review-report-standards.md；README「拒绝 = 单一动作」。
