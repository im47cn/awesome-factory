# ADR-008 · 2026-08-26 · 托管平台抽象层 hosting.py（取代 ADR-007：核心与 GitHub/Codeup 解耦）

- **背景**：链/调度/同步/验证/回归九个脚本 + factory_lib.py 直调 `gh`，GitHub
  schema（reviewDecision/labels[].name/events API）渗入核心逻辑；Codeup 仅作
  git 推送镜像（factory-state/fix-issue 的 REPO_SLUG 双 remote 扫描即为此补丁）。
- **决策**：引入 `.factory/hosting.py` 唯一平台出口——中立 schema
  （issue/pr/label_history：state=open|closed|merged、review=三态、labels=[str]）
  + 双适配器（GitHub=gh CLI 行为保持；Codeup=云效 oapi/v1 org 级端点，
  `x-yunxiao-token`，端点 TLS 失联自动切 openapi-rdc 一次）。state.py 输入
  契约切中立 schema（转移表/语义零变更，9 测试原样通过）；slug 解析自
  factory_lib 迁入 hosting；GH_REPO→FACTORY_HOSTING（默认 github）。
- **Codeup 平台缺口（文档+API 面实证，fail-closed exit 2 绝不静默降级）**：
  (a) issue create 已破案（PR #62 实测：create 本体必填仅 assignedTo/
  spaceId/subject/workitemTypeId；模板必填 SystemCustomField 经
  customFieldValues 平面对象 {"fieldId":"value"} 传，fieldId 从字段配置
  端点发现，value 形态 date=ISO/float=小数字符串/list=option id；
  assignedTo=24-hex 用户 id）——hosting.CodeupAdapter 已实装迁移（env：
  CODEUP_SPACE_ID/WORKITEM_TYPE_ID/ASSIGN_USER_ID）；工作项读/写面五方法
  （view/list/set-labels/comment/get-labels）已实装（#67，2026-08-26 live
  验收：T-18 标签 add→读回→remove→读回空、描述零残留；双键寻址
  serialNumber/id；标签载体 CODEUP_ISSUE_LABELS=native|description——
  Task 类型常无 labels 字段，description 尾部注释块为等价载体；评论端点
  仅认 24-hex id；search category 必填）；(b) MR 类标仅
  LinkMergeRequestLabel、无 Unlink——needs-fix→approved 全部换标转移
  不可表达；(c) 无标签事件时间线——轮次计数（MAX_FIX_ROUNDS）不可派生。
  故 Codeup 上链状态机的 issue 面（a）已解锁；剩余缺口集中在 MR 面
  (b)(c)（#66 评论标记模型承载）；可用面 = MR 读写/评论（comment_type
  +resolved 必填，skills 实测坑位已锁定进适配器）/合并/类标 Link + 工作项
  全套（create/view/list/labels/comment）。
- **验证边界（live 基线已锁定，2026-08-26 更新）**：GitHub 侧行为保持由
  测试（含 hosting 契约：gh 命令构造/原子换标/归一化/CLI 缺口）+ 沙箱
  端到端冒烟覆盖；**Codeup 侧 MR 面已在 gtsp-xx-gateway live 验证**
  （auth/list/view/comment/类标 Link 全通）。live 修正了五处文档推导偏差：
  ① MR 集合是**组织级端点**（无 /repositories 段 + projectIds query，
  仓库级集合 404），分页参数 perPage（非 pageSize）；② 组织级端点返回
  **裸 JSON 数组**（success 包裹仅 dict 响应有）；③ MR 详情**无 labels
  字段**，类标须专用端点读回；④ LinkMergeRequestLabel body 键是
  **labelIdList**（labelIds/labels/labelId 均拒）；⑤ label create body
  形态未破案（多形态探针均拒）——类标创建走云效界面人工路径，
  ensure 的 400 兜底语义保留。issue create 字段形态来自 PR #62 真实
  创建实测，hosting 迁移已对齐。
- **MR close 与 issue create 编号（live 2026-08-26 第二批，gtsp-xx-gateway MR#7/T-21 实测）**：
  ① close 唯一生效形态 = `POST /changeRequests/{n}/close` 空 body；**PUT 详情端点带
  `{"state":"closed"}` 返回 `{"result":true}` 但状态不变（假阳性）**——多形态探针中仅
  POST /close 改变状态。hosting 两侧补 `pr_close`（GitHub=gh pr close 直通）。
  ② issue create 响应只含 24-hex id，无 serialNumber——人类可读编号（T-N）须回查
  详情；回查失败降级 id + stderr 告警。mock 契约同步对齐（create_resp 仅 id、
  detail_resp 承载 serialNumber）。
- **后果**：MISSION 铁律 4「纯 bash + gh」措辞需人类修宪为「纯 bash/Python +
  托管适配层（零 LLM 不变）」；组件数 18→19（ADR-002 触发器 3 余量充足）；
  核心脚本自此禁直调 gh（doc-freshness R1 已盯 README 登记，新增写点必须走
  hosting 出口，租约围栏/factory-lib 收口不变量原样保留）。
- **现状更新**（2026-08-28）：铁律 4 修宪已由人类落地（措辞改为
  「纯 bash/Python + 托管适配层」，2026-08-24/08-28 两次修宪注记见
  MISSION）；triage-batch.sh 头注释「纯 bash + gh 读标签」同步更正。


## ADR-008 附记 · 2026-08-28 · MR 面缺口 (b)(c) 由评论标记模型承载（#66 人工实施）

- **背景**：缺口 (b)(c)（类标无 Unlink、无标签事件时间线）曾判定全链
  状态机在 Codeup 不可运行；承载 issue #66 经链 triage reject（判据 c：
  触周界），按处置协议转人工路径实施。
- **实施**（ADR-007 forge 期已 live 验证的等价物迁移进 hosting）：
  add 标记 = MR 评论 `[factory:label:add] X`（resolved=false）；remove =
  将该 X 未 resolved 的标记评论 PUT 置 resolved（内容保留——轮次计数
  不减，对齐 GitHub label-add 事件语义）；`label_history` = 全部 add
  标记事件流；`[factory:changes-requested]` 评论 → pr_view review
  changes_requested（与 reviewer NOTPASS 取严）。类标 Link 降级为
  best-effort 补充载体（label create 未破案，界面人工路径）。
- **验证**：39 hosting 测试全绿（新增 6：remove 置 resolved 幂等、两
  载体合并、resolved 不减计数、手势映射）；live 冒烟（gtsp MR）待
  Codeup 环境执行后补录。标记格式与 forge 字节级一致——存量标记可读。
- **后果**：真缺口仅剩 diff 全文（变更树可查路径）；`pr_close` 双定义
  事故（后者遮蔽前者）一并收口。
