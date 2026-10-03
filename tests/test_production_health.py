import pytest
from playwright.sync_api import Page

PROD_URL = "https://tippy-on-base.pages.dev"

@pytest.mark.production
def test_prod_csp_and_mime_types(page: Page):
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

@pytest.mark.production
def test_prod_js_mime_type(page: Page):
    """Проверка, что JS отдается с правильным MIME-типом (фикс fe95b97)"""
    # Перехватываем все ответы и ищем JS файлы
    js_responses = []
    page.on("response", lambda r: js_responses.append(r) if "javascript" in r.headers.get("content-type", "").lower() or r.url.endswith(".js") else None)

    page.goto(PROD_URL)

    # Фильтруем именно те, что должны быть JS (например, основной бандл или chain.js)
    for r in js_responses:
        if r.url.endswith(".js"):
            content_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
            assert content_type in ["application/javascript", "text/javascript"], \
                f"Неверный MIME-тип для {r.url}: {content_type}"

@pytest.mark.production
def test_prod_cookie_security(page: Page):
    """Проверка атрибутов безопасности кук (Secure, SameSite)"""
    page.goto(PROD_URL)

    # Триггерим действие, которое должно установить куку (например, попытка загрузки или наличие уже установленной куки)
    # Если кука еще не установлена, тест проверит те, что есть, или можно эмулировать клик по логину
    cookies = page.context.cookies()

    for cookie in cookies:
        if cookie["name"] in ["session", "auth", "tippy_session"]: # замени на реальное имя твоей куки
            assert cookie.get("secure") is True, f"Кука {cookie['name']} не имеет флага Secure"
            assert cookie.get("sameSite") in ["None", "Lax", "Strict"], \
                f"Кука {cookie['name']} имеет небезопасный SameSite: {cookie.get('sameSite')}"

@pytest.mark.production
def test_prod_health_endpoint():
    """Прямая проверка /api/health без браузера"""
    import json
    import urllib.request

    req = urllib.request.Request(f"{PROD_URL}/api/health", method="GET")
    try:
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            data = json.loads(response.read().decode())
            assert data.get("status") == "ok" or "database" in data, "Некорректный ответ health endpoint"
    except urllib.error.HTTPError as e:
        pytest.fail(f"Health endpoint вернул ошибку: {e.code} {e.reason}")
