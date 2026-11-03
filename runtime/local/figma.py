#!/usr/bin/env python3
"""Capture Figma node facts for the local Factory design hand-off.

Authentication is read from FIGMA_TOKEN and is never written to artifacts.
The adapter is deliberately deterministic: it fetches node JSON and an exported
PNG from the Figma REST API, then emits the two artifacts consumed by the
awesome-rules Figma skills.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API = "https://api.figma.com/v1"


def fail(task: Path, code: str, message: str, *, action_required: list[str] | None = None) -> int:
    receipt = {
        "schema": "factory-local/figma-capture/1",
        "provider": "figma-rest",
        "status": "blocked",
        "code": code,
        "message": message,
    }
    if action_required:
        receipt["action_required"] = action_required
    task.mkdir(parents=True, exist_ok=True)
    (task / "figma-capture-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return 1


AUTH_ACTIONS = [
    "1. 在浏览器打开 https://www.figma.com/ 并确认已登录目标账号。",
    "2. 进入 Figma：头像菜单 → Settings → Security → Personal access tokens，创建或复制 Token。",
    "3. 仅在当前终端设置环境变量：export FIGMA_TOKEN='<token>'；不要写入仓库、.env.dev 或日志。",
    "4. 在原 Factory 任务目录重新执行原命令；连接器会重新采集并继续设计契约阶段。",
]


def parse_url(url: str) -> tuple[str, str] | None:
    match = re.search(r"/design/([^/?#]+)", url)
    parsed = urllib.parse.urlparse(url)
    node_id = urllib.parse.parse_qs(parsed.query).get("node-id", [None])[0]
    if not match or not node_id:
        return None
    # Share links use hyphens; REST node and image maps use colon-separated IDs.
    return urllib.parse.unquote(match.group(1)), node_id.replace("-", ":")


def request_json(token: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        API + path,
        headers={"X-Figma-Token": token, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Figma API response must be an object")
    return payload


def request_bytes(token: str, url: str) -> bytes:
    request = urllib.request.Request(url, headers={"X-Figma-Token": token})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def walk(node: dict[str, Any]):
    yield node
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            yield from walk(child)


def color(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(value.get(key), (int, float)) for key in ("r", "g", "b")):
        return None
    return {key: round(float(value[key]), 4) for key in ("r", "g", "b", "a") if key in value}


def facts(root: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    nodes = list(walk(root))
    bounds = root.get("absoluteBoundingBox") or {}
    layout = {
        "root_name": root.get("name", ""),
        "root_type": root.get("type", ""),
        "width": bounds.get("width"),
        "height": bounds.get("height"),
        "child_count": len(root.get("children", []) or []),
    }
    tokens: dict[str, Any] = {}
    components: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    for node in nodes:
        name = node.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        fills = node.get("fills") or []
        for fill in fills:
            if isinstance(fill, dict) and fill.get("visible", True) and (rgb := color(fill.get("color"))):
                tokens.setdefault("fills", []).append({"node": name, "color": rgb, "type": fill.get("type")})
                break
        styles = node.get("style") or {}
        if isinstance(styles, dict) and styles.get("fontFamily"):
            tokens.setdefault("text", []).append({
                "node": name,
                "fontFamily": styles.get("fontFamily"),
                "fontSize": styles.get("fontSize"),
                "fontWeight": styles.get("fontWeight"),
            })
        if node.get("cornerRadius") is not None:
            tokens.setdefault("radius", []).append({"node": name, "value": node.get("cornerRadius")})
        if node.get("type") not in {"DOCUMENT", "CANVAS"} and len(components) < 50:
            components.append({"name": name, "states": ["default"], "type": node.get("type")})
        for reaction in node.get("reactions", []) or []:
            if isinstance(reaction, dict):
                interactions.append({"name": name, "type": reaction.get("action", {}).get("type", "prototype"), "reaction": reaction})
    if not components:
        components = [{"name": str(root.get("name", "Figma node")), "states": ["default"], "type": root.get("type")}]
    if not interactions:
        interactions = [{"name": "none-observed", "type": "none", "source": "figma-node-tree"}]
    if not tokens:
        tokens = {"observed": "Figma node returned no paint or text token"}
    return layout, components, tokens, interactions


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


def urls_from_request(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"https?://[^\s)<>]+figma\.com[^\s)<>]*", text, flags=re.I)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从 Figma REST API 采集节点截图和设计事实")
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--scope", action="append", default=["src"])
    args = parser.parse_args(argv)
    token = os.environ.get("FIGMA_TOKEN", "").strip()
    if not token:
        return fail(
            args.task,
            "figma_auth_required",
            "未检测到 FIGMA_TOKEN，当前未完成 Figma 认证；Factory 已中断，不会猜测设计事实或使用匿名接口。",
            action_required=AUTH_ACTIONS,
        )
    if not args.request_file.is_file():
        return fail(args.task, "request_missing", f"需求文件不存在: {args.request_file}")
    urls = urls_from_request(args.request_file)
    if not urls:
        return fail(args.task, "figma_url_missing", "需求中没有可解析的 Figma URL")

    evidence_dir = args.task / "figma-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_nodes: list[dict[str, Any]] = []
    input_nodes: list[dict[str, Any]] = []
    first_source = urls[0]
    for index, source_url in enumerate(urls, start=1):
        parsed = parse_url(source_url)
        if not parsed:
            return fail(args.task, "figma_url_invalid", f"无法解析 file key 或 node-id: {source_url}")
        file_key, node_id = parsed
        try:
            node_payload = request_json(token, f"/files/{urllib.parse.quote(file_key, safe='')}/nodes?ids={urllib.parse.quote(node_id, safe='')}")
            node_result = node_payload.get("nodes", {}).get(node_id)
            document = node_result.get("document") if isinstance(node_result, dict) else None
            if not isinstance(document, dict):
                return fail(args.task, "figma_node_missing", f"Figma API 没有返回节点: {node_id}")
            image_payload = request_json(token, f"/images/{urllib.parse.quote(file_key, safe='')}?ids={urllib.parse.quote(node_id, safe='')}&format=png&scale=1")
            image_url = image_payload.get("images", {}).get(node_id)
            if not isinstance(image_url, str) or not image_url:
                return fail(args.task, "figma_image_missing", f"Figma API 没有返回节点图片: {node_id}")
            image = request_bytes(token, image_url)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                return fail(
                    args.task,
                    "figma_auth_required",
                    f"Figma API 返回 HTTP {exc.code}，Token 无效、过期或无权访问该文件；Factory 已中断。",
                    action_required=AUTH_ACTIONS,
                )
            return fail(args.task, "figma_api_failed", f"Figma API 返回 HTTP {exc.code}: {exc.reason}")
        except Exception as exc:  # network/auth/API errors become a stable gate result
            return fail(args.task, "figma_api_failed", f"Figma API 采集失败: {exc}")
        dimensions = png_dimensions(image)
        if not dimensions:
            return fail(args.task, "figma_image_invalid", f"节点图片不是有效 PNG: {node_id}")
        filename = f"figma-{index}-{node_id.replace(':', '_')}.png"
        (evidence_dir / filename).write_bytes(image)
        relative = str((evidence_dir / filename).relative_to(args.task))
        captured_at = dt.datetime.now(dt.timezone.utc).isoformat()
        evidence_nodes.append({
            "source_url": source_url,
            "node_id": node_id,
            "screenshot": relative,
            "viewport": {"width": dimensions[0], "height": dimensions[1]},
            "captured_at": captured_at,
        })
        layout, components, tokens, interactions = facts(document)
        input_nodes.append({
            "node_id": node_id,
            "name": str(document.get("name", node_id)),
            "screenshot": relative,
            "viewport": {"width": dimensions[0], "height": dimensions[1]},
            "layout": layout,
            "components": components,
            "tokens": tokens,
            "interactions": interactions,
        })

    combined_layout = {"nodes": [node["layout"] for node in input_nodes]}
    combined_tokens = {"nodes": [node["tokens"] for node in input_nodes]}
    combined_components = [component for node in input_nodes for component in node["components"]]
    combined_interactions = [interaction for node in input_nodes for interaction in node["interactions"]]
    (args.task / "design-evidence.json").write_text(json.dumps({"schema": "factory-local/design-evidence/1", "nodes": evidence_nodes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.task / "design-input.json").write_text(json.dumps({
        "schema": "factory-local/design-input/1",
        "source": first_source,
        "nodes": input_nodes,
        "layout": combined_layout,
        "tokens": combined_tokens,
        "components": combined_components,
        "interactions": combined_interactions,
        "implementation_scope": args.scope,
        "open_questions": ["浏览器页面截图和交互状态在实现后由 visual-guard 采集"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "schema": "factory-local/figma-capture/1",
        "provider": "figma-rest",
        "status": "passed",
        "artifacts": ["design-evidence.json", "design-input.json"],
        "node_count": len(evidence_nodes),
    }
    (args.task / "figma-capture-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
