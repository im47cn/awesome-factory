"""Content-based change accounting and reversible, explicit generated outputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


GENERATED_OUTPUTS = frozenset({
    'types/components.d.ts', 'types/auto-imports.d.ts', '.eslintrc-auto-import.json',
})


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=root)


def snapshot(root: Path) -> dict:
    """Include working content/mode, index entries, untracked files and deletions."""
    names = set(git(root, 'ls-files', '-z').split(b'\0'))
    names.update(git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'))
    index = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if entry:
            metadata, name = entry.split(b'\t', 1)
            index.setdefault(os.fsdecode(name), []).append(os.fsdecode(metadata))
    files = {}
    for raw in sorted(names - {b''}):
        name = os.fsdecode(raw)
        path = root / name
        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
            kind = 'symlink'
        elif path.is_file():
            content = path.read_bytes()
            kind = 'file'
        elif not path.exists():
            content, kind = b'', 'missing'
        else:
            raise ValueError(f'Unsupported source entry: {name}')
        files[name] = {'kind': kind, 'sha256': hashlib.sha256(content).hexdigest(),
                       'mode': stat.S_IMODE(path.lstat().st_mode) if kind != 'missing' else None,
                       'index': index.get(name, [])}
    return {'head': git(root, 'rev-parse', 'HEAD').decode().strip(), 'files': files}


def changed_files(before: dict, after: dict) -> list[str]:
    if before['head'] != after['head']:
        raise ValueError('HEAD changed during Factory execution')
    return sorted(name for name in before['files'].keys() | after['files'].keys()
                  if before['files'].get(name) != after['files'].get(name))


def dirty_files(root: Path) -> set[str]:
    names = git(root, 'diff', 'HEAD', '--name-only', '-z').split(b'\0')
    names += git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    return {os.fsdecode(name) for name in names if name}


def legacy_candidates(root: Path, allowed: list[str]) -> list[str]:
    """Old filename lists cannot prove ownership; conservatively include dirty scope.

    Never call this an exact reconstruction. Existing out-of-scope dirty files are
    preserved as the migration baseline; subsequent edits are compared by content.
    """
    dirty = dirty_files(root)
    return sorted(dirty & set(allowed))


class GeneratedTransaction:
    """Restore only registered generator outputs, never arbitrary source/index edits."""

    def __init__(self, root: Path, names: list[str], evidence: Path):
        if not isinstance(names, list) or any(not isinstance(n, str) or n not in GENERATED_OUTPUTS for n in names):
            raise ValueError('generated_files must contain only supported exact output paths')
        if len(set(names)) != len(names):
            raise ValueError('duplicate generated output')
        self.root, self.evidence = root, evidence
        self.saved = {}
        evidence.mkdir(parents=True, exist_ok=False)
        for name in names:
            path = root / name
            self.check_path(path)
            data = path.read_bytes() if path.exists() else None
            self.saved[name] = (data, stat.S_IMODE(path.stat().st_mode) if data is not None else None)
            if data is not None:
                backup = evidence / 'before' / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(data)

    def check_path(self, path: Path) -> None:
        if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(self.root)):
            raise ValueError(f'Generated output cannot use symlinks: {path}')
        if not path.resolve().is_relative_to(self.root.resolve()) or (path.exists() and not path.is_file()):
            raise ValueError(f'Invalid generated output: {path}')

    def restore(self) -> list[dict]:
        records = []
        for name, (original, mode) in self.saved.items():
            path = self.root / name
            self.check_path(path)
            current = path.read_bytes() if path.exists() else None
            if current != original or (path.exists() and stat.S_IMODE(path.stat().st_mode) != mode):
                if current is not None:
                    after = self.evidence / 'generated' / name
                    after.parent.mkdir(parents=True, exist_ok=True)
                    after.write_bytes(current)
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(path.name + '.factory-restore')
                    # Exclusive creation prevents following an unexpected symlink.
                    with temporary.open('xb') as output:
                        output.write(original)
                    temporary.chmod(mode)
                    temporary.replace(path)
                records.append({'path': name, 'before_sha256': self.hash(original),
                                'generated_sha256': self.hash(current), 'restored': True})
        (self.evidence / 'restoration.json').write_text(json.dumps(records, indent=2) + '\n')
        return records

    @staticmethod
    def hash(data: bytes | None) -> str | None:
        return hashlib.sha256(data).hexdigest() if data is not None else None
