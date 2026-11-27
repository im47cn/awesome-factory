#!/usr/bin/env python3
"""Java comment scanner / remover / ratchet gate.

Subcommands
  scan     --repo DIR [--out FILE]          JSONL: one record per comment block
  apply    --repo DIR --decisions FILE      delete blocks marked {"action":"delete"}
  baseline --repo DIR --out FILE            per-file comment line counts (ratchet)
  check    --repo DIR --baseline FILE [f..] fail when a file exceeds its baseline
  sync     --repo DIR --baseline FILE       regenerate baseline if it drifted after a merge

The lexer understands //, /* */, /** */, string literals, char literals and
Java 15 text blocks, so `//` inside a URL or a string is never touched.
"""
import argparse
import bisect
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile


def tracked_java(repo):
    out = subprocess.run(
        ["git", "-C", repo, "ls-files", "-z", "*.java"],
        capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def line_starts(src):
    starts = [0]
    for i, c in enumerate(src):
        if c == "\n":
            starts.append(i + 1)
    return starts


class SrcRefused(Exception):
    """Refused to read a source file: symlink, not a regular file, or >10 MiB."""


MAX_SRC_BYTES = 10 * 1024 * 1024


def read_src(path):
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise SrcRefused(f"{path}: symlinked source")
    if not stat.S_ISREG(st.st_mode):
        raise SrcRefused(f"{path}: not a regular file")
    if st.st_size > MAX_SRC_BYTES:
        raise SrcRefused(f"{path}: larger than 10 MiB")
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def write_src(path, src):
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(src)


def lex(src):
    """Return [(kind, start, end)] with end exclusive; kind in line/block/javadoc."""
    n, i, out = len(src), 0, []
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            out.append(("line", i, n if j < 0 else j))
            i = n if j < 0 else j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            end = n if j < 0 else j + 2
            javadoc = src.startswith("/**", i) and not src.startswith("/**/", i)
            out.append(("javadoc" if javadoc else "block", i, end))
            i = end
        elif src.startswith('"""', i):
            j = i + 3
            while j < n:
                if src[j] == "\\":
                    j += 2
                elif src.startswith('"""', j):
                    j += 3
                    break
                else:
                    j += 1
            i = j
        elif c == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                elif src[j] == '"':
                    j += 1
                    break
                elif src[j] == "\n":
                    break
                else:
                    j += 1
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                elif src[j] == "'":
                    j += 1
                    break
                elif src[j] == "\n":
                    break
                else:
                    j += 1
            i = j
        else:
            i += 1
    return out


def code_only(src):
    """Comments removed, whitespace removed: a change here means code changed."""
    out, last = [], 0
    for _, s, e in lex(src):
        out.append(src[last:s])
        last = e
    out.append(src[last:])
    return "".join(ch for ch in "".join(out) if not ch.isspace())


def pos(starts, idx):
    li = bisect.bisect_right(starts, idx) - 1
    return li + 1, idx - starts[li]


def block_id(rel, kind, text, occ):
    """sha1 over rel, kind, text and occurrence index; occ tells identical blocks apart."""
    return hashlib.sha1(f"{rel}\0{kind}\0{text}\0{occ}".encode("utf-8", "surrogateescape")).hexdigest()[:12]


def occ_iter(src):
    """[(kind, start, end, occ)] in lex order; occ is the 0-based repeat count of (kind, text)."""
    seen = {}
    for kind, s, e in lex(src):
        key = (kind, src[s:e])
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        yield kind, s, e, occ


def next_code_line(lines, end_line):
    for k in range(end_line, len(lines)):
        s = lines[k].strip()
        if s and not s.startswith(("//", "/*", "*")):
            return s[:120]
    return ""


def prev_code_line(lines, start_line):
    for k in range(start_line - 2, -1, -1):
        s = lines[k].strip()
        if s and not s.startswith(("//", "/*", "*")):
            return s[:120]
    return ""


def scan_file(repo, rel):
    path = os.path.join(repo, rel)
    src = read_src(path)
    starts = line_starts(src)
    lines = src.split("\n")
    recs = []
    for kind, s, e, occ in occ_iter(src):
        sl, sc = pos(starts, s)
        el, ec = pos(starts, max(e - 1, s))
        text = src[s:e]
        recs.append({
            "id": block_id(rel, kind, text, occ),
            "file": rel,
            "kind": kind,
            "start": sl,
            "end": el,
            "start_col": sc,
            "end_col": ec,
            "lines": el - sl + 1,
            "text": text,
            "prev": prev_code_line(lines, sl),
            "next": next_code_line(lines, el),
            "test": rel.startswith("src/test/") or "/src/test/" in rel,
        })
    return recs


def cmd_scan(args):
    recs = []
    for rel in tracked_java(args.repo):
        try:
            recs.extend(scan_file(args.repo, rel))
        except SrcRefused as e:
            print(f"scan: skipped {e}", file=sys.stderr)
    recs.sort(key=lambda r: (r["file"], r["start"]))
    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    for r in recs:
        out.write(json.dumps(r, ensure_ascii=False) + "\n")
    if args.out:
        out.close()
        print(f"scan: {len(recs)} comment blocks -> {args.out}")


def load_delete_ids(path):
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("action") == "delete":
                ids.add(rec["id"])
    return ids


def plan_edits(src, delete_ids, rel):
    starts = line_starts(src)
    lines = src.split("\n")
    del_lines, char_dels = set(), {}
    matched = 0
    for kind, s, e, occ in occ_iter(src):
        text = src[s:e]
        if block_id(rel, kind, text, occ) not in delete_ids:
            continue
        matched += 1
        # A comment acts as whitespace: deleting one that sits between two
        # identifier characters would fuse them into a different token.
        before = src[s - 1] if s else ""
        after = src[e] if e < len(src) else ""
        if (before.isalnum() or before in "_$") and (after.isalnum() or after in "_$"):
            raise SystemExit(
                f"ABORT {rel}:{pos(starts, s)[0]}: comment separates two tokens; "
                "delete it by hand so the split is preserved")
        sl, sc = pos(starts, s)
        el, ec = pos(starts, max(e - 1, s))  # ec = column of last comment char
        end_col_excl = ec + 1
        first, lastline = lines[sl - 1], lines[el - 1]
        if sl == el:
            if first[:sc].strip() == "" and first[end_col_excl:].strip() == "":
                del_lines.add(sl)
            elif first[:sc].strip() == "":
                # Comment starts the line but code follows it: delete from column 0 so the
                # code keeps the indent that preceded the comment. Deleting only the
                # comment chars leaves indent + the comment's own trailing spaces, which
                # shifts the code right — code_only() ignores whitespace and cannot catch it.
                char_dels.setdefault(sl, []).append((0, end_col_excl, ""))
            else:
                # Inline comment flanked by spaces on both sides (`a /*x*/ b`): take one
                # adjacent space too, otherwise `a  b` leaves double spacing. Cosmetic,
                # but keeping original column count makes the diff whitespace-neutral.
                a0 = end_col_excl
                if a0 < len(first) and first[sc - 1] == " " and first[a0] == " ":
                    a0 += 1
                char_dels.setdefault(sl, []).append((sc, a0, ""))
        else:
            if first[:sc].strip() == "":
                del_lines.add(sl)
            else:
                char_dels.setdefault(sl, []).append((sc, len(first), ""))
            del_lines.update(range(sl + 1, el))
            if lastline[end_col_excl:].strip() == "":
                del_lines.add(el)
            else:
                # Code follows the closing `*/` on the same line: deleting from column 0
                # would eat that line's leading indent (multi-line analog of the
                # same-line drift bug), so restore the indent as replacement text.
                li = len(lastline) - len(lastline.lstrip(" \t"))
                char_dels.setdefault(el, []).append((0, end_col_excl, " " * li))
    if not matched:
        return None
    new_lines = []
    for idx, line in enumerate(lines, 1):
        if idx in del_lines:
            continue
        for a, b, repl in sorted(char_dels.get(idx, []), reverse=True):
            line = line[:a] + repl + line[b:]
        new_lines.append(line.rstrip() if idx in char_dels else line)
    return "\n".join(new_lines), matched


def code_lines(src):
    """[(leading_indent, code_text)] for every line that carries code.

    A comment is whitespace, so a line like `    /** doc */    int x;` counts as code with
    indent 4 (its leading whitespace), not the column where `int` happens to start.
    """
    chars = list(src)
    for _, s, e in lex(src):
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = " "
    stripped = "".join(chars).split("\n")
    out = []
    for raw, nocomment in zip(src.split("\n"), stripped):
        if not nocomment.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        out.append((indent, nocomment.strip()))
    return out


def indent_drift(src, new):
    """1-based line number of the first code line whose indent changed, else 0.

    code_only() strips whitespace, so it cannot see a comment deletion that leaves the
    code shifted right; this compares (indent, code) pairs instead. Internal whitespace
    runs are collapsed: `a /*x*/ b` -> `a b` is cosmetic, not indent drift — indent is
    what this guard exists to catch.
    """
    a, b = code_lines(src), code_lines(new)
    if len(a) != len(b):
        return 0  # 行数变化交给 code_only 与人工复核, 这里只管对齐的缩进
    for i, (x, y) in enumerate(zip(a, b), 1):
        if x[0] != y[0] or " ".join(x[1].split()) != " ".join(y[1].split()):
            return i
    return 0


_OP4 = frozenset({">>>="})
_OP3 = frozenset({"<<=", ">>=", ">>>"})
_OP2 = frozenset({"==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=",
                  "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "->", "::"})


def code_tokens(src):
    """Code token stream with comments blanked and maximal-munch operator lexing.

    code_only() strips whitespace, so it cannot see `1 +/*c*/+ 2` collapse into
    `1 ++ 2`; comparing token streams catches any such fusion.
    """
    chars = list(src)
    for _, s, e in lex(src):
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = " "
    text = "".join(chars)
    i, n, out = 0, len(text), []
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isalnum() or c in "_$":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            out.append(text[i:j])
            i = j
        else:
            for ln, ops in ((4, _OP4), (3, _OP3), (2, _OP2)):
                if text[i:i + ln] in ops:
                    out.append(text[i:i + ln])
                    i += ln
                    break
            else:
                out.append(c)
                i += 1
    return out


def cmd_apply(args):
    ids = load_delete_ids(args.decisions)
    if not ids:
        print("apply: nothing marked delete")
        return
    changed = 0
    for rel in tracked_java(args.repo):
        path = os.path.join(args.repo, rel)
        try:
            src = read_src(path)
        except SrcRefused as e:
            print(f"ABORT {e}", file=sys.stderr)
            sys.exit(2)
        plan = plan_edits(src, ids, rel)
        if not plan:
            continue
        new, matched = plan
        if code_only(src) != code_only(new):
            print(f"ABORT {rel}: code changed, fix the tool before retrying", file=sys.stderr)
            sys.exit(2)
        if code_tokens(src) != code_tokens(new):
            print(f"ABORT {rel}: deleting the comment fuses adjacent tokens "
                  "(e.g. `1 +/*c*/+ 2` becomes `1 ++ 2`); delete it by hand", file=sys.stderr)
            sys.exit(2)
        drift = indent_drift(src, new)
        if drift:
            print(f"ABORT {rel}:{drift}: code line indent drifted; code_only() ignores "
                  "whitespace so this guard is the only net", file=sys.stderr)
            sys.exit(2)
        if new != src:
            write_src(path, new)
            changed += 1
            print(f"  {rel}: {matched} block(s)")
    print(f"apply: {changed} file(s) changed")


def count_lines(src):
    starts = line_starts(src)
    hit = set()
    for _, s, e in lex(src):
        a = pos(starts, s)[0]
        b = pos(starts, max(e - 1, s))[0]
        hit.update(range(a, b + 1))
    return len(hit)


def comment_lines(repo):
    counts = {}
    for rel in tracked_java(repo):
        try:
            src = read_src(os.path.join(repo, rel))
        except SrcRefused as e:
            print(f"comment-gate: skipped {e}", file=sys.stderr)
            continue
        counts[rel] = count_lines(src)
    return counts


def head_comment_lines(repo):
    """Per-file comment counts from the HEAD tree — sync must never see a dirty worktree."""
    out = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "-z", "HEAD"],
        capture_output=True, check=True,
    ).stdout
    counts = {}
    for ent in out.decode("utf-8", "surrogateescape").split("\0"):
        if not ent:
            continue
        meta, rel = ent.split("\t", 1)
        mode, kind = meta.split()[:2]
        if kind != "blob" or not rel.endswith(".java"):
            continue
        data = subprocess.run(
            ["git", "-C", repo, "show", f"HEAD:{rel}"],
            capture_output=True, check=True,
        ).stdout
        if len(data) > MAX_SRC_BYTES:
            print(f"comment-gate: skipped {rel}: larger than 10 MiB (in HEAD)", file=sys.stderr)
            continue
        counts[rel] = count_lines(data.decode("utf-8", "surrogateescape"))
    return counts


def baseline_json(files):
    data = {"version": 1, "total": sum(files.values()), "files": files}
    return (json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True) + "\n").encode("utf-8")


def write_baseline_atomic(path, payload):
    """Atomic write refusing symlinks: the baseline is hook-rewritten, so a symlink
    here would let a merge write through into e.g. .git/hooks."""
    if os.path.islink(path):
        print(f"comment-gate: ABORT {path} is a symlink; refusing to write through it", file=sys.stderr)
        sys.exit(2)
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".comment-baseline.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_baseline(args):
    counts = comment_lines(args.repo)
    write_baseline_atomic(args.out, baseline_json(counts))
    print(f"baseline: {len(counts)} files, {sum(counts.values())} comment lines -> {args.out}")


def cmd_check(args):
    if not os.path.exists(args.baseline):
        print("comment-gate: no baseline -> not armed, pass (generate one with the baseline subcommand)")
        return
    with open(args.baseline, encoding="utf-8") as f:
        base = json.load(f)["files"]
    files = args.files or tracked_java(args.repo)
    bad = []
    for rel in files:
        if not rel.endswith(".java") or not os.path.exists(os.path.join(args.repo, rel)):
            continue
        try:
            src = read_src(os.path.join(args.repo, rel))
        except SrcRefused as e:
            bad.append(str(e))
            continue
        starts = line_starts(src)
        hit = set()
        for _, s, e in lex(src):
            hit.update(range(pos(starts, s)[0], pos(starts, max(e - 1, s))[0] + 1))
        c, b = len(hit), base.get(rel)
        if b is None and c:
            bad.append(f"{rel}: {c} comment lines (file not in baseline)")
        elif b is not None and c > b:
            bad.append(f"{rel}: {c} > baseline {b}")
    if bad:
        print("comment-gate: comments may not grow (ratchet). Reduce them or update the baseline deliberately:", file=sys.stderr)
        for x in bad:
            print("  " + x, file=sys.stderr)
        sys.exit(1)
    print(f"comment-gate: ok ({len(files)} file(s) checked)")


def cmd_sync(args):
    if not os.path.exists(args.baseline):
        print("comment-gate: no baseline -> not armed, pass (generate one with the baseline subcommand)")
        return
    if subprocess.run(["git", "-C", args.repo, "rev-parse", "--verify", "HEAD"],
                      capture_output=True).returncode != 0:
        print("comment-gate: no HEAD commit; sync reconciles a merge and needs one", file=sys.stderr)
        sys.exit(1)
    with open(args.baseline, "rb") as f:
        old = f.read()
    try:
        old_files = json.loads(old.decode("utf-8"))["files"]
        if not isinstance(old_files, dict):
            raise ValueError('"files" is not an object')
        for rel, v in old_files.items():
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"bad count for {rel}: {v!r}")
    except (ValueError, KeyError, UnicodeDecodeError) as e:
        print(f"comment-gate: ABORT malformed baseline {args.baseline}: {e}", file=sys.stderr)
        sys.exit(2)
    counts = head_comment_lines(args.repo)
    grew = sorted((rel, old_files[rel], c) for rel, c in counts.items()
                  if rel in old_files and c > old_files[rel])
    if grew:
        print("comment-gate: comments grew vs baseline; sync keeps the old counts (ratchet) — "
              "run the baseline subcommand to bless growth deliberately:", file=sys.stderr)
        for rel, b, c in grew:
            print(f"  {rel}: {b} -> {c}", file=sys.stderr)
    merged = {rel: min(old_files[rel], c) for rel, c in counts.items() if rel in old_files}
    new = baseline_json(merged)
    if new == old:
        return
    write_baseline_atomic(args.baseline, new)
    print("comment-gate: baseline drift after merge — rewritten, stage and amend or commit it")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--repo", required=True); s.add_argument("--out"); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("apply"); s.add_argument("--repo", required=True); s.add_argument("--decisions", required=True); s.set_defaults(fn=cmd_apply)
    s = sub.add_parser("baseline"); s.add_argument("--repo", required=True); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_baseline)
    s = sub.add_parser("check"); s.add_argument("--repo", required=True); s.add_argument("--baseline", required=True); s.add_argument("files", nargs="*"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("sync"); s.add_argument("--repo", required=True); s.add_argument("--baseline", required=True); s.set_defaults(fn=cmd_sync)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
