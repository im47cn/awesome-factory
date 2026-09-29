# ADR-007 · 2026-08-25 · forge 平台适配层（GitHub 单平台 → gh 兼容多平台）【已被 ADR-008 取代】

- **背景**：`.factory` 调用面（issue/pr/label/api/auth ≈9 读 + 9 写）结构性绑定 `gh` CLI 与 GitHub JSON 形状（state.py 消费这些形状）；Codeup（云效）托管的下游仓（gtsp-xx-gateway 等）无 gh 可用，移植被平台锁死。
- **决策**：引入 `.factory/forge`（gh 兼容 argv shim，stdlib Python）：`forge.json` 缺失 → github 后端 `exec gh`（上游零行为变化、零配置）；`backend=codeup` → 云效 REST（`openapi-rdc.aliyuncs.com`，`x-yunxiao-token`），输出同形状 JSON。调用面改动仅二进制名 `gh`→`"${FORGE}"`（feedback-upstream.sh 例外：它指向上游 GitHub，保持 gh）。issue 编号统一字符串（GitHub 数字 / Codeup 工作项序号 T-16 同构；state.py `_linked_issue` 正则放宽 `#([\w][\w-]*)`）。
- **Codeup 事实模型**（无 labels/timeline 的等价物）：issue=projex 工作项（labels 直写）；PR 侧标签/事件=MR 全局评论标记——`--add-label` 发 `[factory:label:add] X` 评论、`--remove-label` 置 resolved（内容保留→轮次计数单调，对齐 GitHub label-add 事件语义）；`reviewDecision`：`TO_BE_MERGED`→APPROVED，人工标记评论 `[factory:changes-requested]`→CHANGES_REQUESTED；`pr diff` 用本地 git（远端分支已推）。未知子命令/标志 fail-closed exit 2。
- **后果**：新增 forge（full 分发）+ forge.json（skip，每仓一份：org/repo/space/workitemType/base_branch）；`test_forge.py` 单测（零网络）+ `tests/` 162 绿；`.factory` 脚本 diff 仅 FORGE 定义块与二进制名替换。已知边界：当前云效令牌 projex 写 403（工作项 labels/评论/创建）——issue 侧状态机落地需令牌补项目管理写权限；Codeup MR 评论写已实测可用。
- **引用**：`.factory/forge`；`.factory/test_forge.py`；README「平台适配层（forge）」。


## ADR-007 补记 · 2026-08-25 · issue 侧标签描述载体与网络韧性

- **标签载体双模**（`forge.json codeup.issue_labels`）：云效 Task 类型字段配置可无 labels 字段（PUT 400 "workitem does not contains field"，非权限）。`native` 直写字段；`description` 走描述尾部 HTML 注释块 `<!-- factory:labels:v1: ... -->`——实测云效富文本完整保留注释、单字段 PUT 不触碰其余字段。读取时标记剥离不进 body，标签从原文解析。gtsp-xx-gateway 现用 description 模式（字段配置后可切回）。
- **握手级重试**：公司网关对快速连续 TLS 握手偶发 RST（SSLEOFError，2026-08-25 实测）。握手失败=请求未发出，全方法重试皆安全（非幂等 POST 不重复执行）；forge.call 内建 3 次退避。

## ADR-007 勘误与补记 · 2026-08-26 · 评论权限放开 + issue create 破案

- **勘误（上节「已知边界」）**：「projex 写 403（工作项 labels/评论/创建）」已过时——令牌权限 2026-08-26 放开后实测：工作项**评论读/写全通**、**标签写（description 模式）全通**。issue 侧状态机链路（triage 落标/回执评论）完整可用。
- **issue create 破案**（此前误判 403/字段不可发现）：根因是 create API 无「计划开始时间」本体字段——它是模板层 SystemCustomField，必经 `customFieldValues {"fieldId":"value"}` **平面对象**（数组形态报 Invalid format）。fieldId 由字段配置接口发现（`GET projects/{spaceId}/workitemTypes/{wit}/fields`，实测 79/80=计划起止、101586=预计工时）；value 形态：date=ISO、float=小数字符串、list=**option id**（非文本）；assignedTo=24-hex 用户 id（配置 `forge.json codeup.assign_user_id`，成员端点不可达）。forge 现按字段配置自动构造默认值，create 全链路实测打通（gtsp-xx-gateway）。
- **空体容错**：云效写操作可返回 200+空 body；`json.load` 裸崩改为按长度守卫返回 `{}`。
- ~~权限矩阵（首测）~~：首测口径（评论写 403）已被上行勘误推翻，勿引用；当前有效矩阵见 forge「Codeup 工作项 OpenAPI 实测知识」（skills/alibabacloud-devops/SKILL.md）——权限随令牌 scope 动态，**以复验为准**。
