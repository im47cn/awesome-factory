# AI 研发工厂建设规划

## 目标

将业务需求稳定地交给本地 Factory 完成：

```text
需求分类 → 技能发现 → 设计/接口证据分析 → requirement → spec
→ AI 编写代码 → 本地验证 → 质量门禁 → 分支提交 → 远端交付
```

Factory 负责编排和状态推进，规则仓负责规范与门禁，浏览器/Figma Connector 负责真实页面证据。
任何一层不能用模型自述替代另一层的证据。

## 当前基线

| 能力 | 归属 | 状态 |
| --- | --- | --- |
| 前端/后端/全栈/本地模式初始化 | awesome-factory | 已实现，本轮补回归 |
| 本地阶段状态、恢复、租约和失败收据 | 业务仓 `.factory` | 已实现 |
| Figma 设计契约和视觉验收 | awesome-rules | 已实现 |
| 验收契约、红绿事故回归 | awesome-factory + awesome-rules | 已实现 |
| 云效/GitHub 推送、MR、流水线 | Factory/DevOps 适配层 | 受远端权限和平台配置约束 |

## 阶段计划

### P0：初始化与本地闭环

- `factory-init` 自动识别 frontend/backend/fullstack。
- `--mode local` 只安装本地链路需要的文件。
- 生成 `factory-local.json`，明确 `remote_side_effects=false` 和 `auto_commit=false`。
- 本地 runner 固定阶段：`classify → skill-discovery → design-analysis → requirement → spec → implement → verify`。
- 每个阶段保存状态、收据、日志、退出码和恢复命令。

完成标准：初始化 profile 回归测试通过；业务仓可以从 request 文件恢复到任一阶段。

### P1：证据驱动的需求和设计链路

- 有 Figma 时先做 Connector 登录预检。
- 采集 `design-evidence.json` 和节点截图。
- 生成 `design-contract.json`，再生成确定性的 `visual-contract.json`。
- P0 布局必须绑定 DOM 测量、Figma 截图和页面截图。
- 没有真实设计证据时阻断，不得声称 UI 已还原。

完成标准：视觉契约、节点映射、尺寸测量和浏览器收据全部通过。

### P1：接口文档和 Mock 链路

- 先读取接口文档并冻结 method/path/请求/响应字段。
- 生成与契约一致的 Mock 数据。
- Mock 只用于本地开发和页面验证，不能替代真实接口验收。
- 接口契约变更同步实现、Mock、测试和 spec。

完成标准：接口契约检查通过，空态/错误态/加载态均有证据。

### P1：质量门禁与交付

- 开发分支提交前运行规则门禁、单测、类型检查、构建和必要的视觉门。
- 质量门禁失败不得进入远端开发分支。
- 提交、推送、MR、流水线状态分别记录，不混写为“已完成”。
- 分支保护由远端平台执行，Factory 不绕过保护规则。

完成标准：本地门禁通过，远端 ancestry 可回读，MR/流水线状态可核验。

### P2：事故闭环和持续改进

- 用户缺陷保留精确事故身份、基线、修复提交和红绿测试收据。
- 失败按 missing-context、wrong-approach、environment-issue、external-blocker 等分类。
- 同一失败签名最多重试三次，重试必须改变策略或增加证据。
- 可泛化经验必须有独立评测后才进入规则仓。

## 统一完成判定

任务只有在以下证据齐全时才能标记完成：

1. requirement 和 spec 已保存并与请求绑定；
2. 代码改动在任务分支完成；
3. 本地验证和质量门禁有退出码；
4. Figma/API/Mock 证据按任务类型完成；
5. 失败和重试有记录；
6. commit、远端分支、MR 或权限阻断状态可回读。

## 当前实施顺序

1. 本轮：为初始化 profile 增加回归测试和文档基线。
2. 下一轮：把业务仓本地 runner 的阶段收据接入统一验收契约。
3. 再下一轮：接入 Figma Connector 登录预检和浏览器证据收据。
4. 最后：接入云效分支/MR/流水线状态回读和团队级日报。
