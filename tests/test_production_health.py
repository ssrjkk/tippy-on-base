import hashlib
import importlib.util
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_design_system import header_blocks, window_for

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

pytestmark = [
    pytest.mark.production,
    pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed"),
]

PROD_URL = "https://tippy-egi.pages.dev"

# ---------------------------------------------------------------------------
# the published edge against this checkout
# ---------------------------------------------------------------------------
#
# Pages is published by hand (`npx wrangler pages deploy`), so the bundle and the
# Function a visitor reaches can be any number of waves behind the tree that
# describes them. Measured on the live host while writing this: every asset
# answered `Cache-Control: public, max-age=0, must-revalidate`, and
# `/fonts/golos-text-cyrillic.woff2` — a file the tree has shipped since the
# self-hosted-face wave, and which the published bundle does not have — answered
# `200 text/html`: Cloudflare Pages answers a missing path with the landing page,
# so a visitor's @font-face src resolves to a whole website, fails to parse as a
# font, and the operating system's Helvetica paints the Cyrillic. That is the same
# defect `/robots.txt` had, still live, on a file no browser shows an error for.

STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"
HEADERS_FILE = STATIC_DIR / "_headers"


def get(path: str) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(f"{PROD_URL}{path}",
                                     headers={"user-agent": "tippy-health-check/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()


def bundle_assets() -> list[str]:
    """Bundle-relative paths the edge can answer as files: every file with a
    suffix, minus the templates and Markdown the mount refuses. The directory is
    the subject set, so this measures the next asset added without being edited."""
    return sorted(str(p.relative_to(STATIC_DIR)).replace("\\", "/")
                  for p in STATIC_DIR.rglob("*")
                  if p.is_file() and p.suffix and not p.name.endswith((".html", ".md")))


def edge_promises() -> list[tuple[str, int]]:
    """`[(pattern, max-age)]` read out of `web/static/_headers` — the file Pages
    itself parses, so this compares the edge's answer with the edge's own promise
    rather than with a copy of it kept in a test. The reader is the one
    `tests/test_design_system.py` reads the same file with and holds to its own
    shape on markup that is not this file: a second parser here is a second place
    to be wrong, and the first version of this one matched no line at all."""
    out: list[tuple[str, int]] = []
    for pattern, rules in header_blocks().items():
        value = next((v for k, v in rules.items() if k.lower() == "cache-control"), None)
        if value is None:
            continue
        found = re.search(r"\bmax-age=(\d+)", value)
        assert found, f"{pattern} promises {value!r}, which states no max-age"
        out.append((pattern, int(found.group(1))))
    return out


def max_age_of(header: str) -> int | None:
    found = re.search(r"\bmax-age=(\d+)", header or "")
    return int(found.group(1)) if found else None


def test_the_published_bundle_is_this_checkouts_bundle():
    """Every file the tree ships in `web/static` must be the file a visitor gets,
    with the window `_headers` promises it and never the landing page.

    Three measurements per asset, and each refuses a different way this site has
    already broken: the bytes (a deploy that never happened, or a file edited in
    place after publishing), the status and content type (Pages answering a
    missing asset with `200 text/html`, which no browser console reports for a
    font), and the cache window (the promise `_headers` makes and the edge
    quietly overrides)."""
    assets = bundle_assets()
    assert len(assets) >= 10, f"the walk saw {assets} — no gate"
    for kind in (".css", ".js", ".png", ".woff2"):
        assert any(a.endswith(kind) for a in assets), f"no {kind} in the walk: {assets}"

    promises = edge_promises()
    assert len(promises) >= 4, f"_headers declared {promises} — no window to measure"

    findings = []
    for asset in assets:
        status, headers, body = get(f"/{asset}")
        local = (STATIC_DIR / asset).read_bytes()
        if status != 200:
            findings.append({"asset": asset, "status": status})
            continue
        ctype = headers.get("content-type", "")
        if ctype.startswith("text/html"):
            findings.append({"asset": asset, "status": status, "served": ctype,
                             "note": "the bundle does not carry this file, so Pages served the landing page"})
            continue
        if hashlib.sha256(body).hexdigest() != hashlib.sha256(local).hexdigest():
            findings.append({"asset": asset, "status": status,
                             "note": f"live {len(body)} B / {hashlib.sha256(body).hexdigest()[:12]} "
                                     f"!= tree {len(local)} B / {hashlib.sha256(local).hexdigest()[:12]}"})
        live_age = max_age_of(headers.get("cache-control", ""))
        if live_age != window_for(asset, promises):
            findings.append({"asset": asset, "status": status,
                             "note": f"edge cached for max-age={live_age}, _headers promises "
                                     f"{window_for(asset, promises)}"})
    assert not findings, ("the published Pages bundle is not this checkout:\n"
                          + "\n".join(f"{f['asset']}: {f.get('note', f.get('served', f.get('status')))}"
                                     for f in findings))


def test_the_published_edge_still_answers_what_it_cannot_find():
    """`/robots.txt` is the origin's document, not a bundle file: the deployed
    Function listed `txt` among the static extensions, missed it in the bundle and
    let Pages answer the landing page with a 200 — a crawler reading a whole
    website where it expected rules. A missing asset is the same shape from the
    other side: the routing has to let the answer reach the origin, which is the
    only thing that knows it is a 404."""
    status, headers, body = get("/robots.txt")
    assert status == 200, status
    assert headers.get("content-type", "").startswith("text/plain"), headers.get("content-type")
    assert b"<!doctype" not in body[:200].lower(), "robots.txt is the landing page"
    assert b"__" not in body, "robots.txt carries an unsubstituted template token"

    missing = get("/wave33-no-such-asset.css")
    assert missing[0] != 200, f"a missing stylesheet answered {missing[0]} {missing[1].get('content-type')}"


def test_prod_csp_and_mime_types(page):
    """Проверка CSP заголовков и корректной отдачи JS файлов"""
    response = page.goto(PROD_URL)
    assert response is not None, "Сайт не отвечает"
    assert response.status == 200

    headers = response.all_headers()
    csp = headers.get("content-security-policy", "")

    # Проверка наличия разрешенного фрейма для Telegram OAuth
    assert "frame-src 'self' https://oauth.telegram.org" in csp, "CSP блокирует Telegram Login Widget"

    # Проверка nonce для скриптов (защита от XSS)
    assert "script-src" in csp and "'nonce-" in csp, "Отсутствует nonce в script-src CSP"

def test_prod_js_mime_type(page):
    """Проверка, что JS отдается с правильным MIME-типом (фикс fe95b97)"""
    # Перехватываем все ответы и ищем JS файлы
    js_responses = []
    page.on("response", lambda r: js_responses.append(r) if "javascript" in r.headers.get("content-type", "").lower() or r.url.endswith(".js") else None)

    page.goto(PROD_URL)

    # Пустой список означал бы, что страница вообще не загрузила ни одного
    # скрипта — тогда цикл ниже не проверяет ничего и тест проходит впустую.
    shipped = [r for r in js_responses if r.url.endswith(".js")]
    assert shipped, "Лендинг не отдал ни одного .js — проверять MIME больше нечего"

    # Фильтруем именно те, что должны быть JS (например, основной бандл или chain.js)
    for r in shipped:
        content_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
        assert content_type in ["application/javascript", "text/javascript"], \
            f"Неверный MIME-тип для {r.url}: {content_type}"

def test_prod_cookie_security(page):
    """Проверка атрибутов безопасности кук (Secure, HttpOnly, SameSite)"""
    page.goto(PROD_URL)

    # Белый список имён оставлял тест пустым: страница выдаёт tippy_login_state
    # (и tippy_session после входа), а проверяла "session", "auth",
    # "tippy_session" — совпадений не было ни разу. Проверяем каждую куку,
    # которую сайт действительно выставил.
    cookies = page.context.cookies()
    assert cookies, "Сайт не выставил ни одной куки — проверять атрибуты негде"

    for cookie in cookies:
        assert cookie.get("secure") is True, f"Кука {cookie['name']} не имеет флага Secure"
        assert cookie.get("httpOnly") is True, f"Кука {cookie['name']} читается из JS"
        assert cookie.get("sameSite") in ["None", "Lax", "Strict"], \
            f"Кука {cookie['name']} имеет небезопасный SameSite: {cookie.get('sameSite')}"

def test_prod_health_endpoint():
    """Прямая проверка /api/health без браузера

    Запрос идёт с явным User-Agent, как у любого реального клиента: Cloudflare
    на аккаунте отвечает `403 error code: 1010` на сигнатуру urllib по умолчанию
    (Python-urllib/3.x), и без этого тест проверял бы правило бот-защиты, а не
    здоровье приложения.
    """
    import json
    import urllib.request

    req = urllib.request.Request(
        f"{PROD_URL}/api/health",
        method="GET",
        headers={"user-agent": "tippy-health-check/1.0"},
    )
    try:
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            data = json.loads(response.read().decode())
            assert data.get("status") == "ok" or "db" in data, "Некорректный ответ health endpoint"
    except urllib.error.HTTPError as e:
        pytest.fail(f"Health endpoint вернул ошибку: {e.code} {e.reason}")
