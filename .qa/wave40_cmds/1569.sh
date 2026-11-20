cd /d/tippy_on_base
python - <<'PY'
import pathlib
t = pathlib.Path("AGENTS.md").read_text(encoding="utf-8")
keys = {
 "Заголовок": "# Agent Guidelines",
 "Раздел Ledger facts": "## Ledger facts",
 "Раздел Interface": "## Interface",
 "Закон про превью edge (мой новый)": "The edge may answer from the bundle",
 "Имя теста про превью": "test_the_edge_previews_the_bundle_only_for_a_browser_reading_a_page",
 "Закон про алерт-чат": "The one alert chat is the owner",
 "Имя теста про алерт-чат": "test_the_alert_chat_falls_back_to_the_owner_rather_than_dropping_the_alert",
 "1547 tests": "1547 tests",
 "244 more": "244 more, marked",
 "2,696 s": "2,696 s",
 "statement_timeout": "statement_timeout",
 "HEALTH_DB_TIMEOUT": "HEALTH_DB_TIMEOUT",
 "wave 40": "wave 40",
}
for k, v in keys.items():
    print(f"  {t.count(v)} × {k}")
print("байт:", len(t.encode()), "строк:", t.count(chr(10)) + 1)
PY
echo "=== git: что изменено в AGENTS.md относительно HEAD (сводка) ==="
git diff --numstat -- AGENTS.md