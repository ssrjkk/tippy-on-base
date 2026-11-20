"""Farcaster Frame for paid posts: a shareable card that funnels Warpcast
users into the Telegram bot (or the raw x402 API).

GET /frame/<item_id> — the Frame HTML (OpenGraph + fc:frame meta tags).
The card image is the brand PNG at the bundle root. ``pages_build_output_dir``
is ``web/static``, so the URL a crawler fetches has no ``/static/`` in it: the
image used to be published as ``/static/frame.png``, which neither the origin
nor the Pages bundle has ever served, and a frame whose ``fc:frame:image``
does not resolve is a frame Warpcast refuses to draw.

Warpcast requires the frame image to be an absolute https URL, so the page
is only meaningful once the webhook URL is configured (after deploy).
"""
from decimal import Decimal
from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from bot import config
from bot.ledger import async_ledger as ledger
from web.mini import SHARE_IMAGE, public_base_url

router = APIRouter()
MICRO = 10 ** config.USDC_DECIMALS


@router.get('/frame/{item_id}', response_class=HTMLResponse)
async def frame_page(item_id: int) -> HTMLResponse:
    item = await ledger.paywall_item(item_id)
    if item is None:
        # A real 404, not a 200 that says "not found": Warpcast and every
        # crawler read the status, and a page that exists for no row at all is
        # a page that can be indexed against an id that never was a post.
        return HTMLResponse(
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta name="robots" content="noindex">\n<title>Post not found — Tippy</title>\n'
            '</head>\n<body>Post not found</body>\n</html>\n',
            status_code=404)
    base_url = public_base_url().rstrip('/')
    price = Decimal(int(item['price_micro'])) / Decimal(MICRO)
    title = escape(item['title'])
    image_url = escape(f'{base_url}{SHARE_IMAGE}')
    bot_url = escape(f'https://t.me/{config.BOT_USERNAME}?start=paywall_{item_id}')
    api_url = escape(f'{base_url}/api/x402/paywall?item={item_id}&amount={price:f}')
    html = f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta property="og:title" content="{title} — {price:f} USDC on Base">\n<meta property="og:description" content="Paywalled USDC content via Tippy. Pay with x402 or inside Telegram.">\n<meta property="og:image" content="{image_url}">\n<meta property="fc:frame" content="vNext">\n<meta property="fc:frame:image" content="{image_url}">\n<meta property="fc:frame:button:1" content="Buy in Telegram">\n<meta property="fc:frame:button:1:action" content="link">\n<meta property="fc:frame:button:1:target" content="{bot_url}">\n<meta property="fc:frame:button:2" content="x402 API (agents)">\n<meta property="fc:frame:button:2:action" content="link">\n<meta property="fc:frame:button:2:target" content="{api_url}">\n</head>\n<body>{title} — {price:f} USDC</body>\n</html>\n'
    return HTMLResponse(html)
