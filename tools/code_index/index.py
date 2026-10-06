#!/usr/bin/env python3
"""Pulsegrid code index, gamedb-style.

Method (adapted from https://github.com/smileybaal/gamedb):
  1. Parse every source file once into structured records
     (files, functions/classes, call edges) instead of grepping.
  2. Assign each file to a subsystem module via derived rules.
  3. Resolve call edges caller -> callee with a confidence grade.
  4. Be honest about what was not parsed (the Gaps sheet).

Python is parsed with `ast` (precise). Rust is parsed with
gamedb-style hand-written matchers (function/struct/enum definitions
via regex, bodies via brace matching, calls via identifier scans) --
call edges there are heuristic and graded accordingly.

Usage:
    python3 index.py [repo-root] [output-xlsx]
"""

import ast
import datetime
import os
import re
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/workspace/pulsegrid")
OUT = (sys.argv[2] if len(sys.argv) > 2
       else os.path.expanduser("~/workspace/your_files/pulsegrid-code-index.xlsx"))

# ---------------------------------------------------------------- modules

# Derived module taxonomy: (module, [path prefixes]).
MODULE_RULES = [
    ("engine:core",     ["engine/src/engine.rs", "engine/src/lib.rs", "engine/src/scheduler.rs"]),
    ("engine:dsp",       ["engine/src/synth.rs", "engine/src/graph.rs", "engine/src/effects.rs"]),
    ("engine:timeline",  ["engine/src/timeline.rs"]),
    ("engine:io",        ["engine/src/wav.rs", "engine/src/backend.rs"]),
    ("bridge",           ["python/daw/engine_bridge.py"]),
    ("model",            ["python/daw/project.py", "python/daw/undo.py"]),
    ("ui:shell",         ["python/daw/ui/app.py", "python/daw/__main__.py",
                          "python/daw/__init__.py", "python/daw/ui/__init__.py"]),
    ("ui:editors",       ["python/daw/ui/sequencer.py", "python/daw/ui/pianoroll.py",
                          "python/daw/ui/playlist.py"]),
    ("ui:panels",        ["python/daw/ui/mixer.py", "python/daw/ui/browser.py",
                          "python/daw/ui/pluginpicker.py", "python/daw/ui/settings.py",
                          "python/daw/ui/transport.py", "python/daw/ui/widgets.py",
                          "python/daw/ui/dialogs.py"]),
    ("tests",            ["tests/"]),
]


def assign_module(relpath):
    for module, prefixes in MODULE_RULES:
        for p in prefixes:
            if relpath == p or relpath.startswith(p):
                return module, "derived"
    return "other", "derived"


# ---------------------------------------------------------------- python

def _py_sig(node):
    parts = []
    for a in node.args.posonlyargs:
        parts.append(a.arg)
    for a in node.args.args:
        parts.append(a.arg)
    if node.args.vararg:
        parts.append("*" + node.args.vararg.arg)
    for a in node.args.kwonlyargs:
        parts.append(a.arg)
    if node.args.kwarg:
        parts.append("**" + node.args.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def parse_python(path, relpath, module):
    """AST parse: functions/methods/classes + call edges. Precise."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.count("\n") + 1
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return {"error": f"SyntaxError: {e}"}, lines

    funcs, calls, classes = [], [], []
    # qualified name -> record, for same-file call resolution
    by_name = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.class_stack = []
            self.func_stack = []  # (qname, node)

        def _qname(self, name):
            return ".".join(self.class_stack + [name])

        def visit_ClassDef(self, node):
            qn = self._qname(node.name)
            rec = {"qname": qn, "kind": "class", "sig": "",
                   "file": relpath, "start": node.lineno,
                   "end": getattr(node, "end_lineno", node.lineno),
                   "module": module, "lang": "python"}
            classes.append(rec)
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def _visit_func(self, node, kind):
            qn = self._qname(node.name)
            rec = {"qname": qn, "kind": kind, "sig": _py_sig(node),
                   "file": relpath, "start": node.lineno,
                   "end": getattr(node, "end_lineno", node.lineno),
                   "module": module, "lang": "python"}
            funcs.append(rec)
            by_name[qn] = rec
            by_name[node.name] = rec  # bare-name fallback
            self.func_stack.append((qn, node))
            for child in ast.iter_child_nodes(node):
                self.visit(child)
            self.func_stack.pop()

        def visit_FunctionDef(self, node):
            self._visit_func(node, "method" if self.class_stack else "function")

        def visit_AsyncFunctionDef(self, node):
            self._visit_func(node, "method" if self.class_stack else "function")

        def visit_Call(self, node):
            if self.func_stack:
                caller = self.func_stack[-1][0]
                raw, target = None, None
                f = node.func
                if isinstance(f, ast.Name):
                    raw = f.id
                elif isinstance(f, ast.Attribute):
                    v = f.value
                    if isinstance(v, ast.Name):
                        raw = f"{v.id}.{f.attr}"
                        if v.id == "self" and self.class_stack:
                            target = f"{self.class_stack[-1]}.{f.attr}"
                    else:
                        raw = f.attr
                if raw:
                    calls.append({"caller": caller, "file": relpath,
                                  "line": node.lineno, "callee_raw": raw,
                                  "callee_hint": target, "lang": "python"})
            self.generic_visit(node)

    Visitor().visit(tree)
    return {"functions": funcs, "calls": calls, "classes": classes,
            "by_name": by_name}, lines


# ---------------------------------------------------------------- rust

RS_FN = re.compile(
    r"^[ \t]*(pub(?:\([^)]*\))?\s+)?(unsafe\s+)?(async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))?",
    re.M)
RS_STRUCT = re.compile(r"^[ \t]*(pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
RS_ENUM = re.compile(r"^[ \t]*(pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
RS_IMPL = re.compile(
    r"^[ \t]*impl(?:\s*<[^>]*>)?\s+([A-Za-z_][A-Za-z0-9_]*(?:::\s*[A-Za-z_][A-Za-z0-9_]*)*)"
    r"(?:\s+for\s+([A-Za-z_][A-Za-z0-9_]*(?:::\s*[A-Za-z_][A-Za-z0-9_]*)*))?",
    re.M)
RS_TRAIT = re.compile(r"^[ \t]*(pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
RS_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\(")
RS_KEYWORDS = {
    "if", "else", "for", "while", "loop", "match", "return", "let", "in",
    "struct", "enum", "impl", "fn", "where", "use", "mod", "pub", "crate",
    "self", "Self", "super", "as", "ref", "mut", "move", "dyn", "const",
    "static", "type", "trait", "unsafe", "async", "await", "try", "do",
    "macro_rules", "extern",
}
RS_MACROS = {"vec", "format", "println", "eprintln", "assert", "assert_eq",
             "assert_ne", "debug_assert", "panic", "todo", "unimplemented",
             "unreachable", "write", "writeln", "include", "include_str",
             "concat", "env", "option_env", "cfg", "matches", "try"}


def _strip_rust_noise(src):
    """Remove comments/strings/chars so brace matching and call scans
    don't trip on them. Length-preserving: every removed character
    becomes a space (newlines stay newlines), so positions and line
    numbers in the cleaned text match the original exactly."""
    chars = list(src)
    n = len(chars)

    def blank(s, e, keep_newlines=True):
        for k in range(s, min(e, n)):
            if chars[k] == "\n" and keep_newlines:
                continue
            chars[k] = " "

    i = 0
    while i < n:
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            blank(i, n if j == -1 else j)
            i = n if j == -1 else j
        elif c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            blank(i, n if j == -1 else j + 2)
            i = n if j == -1 else j + 2
        elif c == '"':
            # raw string r"..." / r#"..."# ?
            k = i - 1
            hashes = 0
            while k >= 0 and chars[k] == "#":
                hashes += 1
                k -= 1
            is_raw = k >= 0 and chars[k] == "r"
            if is_raw:
                end = '"' + "#" * hashes
                k = src.find(end, i + 1)
                blank(i, n if k == -1 else k + len(end))
                i = n if k == -1 else k + len(end)
            else:
                j = i + 1
                while j < n:
                    if chars[j] == "\\":
                        j += 2
                        continue
                    if chars[j] == '"':
                        break
                    j += 1
                blank(i, j + 1)
                i = j + 1
        elif c == "'":
            j = i + 1
            if j < n and chars[j] == "\\":
                j += 2
            else:
                j += 1
            if j < n and chars[j] == "'":
                blank(i, j + 1)  # char literal; lifetimes have no closing '
                i = j + 1
            else:
                i += 1  # lifetime: keep
        else:
            i += 1
    return "".join(chars)


def _brace_match(src, open_idx):
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def parse_rust(path, relpath, module):
    """Regex + brace matching. Definitions are reliable; call edges
    are heuristic (see confidence column)."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.count("\n") + 1
    clean = _strip_rust_noise(src)

    funcs, calls, types = [], [], []
    gaps = []
    fn_names = {}  # bare name -> qname (first wins; collisions noted)

    # impl context: list of (line_no, type_name); `impl X for Y` -> Y
    impls = []
    for m in RS_IMPL.finditer(src):
        name = (m.group(2) or m.group(1)).replace(" ", "")
        impls.append((m.start(), name))

    def impl_at(pos):
        cur = None
        for start, name in impls:
            if start <= pos:
                cur = name
            else:
                break
        return cur

    for m in RS_FN.finditer(src):
        name = m.group(4)
        params = (m.group(5) or "()")
        if len(params) > 80:
            params = params[:77] + "...)"
        start_line = src.count("\n", 0, m.start()) + 1
        impl = impl_at(m.start())
        qname = f"{impl}::{name}" if impl else name
        # body extent via brace matching on the cleaned source
        open_brace = clean.find("{", m.start())
        end_line = start_line
        body_ok = True
        if open_brace == -1:
            body_ok = False
            gaps.append(f"{relpath}:{start_line}: no body brace for fn {qname}")
            close = m.start()
        else:
            close = _brace_match(clean, open_brace)
            if close == -1:
                body_ok = False
                gaps.append(f"{relpath}:{start_line}: unbalanced braces in fn {qname}")
                close = len(clean)
            else:
                end_line = src.count("\n", 0, close) + 1
        rec = {"qname": qname, "kind": "method" if impl else "function",
               "sig": params, "file": relpath, "start": start_line,
               "end": end_line, "module": module, "lang": "rust"}
        funcs.append(rec)
        fn_names.setdefault(name, qname)
        # call scan inside the body
        body = clean[m.start():close]
        for cm in RS_CALL.finditer(body):
            callee = cm.group(2) or cm.group(1)
            qualifier = cm.group(1) if cm.group(2) else None
            if callee in RS_KEYWORDS or callee in RS_MACROS:
                continue
            if qualifier in ("Self", "self"):
                raw = callee
                callee_q = f"{impl}::{callee}" if impl else callee
            elif qualifier:
                # Qualified path (Type::func): keep it whole. It only
                # resolves to a project definition of exactly that path;
                # otherwise it is an honest external/unresolved call.
                raw = f"{qualifier}::{callee}"
                callee_q = raw
            else:
                raw = callee
                callee_q = None
            line = start_line + body.count("\n", 0, cm.start())
            calls.append({"caller": qname, "file": relpath, "line": line,
                          "callee_raw": raw, "callee_hint": callee_q,
                          "lang": "rust", "body_ok": body_ok})

    for pat, kind in ((RS_STRUCT, "struct"), (RS_ENUM, "enum"), (RS_TRAIT, "trait")):
        for m in pat.finditer(src):
            name = m.group(2)
            line = src.count("\n", 0, m.start()) + 1
            types.append({"qname": name, "kind": kind, "sig": "",
                          "file": relpath, "start": line, "end": line,
                          "module": module, "lang": "rust"})

    return {"functions": funcs, "calls": calls, "classes": types,
            "fn_names": fn_names, "gaps": gaps,
            "qname_file": {r["qname"]: relpath for r in funcs}}, lines


# ---------------------------------------------------------------- index

def build_index(repo):
    files, functions, calls, gaps = [], [], [], []
    py_by_name = {}   # (file, qname or bare) -> rec
    rs_names = {}     # bare name -> [(qname, file)]
    rs_qnames = {}    # exact qname -> file

    targets = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", ".venv", "target", "__pycache__",
                                    ".gamedb", "tools")]
        for fn in sorted(filenames):
            if fn.endswith((".py", ".rs")):
                full = os.path.join(dirpath, fn)
                targets.append((full, os.path.relpath(full, repo)))

    for full, rel in targets:
        module, rule = assign_module(rel)
        lang = "python" if rel.endswith(".py") else "rust"
        if lang == "python":
            parsed, lines = parse_python(full, rel, module)
        else:
            parsed, lines = parse_rust(full, rel, module)
        if "error" in parsed:
            gaps.append({"location": rel, "issue": parsed["error"],
                         "severity": "file skipped"})
            files.append({"path": rel, "language": lang, "lines": lines,
                          "module": module, "functions": 0, "classes": 0,
                          "status": "parse failed"})
            continue
        for g in parsed.get("gaps", []):
            gaps.append({"location": g.split(":")[0],
                         "issue": g, "severity": "heuristic"})
        frecs = parsed["functions"] + parsed.get("classes", [])
        functions.extend(frecs)
        for c in parsed["calls"]:
            c["module"] = module
            calls.append(c)
        if lang == "python":
            for qn, rec in parsed["by_name"].items():
                py_by_name[(rel, qn)] = rec
        else:
            for bare, qn in parsed["fn_names"].items():
                rs_names.setdefault(bare, []).append((qn, rel))
            rs_qnames.update(parsed["qname_file"])
        files.append({"path": rel, "language": lang, "lines": lines,
                      "module": module,
                      "functions": len(parsed["functions"]),
                      "classes": len(parsed.get("classes", [])),
                      "status": "ok"})

    # ---- resolve call edges (gamedb `graph` equivalent)
    resolved = []
    for c in calls:
        callee_qname, callee_file, conf = None, None, "low"
        hint = c.get("callee_hint")
        raw = c["callee_raw"]
        if c["lang"] == "python":
            if hint and (c["file"], hint) in py_by_name:
                r = py_by_name[(c["file"], hint)]
                callee_qname, callee_file, conf = hint, r["file"], "high"
            elif (c["file"], raw) in py_by_name:
                r = py_by_name[(c["file"], raw)]
                callee_qname, callee_file, conf = r["qname"], r["file"], "high"
            else:
                # project-wide unique bare-name match
                hits = {(f, q) for (f, q), r in py_by_name.items()
                        if q == raw and r["kind"] in ("function", "method")}
                if len(hits) == 1:
                    (f, q) = next(iter(hits))
                    callee_qname, callee_file, conf = q, f, "medium"
        else:
            # Exact qualified path first (e.g. Engine::make_arrangement),
            # then same-file bare-name heuristic, then low.
            if hint and hint in rs_qnames:
                callee_qname, callee_file = hint, rs_qnames[hint]
                conf = "medium"
            elif raw in rs_names:
                cands = rs_names[raw]
                same = [(q, f) for q, f in cands if f == c["file"]]
                if same:
                    callee_qname, callee_file = same[0]
                    conf = "medium"
                elif len(cands) == 1:
                    callee_qname, callee_file = cands[0]
                    conf = "low"
            if not c.get("body_ok", True) and conf != "low":
                conf = "low"
        resolved.append({
            "caller": c["caller"], "caller_file": c["file"], "line": c["line"],
            "callee": callee_qname or raw,
            "callee_file": callee_file or "",
            "confidence": conf, "module": c["module"], "lang": c["lang"],
        })

    return {"files": files, "functions": functions, "calls": resolved,
            "gaps": gaps}


# ---------------------------------------------------------------- xlsx

def write_xlsx(index, outpath, repo):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    title_font = Font(bold=True, size=14, color="1F4E79")
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    def sheet(name, headers, rows, widths, title=None, note=None):
        ws = wb.create_sheet(name)
        r = 1
        if title:
            ws.merge_cells(start_row=1, start_column=1,
                           end_row=1, end_column=len(headers))
            c = ws.cell(row=1, column=1, value=title)
            c.font = title_font
            r = 2
        if note:
            ws.merge_cells(start_row=r, start_column=1,
                           end_row=r, end_column=len(headers))
            c = ws.cell(row=r, column=1, value=note)
            c.font = Font(italic=True, color="595959")
            c.alignment = wrap
            r += 1
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=r, column=ci, value=h)
            c.font = hdr_font
            c.fill = hdr_fill
            c.border = border
        hr = r
        for ri, row in enumerate(rows, hr + 1):
            for ci, v in enumerate(row, 1):
                c = ws.cell(row=ri, column=ci, value=v)
                c.border = border
                if isinstance(v, str) and len(v) > 60:
                    c.alignment = wrap
        ws.freeze_panes = ws.cell(row=hr + 1, column=1)
        ws.auto_filter.ref = (
            f"A{hr}:{get_column_letter(len(headers))}{hr + len(rows)}"
            if rows else f"A{hr}:{get_column_letter(len(headers))}{hr}")
        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w
        return ws

    files = index["files"]
    funcs = index["functions"]
    calls = index["calls"]
    gaps = index["gaps"]
    total_lines = sum(f["lines"] for f in files)
    today = datetime.date.today().isoformat()
    try:
        import subprocess
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=repo, capture_output=True, text=True,
                                timeout=10).stdout.strip()
    except Exception:
        commit = "unknown"

    # ---- cover
    ws = wb.active
    ws.title = "Cover"
    ws["A1"] = "Pulsegrid Code Index"
    ws["A1"].font = Font(bold=True, size=16, color="1F4E79")
    cover = [
        ("Generated", today),
        ("Repo commit", commit),
        ("Method", "Adapted from gamedb (github.com/smileybaal/gamedb): parse every "
                   "source file once into structured records (files, functions, call "
                   "edges), assign each file to a subsystem module, resolve the call "
                   "graph with confidence grades, and report what was not parsed."),
        ("Python parsing", "ast module — precise definitions and call edges."),
        ("Rust parsing", "Hand-written matchers (gamedb-style): regex definitions, "
                         "brace-matched bodies, identifier call scans. Call edges are "
                         "heuristic — see the Confidence column on the Call graph sheet."),
        ("Confidence", "high = AST-resolved in the same file (Python) · "
                       "medium = unique name match or same-file heuristic (Rust) · "
                       "low = unresolved name or uncertain body extents."),
        ("Files", len(files)),
        ("Lines", total_lines),
        ("Functions/methods/classes", len(funcs)),
        ("Call edges", len(calls)),
        ("Parse gaps", len(gaps)),
    ]
    for i, (k, v) in enumerate(cover, 3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        c = ws.cell(row=i, column=2, value=v)
        c.alignment = wrap
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 100

    # ---- stats
    mods = sorted({f["module"] for f in files})
    stat_rows = []
    for m in mods:
        mf = [f for f in files if f["module"] == m]
        mfunc = [x for x in funcs if x["module"] == m]
        mcalls = [x for x in calls if x["module"] == m]
        stat_rows.append([m, len(mf), sum(f["lines"] for f in mf),
                          len(mfunc), len(mcalls)])
    stat_rows.append(["TOTAL", len(files), total_lines, len(funcs), len(calls)])
    sheet("Stats", ["Module", "Files", "Lines", "Functions", "Call edges"],
          stat_rows, [22, 8, 8, 11, 11],
          title="Codebase stats by subsystem module")

    # ---- modules
    mod_rows = []
    for m in mods:
        for f in sorted([x for x in files if x["module"] == m],
                        key=lambda x: x["path"]):
            mod_rows.append([m, f["path"], f["language"], f["lines"], "derived"])
    sheet("Modules", ["Module", "File", "Language", "Lines", "Rule"],
          mod_rows, [20, 42, 10, 8, 10],
          title="Module taxonomy — which subsystem each file belongs to")

    # ---- files
    file_rows = [[f["path"], f["language"], f["lines"], f["module"],
                  f["functions"], f["classes"], f["status"]] for f in files]
    sheet("Files", ["Path", "Language", "Lines", "Module", "Functions",
                    "Classes", "Parse status"],
          file_rows, [42, 10, 8, 18, 10, 8, 14],
          title="File inventory")

    # ---- functions
    fn_rows = [[x["qname"], x["kind"], x["sig"], x["file"],
                x["start"], x["end"], x["module"], x["lang"]] for x in funcs]
    sheet("Functions",
          ["Qualified name", "Kind", "Signature", "File", "Start", "End",
           "Module", "Language"],
          fn_rows, [44, 10, 30, 36, 8, 8, 18, 10],
          title="Function / method / class index (gamedb `search` equivalent)",
          note="Use the filter row to find definitions by name substring, "
               "module, or file.")

    # ---- call graph
    call_rows = [[c["caller"], f'{c["caller_file"]}:{c["line"]}', c["callee"],
                  c["callee_file"], c["confidence"], c["lang"]]
                 for c in calls]
    sheet("Call graph",
          ["Caller", "Call site", "Callee", "Callee file", "Confidence",
           "Language"],
          call_rows, [40, 24, 40, 30, 12, 10],
          title="Call graph edges (gamedb `graph` equivalent)",
          note="Confidence: high = AST-resolved (Python, same file); medium = "
               "unique/project-wide name match or same-file Rust heuristic; "
               "low = name only, could not resolve to a definition.")

    # ---- gaps
    gap_rows = [[g["location"], g["issue"], g["severity"]] for g in gaps]
    if not gap_rows:
        gap_rows = [["—", "No parse failures. Rust call edges remain heuristic "
                           "by design (see Cover).", "info"]]
    sheet("Gaps", ["Location", "Issue", "Severity"], gap_rows, [30, 90, 12],
          title="What was not parsed or is approximate (honesty sheet)")

    # ---- documentation sheets (from doc_data.py + research.py) ----
    import importlib.util as _ilu
    _ddoc = _ilu.spec_from_file_location(
        "doc_data", os.path.join(os.path.dirname(__file__), "doc_data.py"))
    doc_data = _ilu.module_from_spec(_ddoc)
    _ddoc.loader.exec_module(doc_data)

    ver_rows = [[v, d, c or "", f, s] for v, d, c, f, s in doc_data.VERSIONS]
    sheet("Changelog", ["Version", "Date", "Commit", "Format", "Summary"],
          ver_rows, [10, 12, 10, 8, 110],
          title="Version history (newest first)",
          note="Source: tools/code_index/doc_data.py. Full per-version change "
               "logs live in ~/workspace/your_files/pulsegrid-vX.Y.Z-changes.md.")

    find_rows = [[d, f, i, s] for d, f, i, s in doc_data.FINDINGS]
    sheet("Findings", ["Date", "Finding", "Kind", "Status"],
          find_rows, [12, 90, 12, 70],
          title="Bugs found, design decisions, and honest limitations",
          note="Kind: bug = something broken (now fixed or open); decision = a "
               "deliberate design choice; limitation = a known gap.")

    # Research: infographic studies from tools/fl_research/research.py.
    try:
        _rspec = _ilu.spec_from_file_location(
            "fl_research",
            os.path.join(repo, "tools", "fl_research", "research.py"))
        fl_research = _ilu.module_from_spec(_rspec)
        _rspec.loader.exec_module(fl_research)
        res_rows = []
        for title, summary, sections, takeaways in fl_research.INFOGRAPHICS:
            sec_text = "\n\n".join(
                f"[{s_title}]\n" + "\n".join(f"- {b}" for b in bullets)
                for s_title, bullets in sections)
            take_text = "\n".join(f"- {t}" for t in takeaways)
            res_rows.append([title, summary, sec_text, take_text])
        sheet("Research", ["Infographic", "Summary", "Sections", "Pulsegrid takeaways"],
              res_rows, [28, 50, 80, 60],
              title="FL Studio infographic studies",
              note="Source: tools/fl_research/research.py. Also published in "
                   "~/workspace/your_files/pulsegrid-fl-studio-research.xlsx.")
    except Exception as e:
        sheet("Research", ["Error"], [[str(e)]], [100],
              title="FL Studio infographic studies")

    wb.save(outpath)
    return outpath


# Secondary copies kept in sync (Philip's documentation hub).
SYNC_COPIES = [
    os.path.expanduser("~/workspace/user/files/pulsegrid-code-index.xlsx"),
]


def main():
    print(f"Indexing {REPO} ...")
    index = build_index(REPO)
    print(f"  files={len(index['files'])} functions={len(index['functions'])} "
          f"calls={len(index['calls'])} gaps={len(index['gaps'])}")
    write_xlsx(index, OUT, REPO)
    print(f"Wrote {OUT}")
    import shutil
    for dest in SYNC_COPIES:
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(OUT, dest)
            print(f"Synced {dest}")
        except Exception as e:
            print(f"Sync to {dest} failed: {e}")


if __name__ == "__main__":
    main()
