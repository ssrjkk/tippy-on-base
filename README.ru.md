# Tippy

> **Языки:** [English](README.md) · [Русский](README.ru.md)

Telegram-бот, Mini App и веб-панель для экономики сообщества в USDC на Base:
чаевые, донаты, прогнозные рынки, платный контент и кошельки пользователей.

**Сайт:** https://tippy-egi.pages.dev/  
**Бот:** https://t.me/tippy_on_base_bot

> **Кастодиальность:** встроенные кошельки и Smart Accounts управляются оператором.
> Не храните у бота средства, которые не готовы доверить оператору. Перед запуском
> прочитайте [документацию по безопасности](docs/SECURITY.md).

## Возможности

- Чаевые `/tip`, распределение `/rain` и донатные страницы.
- Рынки LMSR с живыми коэффициентами, покупкой и продажей долей.
- Паримутуэльные опросы и on-chain рынки OutcomeMarket.
- Пополнение, вывод, привязка внешнего кошелька и Basenames.
- Платный контент, платные каналы и x402 HTTP-платежи.
- AI-помощник с OpenAI-совместимыми провайдерами и fallback.
- Публичный dashboard, Proof of Reserves и JSON API.
- Автономный агент с лимитами расходов и журналом действий.

## Установка и запуск

```bash
git clone https://github.com/ssrjkk/tippy-on-base.git
cd tippy-on-base
python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python deploy/run.py
```

Заполните `BOT_TOKEN`, ключи кошельков и параметры базы. Отдельные процессы:

```bash
python -m bot.main
python -m uvicorn web.server:app
```

## Проверки

```bash
python -m ruff check bot web tests deploy agent scripts alembic
python -m pytest -q -m "not production"
```

Для тестов нужна PostgreSQL 16 и отдельная тестовая база; production-проверки
запускаются отдельным workflow против опубликованного сервиса.

## Документация

- [Whitepaper](docs/WHITEPAPER.md)
- [Руководство пользователя](docs/USER_GUIDE.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Деплой](docs/DEPLOY.md)
- [Cloudflare](docs/CLOUDFLARE.md)
- [Безопасность](SECURITY.md)

## Лицензия

MIT — см. [LICENSE](LICENSE).
