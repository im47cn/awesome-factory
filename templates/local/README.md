# 本地 Factory 项目适配

项目只维护 factory-local.json、runtime-lock.json、installation.json 及薄入口。运行方法、升级与证据边界见公共仓 docs/design/shared-local-workflow.md。

```bash
python3 .factory/setup.py check .
python3 .factory/run.py --check-runtime
python3 .factory/run.py --task pilot --request-file /path/to/request.md --only-stage spec-review
python3 .factory/quality-gate.py
python3 .factory/test.py
```

公共源码与 Skill 缺失/漂移时阻断；检查不自动刷新锁。配置使用 upstream_path/rules_path 定位公共仓，部署必须提供对应锁定版本。仅生成 Spec 不授权实施，普通恢复不扩大授权。业务文档保存在 docs，任务日志与快照留在 artifacts/factory-local，不入 Git。

采用 workflow/4、clarification/3。分析授权默认截止编码前独立 `spec-review`；需求分析、澄清和 Spec 都有结构化契约及确定性校验。business_sources 初始为空，由维护者按公共 docs/design/clarification-business-sources.md 登记现行规范、精确摘要与适用目录；不从历史草案或源码自动升级规则。
