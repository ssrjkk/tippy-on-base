"""Two bug classes the existing AST laws do not read.

1. a coroutine used where a *truth value* is wanted — `if ledger.transfer(...)`:
   an un-awaited call object is always truthy, so the money did not move and the
   branch believes it did. The repo law covers the bare *statement* call only.
2. money arithmetic in `float` — a micro-USDC int divided by a float, or a float
   compared to a Decimal, drifts by less than a cent and by more than a gate.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(".")
DIRS = ["bot", "web", "agent", "deploy", "scripts"]

SOURCES = []
for d in DIRS:
    SOURCES += sorted(p for p in (ROOT / d).rglob("*.py"))

# every async def in the ledger/base packages: those are the calls that must be awaited
coroutine_names = set()
for p in list((ROOT / "bot/ledger").glob("*.py")) + [ROOT / "bot/base.py", ROOT / "bot/recurring.py"]:
    tree = ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            coroutine_names.add(node.name)

ok_wrappers = {"gather", "wait_for", "as_completed", "shield", "ensure_future", "create_task", "run"}


def is_awaited(node, parents):
    cur, par = node, parents.get(node)
    while par is not None and isinstance(par, (ast.Await,)):
        return True
    # walk up through call-argument positions that mean "hand the coroutine to a runner"
    while par is not None:
        if isinstance(par, ast.Await):
            return True
        if isinstance(par, ast.Call) and isinstance(par.func, ast.Attribute) and par.func.attr in ok_wrappers:
            return True
        if isinstance(par, (ast.Return, ast.Yield, ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return False
        if isinstance(par, ast.Expr) and isinstance(par.value, ast.Await):
            return True
        cur, par = par, parents.get(par)
    return False


RECEIVERS = {"ledger", "async_ledger", "base", "smart_wallet", "recurring", "self", "conn", "network"}


def _root(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def is_coroutine_call(c):
    return (
        isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr in coroutine_names
        and _root(c.func.value) in RECEIVERS
    )


def truth_positions(tree, parents):
    out = []
    for node in ast.walk(tree):
        tests = []
        if isinstance(node, (ast.If, ast.While)):
            tests = [node.test]
        elif isinstance(node, ast.Assert):
            tests = [node.test]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            tests = [node.operand]
        elif isinstance(node, ast.BoolOp):
            tests = node.values
        elif isinstance(node, ast.IfExp):
            tests = [node.test]
        for t in tests:
            for c in ast.walk(t):
                if is_coroutine_call(c) and not is_awaited(c, parents):
                    out.append((node.lineno, ast.unparse(c)[:90]))
    return out


def float_money(tree):
    out = []
    for node in ast.walk(tree):
        # micro / 1e6 style: int dollars divided by a float literal
        if isinstance(node, ast.BinOp) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, float):
            if node.right.value >= 1e6:
                out.append((node.lineno, f"деление на float {node.right.value!r}"))
        if isinstance(node, ast.Compare):
            for comp in node.comparators + [node.left]:
                if isinstance(comp, ast.Call) and isinstance(comp.func, ast.Name) and comp.func.id == "Decimal":
                    arg = comp.args[0] if comp.args else None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, float):
                        out.append((node.lineno, "Decimal(float) — бинарный шум в деньгах"))
    return out


def float_roundtrip(src_text):
    out = []
    for m in re.finditer(r"\bfloat\(\s*(\w+(?:\.\w+)+)\s*\)", src_text):
        name = m.group(1)
        if any(k in name.lower() for k in ("micro", "amount", "balance", "usdc", "owed", "liabilit")):
            line = src_text[: m.start()].count("\n") + 1
            out.append((line, f"float({name})"))
    return out


bad = 0
for p in SOURCES:
    text = p.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(text, filename=str(p))
    except SyntaxError as e:
        print(f"НЕ РАЗБИРАЕТСЯ {p}: {e}")
        continue
    parents = {}
    for par in ast.walk(tree):
        for ch in ast.iter_child_nodes(par):
            parents[ch] = par
    for kind, rows in (
        ("корутинa в булевой позиции", truth_positions(tree, parents)),
        ("float-арифметика над деньгами", float_money(tree)),
        ("float() над денежным полем", float_roundtrip(text)),
    ):
        for line, what in rows:
            bad += 1
            print(f"{p}:{line}  [{kind}]  {what}")
print("---")
print("корутин:", len(coroutine_names), "| находок:", bad)
