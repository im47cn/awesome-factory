# 三项公共能力迁移验证（2026-10-26）

本次只迁移需求澄清、独立审查、验收环境预检及其公共依赖，不提交推送。业务行为代码未修改。

| 分层 | 最终落点 |
| --- | --- |
| 方法与输出协议 | awesome-rules/skills/factory-clarification、factory-independent-review、factory-environment-preflight |
| 编排、确定性门禁、证据、恢复 | awesome-factory/runtime/local |
| 公共回归与安装模板 | awesome-factory/tests/local、templates/local、tools/factory-local-setup.py |
| admin 配置与适配 | .factory/factory-local.json、runtime-lock.json、installation.json、薄入口和项目适配回归 |

admin 原有 MISSION、项目周界 guard/缺陷样例和格式回归保留；既有交互澄清 Skill 保留，仅更新 Factory 实现位置引用。没有覆盖并行任务对其正文和需求文档的其他修改。

## 实际验证

- 三个新 Skill 通过 skill-creator quick_validate；真实技能目录与路由配置两项检查通过。
- 公共 unittest 76 项通过。覆盖完整十阶段、业务澄清与原始答复、未决问题恢复、环境时效与替换攻击、Mock 不取消真实环境义务、独立审查与准确差异、必验项控制完成、实际范围、修订链接和生成文件恢复。
- 新增接入回归实跑两个临时 Git 项目：命名空间/ROOT/命令隔离、缺运行时、错误锁、缺/变更 Skill、篡改公共测试 helper 均按预期通过或阻断。公共测试输入与其执行模板均纳入摘要锁。
- 既有运行时、初始化和旧外部协议组合测试共 168 项：全量首轮 167 通过，1 项旧夹具仅将第一项验证设为可选，未处理新增的浏览器必验项；改为将全部验证设为可选后，该负向用例单独复跑通过。原始失败和重跑日志均保留，没有放宽运行器门禁。
- admin 通过同一 test:factory 入口执行公共 76 项和项目 3 项回归。前端 37 文件/522 用例通过；治理单测 34 文件/497 用例通过，111 个治理文件全部满足普通 80%/核心 90% 的逐文件行覆盖率门禁；治理范围行覆盖率为 2343/2384 = 98.28%。
- 隔离副本完整质量入口最终 9/9 通过：格式、ESLint、Stylelint、类型、test:ci、三项契约检查和开发构建。接入 check 的工具、锁定、guard 和变异检查全部通过。
- 独立只读审查发现的自定义 rules_path、预检方法指导、初始化 smoke 测试、执行闭包锁定及动态加载问题均修复；复核未发现新的阻断缺陷。

## 证据与限制

admin 的本地证据位于 `artifacts/factory-local/publicization-20261026/`，包含输入清单、源/索引审计、质量收据、日志与 result.json。这些是运行产物，不入 Git。

原工作树的接入检查中，四项变异测试因既有 MISSION/配置脏改动主动跳过，不能据此称全部通过。隔离副本使用最终源文件建立临时测试基线后，接入检查完整通过。

完整格式检查另识别出本次开始前即存在的 `docs/requirements/callback-batch-disable-real-20261026/requirement.md` 格式问题。原文件摘要保持不变；仅在隔离副本格式化该 Markdown 后完成全套质量验证，差异记录在 validation-only-variation.json。首次并行执行接入检查与质量检查，生成的变异证据 stamp 也被完整性检查正确阻断；接入完成后建立稳定测试基线、顺序重验，最终源/索引未出现非预期变化。

确定性阶段、CLI、命令和证据校验均真实执行；测试中的模型响应和环境观察是明确的受控夹具。迁移后尚未重新执行付费模型的完整业务任务，也未验证真实接口/账号环境或云效平台侧多仓部署。

部署必须同时提供 runtime-lock 锁定的 awesome-factory 与 awesome-rules，不能只 checkout admin；公共文件缺失或漂移时明确失败，不自动更新或降级。公共发布与平台接线需在另行授权提交推送后处理。

三个原仓库 HEAD 和暂存区摘要均与任务开始时一致。临时 Git 项目的测试基线提交不涉及原分支或远端。
