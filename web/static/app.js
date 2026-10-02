const $ = (id) => document.getElementById(id);
document.body.classList.add("js");

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const fmtUSDC = (x) =>
  (x ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 2, minimumFractionDigits: 0 });

/* ---------- reveal-on-scroll ---------- */
const io = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add("is-visible");
        io.unobserve(e.target);
      }
    }
  },
  { threshold: 0.1 }
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
  const dur = 900;
  const start = performance.now();
  function frame(now) {
    const p = Math.min((now - start) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmtUSDC(target * eased);
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function setCount(el, value) {
  if (!el) return;
  el.classList.remove("skeleton");
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
  box.appendChild(s);
}

/* Command menu comes from bot/commands_catalog.py through /api/info, so it is
 * the list the bot actually installs with set_my_commands — not a written-out
 * copy that can drift away from the handlers. */
function renderCommands(info) {
  const el = $("commands-grid");
  if (!el) return;
  const cmds = info.commands || [];
  if (!cmds.length) {
    el.innerHTML = '<div class="empty">Список команд сейчас недоступен</div>';
    return;
  }
  el.innerHTML = cmds
    .map((c) => {
      const cmd = escapeHtml(c.command);
      return `<button class="cmd" type="button" data-cmd="${cmd}" title="Скопировать /${cmd}">`
        + `<b>/${cmd}</b><span>${escapeHtml(c.description)}</span></button>`;
    })
    .join("");
}

function setMsg(id, text, ok) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

/* Terms are read from the same endpoint the write handlers enforce against,
 * so the page cannot drift away from the configured limits. */
function renderTerms(info) {
  const el = $("terms-body");
  if (!el || !info.limits || !info.fees) return;
  const L = info.limits, F = info.fees;
  const rows = [
    ["Комиссия на вывод", F.withdraw_pct + "%"],
    ["Комиссия с выигрыша", F.win_pct + "%"],
    ["Максимум чаевых за раз", fmtUSDC(L.max_tip_usdc) + " USDC"],
    ["Максимум ставки за раз", fmtUSDC(L.max_bet_usdc) + " USDC"],
    ["Максимум сделки на рынке", fmtUSDC(L.max_trade_usdc) + " USDC"],
    ["Минимальный вывод", fmtUSDC(L.min_withdraw_usdc) + " USDC"],
    ["Выводов в сутки", L.max_withdraws_per_day],
    [
      "Субсидия ликвидности рынка",
      fmtUSDC(L.min_subsidy_usdc) + "–" + fmtUSDC(L.max_subsidy_usdc) + " USDC",
    ],
    ["Сеть", "Base · chain id " + info.chain_id],
    ["Контракт USDC", info.usdc_address],
    ["Ончейн-рынки (Cally)", info.onchain_markets_enabled ? "включены" : "контракт не подключён"],
    ["Gasless-покупка долей", info.smart_wallet_enabled ? "включена" : "не настроена"],
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

async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    const s = await r.json();
    setCount($("stat-volume"), s.volume_usdc);
    setCount($("stat-vol30"), s.volume_30d_usdc);
    setCount($("stat-users"), s.users);
    setCount($("stat-markets"), s.open_markets);
    setCount($("stat-tx"), s.transactions);
    setCount($("stat-fees"), s.fees_usdc);
  } catch (e) { /* keep placeholders */ }
}

function fmtDay(day) {
  const [y, m, d] = day.split("-");
  return d + "." + m;
}

async function loadVolumeChart() {
  try {
    const r = await fetch("/api/volume_history?days=14");
    const days = await r.json();
    const el = $("volume-chart");
    if (!days.length) {
      el.innerHTML = '<div class="empty">Пока нет данных — объём появится после первых операций</div>';
      return;
    }
    const max = Math.max(...days.map((d) => d.volume_usdc), 1);
    el.innerHTML = days.map((d, i) => `
      <div class="chart-col" title="${fmtDay(d.day)}: ${fmtUSDC(d.volume_usdc)} USDC">
        <div class="chart-bar" style="height:${Math.max(d.volume_usdc / max * 100, 2)}%;animation-delay:${Math.min(i * 60, 600)}ms"></div>
        <div class="chart-day">${fmtDay(d.day)}</div>
      </div>`).join("");
  } catch (e) {
    $("volume-chart").innerHTML = '<div class="empty">Не удалось загрузить график</div>';
  }
}

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
    $("reserve-source").textContent =
      s.reserves_source === "vault" ? "TipBotVault (on-chain)" : "горячий кошелёк";
    $("wallet-solvent").textContent =
      s.solvent === true ? "✅ покрыто"
      : s.solvent === false ? "⚠️ недостаточно"
      : "RPC недоступен";
    if (s.vault_address) {
      $("wallet-vault-row").style.display = "";
      $("wallet-vault-addr").style.display = "";
      $("wallet-vault").textContent =
        s.vault_balance_usdc === null || s.vault_balance_usdc === undefined
          ? "RPC недоступен"
          : fmtUSDC(s.vault_balance_usdc) + " USDC";
      $("wallet-vault-addr").textContent = "Vault: " + s.vault_address;
    }
  } catch (e) { /* keep placeholders */ }
}

function statusBadge(status) {
  if (status === "open") return '<span class="chip chip-open">● открыт</span>';
  if (status === "resolved") return '<span class="chip chip-resolved">закрыт</span>';
  return '<span class="chip chip-cancelled">отменён</span>';
}

function relDeadline(ts) {
  const left = ts * 1000 - Date.now();
  if (left <= 0) return "дедлайн прошёл";
  const s = Math.floor(left / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return "осталось " + d + "д " + h + "ч";
  if (h) return "осталось " + h + "ч " + m + "м";
  return "осталось " + m + "м";
}

function marketCard(m, i) {
  const deadline = m.expired
    ? "🕳️ истёк — можно вернуть деньги"
    : m.close_at
      ? "⏰ <span data-close-at=\"" + parseInt(m.close_at) + "\">" + relDeadline(m.close_at) + "</span>"
      : "";
  const winnerIdx = m.status === "resolved" && m.winner !== null && m.winner !== undefined ? m.winner : null;
  const options = m.options.map((o, j) => {
    const isWinner = winnerIdx !== null && o.index === winnerIdx;
    return `
    <div class="option${isWinner ? " option-winner" : ""}">
      <div class="option-top">
        <span class="option-label">${isWinner ? "🏆 " : ""}${escapeHtml(o.label)}</span>
        <span class="option-val">${fmtUSDC(o.pool_usdc)} USDC · ${escapeHtml(String(o.probability))}% · ${escapeHtml(String(o.backers))}👤</span>
      </div>
      <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" style="width:${Math.max(o.probability, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
    </div>`;
  }).join("");

  return `
    <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
      <div class="market-head">
        <span class="market-question">#${m.id} ${escapeHtml(m.question)}</span>
        <span class="market-meta">${deadline} · ${statusBadge(m.status)}</span>
      </div>
      ${options}
      <div class="market-footer">
        <span class="pot">Пул: <b>${fmtUSDC(m.pot_usdc)} USDC</b> · ${escapeHtml(String(m.total_backers))}👤</span>
        <span class="pot">
          <a class="m-link" href="/m/${m.id}">Подробнее →</a>
          <span style="margin-left: 10px">@${escapeHtml(m.creator.username || ("id" + m.creator.id))}</span>
        </span>
      </div>
    </div>`;
}

async function loadMarkets() {
  try {
    const r = await fetch("/api/markets");
    const markets = await r.json();
    const el = $("markets-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Открытых рынков пока нет — создай первый в боте: /bet create</div>';
      return;
    }
    el.innerHTML = markets.map(marketCard).join("");
  } catch (e) {
    $("markets-list").innerHTML = '<div class="empty">Не удалось загрузить рынки</div>';
  }
}

async function loadClosedMarkets() {
  try {
    const r = await fetch("/api/markets?status=resolved");
    const markets = await r.json();
    const el = $("closed-markets-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Закрытых рынков пока нет</div>';
      return;
    }
    el.innerHTML = markets.map(marketCard).join("");
  } catch (e) {
    $("closed-markets-list").innerHTML = '<div class="empty">Не удалось загрузить</div>';
  }
}

function predictionCard(m, i) {
  const winner = m.status === "resolved" && m.winner != null ? m.winner : null;
  const deadline = m.close_at
    ? "⏰ <span data-close-at=\"" + parseInt(m.close_at) + "\">" + relDeadline(m.close_at) + "</span>"
    : "";
  const options = m.options.map((o, j) => {
    const isWinner = winner !== null && o.index === winner;
    return `
    <div class="option${isWinner ? " option-winner" : ""}">
      <div class="option-top">
        <span class="option-label">${isWinner ? "🏆 " : ""}${escapeHtml(o.label)}</span>
        <span class="option-val">${escapeHtml(String(o.price_pct))}%</span>
      </div>
      <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" style="width:${Math.max(o.price_pct, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
    </div>`;
  }).join("");

  return `
    <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
      <div class="market-head">
        <span class="market-question">#${m.id} ${escapeHtml(m.question)}</span>
        <span class="market-meta">${deadline} · ${statusBadge(m.status)}</span>
      </div>
      ${options}
      <div class="market-footer">
        <span class="pot">Ликвидность: <b>${fmtUSDC(m.liquidity_usdc)} USDC</b></span>
        <span class="pot">Оборот долей: ${fmtUSDC(m.volume_usdc)} USDC · ${escapeHtml(String(m.traders))}👤</span>
      </div>
    </div>`;
}

async function loadPredictions() {
  try {
    const r = await fetch("/api/predictions");
    const markets = await r.json();
    const el = $("predictions-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Рынков с открытым торгом пока нет</div>';
      return;
    }
    el.innerHTML = markets.map(predictionCard).join("");
  } catch (e) {
    $("predictions-list").innerHTML = '<div class="empty">Не удалось загрузить рынки</div>';
  }
}

async function loadLeaderboard() {
  try {
    const r = await fetch("/api/leaderboard");
    const rows = await r.json();
    const el = $("leaderboard");
    if (!rows.length) {
      el.innerHTML = '<div class="empty">Пока пусто</div>';
      return;
    }
    const medals = ["🥇", "🥈", "🥉"];
    el.innerHTML = rows.map((row, i) => `
      <div class="lb-row" style="animation-delay:${Math.min(i * 60, 400)}ms">
        <span class="lb-place">${medals[i] || (i + 1)}</span>
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
    const r = await fetch("/api/onchain/markets");
    if (!r.ok) throw new Error();
    const markets = await r.json();
    const section = $("onchain-markets");
    const el = $("onchain-markets-list");
    if (!section || !el) return;
    if (!markets.length) {
      // Contract not deployed or nothing created yet — hide the section.
      section.style.display = "none";
      return;
    }
    section.style.display = "";
    el.innerHTML = markets.map((m, i) => {
      const winnerIdx = m.resolved ? m.winner : null;
      const badge = m.cancelled
        ? '<span class="chip chip-cancelled">отменён</span>'
        : m.resolved
          ? '<span class="chip chip-resolved">завершён</span>'
          : "";
      const deadline = m.resolved || m.cancelled
        ? ""
        : m.close_at
          ? "⏰ <span data-close-at=\"" + parseInt(m.close_at) + "\">" + relDeadline(m.close_at) + "</span>"
          : "";
      const options = m.options.map((o, j) => {
        const isWinner = winnerIdx !== null && o.index === winnerIdx;
        return `
        <div class="option${isWinner ? " option-winner" : ""}">
          <div class="option-top">
            <span class="option-label">${isWinner ? "🏆 " : ""}${escapeHtml(o.label)}</span>
            <span class="option-val">${escapeHtml(String(o.price_pct))}%</span>
          </div>
          <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" style="width:${Math.max(o.price_pct, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
        </div>`;
      }).join("");
      return `
        <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
          <div class="market-head">
            <span class="market-question">⛓️ #${m.id} ${escapeHtml(m.question)}</span>
            <span class="market-meta">${deadline} · ${badge}</span>
          </div>
          ${options}
          <div class="market-footer">
            <span class="pot">On-chain · ERC-1155 · USDC на Base</span>
            <span class="pot">${m.market_address ? `<a class="m-link" href="https://basescan.org/address/${encodeURIComponent(m.market_address)}" target="_blank" rel="noopener">🔗 Basescan</a>` : ""}</span>
          </div>
        </div>`;
    }).join("");
  } catch (e) {
    const section = $("onchain-markets");
    if (section) section.style.display = "none";
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
    try {
      await navigator.clipboard.writeText(cmd);
      setMsg("commands-msg", "Скопировано: " + cmd, true);
    } catch (err) {
      setMsg(
        "commands-msg",
        "Браузер не дал доступ к буферу обмена — выделите команду и скопируйте вручную.",
        false
      );
    }
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
    el.textContent = relDeadline(ts);
    el.classList.toggle("countdown-urgent", ts * 1000 - Date.now() < 3600 * 1000);
  });
}