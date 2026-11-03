# 统一阶段收据验收契约

本契约覆盖本地 Factory 的七个阶段：

`classify → skill-discovery → design-analysis → requirement → spec → implement → verify`

每个阶段必须输出一份 `factory-stage-receipt/1` 收据，由任务 manifest 按固定顺序引用。收据中的 `task_id`、阶段名、退出码、输入/输出文件和 evidence 必须可回读；`passed` 必须为退出码 0，`failed`/`blocked` 必须包含失败分类、原因和恢复命令。

统一验收器是 fail-closed 的：缺少收据、阶段乱序、任务漂移、证据文件缺失、失败后仍有后续阶段通过，均返回退出码 1。运行错误返回退出码 2。验收器不推进状态、不提交代码、不替代 Figma、接口或浏览器证据。

机器契约位于 `schemas/stage-receipt.schema.json` 和 `schemas/acceptance-manifest.schema.json`，本地验证入口为：

```bash
python3 tools/validate_stage_receipts.py .factory/acceptance-manifest.json --root .
```

项目仓可以将 manifest 和收据放在 `.factory/receipts/` 或其他受保护目录；Factory runner 负责写入，验收器只读。该契约与既有固定测试验收（`.factory/acceptance.py`）互补：前者证明阶段链路完整，后者证明批准的独立测试通过。
