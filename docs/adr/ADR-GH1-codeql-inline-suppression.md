# ADR-GH1 — test_hosting.py CodeQL 行内抑制（2026-08-30，上游根修）

- **背景**：xx-python-sdk 移植本工厂后，GitHub org ruleset（main）code_scanning 规则对
  `.factory/tests/test_hosting.py` `TestCodeupEndpointFallback.test_urLError_retries_rdc` 的断言报
  py/incomplete-url-substring-sanitization（high）——规则针对「URL 授权用子串包含判断」的反模式；
  此处是测试断言异常消息包含端点域名，非安全校验，属测试夹具误报（本仓托管 Codeup，无 CodeQL，
  从未暴露此告警）。
- **根修决策**：官方行内抑制注解 `# codeql[py/incomplete-url-substring-sanitization]` + 断言改
  `re.search` 形态（regex 脱离子串校验 sink 模式，规则不再匹配），上游落位后 6 仓经 sync 收敛，
  无需各仓本地补丁。
- **一致性**：与 xx-python-sdk 本地修复（ADR-GH1 首装版）同文同注解，下游零漂移。
