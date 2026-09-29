# awesome-factory

维护工厂（black-box factory）本体仓——一个 issue 进、一个待人工合并的 PR 出的全自动
维护链，及其分发、租约仲裁、反馈体系。2026-09-29 自
[awesome-rules](https://github.com/im47cn/awesome-rules) 整体迁出，成为 full 面
文件的唯一上游真相源。

- 运维手册：[.factory/README.md](.factory/README.md)
- 治理宪法：[MISSION.md](MISSION.md)
- 设计文档与决策：[docs/design/](docs/design/)、[docs/adr/](docs/adr/)
- 下游消费：各仓 `bash .factory/sync-from-upstream.sh ~/sources/awesome-factory`
  （factory-local.json 的 `upstream_repo` / `upstream_path` 指向本仓）

## 快速开始

```bash
bash .factory/dispatch.sh --dry-run               # 派发器单轮演练
.factory/fix-issue.sh 42 --dry-run                # 单 issue 链干跑
python3 -m pytest .factory/tests -o addopts= -q   # 工厂测试套件
```

前置条件与完整手册见 [.factory/README.md](.factory/README.md)。
