"""/ask — the assistant: an LLM provider when one answers, ledger data when none does."""
import html

from aiogram import types
from aiogram.filters import Command

from .. import ai, ai_data, i18n
from . import _common as common

__all__ = ['cmd_ask']

def _quote_context(message: types.Message) -> str:
    ref = getattr(message, 'reply_to_message', None)
    if not ref or not ref.text:
        return ''
    snippet = ref.text[:800]
    return f'\n\nContext (replied message):\n{snippet}'

@common.router.message(Command('ask'))
async def cmd_ask(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    question = message.text.split(maxsplit=1)[1].strip() if len(message.text.split(maxsplit=1)) > 1 else ''
    if not question:
        await message.answer(i18n.t(lang, 'ai_question_empty'))
        return
    if len(question) > common.config.AI_MAX_QUESTION_LEN:
        await message.answer(i18n.t(lang, 'ai_question_long', n=common.config.AI_MAX_QUESTION_LEN))
        return
    wait = await common._throttle(message.from_user.id, 'ask')
    if wait:
        await message.answer(wait)
        return
    full_question = question + _quote_context(message)
    try:
        await message.bot.send_chat_action(message.chat.id, 'typing')
    except Exception:
        pass
    try:
        answer = await ai.ask_about_markets(full_question)
    except RuntimeError:
        # No provider configured, every one of them failed, or today's token
        # budget is spent. The question still gets an answer: markets, pools
        # and commands are read from the ledger instead of from a model.
        answer = await ai_data.answer(full_question, lang)
    if not answer:
        await message.answer(i18n.t(lang, 'ai_empty_answer'))
        return
    if len(answer) > common.config.AI_MAX_ANSWER_CHARS:
        answer = answer[:common.config.AI_MAX_ANSWER_CHARS - 1] + '…'
    header = '🧠 <b>Tippy AI</b>\n\n'
    body = html.escape(answer)[:4096 - len(header)]
    # The cut can land inside an escaped entity ('&l' without ';'), which
    # Telegram rejects as malformed markup — drop a dangling partial entity.
    amp = body.rfind('&')
    if amp != -1 and ';' not in body[amp:] and len(body) - amp <= 7:
        body = body[:amp]
    await message.answer(header + body)
