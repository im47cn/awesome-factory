# 澄清统一方法与业务来源协议

公共方法只保留 awesome-rules/skills/factory-clarification 一份。交互模式主持查证与提问，Factory 模式只生成澄清节点产物；admin 的 clarify-requirements 仅为本项目入口与输出位置适配。项目 docs 仍是知识正文，不复制为另一套知识台账。

当前 workflow/4、clarification/3。需求分析先提取行为候选，澄清节点逐项确认或转为问题；规范来源由项目 `.factory/factory-local.json` 的 business_sources 登记：

```json
{
  "schema": "factory-local/business-source-policy/1",
  "sources": [{
    "id": "module-rules",
    "path": "src/views/callback/README.md",
    "kind": "normative",
    "status": "active",
    "authority": "project-maintained",
    "reviewed_sha256": "维护者核对原文后填写精确 SHA256",
    "scope_paths": ["src/views/callback/"]
  }]
}
```

初次接入为空列表，不自动扫描文档并赋予权威。维护者核对现有文件分类、有效性与适用范围后登记；不记录虚构的审批人。现有 docs_context 的按需阅读不等于取得规范引用资格。只有 active normative 可引用，historical/record/guidance 和 superseded/reference 被排除。路径仅支持项目内精确文件或以 `/` 结尾的目录前缀，不接受通配符、越界与符号链接；规范总量超过 128 KiB 直接阻塞，不截断。

运行器检查 reviewed_sha256 并冻结 business-sources.snapshot.json，含来源原文、内容版本、范围与策略摘要。澄清绑定该快照摘要；`project-rule` 引用须带 source_id/source_sha256/source_version/quote，intent=preserve，targets 精确列出受约束文件。Spec 必须包含这些文件并映射全部规则到 AC。规范只保留现有约束，不能授权新需求；具体业务语义是否被引文支持仍由澄清方法与独立审查核对。

用户要求与现行规范冲突时，保留原话、登记 source_conflicts 并关联阻塞问题。不得改规范摘要、自称已确认或把业务冲突改成工程假设。需要改变现行规范时，由维护者另行审核并更新登记版本，再从新的输入重跑；用户答复文件本身不自动改写项目规范。运行器检查已声明冲突与历史未答问题，不能自动推理所有未声明的语义冲突。

## 升级与恢复

核对代码/Skill/项目来源配置后，将项目 workflow_version 升为 workflow/4，更新薄 bridge，显式生成 runtime-lock.json，并审核更新 installation.json。执行 check-runtime、项目 setup check、共享回归与项目质量入口。Skill 所有 references 正文均锁定；节点只注入本阶段必需参考资料。

旧 clarification/1、clarification/2 与 workflow/2、workflow/3 收据不兼容。保留旧 attempts，按恢复指引从 classify 重跑，不手改产物版本或 passed 状态；输入扩大使用关联修订。普通 check 不自动登记规范、刷新内容锁或制造答复。

## 回放评测

历史原话与有限上下文/参考判据分别位于 awesome-rules/skills/factory-clarification/evals。工具 `tools/evaluate-clarification.py` prepare 生成无 oracle 的工作区，独立生成、独立判读后 score；缺报告、输入/候选/判据漂移、缺评条目与语义问题均不能成为通过。具体命令、判读 schema 和评价边界见该集 README。代理判据尚未经业务维护者正式评审，不能当生产验收或 Superpowers 对照成绩。
