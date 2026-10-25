"""Canonical Telegram command menu as plain data.

Kept free of aiogram imports so both the bot (which wraps these into
``BotCommand``) and the web process (which publishes them through
``/api/info``) can read the same list without pulling the handler stack.

The third field is a presentation group for the website's command table —
Telegram's own menu is flat, so the bot ignores it.
"""

BOT_COMMAND_SPECS = [
    ('menu', 'Главное меню', 'account'),
    ('help', 'Все команды', 'account'),
    ('balance', 'Баланс кошелька', 'wallet'),
    ('deposit', 'Пополнить USDC', 'wallet'),
    ('withdraw', 'Вывести USDC', 'wallet'),
    ('tip', 'Чаевые USDC', 'tips'),
    ('rain', 'Дождь: раздать USDC в чате', 'tips'),
    ('markets', 'Рынки предсказаний', 'markets'),
    ('market', 'Открыть рынок по id', 'markets'),
    ('trade', 'Купить доли на рынке', 'markets'),
    ('sell', 'Продать доли', 'markets'),
    ('positions', 'Мои позиции и PnL', 'markets'),
    ('bet', 'Ставка-пул: создать/поставить', 'bets'),
    ('bets', 'Открытые ставки-пулы', 'bets'),
    ('oc', 'Cally — ончейн-рынки (ERC-1155)', 'onchain'),
    ('oc_create', 'Создать ончейн-рынок', 'onchain'),
    ('oc_buy', 'Купить ончейн-доли', 'onchain'),
    ('oc_sell', 'Продать ончейн-доли', 'onchain'),
    ('oc_redeem', 'Забрать выигрыш с ончейн-рынка', 'onchain'),
    ('oc_pos', 'Мои ончейн-доли', 'onchain'),
    ('mybets', 'Мои ставки', 'bets'),
    ('resolve', 'Закрыть ставку (создатель)', 'bets'),
    ('cancel', 'Отменить свою ставку', 'bets'),
    ('stats', 'Статистика бота', 'account'),
    ('top', 'Топ пользователей', 'account'),
    ('history', 'История операций', 'account'),
    ('donate', 'Твоя страница донатов', 'tips'),
    ('link', 'Привязать внешний кошелёк', 'wallet'),
    ('confirm', 'Подтвердить привязку', 'wallet'),
    ('claim', 'Забрать дивиденды', 'creator'),
    ('wallet', 'Кошелёк: адрес и ключи', 'wallet'),
    ('import', 'Импорт по сид-фразе', 'wallet'),
    ('export', 'Выгрузить ключ и сид', 'wallet'),
    ('tx', 'Проверить транзакцию в Base', 'wallet'),
    ('paywall', 'Платные посты', 'creator'),
    ('basename', 'Basename: ончейн-имя на Base', 'creator'),
    ('settings', 'Настройки', 'account'),
    ('language', 'Сменить язык / Language', 'account'),
    ('about', 'О боте — что это такое', 'account'),
    ('app', 'Мини-приложение', 'account'),
    ('ask', 'Спросить ИИ-ассистента', 'account'),
    ('gasless', 'Бесплатные транзакции', 'wallet'),
    ('subscribe', 'Подписка на платежи', 'tips'),
    ('subscriptions', 'Мои подписки', 'tips'),
    ('cancelsub', 'Отменить подписку', 'tips'),
    ('credit', 'Кредитный рейтинг', 'creator'),
    ('createtoken', 'Создать токен создателя', 'creator'),
    ('buytoken', 'Купить токен создателя', 'creator'),
]
