const $ = (id) => document.getElementById(id);
document.body.classList.add("js");

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- scroll progress ---------- */
const scrollProgress = $("scrollProgress");
if (scrollProgress) {
  window.addEventListener("scroll", () => {
    const scrolled = (window.scrollY / (document.documentElement.scrollHeight - window.innerHeight)) * 100;
    scrollProgress.style.width = scrolled + "%";
  }, { passive: true });
}

/* A painted number states its own precision: two decimals for money, one for
 * odds. `maximumFractionDigits` alone let 25 print as "25" beside 24.88 as
 * "24,88", so a column of amounts or odds had no shared width to align to and
 * the tabular figures underneath it bought nothing. */
const fmtUSDC = (x) =>
  (x ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

// Odds and rates are the only figures the page shows as text, so they take the
// page's locale: `String(24.88)` painted "24.88%" with a dot into a ru-RU
// document whose money is written "24,88".
const fmtPct = (x) =>
  (x ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 1, minimumFractionDigits: 1 }) + "%";
// Probability display may round, but configured fees must preserve their stated rate.
const fmtFeePct = (x) =>
  (x ?? 0).toLocaleString("ru-RU", { maximumSignificantDigits: 15 }) + "%";

/* ---------- reveal-on-scroll ---------- */
/* threshold 0.1 meant a section taller than ten viewports could never reach
 * 10% visibility and stayed transparent; any pixel entering the band above
 * the last 10% now triggers it. */
const io = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add("is-visible");
        io.unobserve(e.target);
      }
    }
  },
  { rootMargin: "0px 0px -10% 0px" }
);
document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

/* ---------- count-up for numbers ---------- */
function animateCount(el, target) {
  // requestAnimationFrame is suspended while the tab is hidden, so a page
  // loaded in the background would keep showing the "–" placeholder. Write the
  // real value straight away instead of waiting for a frame that will not come.
  if (REDUCED_MOTION || document.hidden) {
    el.textContent = fmtUSDC(target);
    return;
  }
  const integer = Number.isInteger(target);
  const dur = 900;
  const start = performance.now();
  function frame(now) {
    // rAF timestamps are the frame start and can precede `start`; unclamped,
    // the cubic ease overshoots far below zero.
    const p = Math.min(Math.max((now - start) / dur, 0), 1);
    const eased = 1 - Math.pow(1 - p, 3);
    const v = target * eased;
    el.textContent = fmtUSDC(integer ? Math.round(v) : v);
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function setCount(el, value) {
  if (!el) return;
  el.classList.remove("skeleton");
  // A failed /api/stats used to leave "NaN" behind: fmtUSDC only defaults
  // null/undefined, and NaN survives toLocaleString.
  if (!Number.isFinite(value)) { el.textContent = "–"; return; }
  animateCount(el, value);
}

async function loadInfo() {
  try {
    const r = await fetch("/api/info");
    const info = await r.json();
    const username = info.bot_username;
    const tgLink = username ? "https://t.me/" + encodeURIComponent(username) : null;
    if (tgLink) {
      document.querySelectorAll("[data-tg-link]").forEach((a) => {
        a.href = tgLink;
        a.target = "_blank";
        a.rel = "noopener";
      });
    }
    renderTelegramWidget(username);
    renderTerms(info);
    renderCommands(info);
    applyNetInfo(info);
    setupX402(info);
    /* The cap is configuration, not a property of one market, so the drawer can
     * state it and size its quick amounts before any quote has been asked. */
    if (info.limits && info.limits.max_trade_usdc != null) {
      quoteCap = info.limits.max_trade_usdc;
      renderQuoteChips();
    }
    if (info.limits && info.limits.min_trade_usdc != null) {
      applyQuoteFloor(info.limits.min_trade_usdc);
    }
  } catch (e) { /* keep original links */ }
}

/* The Telegram Login Widget renders inline so signing in never leaves the
 * workspace: the widget sends its signed fields to /api/auth/telegram, which
 * verifies the HMAC and sets the session cookie. The CSRF state that callback
 * demands is issued by GET / — see web/server.py. */
function renderTelegramWidget(username) {
  const box = $("tg-widget");
  if (!box || box.dataset.ready) return;
  box.dataset.ready = "1";
  if (!username) {
    box.innerHTML = '<div class="hint">Имя бота не настроено — вход через Telegram недоступен.</div>';
    return;
  }
  const s = document.createElement("script");
  s.async = true;
  s.src = "https://telegram.org/js/telegram-widget.js?22";
  s.setAttribute("data-telegram-login", username);
  s.setAttribute("data-size", "large");
  s.setAttribute("data-auth-url", "/api/auth/telegram");
  s.setAttribute("data-request-access", "write");
  /* Telegram writes its own <iframe> and names it nothing: a screen reader
   * reading the page reaches an unlabelled frame and a keyboard user cannot
   * tell what the box signs them up for, which axe scores as a serious
   * violation. The observer names the frame the moment the widget script
   * inserts it — a load handler would miss the case where the script is
   * inserted while the observer is already watching an empty box. */
  new MutationObserver(() => {
    box.querySelectorAll("iframe:not([title])").forEach((frame) => {
      frame.setAttribute("title", "Вход через Telegram");
    });
  }).observe(box, { childList: true, subtree: true });
  box.appendChild(s);
}

/* Command menu comes from bot/commands_catalog.py through /api/info, so it is
 * the list the bot actually installs with set_my_commands — not a written-out
 * copy that can drift away from the handlers.
 *
 * The catalogue is 48 rows; as one grid it reads as a wall. The `group` field
 * splits it into the seven things a visitor actually came to do. Commands with
 * no group (an older /api/info) land in «Прочее» rather than being dropped — a
 * missing label is a reason to show a command, not to hide it. */
const CMD_GROUPS = [
  ["wallet", "Кошелёк и сеть"],
  ["tips", "Чаевые и подписки"],
  ["markets", "Рынки на LMSR"],
  ["bets", "Ставки-пулы"],
  ["onchain", "Ончейн-доли Cally"],
  ["creator", "Создателям"],
  ["account", "Аккаунт и справки"],
];

function renderCommands(info) {
  const el = $("commands-grid");
  if (!el) return;
  const cmds = info.commands || [];
  if (!cmds.length) {
    el.innerHTML = '<div class="empty">Список команд сейчас недоступен</div>';
    return;
  }
  const cell = (c) => {
    const cmd = escapeHtml(c.command);
    return `<button class="cmd" type="button" data-cmd="${cmd}" title="Скопировать /${cmd}">`
      + `<b>/${cmd}</b><span>${escapeHtml(c.description)}</span></button>`;
  };
  const buckets = new Map(CMD_GROUPS.map(([key, title]) => [key, { title, rows: [] }]));
  const other = { title: "Прочее", rows: [] };
  for (const c of cmds) {
    (buckets.get(c.group) || other).rows.push(c);
  }
  el.innerHTML = [...buckets.values(), other]
    .filter((g) => g.rows.length)
    .map((g) => `<div class="cmd-group">`
      + `<h3 class="cmd-group-title">${escapeHtml(g.title)}</h3>`
      + `<div class="cmd-grid">${g.rows.map(cell).join("")}</div></div>`)
    .join("");
}

function setMsg(id, text, ok) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

/* Clipboard access is refused without warning — over http://, in a
 * cross-origin frame, or when the visitor declines — and a copy button that
 * silently does nothing is worse than one that says what to do instead. */
async function copyToClipboard(text, msgId, what) {
  try {
    await navigator.clipboard.writeText(text);
    setMsg(msgId, "Скопировано: " + text, true);
  } catch (err) {
    setMsg(
      msgId,
      "Браузер не дал доступ к буферу обмена — выделите " + what
      + " и скопируйте вручную.",
      false
    );
  }
}

/* Terms are read from the same endpoint the write handlers enforce against,
 * so the page cannot drift away from the configured limits. */
function renderTerms(info) {
  const el = $("terms-body");
  if (!el || !info.limits || !info.fees) return;
  const L = info.limits, F = info.fees;
  const rows = [
    ["Комиссия на вывод", fmtFeePct(F.withdraw_pct)],
    ["Комиссия с выигрыша", fmtFeePct(F.win_pct)],
    ["Максимум чаевых за раз", fmtUSDC(L.max_tip_usdc) + " USDC"],
    ["Максимум ставки за раз", fmtUSDC(L.max_bet_usdc) + " USDC"],
    ["Максимум сделки на рынке", fmtUSDC(L.max_trade_usdc) + " USDC"],
    ["Минимальный вывод", fmtUSDC(L.min_withdraw_usdc) + " USDC"],
    ["Выводов в сутки", L.max_withdraws_per_day],
    [
      "Субсидия ликвидности рынка",
      fmtUSDC(L.min_subsidy_usdc) + "–" + fmtUSDC(L.max_subsidy_usdc) + " USDC",
    ],
    ["Сеть", info.chain_name + " · chain id " + info.chain_id],
    ["Контракт USDC", info.usdc_address],
    ["Ончейн-рынки (Cally)", info.onchain_markets_enabled ? "включены" : "контракт не подключён"],
    ["Покупка долей без газа", info.smart_wallet_enabled ? "включена" : "не настроена"],
  ];
  el.innerHTML = rows
    .map((row) => {
      const value = /^0x[0-9a-fA-F]{40}$/.test(String(row[1]))
        ? '<span class="mono">' + escapeHtml(row[1]) + "</span>"
        : escapeHtml(String(row[1]));
      return "<tr><td>" + escapeHtml(row[0]) + "</td><td>" + value + "</td></tr>";
    })
    .join("");
}

const STAT_IDS = ["stat-volume", "stat-vol30", "stat-users", "stat-markets", "stat-tx", "stat-fees"];

async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    if (!r.ok) throw new Error("stats " + r.status);
    const s = await r.json();
    setCount($("stat-volume"), s.volume_usdc);
    setCount($("stat-vol30"), s.volume_30d_usdc);
    setCount($("stat-users"), s.users);
    setCount($("stat-markets"), s.open_markets);
    setCount($("stat-tx"), s.transactions);
    setCount($("stat-fees"), s.fees_usdc);
  } catch (e) {
    // Shimmering forever reads as a slow load, not a failed one. Settle every
    // card on the dash so the grid still makes sense when the API is down.
    STAT_IDS.forEach((id) => setCount($(id), null));
  }
}

function fmtDay(day) {
  const [y, m, d] = day.split("-");
  return d + "." + m;
}

async function loadVolumeChart() {
  try {
    const r = await fetch("/api/volume_history?days=" + chartDays);
    if (!r.ok) throw new Error("volume_history " + r.status);
    const days = await r.json();
    chartSeries = days;
    chartTotal = days.reduce((sum, d) => sum + d.volume_usdc, 0);
    const el = $("volume-chart");
    const max = Math.max(...days.map((d) => d.volume_usdc), 0);
    if (max <= 0) {
      chartSummaryTotal();
      el.innerHTML = '<div class="empty">Пока нет данных — объём появится после первых операций</div>';
      return;
    }
    // A zero day draws no bar: `.chart-bar` has a 2px min-height, so a bar
    // element would paint volume the ledger does not hold.
    // Past twenty columns the axis cannot fit a label per day; the label still
    // ships (visibility, not absence) so every bar keeps one baseline.
    const labelStep = days.length > 20 ? 5 : 1;
    el.innerHTML = days.map((d, i) => {
      const dayClass = i % labelStep === 0 ? "chart-day" : "chart-day chart-day-off";
      return `
      <div class="chart-col" data-day="${d.day}" data-volume="${d.volume_usdc}" title="${fmtDay(d.day)}: ${fmtUSDC(d.volume_usdc)} USDC">
        <div class="chart-plot">${d.volume_usdc > 0 ? `<div class="chart-bar" aria-hidden="true" style="height:${Math.max(d.volume_usdc / max * 100, 2)}%;animation-delay:${Math.min(i * 60, 600)}ms"></div>` : ""}</div>
        <div class="${dayClass}">${fmtDay(d.day)}</div>
      </div>`;
    }).join("");
    chartSummaryTotal();
  } catch (e) {
    chartSeries = [];
    chartTotal = 0;
    $("volume-chart").innerHTML = '<div class="empty">Не удалось загрузить график</div>';
    $("chart-summary").textContent = "Не удалось загрузить график.";
  }
}

/* The summary line is one element with two voices: the period's totals, and —
 * while the pointer is on a column — that day. A floating tooltip would cover
 * the plot the reader is comparing, and a native `title` cannot be styled or
 * read next to the numbers it explains. */
function chartSummaryTotal() {
  const el = $("chart-summary");
  if (!chartSeries.length || chartTotal <= 0) {
    el.textContent = "Наводите на столбцы, чтобы увидеть день и его объём.";
    return;
  }
  const peak = chartSeries.reduce((best, d) => (d.volume_usdc > best.volume_usdc ? d : best));
  el.innerHTML =
    `Всего <b>${fmtUSDC(chartTotal)} USDC</b> · в среднем за день <b>${fmtUSDC(chartTotal / chartSeries.length)}</b>` +
    ` · максимум <b>${fmtUSDC(peak.volume_usdc)}</b> ${fmtDay(peak.day)}`;
}

function chartSummaryDay(col) {
  const vol = Number(col.dataset.volume);
  const share = chartTotal > 0 ? (vol / chartTotal) * 100 : 0;
  $("chart-summary").innerHTML =
    `${fmtDay(col.dataset.day)}: <b>${fmtUSDC(vol)} USDC</b> · ${fmtPct(share)} объёма периода`;
}

$("chart-ranges").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-days]");
  if (!btn) return;
  const days = Number(btn.dataset.days);
  if (days === chartDays) return;
  chartDays = days;
  $("chart-title").textContent = "Последние " + days + " дней";
  for (const chip of $("chart-ranges").children) {
    const on = chip === btn;
    chip.classList.toggle("active", on);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
  }
  loadVolumeChart();
});

const volumeChartEl = $("volume-chart");
volumeChartEl.addEventListener("pointerover", (e) => {
  const col = e.target.closest(".chart-col");
  if (col) chartSummaryDay(col);
});
volumeChartEl.addEventListener("pointerleave", chartSummaryTotal);

async function loadWallet() {
  try {
    const r = await fetch("/api/wallet");
    const w = await r.json();
    $("wallet-address").textContent = w.address;
    $("wallet-balance").textContent = w.balance_usdc === null || w.balance_usdc === undefined
      ? "RPC недоступен"
      : fmtUSDC(w.balance_usdc) + " USDC";
  } catch (e) { /* keep placeholders */ }

  try {
    const r = await fetch("/api/solvency");
    const s = await r.json();
    $("wallet-liabilities").textContent = fmtUSDC(s.liabilities_usdc) + " USDC";
    $("wallet-pending").textContent = fmtUSDC(s.pending_deposits_usdc) + " USDC";
    $("wallet-reserve").textContent =
      s.reserve_usdc === null || s.reserve_usdc === undefined
        ? "RPC недоступен"
        : fmtUSDC(s.reserve_usdc) + " USDC";
    const source =
      s.reserves_source === "vault" ? "контракт TipBotVault" : "горячий кошелёк";
    $("reserve-source").textContent = source;
    const chip = $("reserve-chip");
    if (chip) {
      $("reserve-chip-text").textContent = "Резервы: " + source;
      chip.hidden = false;
    }
    const solvent = $("wallet-solvent");
    solvent.textContent =
      s.solvent === true ? "покрыто"
      : s.solvent === false ? "недостаточно"
      : "RPC недоступен";
    solvent.className = s.solvent === true ? "pos" : s.solvent === false ? "neg" : "";
    if (s.vault_address) {
      $("wallet-vault-row").hidden = false;
      $("wallet-vault-addr").hidden = false;
      $("wallet-vault").textContent =
        s.vault_balance_usdc === null || s.vault_balance_usdc === undefined
          ? "RPC недоступен"
          : fmtUSDC(s.vault_balance_usdc) + " USDC";
      $("wallet-vault-addr").textContent = "Vault: " + s.vault_address;
    }
    paintSolvency(s);
    paintExplorerLinks();
  } catch (e) { /* keep placeholders */ }
}

/* The ring draws the two figures the table beside it prints — what the address
 * actually holds against all liabilities — so a glance and the exact row can
 * never disagree. The coverage is a fraction of the *held* balance: `reserve_usdc`
 * is the free reserve, the difference between held and owed, and a difference is
 * not a fraction. Dividing by it made an insolvent bot paint a 95% full ring and
 * print «-94,8%» — a negative percentage that wraps the dash pattern back around.
 * `reserve_usdc` stays the no-data signal, because the server nulls it exactly
 * when the chain did not answer. */
const GAUGE_RADIUS = 52;

function paintSolvency(s) {
  const arc = $("gaugeArc");
  const ring = $("solvencyGauge");
  const circumference = 2 * Math.PI * GAUGE_RADIUS;
  arc.style.strokeDasharray = circumference + " " + circumference;
  const free = s.reserve_usdc;
  const owed = s.liabilities_usdc;
  const held = s.reserves_source === "vault" ? s.vault_balance_usdc : s.hot_wallet_balance_usdc;
  if (free === null || free === undefined) {
    arc.style.strokeDashoffset = circumference;
    arc.classList.remove("gauge-value-low");
    $("gaugePct").textContent = "–";
    $("gaugeCap").textContent = "нет данных";
    $("gaugeDetail").textContent = "Баланс кошелька не прочитан — покрытие посчитать нельзя.";
    ring.setAttribute("aria-label", "Покрытие резервами неизвестно: RPC не ответил.");
    return;
  }
  const covered = owed > 0 ? Math.max(0, Math.min(held / owed, 1)) : 1;
  arc.style.strokeDashoffset = circumference * (1 - covered);
  arc.classList.toggle("gauge-value-low", s.solvent === false);
  $("gaugePct").textContent = fmtPct(covered * 100);
  $("gaugeCap").textContent = "покрытие";
  $("gaugeDetail").innerHTML = owed > 0
    ? "На адресе <b>" + fmtUSDC(held) + "</b> USDC при <b>" + fmtUSDC(owed)
      + "</b> USDC обязательств — "
      + (s.solvent === false
        ? "не хватает " + fmtUSDC(Math.abs(free)) + " USDC."
        : "обязательства покрыты.")
    : "На адресе <b>" + fmtUSDC(held) + "</b> USDC, обязательств у бота сейчас нет.";
  ring.setAttribute(
    "aria-label",
    "Резервы покрывают " + fmtPct(covered * 100) + " обязательств: "
    + fmtUSDC(held) + " из " + fmtUSDC(owed) + " USDC."
  );
}

/* The explorer base comes from /api/info like every other network string, and
 * the link stays hidden until it names a real 0x… address: on a page whose
 * balance call failed there is nothing to open. */
/* A control that would link or copy the placeholder text has to know the
 * difference: the balance call can fail and leave "загрузка…" on screen. */
const isAddress = (a) => /^0x[0-9a-fA-F]{40}$/.test(a);

function paintExplorerLinks() {
  const hot = $("wallet-address").textContent.trim();
  const hotLink = $("walletExplorer");
  if (NET.explorer && isAddress(hot)) {
    hotLink.href = NET.explorer + "/address/" + hot;
    hotLink.hidden = false;
  } else {
    hotLink.hidden = true;
  }
  const vault = $("wallet-vault-addr").textContent.replace(/^Vault:\s*/, "").trim();
  const vaultLink = $("vaultExplorer");
  if (NET.explorer && isAddress(vault)) {
    vaultLink.href = NET.explorer + "/address/" + vault;
    vaultLink.hidden = false;
  } else {
    vaultLink.hidden = true;
  }
}

$("copyWalletAddress").addEventListener("click", () => {
  const addr = $("wallet-address").textContent.trim();
  if (!isAddress(addr)) {
    setMsg("walletMsg", "Адрес ещё не прочитан — обновите страницу.", false);
    return;
  }
  copyToClipboard(addr, "walletMsg", "адрес");
});

/* One literal per state `marketState` can answer with. A lookup rather than an
 * `if`-chain with a fallthrough: an unmapped state paints nothing and the gate
 * that walks the states refuses, instead of quietly calling it «отменён». */
const CHIP = {
  open: '<span class="chip chip-open">открыт</span>',
  awaiting: '<span class="chip chip-awaiting">ждёт решения</span>',
  resolved: '<span class="chip chip-resolved">завершён</span>',
  cancelled: '<span class="chip chip-cancelled">отменён</span>',
};

function statusBadge(m) {
  return CHIP[marketState(m)] || "";
}

/* Cards carry an optional deadline and a status chip, and either can be empty.
 * Joining them with a literal " · " left a dangling separator in the meta line. */
function metaLine(...parts) {
  return parts.filter(Boolean).join(" · ");
}

/* The text beside the chip. Once the deadline has passed the countdown stops
 * speaking (chain.js `deadlineLine`) and the chip names the state; a surface
 * whose money the ledger can still hand back passes its own note, and the
 * on-chain cards pass none — there the contract decides, and the page must not
 * promise a refund it cannot perform. */
function deadlineNote(m, note) {
  return marketState(m) === "awaiting" ? (note || "") : deadlineLine(m);
}

/* The markup for the deadline half of a meta line: a ticking span only while
 * there is time left, plain text for what a finished clock leaves behind. */
function deadlineHtml(m, note) {
  const text = deadlineNote(m, note);
  if (!text) return "";
  return marketState(m) === "open"
    ? '<span data-close-at="' + parseInt(m.close_at) + '" data-close-note="' + (note || "") + '">'
      + text + "</span>"
    : text;
}

/* The chip's word without the chip: a ⌘K row has no room for a pill, and an
 * empty column is what the countdown leaves once the clock has run out. Cut
 * from the same literal, so a surface can never name a state differently. */
function statusWord(m) {
  return (CHIP[marketState(m)] || "").replace(/<[^>]*>/g, "");
}

function marketCard(m, i) {
  const deadline = deadlineHtml(m, REFUND_NOTE);
  const winnerIdx = m.status === "resolved" && m.winner !== null && m.winner !== undefined ? m.winner : null;
  const options = m.options.map((o, j) => {
    const isWinner = winnerIdx !== null && o.index === winnerIdx;
    return `
    <div class="option${isWinner ? " option-winner" : ""}">
      <div class="option-top">
        <span class="option-label">${escapeHtml(o.label)}</span>
        <span class="option-val">${fmtUSDC(o.pool_usdc)}&nbsp;USDC · ${fmtPct(o.probability)} · ${escapeHtml(String(o.backers))}&nbsp;чел.</span>
      </div>
      <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" aria-hidden="true" style="width:${Math.max(o.probability, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
    </div>`;
  }).join("");

  return `
    <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
      <div class="market-head">
        <span class="market-question">#${m.id} ${escapeHtml(m.question)}</span>
        <span class="market-meta">${metaLine(deadline, statusBadge(m))}</span>
      </div>
      ${options}
      <div class="market-footer">
        <span class="pot">Пул: <b>${fmtUSDC(m.pot_usdc)} USDC</b> · ${escapeHtml(String(m.total_backers))}&nbsp;чел.</span>
        <span class="pot">
          <a class="m-link" href="/m/${m.id}">Подробнее →</a>
          <span class="market-author">@${escapeHtml(m.creator.username || ("id" + m.creator.id))}</span>
        </span>
      </div>
    </div>`;
}

async function loadMarkets() {
  try {
    const r = await fetch("/api/markets");
    const markets = await r.json();
    searchIndex.pool = markets;
    const el = $("markets-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Открытых рынков пока нет — создай первый в боте: /bet create</div>';
      return;
    }
    el.innerHTML = markets.map(marketCard).join("");
  } catch (e) {
    searchIndex.pool = [];
    $("markets-list").innerHTML = '<div class="empty">Не удалось загрузить рынки</div>';
  }
}

async function loadClosedMarkets() {
  try {
    const r = await fetch("/api/markets?status=resolved");
    const markets = await r.json();
    searchIndex.closed = markets;
    const el = $("closed-markets-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Закрытых рынков пока нет</div>';
      return;
    }
    el.innerHTML = markets.map(marketCard).join("");
  } catch (e) {
    searchIndex.closed = [];
    $("closed-markets-list").innerHTML = '<div class="empty">Не удалось загрузить</div>';
  }
}

function predictionCard(m, i) {
  const winner = m.status === "resolved" && m.winner != null ? m.winner : null;
  const deadline = deadlineHtml(m, REFUND_NOTE);
  const options = m.options.map((o, j) => {
    const isWinner = winner !== null && o.index === winner;
    return `
    <div class="option${isWinner ? " option-winner" : ""}">
      <div class="option-top">
        <span class="option-label">${escapeHtml(o.label)}</span>
        <span class="option-val">${fmtPct(o.price_pct)}</span>
      </div>
      <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" aria-hidden="true" style="width:${Math.max(o.price_pct, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
    </div>`;
  }).join("");

  return `
    <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
      <div class="market-head">
        <span class="market-question">#${m.id} ${escapeHtml(m.question)}</span>
        <span class="market-meta">${metaLine(deadline, statusBadge(m))}</span>
      </div>
      ${options}
      <div class="market-footer">
        <span class="pot">Ликвидность: <b>${fmtUSDC(m.liquidity_usdc)} USDC</b> · оборот долей <b>${fmtUSDC(m.volume_usdc)} USDC</b> · ${escapeHtml(String(m.traders))}&nbsp;чел.</span>
        <button class="btn btn-ghost btn-sm" type="button" data-quote="${m.id}">Котировка</button>
      </div>
    </div>`;
}

/* The hero needs one live number rather than a slogan: the deepest market and
 * its real odds. /api/predictions already answers for the section below, so this
 * costs no extra request — it is the same rows, re-picked by liquidity, because
 * the endpoint answers by recency. */
function renderHeroSpot(markets) {
  const box = $("heroSpot");
  const body = $("hero-spot-body");
  if (!box || !body) return;
  const top = markets.reduce((best, m) => (m.liquidity_usdc > best.liquidity_usdc ? m : best), markets[0]);
  if (!top) { box.hidden = true; return; }
  body.innerHTML = `
    <div class="spot-question">#${top.id} ${escapeHtml(top.question)}</div>
    <div class="spot-meta">${metaLine(deadlineHtml(top, REFUND_NOTE), statusBadge(top))}</div>
    ${top.options.slice(0, 3).map(o => `
      <div class="option">
        <div class="option-top">
          <span class="option-label">${escapeHtml(o.label)}</span>
          <span class="option-val">${fmtPct(o.price_pct)}</span>
        </div>
        <div class="bar"><div class="bar-fill" aria-hidden="true" style="width:${Math.max(o.price_pct, 2)}%"></div></div>
      </div>`).join("")}
    <div class="spot-foot">
      <span class="pot">Ликвидность: <b>${fmtUSDC(top.liquidity_usdc)} USDC</b> · ${escapeHtml(String(top.traders))}&nbsp;чел.</span>
      <a class="m-link" href="#predictions">Все рынки →</a>
    </div>`;
  box.hidden = false;
}

async function loadPredictions() {
  try {
    const r = await fetch("/api/predictions");
    const markets = await r.json();
    predictions = markets;
    searchIndex.lmsr = markets;
    const el = $("predictions-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Рынков с открытым торгом пока нет</div>';
      return;
    }
    el.innerHTML = markets.map(predictionCard).join("");
    renderHeroSpot(markets);
  } catch (e) {
    searchIndex.lmsr = [];
    $("predictions-list").innerHTML = '<div class="empty">Не удалось загрузить рынки</div>';
  }
}

/* ---------- LMSR quote drawer ---------- */

/* Every number here is the server's own: `/api/prediction/{id}/quote` runs the
 * same 40-digit LMSR and the same floor-to-micro-share search that `buy_shares`
 * books with, so the panel cannot promise a share count the ledger will not
 * hand out. Nothing is reserved — this is a price, not an order. */
let predictions = [];
let quoteMarket = null;
let quoteOption = 0;
let quoteSeq = 0;          // a slow answer must not overwrite a newer one
let quoteTimer = null;
let quoteCap = null;
let quoteFloor = null;
const quoteDrawer = $("quoteDrawer");

/* The drawer is where a trade is decided, so it keeps six decimals where the
 * dash rounds to two: `fmtUSDC` would print a cost of 9.999999 as 10 and the
 * reader would conclude the tail was debited too. */
const fmtMicro = (x) =>
  (x ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 6, minimumFractionDigits: 0 });

function quoteRowsEmpty() {
  for (const id of ["quoteShares", "quoteAvg", "quoteCost", "quotePrice", "quotePayout", "quoteRoi"]) {
    const el = $(id);
    el.textContent = "–";
    el.className = "row-v mono";
  }
}

function quoteMsgClear() {
  const el = $("quoteMsg");
  el.className = "msg";
  el.textContent = "";
}

function renderQuoteChips() {
  $("quoteChips").innerHTML = [1, 5, 10, 50, 100]
    .filter((v) => quoteCap === null || v <= quoteCap)
    .map((v) => `<button class="amount-chip" type="button" data-amount="${v}">${v} USDC</button>`)
    .join("");
  $("quoteLimit").textContent = quoteCap === null || quoteFloor === null
    ? ""
    : "От " + fmtUSDC(quoteFloor) + " до " + fmtUSDC(quoteCap) + " USDC за одну сделку.";
}

/* Both bounds are the server's. The drawer used to type the lower one by hand
 * next to a number it had just fetched, so a change to the ledger's dust floor
 * left the sentence promising a trade the quote endpoint then refused. */
function applyQuoteFloor(usdc) {
  if (usdc == null || usdc === quoteFloor) return;
  quoteFloor = usdc;
  $("quoteAmount").min = usdc;
  renderQuoteChips();
}

async function requestQuote() {
  if (!quoteMarket) return;
  const seq = ++quoteSeq;
  const amount = $("quoteAmount").value.trim();
  if (!amount) {
    quoteRowsEmpty();
    quoteMsgClear();
    return;
  }
  const url = "/api/prediction/" + quoteMarket.id + "/quote"
    + "?option=" + quoteOption + "&amount=" + encodeURIComponent(amount);
  let q = null;
  let refusal = "";
  try {
    const r = await fetch(url);
    if (r.ok) q = await r.json();
    else refusal = (await r.json()).detail || "Котировка недоступна.";
  } catch (e) {
    refusal = "Сервер не ответил — котировки нет.";
  }
  if (seq !== quoteSeq) return;
  if (!q) {
    quoteRowsEmpty();
    setMsg("quoteMsg", refusal, false);
    return;
  }
  if (q.max_trade_usdc !== quoteCap) {
    quoteCap = q.max_trade_usdc;
    renderQuoteChips();
  }
  applyQuoteFloor(q.min_trade_usdc);
  $("quoteShares").textContent = fmtMicro(q.shares_usdc) + " шт.";
  $("quoteAvg").textContent = fmtMicro(q.avg_price_usdc) + " USDC";
  $("quoteCost").textContent = fmtMicro(q.cost_usdc) + " USDC";
  $("quotePrice").textContent = fmtPct(q.price_before_pct) + " → " + fmtPct(q.price_after_pct);
  $("quotePayout").textContent = fmtMicro(q.payout_usdc) + " USDC";
  const roi = $("quoteRoi");
  roi.textContent = sign(q.profit_usdc) + fmtMicro(q.profit_usdc) + " USDC · "
    + sign(q.roi_pct) + fmtPct(q.roi_pct);
  roi.className = "row-v mono " + (q.profit_usdc >= 0 ? "pos" : "neg");
  quoteMsgClear();
}

/* A number that can only be a gain or a loss reads ambiguous as "0,04": the
 * drawer is a comparison, so the sign is part of the figure. */
const sign = (x) => (x >= 0 ? "+" : "");

function quoteRequestedSoon() {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(requestQuote, 250);
}

function paintQuoteOptions() {
  $("quoteOptions").innerHTML = quoteMarket.options.map((o) => {
    const on = o.index === quoteOption;
    return `
    <button class="${on ? "opt-btn selected" : "opt-btn"}" type="button"
            data-option="${o.index}" aria-pressed="${on}">
      <span class="opt-label">${escapeHtml(o.label)}</span>
      <span class="opt-price">${fmtPct(o.price_pct)}</span>
    </button>`;
  }).join("");
}

function openQuote(marketId) {
  const m = predictions.find((x) => x.id === marketId);
  if (!m) return;
  quoteMarket = m;
  quoteOption = m.options.length ? m.options[0].index : 0;
  $("quoteQuestion").textContent = "#" + m.id + " " + m.question;
  paintQuoteOptions();
  $("quoteAmount").value = "10";
  quoteMsgClear();
  $("quoteTrade").textContent = $("workspace").hidden
    ? "Войти, чтобы торговать"
    : "Перенести в терминал";
  quoteDrawer.showModal();
  requestQuote();
}

function closeQuote() {
  quoteDrawer.close();
  quoteMarket = null;
  clearTimeout(quoteTimer);
  ++quoteSeq;
}

quoteDrawer.addEventListener("click", (e) => {
  // The dialog is the full-viewport layer, so a click that reaches it rather
  // than the panel is the pointer missing the card — the backdrop gesture.
  if (e.target === quoteDrawer) closeQuote();
});
$("quoteClose").addEventListener("click", closeQuote);
$("quoteAmount").addEventListener("input", quoteRequestedSoon);
$("quoteChips").addEventListener("click", (e) => {
  const chip = e.target.closest("button[data-amount]");
  if (!chip) return;
  $("quoteAmount").value = chip.dataset.amount;
  requestQuote();
});
$("quoteOptions").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-option]");
  if (!btn) return;
  quoteOption = Number(btn.dataset.option);
  paintQuoteOptions();
  requestQuote();
});
$("predictions-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-quote]");
  if (btn) openQuote(Number(btn.dataset.quote));
});

/* Handing the same intent to the workspace instead of asking for it twice:
 * tab, market and outcome are clicked in the order a person would click them,
 * so `site.js` stays the only writer of a pending trade and the drawer never
 * grows a second money path. */
$("quoteTrade").addEventListener("click", () => {
  if (!$("workspace").hidden) {
    handToTerminal();
    return;
  }
  closeQuote();
  $("workspace-section").scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
});

function handToTerminal() {
  const select = $("tradeMarket");
  const tab = document.querySelector('.tab[data-tab="markets"]');
  if (!select || !tab) return;
  const id = String(quoteMarket.id);
  select.value = id;
  if (select.value !== id) {
    setMsg("quoteMsg", "Этого рынка уже нет в списке — обновите страницу.", false);
    return;
  }
  tab.click();
  select.dispatchEvent(new Event("change"));
  const option = document.querySelector('#tradeOptions .opt-btn[data-option="' + quoteOption + '"]');
  if (option) option.click();
  $("tradeAmount").value = $("quoteAmount").value;
  closeQuote();
}

/* ---------- x402 handshake ---------- */
/* The page signs nothing and pays nothing. It asks this same server for a real
 * invoice — the numbers on screen are the ones an agent would be held to — and
 * prints the two headers that answer it. An invoice is a DB row bound to
 * (recipient, amount), so the only request is the one behind the button; the
 * page never asks for a invoice on its own. */
let x402Invoice = null;

const HTTP_REASON = {
  200: "OK", 400: "Bad Request", 402: "Payment Required",
  404: "Not Found", 409: "Conflict", 500: "Internal Server Error",
  502: "Bad Gateway", 503: "Service Unavailable",
};

const X402_MEANING = {
  400: "Запрос непонятен: получатель должен существовать, а сумма — попасть в пределы ниже.",
  404: "Бот не нашёл такого получателя — счёт не выдаётся, платить не за что.",
  409: "Такая оплата уже прошла: счёт не оплачивают повторно.",
  503: "Приём x402 на этом сайте выключен, поэтому счёта и не будет.",
};

function x402Field(id) {
  return $(id).value.trim();
}

function x402Target() {
  const recipient = x402Field("x402Recipient");
  const amount = x402Field("x402Amount");
  return "/api/x402/tip?recipient=" + encodeURIComponent(recipient || "@username")
    + "&amount=" + encodeURIComponent(amount || "0.10");
}

function x402Curl(path) {
  return "curl -i -X POST '" + location.origin + x402Target() + "'" + (path ? " \\\n  -H '" + path + "'" : "");
}

/* The envelope is what goes into X-PAYMENT base64-encoded. Before an invoice
 * exists its fields are named placeholders; after one, they are the invoice's
 * own values, so a developer can read the amount, the payTo and the network
 * off the page instead of decoding the header by hand. */
function x402Envelope() {
  const a = x402Invoice && x402Invoice.accept;
  return [
    "{",
    '  "x402Version": ' + (x402Invoice ? x402Invoice.body.x402Version : 1) + ",",
    '  "scheme": "' + (a ? a.scheme : "exact") + '",',
    '  "networkId": "' + (a ? a.networkId : "base") + '",',
    '  "payload": {',
    '    "authorization": {',
    '      "from": "<адрес плательщика USDC>",',
    '      "to": "' + (a ? a.payTo : "<payTo из счёта>") + '",',
    '      "value": ' + (a ? a.maxAmountRequired : "<микро-USDC из счёта>") + ",",
    '      "validAfter": 0,',
    '      "validBefore": "<unix-время now + ' + (a ? a.maxTimeoutSeconds : 60) + ' с>",',
    '      "nonce": "<32 байта>"',
    "    },",
    '    "signature": "<0x… подпись EIP-3009>"',
    "  }",
    "}",
  ].join("\n");
}

function renderX402Curl() {
  $("x402CurlAsk").textContent = x402Curl("");
  $("x402CurlSpec").textContent = x402Curl("X-PAYMENT: <base64 объекта ниже>")
    + "\n\n" + x402Envelope();
  $("x402CurlLegacy").textContent = x402Curl(
    "x-402-payment: 0x<хэш перевода USDC на адрес счёта>"
  );
}

function hsRow(k, v) {
  return '<div class="row"><span class="row-k">' + k + '</span>'
    + '<span class="row-v">' + v + "</span></div>";
}

function paintX402Answer(status, body) {
  const el = $("x402Answer");
  const links = $("x402AnswerLinks");
  const rows = [hsRow("Ответ", status + " " + (HTTP_REASON[status] || ""))];
  const a = status === 402 && Array.isArray(body.accepts) ? body.accepts[0] : null;
  x402Invoice = a ? { body, accept: a } : null;
  if (a) {
    rows.push(hsRow("Схема", "exact · x402 v" + escapeHtml(body.x402Version)
      + " · " + escapeHtml(a.networkId)));
    rows.push(hsRow("К оплате", fmtMicroUSDC(a.maxAmountRequired) + " USDC"));
    rows.push(hsRow("Адрес счёта", escapeHtml(a.payTo)));
    rows.push(hsRow("Контракт USDC", escapeHtml(a.asset)));
    rows.push(hsRow("Окно подписи", a.maxTimeoutSeconds + " с"));
    if (body.expires_in_seconds) {
      rows.push(hsRow("Счёт живёт", body.expires_in_seconds + " с"));
    }
    if (body["x-402-invoice-id"]) {
      rows.push(hsRow("id счёта", escapeHtml(body["x-402-invoice-id"])));
    }
    rows.push(hsRow("Ресурс", escapeHtml(a.resource)));
    links.hidden = false;
  } else {
    links.hidden = true;
    if (body.detail) {
      rows.push(hsRow("Сообщение сервера", escapeHtml(body.detail)));
    }
  }
  el.innerHTML = rows.join("");
}

function fmtMicroUSDC(micro) {
  return fmtUSDC(Number(micro) / 1e6);
}

function setupX402(info) {
  const cfg = info.x402;
  if (!cfg) return;
  const amount = $("x402Amount");
  amount.min = cfg.min_usdc;
  amount.max = cfg.max_usdc;
  amount.step = "0.01";
  $("x402Bounds").textContent = "Этот сайт выставляет счета от "
    + fmtUSDC(cfg.min_usdc) + " до " + fmtUSDC(cfg.max_usdc) + " USDC, счёт живёт "
    + cfg.invoice_ttl_seconds + " с."
    + (cfg.enabled ? "" : " Приём x402 здесь выключен, поэтому запрос ответит 503.");
  const chips = [cfg.min_usdc, 1, cfg.max_usdc].filter((v, i, all) =>
    v >= cfg.min_usdc && v <= cfg.max_usdc && all.indexOf(v) === i);
  $("x402Amounts").innerHTML = chips.map((v) =>
    '<button class="amount-chip" type="button" data-amt="' + v + '">'
    + fmtUSDC(v) + " USDC</button>").join("");
}

$("x402Amounts").addEventListener("click", (e) => {
  const chip = e.target.closest(".amount-chip");
  if (!chip) return;
  $("x402Amount").value = chip.dataset.amt;
  renderX402Curl();
});

for (const id of ["x402Recipient", "x402Amount"]) {
  $(id).addEventListener("input", renderX402Curl);
}

$("x402Ask").addEventListener("click", async () => {
  const recipient = x402Field("x402Recipient");
  const amount = x402Field("x402Amount");
  if (!recipient || !amount) {
    setMsg("x402Msg", "Нужны оба поля: счёт привязан к паре (получатель, сумма).", false);
    return;
  }
  const btn = $("x402Ask");
  btn.disabled = true;
  try {
    const r = await fetch("/api/x402/tip?recipient=" + encodeURIComponent(recipient)
      + "&amount=" + encodeURIComponent(amount), { method: "POST" });
    const body = await r.json().catch(() => ({}));
    paintX402Answer(r.status, body);
    renderX402Curl();
    const meaning = r.status === 402
      ? "Счёт выдан. Теперь агент подписывает перевод на адрес счёта и повторяет запрос."
      : (X402_MEANING[r.status] || "Сервер ответил кодом " + r.status + ".");
    setMsg("x402Msg", meaning, r.status === 402);
  } catch (e) {
    $("x402Answer").innerHTML = '<div class="empty">Запрос не дошёл до сервера.</div>';
    setMsg("x402Msg", "Соединение не прошло: " + e.message, false);
  } finally {
    btn.disabled = false;
  }
});

$("x402CopyPay").addEventListener("click", () => {
  const a = x402Invoice && x402Invoice.accept;
  if (!a) { setMsg("x402Msg", "Сначала запросите счёт — адрес появляется из ответа.", false); return; }
  copyToClipboard(a.payTo, "x402Msg", "адрес счёта");
});

for (const [btnId, preId] of [["x402CopyAsk", "x402CurlAsk"],
  ["x402CopySpec", "x402CurlSpec"], ["x402CopyLegacy", "x402CurlLegacy"]]) {
  $(btnId).addEventListener("click", () => {
    copyToClipboard($(preId).textContent, "x402Msg", "команду");
  });
}

renderX402Curl();

/* ---------- market search ---------- */

/* One index over the four public lists the page already loads. Search fetches
 * nothing: a palette that went to the server would put a rate limit between a
 * visitor and a market that is already on screen. A row keeps the list it came
 * from and its position there, because these cards carry no per-market URL on
 * the landing — the answer to a search is the card, not another page. */
const SEARCH_KINDS = {
  lmsr: { label: "LMSR", list: "predictions-list", section: "predictions" },
  pool: { label: "Пул", list: "markets-list", section: "markets" },
  closed: { label: "Закрыт", list: "closed-markets-list", section: "closed-markets" },
  onchain: { label: "Ончейн", list: "onchain-markets-list", section: "onchain-markets" },
};
const searchIndex = { lmsr: [], pool: [], closed: [], onchain: [] };

/* Each list prints one figure on its own card, and the row prints that same
 * figure rather than a second number to compare: the pool lists show the escrow
 * or the liquidity they hold, the chain lists show the clock they run on. */
const SEARCH_META = {
  lmsr: (m) => fmtUSDC(m.liquidity_usdc) + " USDC",
  pool: (m) => fmtUSDC(m.pot_usdc) + " USDC",
  closed: (m) => fmtUSDC(m.pot_usdc) + " USDC",
  onchain: (m) => deadlineNote(m) || statusWord(m),
};

const SEARCH_LIMIT = 40;
const searchDialog = $("searchDialog");
let searchKind = "";

function searchRows() {
  const needle = $("searchInput").value.trim().toLowerCase();
  const rows = [];
  for (const kind of Object.keys(SEARCH_KINDS)) {
    if (searchKind && kind !== searchKind) continue;
    searchIndex[kind].forEach((m, idx) => {
      const hay = ("#" + m.id + " " + m.question).toLowerCase();
      let rank = 0;
      if (needle) {
        const at = hay.indexOf(needle);
        if (at < 0) return;
        rank = needle === "#" + m.id ? -1 : at;
      }
      rows.push({ kind, idx, m, rank });
    });
  }
  /* The sort is stable, so equal ranks keep the order the lists arrived in —
   * which is why an empty query reads as a browse of what is on the page. */
  return rows.sort((a, b) => a.rank - b.rank);
}

function paintSearchKinds() {
  $("searchKinds").innerHTML = [["", "Все"]]
    .concat(Object.keys(SEARCH_KINDS).map((k) => [k, SEARCH_KINDS[k].label]))
    .map(([id, label]) => {
      const on = id === searchKind;
      return `<button class="amount-chip${on ? " active" : ""}" type="button"
                      data-kind="${id}" aria-pressed="${on}">${label}</button>`;
    }).join("");
}

function paintSearch() {
  const query = $("searchInput").value.trim();
  const rows = searchRows();
  const shown = rows.slice(0, SEARCH_LIMIT);
  $("searchResults").innerHTML = shown.length
    ? shown.map((r) => `
      <button class="pal-row" type="button" data-kind="${r.kind}" data-idx="${r.idx}">
        <span class="pal-q">#${r.m.id} ${escapeHtml(r.m.question)}</span>
        <span class="pal-meta">${escapeHtml(SEARCH_META[r.kind](r.m))}</span>
        <span class="pal-kind">${SEARCH_KINDS[r.kind].label}</span>
      </button>`).join("")
    : '<div class="empty">Ничего не найдено</div>';
  const foot = $("searchFoot");
  if (!rows.length) {
    foot.textContent = query ? "Ничего не найдено." : "Открытых рынков на странице пока нет.";
    return;
  }
  foot.textContent = (query ? "Совпадений: " : "На странице: ") + rows.length
    + (rows.length > shown.length ? " — показано " + shown.length + ", уточните запрос."
      : ". Стрелки — выбор, Enter — к карточке.");
}

function gotoMarket(kind, idx) {
  const where = SEARCH_KINDS[kind];
  /* The index is a position in the same array the list was rendered from, so a
   * refresh between opening the panel and clicking can move the card. Falling
   * back to the section is the honest answer to that: closer, not nowhere. */
  const card = $(where.list).querySelectorAll(".market-card")[idx] || $(where.section);
  searchDialog.close();
  card.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "center" });
  if (card !== $(where.section)) {
    card.classList.add("is-found");
    setTimeout(() => card.classList.remove("is-found"), 1400);
  }
}

function openSearch() {
  paintSearchKinds();
  paintSearch();
  searchDialog.showModal();
  const input = $("searchInput");
  input.focus();
  input.select();
}

$("searchOpen").addEventListener("click", openSearch);
$("searchClose").addEventListener("click", () => searchDialog.close());
searchDialog.addEventListener("click", (e) => {
  // The dialog is the full-viewport layer, so a click that reaches it rather
  // than the panel is the pointer missing the card — the backdrop gesture.
  if (e.target === searchDialog) searchDialog.close();
});
$("searchInput").addEventListener("input", paintSearch);
$("searchKinds").addEventListener("click", (e) => {
  const chip = e.target.closest("button[data-kind]");
  if (!chip) return;
  searchKind = chip.dataset.kind;
  paintSearchKinds();
  paintSearch();
  $("searchInput").focus();
});
$("searchResults").addEventListener("click", (e) => {
  const row = e.target.closest(".pal-row");
  if (row) gotoMarket(row.dataset.kind, Number(row.dataset.idx));
});
searchDialog.addEventListener("keydown", (e) => {
  /* A search field takes the first Escape for itself and clears its text, so
   * the palette used to stay on screen until a second press — and the reader
   * had no way to know the box, not the panel, was the reason. */
  if (e.key === "Escape") {
    searchDialog.close();
    return;
  }
  const rows = Array.prototype.slice.call($("searchResults").querySelectorAll(".pal-row"));
  if (!rows.length) return;
  const at = rows.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const last = rows.length - 1;
    rows[e.key === "ArrowDown"
      ? (at < 0 ? 0 : (at + 1) % rows.length)
      : (at < 0 ? last : (at - 1 + rows.length) % rows.length)].focus();
  } else if (e.key === "Enter" && document.activeElement === $("searchInput")) {
    e.preventDefault();
    rows[0].click();
  }
});
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.code !== "KeyK") return;
  e.preventDefault();
  if (searchDialog.open) searchDialog.close(); else openSearch();
});

/* The hint has to name the key the visitor actually presses; a Mac keyboard
 * has no Ctrl to point at. */
$("searchKey").textContent =
  /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent || "") ? "⌘K" : "Ctrl K";

async function loadLeaderboard() {
  try {
    const r = await fetch("/api/leaderboard");
    const rows = await r.json();
    const el = $("leaderboard");
    if (!rows.length) {
      el.innerHTML = '<div class="empty">Пока пусто</div>';
      return;
    }
    el.innerHTML = rows.map((row, i) => `
      <div class="lb-row" style="animation-delay:${Math.min(i * 60, 400)}ms">
        <span class="lb-place">${String(i + 1).padStart(2, "0")}</span>
        <span class="lb-name">@${escapeHtml(row.username)}</span>
        <span class="lb-amt">${fmtUSDC(row.total_usdc)} USDC</span>
      </div>`).join("");
  } catch (e) {
    $("leaderboard").innerHTML = '<div class="empty">Не удалось загрузить</div>';
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function loadOnchainMarkets() {
  try {
    const net = await netInfo();
    const r = await fetch("/api/onchain/markets");
    if (!r.ok) throw new Error();
    const markets = await r.json();
    const section = $("onchain-markets");
    const el = $("onchain-markets-list");
    if (!section || !el) return;
    if (!markets.length) {
      // Contract not deployed or nothing created yet — hide the section.
      searchIndex.onchain = [];
      section.hidden = true;
      return;
    }
    searchIndex.onchain = markets;
    section.hidden = false;
    /* Reveal only un-hides on intersection, and this section can be unhidden
       after the user has already scrolled past it — then it would never
       intersect and stay invisible. */
    section.classList.add("is-visible");
    el.innerHTML = markets.map((m, i) => {
      const winnerIdx = m.resolved ? m.winner : null;
      const badge = statusBadge(m);
      const deadline = deadlineHtml(m);
      const options = m.options.map((o, j) => {
        const isWinner = winnerIdx !== null && o.index === winnerIdx;
        return `
        <div class="option${isWinner ? " option-winner" : ""}">
          <div class="option-top">
            <span class="option-label">${escapeHtml(o.label)}</span>
            <span class="option-val">${fmtPct(o.price_pct)}</span>
          </div>
          <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" aria-hidden="true" style="width:${Math.max(o.price_pct, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
        </div>`;
      }).join("");
      return `
        <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
          <div class="market-head">
            <span class="market-question">#${m.id} ${escapeHtml(m.question)}</span>
            <span class="market-meta">${metaLine(deadline, badge)}</span>
          </div>
          ${options}
          <div class="market-footer">
            <span class="pot">On-chain · ERC-1155 · USDC на ${escapeHtml(net.chain_name || "—")}</span>
            <span class="pot">
              <a class="m-link" href="/m/oc/${m.id}">Подробнее →</a>
              ${m.market_address && net.explorer ? `<a class="m-link" href="${escapeHtml(net.explorer)}/address/${encodeURIComponent(m.market_address)}" target="_blank" rel="noopener">Открыть в обозревателе</a>` : ""}
            </span>
          </div>
        </div>`;
    }).join("");
  } catch (e) {
    const section = $("onchain-markets");
    if (section) section.hidden = true;
  }
}

/* Copy-on-click for the command menu — delegated because the CSP blocks inline
 * event-handler attributes. */
const commandsGrid = $("commands-grid");
if (commandsGrid) {
  commandsGrid.addEventListener("click", async (e) => {
    const btn = e.target.closest(".cmd");
    if (!btn) return;
    const cmd = "/" + btn.dataset.cmd;
    copyToClipboard(cmd, "commands-msg", "команду");
  });
}

/* The workspace above creates markets and bets; the public lists have to pick
 * the new entry up without a page reload. */
window.addEventListener("tippy:created", () => {
  loadMarkets();
  loadPredictions();
  loadClosedMarkets();
  loadOnchainMarkets();
  loadStats();
});

loadInfo();
loadStats();
loadWallet();
loadMarkets();
loadPredictions();
loadClosedMarkets();
loadOnchainMarkets();
loadLeaderboard();
loadVolumeChart();
setInterval(loadStats, 15000);
setInterval(loadWallet, 30000);
setInterval(loadMarkets, 60000);
setInterval(loadPredictions, 60000);
setInterval(loadOnchainMarkets, 60000);
setInterval(tickCountdowns, 10000);

function tickCountdowns() {
  document.querySelectorAll("[data-close-at]").forEach((el) => {
    const ts = parseInt(el.dataset.closeAt, 10);
    if (ts * 1000 <= Date.now()) {
      /* A reader may be looking at the card when the clock runs out. The chip
       * changes with it and the span stops ticking — otherwise the line would
       * call the market «открыт» until the next list reload, which is the
       * contradiction the state function exists to remove. */
      el.removeAttribute("data-close-at");
      const meta = el.closest(".market-meta");
      const chip = meta && meta.querySelector(".chip");
      if (chip) chip.outerHTML = statusBadge({ status: "open", close_at: ts });
      if (el.dataset.closeNote) el.textContent = el.dataset.closeNote;
      else el.remove();
      return;
    }
    el.textContent = deadlineLine({ status: "open", close_at: ts });
    el.classList.toggle("countdown-urgent", ts * 1000 - Date.now() < 3600 * 1000);
  });
}