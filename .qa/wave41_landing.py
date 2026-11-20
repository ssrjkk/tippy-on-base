"""Wave 41 (design v2), stage 3: the landing.

The v2 pack ships a telemetry strip, a hairline KPI strip, a status matrix and
square chips. Every one of those is painted here from a payload this origin
already answers with — the mock's `DEMO` object is not ported, because a number
nobody serves is a number nobody can check.
"""
from pathlib import Path

CSS = Path("web/static/style.css")
HTML = Path("web/static/index.html")
JS = Path("web/static/app.js")


class Editor:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.text = path.read_text(encoding="utf-8-sig")

    def sub(self, old: str, new: str) -> None:
        n = self.text.count(old)
        assert n == 1, "%s: pattern found %d times, expected 1: %r" % (self.path, n, old[:110])
        self.text = self.text.replace(old, new)

    def save(self) -> None:
        assert "\r\n" not in self.text, "%s: CRLF introduced" % self.path
        self.path.write_bytes(self.text.encode("utf-8"))
        print("wrote", self.path)


# --------------------------------------------------------------- stylesheet --
css = Editor(CSS)
css.sub(
    """.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: var(--pill);""",
    """.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 9px; border-radius: 0; border: 1px solid var(--border);""",
)
css.sub(
    """.amount-chip {
  background: var(--canvas); border: 1px solid var(--border-strong);
  color: var(--text-secondary); font: inherit; font-size: var(--t-small); font-weight: 500;
  padding: 6px 14px; border-radius: var(--pill); cursor: pointer;""",
    """.amount-chip {
  background: var(--canvas); border: 1px solid var(--border-strong);
  color: var(--text-secondary); font: inherit; font-size: var(--t-small); font-weight: 500;
  padding: 6px 14px; border-radius: 0; cursor: pointer;""",
)
css.sub(
    """.hero-cta { display: flex; gap: 12px; justify-content: flex-start; flex-wrap: wrap; animation: fadeUp 0.5s var(--ease) 0.15s both; }""",
    """.hero-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 22px; }
.hero-cta { display: flex; gap: 12px; justify-content: flex-start; flex-wrap: wrap; animation: fadeUp 0.5s var(--ease) 0.15s both; }""",
)
# The KPI strip takes the metrics slot the bento rules already style; the old
# centred hero strip stays for the pages that still use it.
css.sub(
    """.hero-metrics {
  margin: 0 24px 72px;""",
    """.hero-metrics {
  margin: 0 24px 56px;""",
)
css.save()

# ----------------------------------------------------------------- document --
html = Editor(HTML)
html.sub(
    """  <div class="scroll-progress" id="scrollProgress"></div>

  <header class="nav">""",
    """  <div class="scroll-progress" id="scrollProgress"></div>

  <!-- The node, not the marketing copy: which chain this origin signs on, where
       its reserves actually sit and what they cover. app.js paints every cell
       from /api/info and /api/solvency, so the strip states no figure of its
       own — a typed one would be a second copy of a knob nobody diffs. -->
  <div class="telemetry" aria-label="Состояние сети">
    <div class="telemetry-inner">
      <span><span class="telemetry-k" id="telemetryChain">BASE</span> · USDC · <span id="telemetryChainId">–</span></span>
      <span>РЕЗЕРВЫ: <span id="telemetrySource">–</span></span>
      <span>ПОКРЫТИЕ: <span id="telemetryCoverage">–</span></span>
      <a class="telemetry-link" href="/api/solvency">PROOF OF RESERVES → JSON</a>
    </div>
  </div>

  <header class="nav">""",
)
html.sub(
    """        <h1>Экономика сообщества<br />в USDC на <span class="accent">Base</span></h1>""",
    """        <div class="hero-tags">
          <span class="badge badge-brand">LMSR AMM</span>
          <span class="badge">ПАРАМЮЭЛЬ</span>
          <span class="badge">ERC-1155</span>
          <span class="badge">x402</span>
        </div>
        <h1>Экономика сообщества<br />в USDC на <span class="accent">Base</span></h1>""",
)
html.sub(
    """    <section class="hero-metrics reveal" id="metrics" aria-label="Метрики сети">
      <div class="stats reveal-stagger" id="stats">
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-volume">–</div><div class="stat-label">Объём, USDC</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-vol30">–</div><div class="stat-label">Объём за 30 дней, USDC</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-users">–</div><div class="stat-label">Пользователей</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-markets">–</div><div class="stat-label">Открытых ставок-пулов</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-tx">–</div><div class="stat-label">Транзакций</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-fees">–</div><div class="stat-label">Собрано комиссий, USDC</div></div>
      </div>
    </section>""",
    """    <section class="kpi-strip reveal" id="metrics" aria-label="Метрики сети">
      <div class="kpi-cell" id="stats">
        <div class="kpi-label">Объём, USDC</div>
        <div class="kpi-val skeleton" id="stat-volume">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Объём за 30 дней</div>
        <div class="kpi-val skeleton" id="stat-vol30">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Пользователей</div>
        <div class="kpi-val skeleton" id="stat-users">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Открытых ставок-пулов</div>
        <div class="kpi-val skeleton" id="stat-markets">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Транзакций</div>
        <div class="kpi-val skeleton" id="stat-tx">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Собрано комиссий, USDC</div>
        <div class="kpi-val skeleton" id="stat-fees">–</div>
      </div>
    </section>""",
)
html.sub(
    """            <div class="wallet-row"><span>Платёжеспособность</span><b id="wallet-solvent">–</b></div>""",
    """            <div class="wallet-row"><span>Платёжеспособность</span><b id="wallet-solvent">–</b></div>
            <!-- The same three figures as one bar: what is owed, what is still
                 in flight, what is left over. Painted by loadWallet from the
                 /api/solvency response this panel already reads. -->
            <div class="seg-bar" role="img" aria-label="Структура резервов" id="reserveBar" hidden>
              <div class="seg-owed" id="segOwed"></div>
              <div class="seg-pending" id="segPending"></div>
              <div class="seg-surplus" id="segSurplus"></div>
            </div>
            <div class="seg-key" id="reserveKey" hidden>
              <span class="seg-key-item"><span class="seg-swatch seg-owed"></span>обязательства</span>
              <span class="seg-key-item"><span class="seg-swatch seg-pending"></span>в ожидании</span>
              <span class="seg-key-item"><span class="seg-swatch seg-surplus"></span>свободно</span>
            </div>""",
)
# What a visitor otherwise has to read six paragraphs to learn: which parts of
# the product this deployment actually runs. Every word comes from /api/info.
html.sub(
    """    <section class="section section--band reveal" id="terms">""",
    """    <section class="section reveal" id="status">
      <span class="eyebrow">Состояние внедрения</span>
      <h2>Что работает на этом инстансе</h2>
      <div class="wp-grid">
        <div class="wp-card">
          <span class="badge" id="statusAiBadge">–</span>
          <h3>Ассистент рынка</h3>
          <p id="statusAiText">–</p>
        </div>
        <div class="wp-card">
          <span class="badge" id="statusOnchainBadge">–</span>
          <h3>Ончейн-доли ERC-1155</h3>
          <p id="statusOnchainText">–</p>
        </div>
        <div class="wp-card">
          <span class="badge" id="statusX402Badge">–</span>
          <h3>Оплата за HTTP-запрос</h3>
          <p id="statusX402Text">–</p>
        </div>
      </div>
    </section>

    <section class="section section--band reveal" id="terms">""",
)
html.save()

# --------------------------------------------------------------------- js ----
js = Editor(JS)
js.sub(
    """    applyNetInfo(info);
    setupX402(info);""",
    """    applyNetInfo(info);
    paintStatus(info);
    setupX402(info);""",
)
js.sub(
    """/* The Telegram Login Widget renders inline so signing in never leaves the""",
    """/* The v2 status matrix: three cards that say what this deployment runs, read
 * off the same /api/info payload the fields take their bounds from. Nothing
 * here is typed, because a promise the config does not keep is worse than a
 * blank. */
function paintStatus(info) {
  const cell = (badgeId, textId, live, onText, offText) => {
    const badge = $(badgeId);
    const text = $(textId);
    if (!badge || !text) return;
    badge.textContent = live ? "работает" : "не включено";
    badge.className = live ? "badge badge-yes" : "badge";
    text.textContent = live ? onText : offText;
  };
  cell("statusAiBadge", "statusAiText", info.ai_mode === "llm",
    "Отвечает модель из цепочки провайдеров; когда ни один не ответил, /ask отдаёт данные реестра.",
    "Модель не настроена — /ask отвечает данными реестра и говорит об этом прямо.");
  cell("statusOnchainBadge", "statusOnchainText", !!info.onchain_markets_enabled,
    "Контракт долей подключён: доли ERC-1155 и вывод на адрес существуют.",
    "Адрес контракта долей не задан — рынки живут в реестре, доли не выпускаются.");
  const x402 = info.x402 || {};
  cell("statusX402Badge", "statusX402Text", !!x402.enabled,
    "origin отвечает 402 и зачитывает платёж; границы счёта публикует /api/info.",
    "Счёт за один HTTP-запрос выключен: эндпоинт отвечает 404, а не 402.");}

/* The Telegram Login Widget renders inline so signing in never leaves the""",
)
js.sub(
    """    applyNetInfo(info);
    paintStatus(info);
    setupX402(info);""",
    """    applyNetInfo(info);
    paintStatus(info);
    paintTelemetryNetwork(info);
    setupX402(info);""",
)
js.sub(
    """    $("reserve-source").textContent = source;""",
    """    $("reserve-source").textContent = source;
    paintReserves(s, source);""",
)
js.sub(
    """/* The v2 status matrix: three cards that say what this deployment runs, read""",
    """/* Two cells of the telemetry strip come from /api/info, two from /api/solvency
 * — the same two reads the panels below already make, so the strip adds no
 * request and no number of its own. */
function paintTelemetryNetwork(info) {
  const name = $("telemetryChain");
  const id = $("telemetryChainId");
  if (name && info.chain_name) name.textContent = info.chain_name;
  if (id) id.textContent = info.chain_id == null ? "–" : "chain " + info.chain_id;
}

/* The reserves bar is the panel's own three figures laid end to end: what is
 * owed, the part of it still in flight, and what is left over. `pending` is one
 * of the six liability categories, not a fourth thing beside them, so it is cut
 * out of the owed segment instead of added to the total. */
function paintReserves(s, source) {
  const src = $("telemetrySource");
  if (src) src.textContent = source;
  const cov = $("telemetryCoverage");
  if (cov) cov.textContent = s.coverage_pct == null ? "нет данных" : fmtPct(s.coverage_pct);
  const bar = $("reserveBar");
  const key = $("reserveKey");
  if (!bar || !key) return;
  const owed = Number(s.liabilities_usdc) || 0;
  const inflight = Math.min(Number(s.pending_deposits_usdc) || 0, owed);
  const free = Math.max(Number(s.reserve_usdc) || 0, 0);
  const total = owed + free;
  if (total <= 0) return;
  $("segOwed").style.width = barWidth(((owed - inflight) / total) * 100) + "%";
  $("segPending").style.width = barWidth((inflight / total) * 100) + "%";
  $("segSurplus").style.width = barWidth((free / total) * 100) + "%";
  bar.hidden = false;
  key.hidden = false;
}

/* The v2 status matrix: three cards that say what this deployment runs, read""",
)
js.save()
