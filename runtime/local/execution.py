"""Cooperative worktree lease, immutable runtime copies and receipt-based summaries."""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid


def git_directory(root):
    return Path(subprocess.check_output(['git', 'rev-parse', '--absolute-git-dir'], cwd=root, text=True).strip())


class RepositoryLease:
    def __init__(self, root, label='runner'):
        self.root, self.label = root, label

    def __enter__(self):
        path = git_directory(self.root) / 'factory-runner.lock'
        self.stream = path.open('a+')
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.seek(0)
            try:
                owner = json.loads(self.stream.read())
            except ValueError:
                owner = {}
            self.stream.close()
            inherited = os.environ.get('FACTORY_RUN_LEASE_TOKEN')
            if self.label == 'quality' and inherited and owner.get('token') == inherited and owner.get('root') == str(self.root):
                self.stream = None
                return self
            raise ValueError('当前工作目录已有 Factory 执行；请等待结束，或在独立 Git worktree 中运行另一任务')
        self.previous_token = os.environ.get('FACTORY_RUN_LEASE_TOKEN')
        self.token = uuid.uuid4().hex
        os.environ['FACTORY_RUN_LEASE_TOKEN'] = self.token
        self.stream.seek(0)
        self.stream.truncate()
        self.stream.write(json.dumps({'pid': os.getpid(), 'root': str(self.root), 'owner': self.label,
                                      'token': self.token, 'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}))
        self.stream.flush()
        return self

    def __exit__(self, *args):
        if self.stream is None:
            return
        self.stream.close()  # Never unlink a lock inode while another process may hold it.
        if self.previous_token is None:
            os.environ.pop('FACTORY_RUN_LEASE_TOKEN', None)
        else:
            os.environ['FACTORY_RUN_LEASE_TOKEN'] = self.previous_token


def freeze_runtime(project, source, lock):
    """Execute pinned bytes from Git metadata, outside source/watch paths; never repin."""
    files = lock.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('运行时冻结缺少文件清单')
    identity = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    directory = git_directory(project) / 'factory-runtime' / identity
    def verify(base):
        for name, expected in files.items():
            path = base / name
            if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
                    or not path.resolve().is_relative_to(base.resolve()) or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError('冻结运行时缺失或漂移：' + name)
    verify(source)
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.freeze-', dir=directory.parent))
        try:
            for name in files:
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / name, target)
            verify(temporary)
            try:
                temporary.rename(directory)
            except OSError:
                if not directory.exists():
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    verify(directory)
    return directory


def summarize_attempt(directory, state, commands):
    path = directory / 'events.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    starts, stages = {}, []
    for row in events:
        stage, status = row['stage'], row['status']
        if status == 'started':
            starts[stage] = row['time']
        elif stage in starts:
            started = starts.pop(stage)
            stages.append({'stage': stage, 'status': status, 'started_at': started,
                           'finished_at': row['time'], 'duration_seconds': max(0, (
                               dt.datetime.fromisoformat(row['time']) - dt.datetime.fromisoformat(started)).total_seconds()),
                           'reason': row.get('reason'), 'exit_code': row.get('rc')})
    for stage, started in starts.items():
        stages.append({'stage': stage, 'status': state['status'], 'started_at': started,
                       'finished_at': state['updated_at'], 'duration_seconds': max(0, (
                           dt.datetime.fromisoformat(state['updated_at']) - dt.datetime.fromisoformat(started)).total_seconds())})
    failure = next((row for row in commands if row['exit_code'] != 0), None)
    return {'schema': 'factory-local/execution-summary/1',
            **{key: state.get(key) for key in ('task_id', 'attempt_id', 'status', 'rc', 'reason', 'recovery_command', 'started_at', 'updated_at')},
            'duration_seconds': (dt.datetime.fromisoformat(state['updated_at']) - dt.datetime.fromisoformat(state['started_at'])).total_seconds()
            if state.get('started_at') and state.get('updated_at') else None,
            'stages': stages, 'commands': commands, 'first_command_failure': failure,
            'counts': {'executed_commands': len(commands),
                       'failed_commands': sum(row['exit_code'] != 0 for row in commands)},
            'boundary': '阶段等待/阻断按状态记录；非零命令不是任务失败率，也不推断人工介入次数'}
