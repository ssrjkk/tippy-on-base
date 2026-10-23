"""Login for the personal dashboard: Telegram widget or wallet signature.

Two ways to prove identity, both verified with real crypto:

- **Telegram Login Widget**: HMAC-SHA256 over the widget's data-check-string,
  keyed with SHA256(BOT_TOKEN) — the official algorithm from
  https://core.telegram.org/widgets/login#checking-authorization.
- **Wallet connect**: EIP-191 ``personal_sign`` recovered with eth_account and
  matched against ``wallet_links`` (the same table the bot's /link flow
  writes), so a web login is impossible for a wallet the bot doesn't know.

Sessions are stateless signed cookies: ``base64(tg_id:expiry).hmac``. No
server-side session store; tampering breaks the HMAC; expiry bounds replay.
"""
import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from bot import config
from bot.ledger import async_ledger as ledger

MICRO = 10 ** config.USDC_DECIMALS

def _usdc_from_micro(micro) -> float:
    """Exact micro→USDC float. Matches web/mini._fmt and web/server._usdc."""
    return float(Decimal(micro) / Decimal(MICRO))

router = APIRouter()
COOKIE_NAME = 'tippy_session'
SESSION_TTL_SECONDS = 30 * 24 * 3600
TG_AUTH_DATE_TTL = 24 * 3600
TG_AUTH_DATE_FUTURE_SKEW = 300
WALLET_MSG_TTL = 600
LOGIN_STATE_COOKIE = 'tippy_login_state'
LOGIN_STATE_TTL = 30 * 60
_consumed_login_states: dict[str, float] = {}

def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()

def make_session(tg_id: int, ttl: int=SESSION_TTL_SECONDS) -> str:
    payload = base64.urlsafe_b64encode(f'{tg_id}:{int(time.time()) + ttl}'.encode()).decode()
    return f'{payload}.{_sign(payload)}'

def parse_session(token: str | None) -> int | None:
    """Return tg_id for a valid, unexpired session cookie, else None."""
    if not token or '.' not in token:
        return None
    payload, sig = token.rsplit('.', 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        raw = base64.urlsafe_b64decode(payload.encode()).decode()
        tg_id_s, exp_s = raw.split(':')
        tg_id, exp = (int(tg_id_s), int(exp_s))
    except Exception:
        return None
    if exp < time.time():
        return None
    return tg_id

def verify_telegram(params: dict[str, str]) -> int:
    """Validate Telegram Login Widget fields, return the tg_id."""
    received_hash = params.get('hash', '')
    if not received_hash:
        raise HTTPException(403, 'missing hash')
    check_string = '\n'.join((f'{k}={v}' for k, v in sorted(params.items()) if k != 'hash'))
    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(403, 'bad signature')
    try:
        auth_date = int(params.get('auth_date', '0'))
    except ValueError:
        raise HTTPException(403, 'bad auth_date') from None
    now = int(time.time())
    if now - auth_date > TG_AUTH_DATE_TTL:
        raise HTTPException(403, 'stale auth_date')
    if auth_date > now + TG_AUTH_DATE_FUTURE_SKEW:
        # A signed widget hash with a fabricated future timestamp would be
        # replayable forever; refuse anything that claims to be from the future.
        raise HTTPException(403, 'auth_date in the future')
    try:
        return int(params['id'])
    except (KeyError, ValueError):
        raise HTTPException(403, 'missing id') from None

class WalletLogin(BaseModel):
    address: str
    message: str
    signature: str

async def verify_wallet(body: WalletLogin) -> int:
    """Recover the signer and map it to a linked bot user."""
    lines = body.message.strip().splitlines()
    exp = next((ln for ln in lines if ln.startswith('Expires:')), None)
    nonce = next((ln for ln in lines if ln.startswith('Nonce:')), None)
    if not exp or not nonce or len(nonce.split(': ', 1)) < 2:
        raise HTTPException(403, 'malformed login message')
    try:
        expires = int(exp.split(': ', 1)[1])
    except ValueError:
        raise HTTPException(403, 'malformed expiry') from None
    now = time.time()
    if not now < expires <= now + WALLET_MSG_TTL:
        raise HTTPException(403, 'expired login message')
    from eth_account import Account
    from eth_account.messages import encode_defunct
    try:
        recovered = await asyncio.to_thread(
            Account.recover_message,
            encode_defunct(text=body.message), signature=body.signature,
        )
    except Exception:
        raise HTTPException(403, 'bad signature') from None
    if recovered.lower() != body.address.lower():
        raise HTTPException(403, 'signature does not match address')
    tg_id = await ledger.tg_id_of_address(recovered)
    if tg_id is None:
        raise HTTPException(404, f'wallet is not linked to any Tippy user - link it in the bot first (t.me/{config.BOT_USERNAME})')
    # One-time use per signed message: the same (message, signature) must not
    # mint a fresh session twice. Consumed only after full verification.
    nonce_val = nonce.split(': ', 1)[1].strip()
    if not await ledger.consume_login_nonce(nonce_val):
        raise HTTPException(403, 'login message already used')
    return tg_id


def _issue_login_state() -> str:
    """One-time signed state issued on every page that renders the widget; the
    auth callback must echo it back.

    A cross-site attacker cannot forge it (HMAC over SECRET_KEY) and cannot
    read it (HttpOnly), so a forged /api/auth/telegram from another site dies
    here before a session cookie is minted.
    """
    payload = f'{int(time.time())}:{secrets.token_hex(16)}'
    return f'{payload}.{_sign(payload)}'


def _check_login_state(request: Request) -> bool:
    """Validate and consume the login-state cookie (single-use).

    A cross-site attacker cannot forge it (HMAC over SECRET_KEY) and cannot
    read it (HttpOnly), so a forged /api/auth/telegram from another site dies
    here before a session cookie is minted. Consumed after first use so the
    same state cannot mint multiple sessions.
    """
    token = request.cookies.get(LOGIN_STATE_COOKIE, '')
    if not token or '.' not in token:
        return False
    payload, sig = token.rsplit('.', 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        ts_s, _nonce = payload.split(':', 1)
        ts = int(ts_s)
    except ValueError:
        return False
    ttl = int(time.time()) - ts
    if not (0 <= ttl <= LOGIN_STATE_TTL):
        return False
    # Single-use: mark consumed so the same state cannot be replayed.
    state_key = f'{payload}.{sig}'
    if state_key in _consumed_login_states:
        return False
    _consumed_login_states[state_key] = time.time()
    # TTL-based eviction: only remove entries older than the state TTL,
    # so a full clear never opens a replay window for recent states.
    cutoff = time.time() - LOGIN_STATE_TTL
    expired = [k for k, v in _consumed_login_states.items() if v < cutoff]
    for k in expired:
        del _consumed_login_states[k]
    return True

def secure_request(request: Request) -> bool:
    """True when the browser reached us over HTTPS, including behind a proxy
    (Cloudflare Pages sets ``X-Forwarded-Proto`` and terminates TLS itself)."""
    return (request.url.scheme == 'https'
            or request.headers.get('x-forwarded-proto', '').lower() == 'https')

def attach_login_state(request: Request, response):
    """Issue the signed one-time state the Telegram callback must echo back.

    Both ``/`` and the legacy ``/login`` bookmark call this: the landing page
    renders the widget inline, so signing in there must not require a prior
    trip elsewhere.
    """
    response.set_cookie(
        LOGIN_STATE_COOKIE, _issue_login_state(),
        max_age=LOGIN_STATE_TTL, httponly=True, samesite='lax',
        secure=secure_request(request),
    )
    return response

@router.get('/login', include_in_schema=False)
async def login_page(request: Request) -> RedirectResponse:
    """Old bookmark — the cabinet lives on the landing page now.

    The redirect still issues the login-state cookie: someone arriving from a
    saved link or a cached page gets the widget's callback accepted without a
    second trip.
    """
    return attach_login_state(request, RedirectResponse('/#workspace-section', status_code=303))

# Stateless session: signed HMAC cookie, no server-side store.  The only
# revocation mechanism is rotating SECRET_KEY, which logs out every user.
# Keep this comment so future contributors know there's no per-session kill.
def _session_response(request: Request, tg_id: int, redirect: str | None=None) -> JSONResponse:
    resp: JSONResponse | RedirectResponse
    if redirect:
        resp = RedirectResponse(redirect, status_code=303)
    else:
        resp = JSONResponse({'ok': True, 'tg_id': tg_id})
    resp.set_cookie(
        COOKIE_NAME, make_session(tg_id), max_age=SESSION_TTL_SECONDS,
        httponly=True, samesite='lax',
        secure=secure_request(request),
    )
    return resp

@router.get('/api/auth/telegram', include_in_schema=False)
async def auth_telegram(request: Request):
    # Login CSRF guard: only proceed when the request carries a fresh, signed
    # login-state cookie issued by our own pages (HttpOnly, so a cross-site
    # page cannot forge or read it, and a forged callback dies here before a
    # session cookie is minted).
    if not _check_login_state(request):
        raise HTTPException(403, 'missing or expired login state — reload the page and try again')
    params = dict(request.query_params)
    tg_id = verify_telegram(params)
    # One-time use per signed blob. Telegram's hash stays valid for
    # TG_AUTH_DATE_TTL (24h), so a captured callback URL — from a shoulder, an
    # extension, a leaked history entry — would otherwise mint sessions in the
    # attacker's browser for a full day. The key is (id, auth_date), not the
    # hash: Telegram stamps auth_date when it renders the widget, so every real
    # page load presents a new key and only a literal replay collides.
    if not await ledger.consume_login_nonce(f'tg-widget:{tg_id}:{params["auth_date"]}'):
        raise HTTPException(403, 'this Telegram login has already been used — reload the page and sign in again')
    username = params.get('username', '')
    await ledger.ensure_user(tg_id, username or None)
    return _session_response(request, tg_id, redirect='/')

@router.post('/api/auth/wallet', tags=['auth'])
async def auth_wallet(request: Request, body: WalletLogin):
    tg_id = await verify_wallet(body)
    return _session_response(request, tg_id)

@router.post('/logout', include_in_schema=False)
async def logout(request: Request):
    resp = RedirectResponse('/', status_code=303)
    # delete_cookie must mirror set_cookie's flags (httponly, samesite, secure)
    # or the browser refuses to clear the cookie — a mismatched Secure flag is
    # the common case: an HTTPS-only cookie cannot be deleted from an http://
    # response, so the user stays logged in after clicking logout.
    secure = secure_request(request)
    resp.delete_cookie(COOKIE_NAME, httponly=True, samesite='lax', secure=secure)
    resp.delete_cookie(LOGIN_STATE_COOKIE, httponly=True, samesite='lax', secure=secure)
    return resp

@router.get('/api/me', tags=['users'])
async def api_me(request: Request):
    tg_id = parse_session(request.cookies.get(COOKIE_NAME))
    if tg_id is None:
        raise HTTPException(401, 'not logged in')
    if not await ledger.user_exists(tg_id):
        raise HTTPException(401, 'unknown user')
    bal = await ledger.balance(tg_id)
    positions = [{'market_id': p['market_id'], 'question': p['question'], 'option': p['option'], 'shares': _usdc_from_micro(p['shares']), 'cost_usdc': _usdc_from_micro(p['cost']), 'price': float(p['price']), 'value_usdc': _usdc_from_micro(p['value'])} for p in await ledger.user_market_positions(tg_id)]
    return {'tg_id': tg_id, 'balance_usdc': float(bal), 'linked_address': await ledger.linked_address(tg_id), 'positions': positions, 'bot_link': f'https://t.me/{config.BOT_USERNAME}'}
