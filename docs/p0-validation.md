# P0 本地候选基线验证

> 历史记录：对应 2026-10-12 的 `chore/p0-runtime` 分支。以下版本、测试结果及发布状态均为当时快照，不代表当前合并分支的验证结果。

固定运行环境：Python 3.12.14 / Git 2.53.0 / omp 18.10.1 / Bun 1.3.14。

原系统环境套件结果：16 failed、533 passed、15 skipped、36 errors。问题集中在系统 Git不支持新参数和测试夹具强制使用旧系统工具。

本分支保留复制的已有验收功能改动，并新增三个测试辅助文件修正。仅暴露已选 Python/Git/Bash入口；Git/Bash exec包装保持原运行器寻址，不引入其宿主目录下其他 CLI。独立审查通过。

最终全量命令：

```sh
PATH=/Users/wanglu/.local/share/wanlianyida-factory-runtime/venv/bin:/Users/wanglu/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback:$PATH python3 -m pytest .factory/tests -o addopts= -q
```

结果：586 passed, 15 skipped in 139.16s。跳过不计作已验证；测试包含夹具验证，不证明真实云效接口权限或完整业务链。

独立无工具模型探针成功，返回 FACTORY_P0_OK。云效 API认证待配置，未进行远程写入。此版本为本地候选，尚未发布上游。
