"""Wave 41, stage 5 — the two pool pages take the v2 workspace frame.

The delivered design reads a market as **one object in two columns**: the thing you are
deciding about on the left, the decision and its rules on the right. The shipped pages
stacked a card, a CTA row and a wall of prose in one 720 px column, so the odds and the
mechanics were 600 px apart on a desktop and the hairline grid vocabulary (`.wp-card`,
`.list-row`) existed in the stylesheet for nothing.

Nothing new is invented: every figure still comes from the same `/api/market/{id}` and
`/api/onchain/market/{id}` reads through the same ids. `#m-body`/`#oc-body` stay
heading-free because the not-found and crash branches write into them, and the CTA keeps
its `hidden` attribute so a pool the ledger does not have still shows no working button.

`.out-grid`/`.out-cell` are deleted rather than used: the option row this site paints is
`.option` with a bar, shared with `agent.js`, and a second row vocabulary for the same
payload is exactly the drift the label-voice laws refuse.
"""

from pathlib import Path

CSS = Path("web/static/style.css")
MARKET = Path("web/static/market.html")
OC = Path("web/static/oc.html")

n = 0


def patch(path: Path, pairs: list[tuple[str, str]]) -> None:
    global n
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    t = raw.decode("utf-8-sig")
    for old, new in pairs:
        assert t.count(old) == 1, f"{path.name}: found {t.count(old)}x, expected 1: {old[:90]!r}"
        t = t.replace(old, new, 1)
        n += 1
    assert "\r\n" not in t
    out = t.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" + out) if had_bom else out)


# ---------------------------------------------------------------- style.css
OUT_CSS = """/* An option cell in a price row: the choice and its odds in one hairline box. */
.out-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.out-cell {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  padding: 8px 12px; border: 1px solid var(--border); background: var(--canvas);
  font-size: var(--t-meta); font-weight: 700; color: var(--ink-2);
}
.out-cell b {
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: var(--t-ui); color: var(--ink); font-weight: 600;
}

"""

patch(CSS, [
    (OUT_CSS, ""),
    (
        ".wp-card { background: var(--surface); padding: 20px 18px; }\n"
        ".wp-card h3 { font-size: var(--t-ui); font-weight: 700; margin: 10px 0 6px; }",
        ".wp-card { background: var(--surface); padding: 20px 18px; }\n"
        "/* A card opens with its own title, so the title carries no top offset; on the\n"
        "   status grid the first child is a badge, which had none to lose. */\n"
        ".wp-card > :first-child { margin-top: 0; }\n"
        ".wp-card h2, .wp-card h3 { font-size: var(--t-ui); font-weight: 700; margin: 10px 0 6px; }",
    ),
])

# ---------------------------------------------------------------- market.html
MARKET_OLD = """      <div id="m-body">
        <div class="page-meta" id="m-meta"></div>

        <div class="market-card">
          <div id="m-options"></div>
          <div class="market-footer">
            <span class="pot" id="m-pot"></span>
            <span class="pot" id="m-creator"></span>
          </div>
        </div>

        <div class="cta-row" id="m-cta" hidden>
          <a class="btn btn-primary" id="m-bet" target="_blank" rel="noopener">Поставить в Telegram</a>
          <button class="btn btn-ghost" id="m-share" type="button">Скопировать ссылку</button>
        </div>
      </div>

      <section class="page-block">
        <span class="eyebrow">Механика</span>
        <p class="hint">
          Все ставки складываются в общий пул. Если ваш исход выиграл, вы получаете
          долю всего пула, пропорциональную вашему вкладу в этот исход; шанс рядом
          с вариантом — его доля в пуле на текущий момент. Вручную в боте:
          <code>/bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;</code>.
        </p>
      </section>"""

MARKET_NEW = """      <div class="workspace-row">
        <div class="workspace-col">
          <div class="wp-card" id="m-body">
            <div class="page-meta" id="m-meta"></div>
            <div id="m-options"></div>
            <div class="market-footer">
              <span class="pot" id="m-pot"></span>
              <span class="pot" id="m-creator"></span>
            </div>
          </div>
        </div>
        <div class="workspace-col">
          <section class="wp-card">
            <h2>Ставка</h2>
            <div class="cta-row" id="m-cta" hidden>
              <a class="btn btn-primary" id="m-bet" target="_blank" rel="noopener">Поставить в Telegram</a>
              <button class="btn btn-ghost" id="m-share" type="button">Скопировать ссылку</button>
            </div>
            <p class="hint">
              Пул открывается и закрывается в боте: он держит взнос до исхода и
              выплачивает долю тем, чей вариант победил.
            </p>
          </section>
          <section class="wp-card">
            <h2>Механика</h2>
            <div class="list-row"><span>Пул</span><b>общая касса</b></div>
            <div class="list-row"><span>Шанс варианта</span><b>его доля в пуле</b></div>
            <div class="list-row"><span>Выплата</span><b>пул на вашу долю</b></div>
            <div class="list-row"><span>Вручную</span><b>/bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;</b></div>
          </section>
        </div>
      </div>"""

patch(MARKET, [(MARKET_OLD, MARKET_NEW)])

# ---------------------------------------------------------------- oc.html
OC_OLD = """      <div id="oc-body">
        <div class="page-meta" id="oc-meta"></div>

        <div class="market-card">
          <div id="oc-options"></div>
          <div class="market-footer">
            <span class="pot" id="oc-pool"></span>
            <span class="pot" id="oc-creator"></span>
          </div>
        </div>
      </div>

      <div class="cta-row">
        <a class="btn btn-primary" href="https://t.me/__BOT_USERNAME__" data-tg-link>Торговать в Telegram</a>
        <a class="btn btn-ghost" id="oc-scan" target="_blank" rel="noopener" hidden>Контракт в обозревателе</a>
        <button class="btn btn-ghost" id="oc-share" type="button">Скопировать ссылку</button>
      </div>

      <section class="page-block">
        <span class="eyebrow">Механика</span>
        <p class="hint">
          Исходные доли — токены ERC-1155 в контракте OutcomeMarket, расчёты идут в
          USDC на-цепи. Цены держит LMSR-кривая: чем больше доли куплены, тем выше
          их цена, и покупка следующего юнита всегда дороже. В боте:
          <code>/oc_create</code>, <code>/oc_buy &lt;id&gt; &lt;вариант&gt; &lt;сумма&gt;</code>,
          <code>/oc_sell</code>, <code>/oc_redeem &lt;id&gt;</code>.
        </p>
      </section>"""

OC_NEW = """      <div class="workspace-row">
        <div class="workspace-col">
          <div class="wp-card" id="oc-body">
            <div class="page-meta" id="oc-meta"></div>
            <div id="oc-options"></div>
            <div class="market-footer">
              <span class="pot" id="oc-pool"></span>
              <span class="pot" id="oc-creator"></span>
            </div>
          </div>
        </div>
        <div class="workspace-col">
          <section class="wp-card">
            <h2>Торговля</h2>
            <div class="cta-row">
              <a class="btn btn-primary" href="https://t.me/__BOT_USERNAME__" data-tg-link>Торговать в Telegram</a>
              <a class="btn btn-ghost" id="oc-scan" target="_blank" rel="noopener" hidden>Контракт в обозревателе</a>
              <button class="btn btn-ghost" id="oc-share" type="button">Скопировать ссылку</button>
            </div>
            <p class="hint">
              Доли и USDC живут в контракте OutcomeMarket: бот подписывает сделку,
              сеть её исполняет.
            </p>
          </section>
          <section class="wp-card">
            <h2>Механика</h2>
            <div class="list-row"><span>Доли</span><b>ERC-1155</b></div>
            <div class="list-row"><span>Расчёт</span><b>USDC на-цепи</b></div>
            <div class="list-row"><span>Цены</span><b>кривая LMSR</b></div>
            <div class="list-row"><span>В боте</span><b>/oc_buy &lt;id&gt; &lt;вариант&gt; &lt;сумма&gt;</b></div>
            <div class="list-row"><span>Ещё</span><b>/oc_create · /oc_sell · /oc_redeem</b></div>
          </section>
        </div>
      </div>"""

patch(OC, [(OC_OLD, OC_NEW)])

print(f"pool pages re-framed as a v2 workspace ({n} edits)")
