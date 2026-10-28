#!/usr/bin/env python3
"""Apply safe session classification to V5.18. Does not touch broker order logic."""
import ast
from pathlib import Path
root=Path(__file__).resolve().parent
source=root/"oneil_live_v5_18.py"
if not source.exists():
    raise SystemExit("Run at repository root, beside oneil_live_v5_18.py")
s=source.read_text(encoding="utf8")
if "from oneil_v519_session_guard import phase" not in s:
    tree=ast.parse(s)
    funcs=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=="phase"]
    if len(funcs)!=1: raise SystemExit("Expected exactly one V5.18 phase function; stopped safely")
    node=funcs[0]
    lines=s.splitlines(keepends=True)
    del lines[node.lineno-1:node.end_lineno]
    s="".join(lines)
    marker="from oneil_live_v5_17 import parse_frame\n"
    if marker not in s:raise SystemExit("Unknown V5.18 import structure; stopped safely")
    s=s.replace(marker,marker+"from oneil_v519_session_guard import phase\n",1)
    ast.parse(s)
    source.write_text(s,encoding="utf8")
print("V5.18_SESSION_PHASE_PATCH=READY; NO_ORDER_CHANGES")
