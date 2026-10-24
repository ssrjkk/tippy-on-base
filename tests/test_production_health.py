import importlib.util

import pytest

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

pytestmark = [
    pytest.mark.production,
    pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed"),
]

PROD_URL = "https://tippy-egi.pages.dev"

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
