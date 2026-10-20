# 冻结验收契约与逐项证据

## 接入与批准

旧下游配置默认未启用。负责人在 `factory-local.json` 增加：

```json
{"acceptance_required": true, "acceptance_contract_dir": ".factory/acceptance-contracts"}
```

同时保证业务仓的 MISSION/perimeter 保护整个 `.factory/` 或契约目录及 runner，
通过受保护分支的人类 PR 提交契约；不能由待测 Agent 修改审批、验证器或收据。
本上游仓未配置真实任务契约，保持旧演练兼容；不能把未启用模式视为完整需求验收。

契约样例（`issue-42.json`）：

```json
{
  "schema": "acceptance-contract/1",
  "requirement_id": "REQ-42",
  "goal": "重复取消只释放一次库存",
  "non_goals": ["不改退款"],
  "acceptance": [{
    "id": "AC-01", "criterion": "重复调用不会重复释放库存",
    "argv": ["python3", "-m", "pytest", "tests/test_cancel.py::test_idempotency", "-v", "-o", "addopts="],
    "test_files": ["tests/test_cancel.py"], "timeout_seconds": 300
  }],
  "approval": null
}
```

先准备独立验收测试（可以针对尚未实现行为写红测），由人确认目标、范围、命令及测试断言后执行：

```bash
python3 .factory/acceptance.py approve .factory/acceptance-contracts/issue-42.json --by reviewer-name --repo .
```

批准命令将每个 test_files 的字节摘要写入 test_hashes，摘要参与批准版本。
执行前拒绝与批准字节不同的测试；新增/修改验收测试必须重批。
批准命令不验证人的身份；真实授权由文件所有权、分支审批与账号权限保证。
argv 是可执行权限，必须人工审查，不从 issue/模型输出自动抽取执行。
无 shell=True；这也不是运行沙箱，命令权限与网络范围须由 CI/容器限制。

## 运行与链路

`fix-issue.sh` 在 prime/plan/implement 之前冻结批准契约；backstop 提交及全量测试后
逐项复跑；Holdout 前和 push 前检查 commit/tree、契约、测试和日志指纹。
逐项失败立即停止，不允许模型裁决推翻失败。Holdout 只读契约和运行证据，不读计划。

`validate-pr.sh` 使用 PR 中 Closes 关联的 issue 契约，启用时要求当前 checkout
与 origin 上 PR 源分支 HEAD 一致，最终打标签前再次核对远端未移动（同仓 PR；fork PR 不支持此路径），重新执行验收。
不要在其他分支上拿测试结果给 PR 背书。

收据 `acceptance-evidence/1` 保留 requirement_id/contract_hash/commit/tree/时间/
逐条 ID、argv、测试摘要、exit_code、日志摘要。单命令的断言强度由审查负责。
`test_files` 须列出验证脚本与直接测试辅助依赖；遗漏的外部工具/环境无法靠指纹证明，
项目应固定解释器、模型、依赖和服务版本。工作树在验收前后必须干净且身份相同。

手动复跑：

```bash
python3 .factory/acceptance.py freeze /evidence/approved.json /evidence/frozen.json
python3 .factory/acceptance.py run --source /evidence/approved.json --frozen /evidence/frozen.json --repo /business/repo --receipt /evidence/fixed.json
python3 .factory/acceptance.py verify --source /evidence/approved.json --frozen /evidence/frozen.json --repo /business/repo --receipt /evidence/fixed.json
```

输出路径与冻结快照放在业务仓外或受保护的 ignored artifacts 区。

## 事故红绿闭环

在加入回归测试但旧缺陷尚存的已提交基线上，用同一命令加 `--phase baseline`
生成红端。至少一项正常断言失败（exit 1），其他退出码和超时都视为无效运行；
修复并提交后 `--phase fixed` 必须全部成功。测试字节保持不变。
两份收据交给规则仓的 `scripts/incident_check.py`：核对 Git 祖先关系、测试字节、
原始日志、回归保留与经验处置（pending 候选或有理由的不晋升）。只产生本地证据，
不自动创建远程 issue，不自动应用经验条款。

## 移植与边界

`acceptance.py`、`acceptance-lib.sh` 是 full 分发；本 README、每仓批准契约为 skip。
下游配置显式启用，缺契约/批准失配/路径越界/运行错误均非零退出。
收据哈希用于防漂移，不是身份签名。可信 runner 和归档写权限是必要条件；能同时
篡改收据与日志的执行者仍可伪造，需由 CI 身份、受控目录/沙箱解决。
测试可能在最终一次身份检查后发生并发变化，部署仍须绑定不可变发布产物；本功能
仅提供当前任务与 PR 的验收证据，不提供生产发布证明。

测试入口：`python3 -m pytest .factory/tests/test_acceptance.py -o addopts= -q`。

运行要求：Python 3.9+（推荐3.12）；执行Shell时PATH里的 python3 须与测试解释器一致。
PR最后检查到标签写入仍存在远端竞态窗口；validated标签不能替代绑定commit SHA的
平台required checks，分支保护必须拒绝用旧提交的状态背书新提交。
