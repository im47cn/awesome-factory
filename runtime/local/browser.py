"""Trusted browser adapter. Business assertions come from a reviewed project plan."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .policy import relative_path, sensitive_path


def execute(root, task, directory, expected, config, run, write_json, sha, exclude_ids=()):
    settings = config.get('browser_executor')
    if not settings:
        return None
    if not isinstance(settings, dict) or settings.get('provider') != 'playwright':
        raise ValueError('browser_executor.provider 仅支持 playwright')
    name = relative_path(settings.get('plan'))
    plan_path = root / name
    if (sensitive_path(name) or not plan_path.is_file() or not plan_path.resolve().is_relative_to(root)
            or any(p.is_symlink() for p in (plan_path, *plan_path.parents) if p.is_relative_to(root))):
        raise ValueError('浏览器计划缺失、敏感或经过符号链接')
    plan_hash = sha(plan_path)
    plan = json.loads(plan_path.read_text())
    requested = {row['id']: row for row in expected['checks']}
    if not isinstance(plan, dict) or plan.get('schema') != 'factory-local/browser-plan/1':
        raise ValueError('browser-plan schema 无效')
    checks = plan.get('checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('浏览器计划必须包含真实断言')
    seen = set()
    for row in checks:
        if (not isinstance(row, dict) or row.get('id') not in requested or row['id'] in seen
                or row.get('environment') != requested[row['id']]['environment']):
            raise ValueError('浏览器计划 ID/环境与 Spec 不一致')
        seen.add(row['id'])
    checks = [row for row in checks if row['id'] not in exclude_ids]
    if not checks:
        return None
    plan = {**plan, 'checks': checks}
    url = settings.get('base_url', '')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('browser_executor.base_url 必须为无凭证的 HTTP 地址')
    if any(requested[row['id']]['environment'] in {'local', 'mock'} for row in checks) and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('local/mock 浏览器只能连接本机地址；真实环境须声明 test-environment')
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / 'plan.json', plan)
    write_json(directory / 'request.json', {**expected, 'checks': [requested[row['id']] for row in checks],
                                          'base_url': url, 'executable_path': settings.get('executable_path'),
                                          'plan_sha256': plan_hash})
    server = None
    server_log = None
    try:
        serve = settings.get('serve')
        if serve:
            argv = serve.get('argv') if isinstance(serve, dict) else None
            if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
                raise ValueError('browser_executor.serve.argv 必须是维护者配置的命令')
            if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
                raise ValueError('Factory 只能启动本机验收服务')
            # Refuse an existing listener instead of accidentally accepting another task's app.
            if reachable(url):
                raise ValueError('浏览器验收端口已被占用，请配置独立端口；不会终止既有服务')
            server_log = (directory / 'server.log').open('w')
            server = subprocess.Popen(argv, cwd=root, stdout=server_log, stderr=subprocess.STDOUT,
                                      start_new_session=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            timeout = serve.get('timeout_seconds', 30)
            if type(timeout) is not int or not 1 <= timeout <= 60:
                raise ValueError('服务等待时间应为 1..60 秒')
            deadline = time.monotonic() + timeout
            while not reachable(url):
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('验收服务未就绪，查看 browser/server.log')
                time.sleep(.1)
        timeout = settings.get('timeout_seconds', 120)
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise ValueError('浏览器超时应为 1..900 秒')
        # The driver is pinned Factory code; no command is accepted from the model's Spec.
        result = run(['node', str(Path(__file__).with_name('browser-driver.mjs')),
                      str(directory / 'request.json'), str(directory / 'plan.json'), str(directory)],
                     log=task / 'browser-executor.log', check=False, timeout=timeout,
                     env={'FACTORY_BROWSER_PROJECT': str(root)})
        receipt = directory / 'receipt.json'
        if not receipt.is_file():
            raise ValueError('浏览器执行器未生成收据；检查 Playwright 安装和 browser-executor.log')
        if sha(plan_path) != plan_hash:
            raise ValueError('浏览器执行期间验收计划发生变化')
        value = json.loads(receipt.read_text())
        if result.returncode and all(row.get('status') == 'passed' for row in value.get('checks', [])):
            raise ValueError('浏览器退出失败但自报通过，拒绝收据')
        return receipt
    finally:
        if server is not None:
            try:
                os.killpg(server.pid, signal.SIGTERM)
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()
            except ProcessLookupError:
                pass
        if server_log:
            server_log.close()


def reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=.5) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
