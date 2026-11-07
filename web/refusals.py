"""The sentence an API refusal leaves on the screen.

Almost every refusal in `web/mini.py` is money: the user taps «Отправить», the
ledger says no, and what fills the message slot is whatever this module
returns. Until now that was a developer string — `insufficient balance`,
`tip exceeds the 1000 USDC cap` — painted in front of a Russian or Chinese
interface, next to the receipt line it was standing in for.

The copy lives in `bot/i18n.py` with the bot's own cards, so one table answers
three languages and `scripts/check_i18n.py` keeps it whole. The language is the
one the session already chose (`/api/mini/lang` and the bot's /lang write the
same setting the bot reads), cached on `request.state` when the caller is
identified.

A refusal raised *before* identity — a signature that does not check out, a
login link that expired — has no language to inherit and gets the product
default, which is the language of the page that made the call: the landing is
Russian. The exact check that failed goes to the log instead of the response;
telling a visitor which HMAC comparison broke helps an attacker more than it
helps them. The Mini App never paints those at all — it answers 401/403 with
its own localized line, because its language is only known after the login it
is refusing.
"""
import logging

from fastapi import HTTPException, Request

from bot import i18n

log = logging.getLogger('web.refusals')


def lang_of(request: Request | None) -> str:
    """The caller's language, or the product default before they are identified."""
    return i18n.norm(getattr(request.state, 'lang', None) if request is not None else None)


def refuse(request: Request | None, status: int, key: str, **kwargs) -> HTTPException:
    """Build the refusal to raise: `raise refuse(request, 400, 'api_insufficient')`."""
    return HTTPException(status, i18n.t(lang_of(request), key, **kwargs))


def refuse_protocol(request: Request | None, status: int, key: str, reason: str) -> HTTPException:
    """A refusal whose precise reason is for the log, not for the visitor."""
    log.info('protocol refusal (%s): %s', status, reason)
    return refuse(request, status, key)


class LoginRejected(Exception):
    """A login did not verify; `reason` says which check said no.

    The verifier must not decide how the visitor learns this: a fetch paints
    the localized refusal, a browser navigation gets the page back with a
    reason code. So it raises this, and the route answers in its own shape —
    and the reason goes to the log, never into what the page shows, because
    naming the weak comparison helps whoever sent the bad blob.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
