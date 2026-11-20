cd /d/tippy_on_base && python -X utf8 - <<'EOF'
import pathlib
# /app нёс семь незаменённых токенов, один назван именем — значит «six more», не «five».
for rel, a, b in (
    ("docs/CLOUDFLARE.md", "and five more placeholders unsubstituted", "and six more placeholders unsubstituted"),
):
    p = pathlib.Path(rel); s = p.read_bytes().decode("utf-8")
    assert a in s, rel
    p.write_bytes(s.replace(a, b, 1).encode("utf-8")); print("поправлено:", rel)
EOF
grep -n "a remedy in each row, because four reasons point" AGENTS.md | cut -c1-80; grep -n "The program that decides whether the live site heals" AGENTS.md | cut -c1-60; grep -n "because four reasons point at four different places to fix" AGENTS.md | tail -1 | cut -c1-40