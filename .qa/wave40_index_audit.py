"""Which columns do the shipped queries filter or sort on, and which are indexed?

Heuristic on purpose: it reads SQL string literals out of the runtime AST, takes the
table each statement names, and asks whether an index covers the equality/ORDER BY
columns. It reports candidates for a human to judge, not verdicts.
"""
import ast
import pathlib
import re
from collections import defaultdict

ROOT = pathlib.Path(".")
SOURCES = sorted(list(ROOT.glob("bot/**/*.py")) + list(ROOT.glob("web/**/*.py")) + list(ROOT.glob("agent/**/*.py")))
MIGS = sorted((ROOT / "alembic/versions").glob("*.py")) + [ROOT / "bot/ledger/_schema.py"]

TABLE_RE = re.compile(r"\b(?:FROM|UPDATE|INSERT INTO|DELETE FROM)\s+([a-z_][a-z0-9_]*)", re.I)
EQ_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*(?:=|IN)\s", re.I)
ORDER_RE = re.compile(r"ORDER\s+BY\s+([a-z_][a-z0-9_]*)", re.I)
JOIN_RE = re.compile(r"\bJOIN\s+([a-z_][a-z0-9_]*)\s*\w*\s*ON\s+([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)", re.I)
INDEX_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF NOT EXISTS)?\s+(\w+)\s+ON\s+([a-z_][a-z0-9_]*)\s*\(([^)]*)\)", re.I)
CREATE_RE = re.compile(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\n\s*\);", re.S | re.I)
WORD = re.compile(r"[a-z_][a-z0-9_]*", re.I)

STOP = {
    "and", "or", "not", "where", "select", "true", "false", "null", "case", "when", "then", "else",
    "end", "count", "sum", "coalesce", "greatest", "least", "now", "interval", "cast", "limit",
    "offset", "set", "values", "into", "exists", "is", "in", "on", "as", "by", "the", "this",
}


def literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 24:
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", node.value, re.I):
                out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            txt = "".join(v.value if isinstance(v, ast.Constant) else " ? " for v in node.values)
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", txt, re.I):
                out.append(txt)
    return out


indexed = defaultdict(set)


def add_col(table, col):
    col = col.strip().strip('"').split()[0].lower() if col.strip() else ""
    if WORD.fullmatch(col):
        indexed[table.lower()].add(col)


for m in MIGS:
    if not m.exists():
        continue
    text = m.read_text(encoding="utf-8-sig")
    for _name, table, cols in INDEX_RE.findall(text):
        for c in cols.split(","):
            add_col(table, re.split(r"\s+(?:ASC|DESC)\b", c.strip(), flags=re.I)[0])
    for table, body in CREATE_RE.findall(text):
        for pk in re.findall(r"PRIMARY\s+KEY\s*\(([^)]*)\)", body, re.I):
            first = pk.split(",")[0].strip().strip('"')
            if WORD.fullmatch(first):
                indexed[table.lower()].add(first.lower())
        for u in re.findall(r"([a-z_][a-z0-9_]*)\s+[A-Za-z0-9_ ]*(?:\([^)]*\))?\s*(?:REFERENCES\s+\w+(?:\([^)]*\))?)?\s*UNIQUE", body, re.I):
            indexed[table.lower()].add(u.lower())
        for line in body.splitlines():
            stripped = line.strip()
            if re.search(r"\bPRIMARY\s+KEY\b", stripped, re.I) and not re.match(r"PRIMARY\s+KEY\s*\(", stripped, re.I):
                c = stripped.split()[0].strip('"')
                if WORD.fullmatch(c):
                    indexed[table.lower()].add(c.lower())

used = defaultdict(lambda: defaultdict(int))
for src in SOURCES:
    try:
        sqls = literals(src)
    except SyntaxError:
        continue
    for sql in sqls:
        clean = re.sub(r"'(?:[^']|'')*'", " ? ", sql)
        # `SET col = ...` (plain UPDATE and INSERT ... ON CONFLICT DO UPDATE) names the
        # columns a query WRITES, not the ones it has to FIND: only the WHERE half and the
        # JOIN ON half say anything about an index.
        clean = re.sub(
            r"SET.*?(WHERE|RETURNING|$)", r" ", clean, flags=re.S | re.I
        )
        tables = [t.lower() for t in TABLE_RE.findall(clean)]
        tables += [t.lower() for t, _a, _c in JOIN_RE.findall(clean)]
        targets = {t for t in tables if t in indexed}
        if not targets:
            continue
        for col in EQ_RE.findall(clean) + ORDER_RE.findall(clean):
            col = col.lower()
            if col in STOP:
                continue
            for t in targets:
                used[t][col] += 1

rows = [(t, c, n) for t, cols in used.items() for c, n in cols.items() if c not in indexed[t]]
rows.sort(key=lambda r: (-r[2], r[0]))
print("таблиц с определениями индексов:", len(indexed))
print("кандидатов (фильтр/сортировка без индекса):", len(rows))
for t, c, n in rows:
    print(f"  {t:<26} {c:<26} x{n}")
