#!/usr/bin/env python3
"""Java comment scanner / remover / ratchet gate.

Subcommands
  scan     --repo DIR [--out FILE]          JSONL: one record per comment block
  apply    --repo DIR --decisions FILE      delete blocks marked {"action":"delete"}
  baseline --repo DIR --out FILE            per-file comment line counts (ratchet)
  check    --repo DIR --baseline FILE [f..] fail when a file exceeds its baseline

The lexer understands //, /* */, /** */, string literals, char literals and
Java 15 text blocks, so `//` inside a URL or a string is never touched.
"""
import argparse
import bisect
import hashlib
import json
import os
import subprocess
import sys


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


def read_src(path):
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


def block_id(rel, kind, text):
    return hashlib.sha1(f"{rel}\0{kind}\0{text}".encode("utf-8", "surrogateescape")).hexdigest()[:12]


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
    for kind, s, e in lex(src):
        sl, sc = pos(starts, s)
        el, ec = pos(starts, max(e - 1, s))
        text = src[s:e]
        recs.append({
            "id": block_id(rel, kind, text),
            "file": rel,
            "kind": kind,
            "start": sl,
            "end": el,
            "start_col": sc,
            "end_col": ec,
            "lines": el - sl + 1,
            "text": text,
            "prev": prev_code_line(lines, sl),
            "next": next_code_line(lines, el + 1),
            "test": "/src/test/" in rel,
        })
    return recs


def cmd_scan(args):
    recs = []
    for rel in tracked_java(args.repo):
        recs.extend(scan_file(args.repo, rel))
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
    for kind, s, e in lex(src):
        text = src[s:e]
        if block_id(rel, kind, text) not in delete_ids:
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


def cmd_apply(args):
    ids = load_delete_ids(args.decisions)
    if not ids:
        print("apply: nothing marked delete")
        return
    changed = 0
    for rel in tracked_java(args.repo):
        path = os.path.join(args.repo, rel)
        src = read_src(path)
        plan = plan_edits(src, ids, rel)
        if not plan:
            continue
        new, matched = plan
        if code_only(src) != code_only(new):
            print(f"ABORT {rel}: code changed, fix the tool before retrying", file=sys.stderr)
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


def comment_lines(repo):
    counts = {}
    for rel in tracked_java(repo):
        src = read_src(os.path.join(repo, rel))
        starts = line_starts(src)
        hit = set()
        for _, s, e in lex(src):
            a = pos(starts, s)[0]
            b = pos(starts, max(e - 1, s))[0]
            hit.update(range(a, b + 1))
        counts[rel] = len(hit)
    return counts


def cmd_baseline(args):
    counts = comment_lines(args.repo)
    data = {"version": 1, "total": sum(counts.values()), "files": counts}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, sort_keys=True)
        f.write("\n")
    print(f"baseline: {len(counts)} files, {data['total']} comment lines -> {args.out}")


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
        src = read_src(os.path.join(args.repo, rel))
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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--repo", required=True); s.add_argument("--out"); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("apply"); s.add_argument("--repo", required=True); s.add_argument("--decisions", required=True); s.set_defaults(fn=cmd_apply)
    s = sub.add_parser("baseline"); s.add_argument("--repo", required=True); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_baseline)
    s = sub.add_parser("check"); s.add_argument("--repo", required=True); s.add_argument("--baseline", required=True); s.add_argument("files", nargs="*"); s.set_defaults(fn=cmd_check)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
