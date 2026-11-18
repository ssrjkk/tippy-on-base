/* Agent dashboard: /api/agent/status + /api/agent/audit, both owner-only.
 * External file because the CSP allows 'self' scripts and nonce-injects inline
 * ones; a separate file keeps the page markup free of behaviour. */
(function () {
  'use strict';

  var REFRESH_MS = 30000;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Money is painted with two decimals, counts with none: the precision is
  // in the name, not in an argument a call site can forget.
  function fmtMoney(x) {
    var n = Number(x);
    if (!isFinite(n)) n = 0;
    return n.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtCount(x) {
    var n = Number(x);
    if (!isFinite(n)) n = 0;
    return n.toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function fmtTime(sec) {
    var d = new Date(sec * 1000);
    return isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  function fail(text) {
    $('agent-error').textContent = text;
  }

  // The API answers these two in English; the interface language is Russian.
  var ERRORS = {
    'AGENT_TG_ID not configured': 'AGENT_TG_ID не задан — агент не привязан к аккаунту.',
    'Agent user not found': 'Аккаунт агента не найден в базе.',
  };

  function capBar(label, used, total, unit) {
    var pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
    var cls = pct >= 100 ? ' bar-fill-over' : pct >= 80 ? ' bar-fill-warn' : '';
    return '<div class="option">' +
      '<div class="option-top"><span class="option-label">' + esc(label) + '</span>' +
      '<span class="option-val">' + fmtMoney(used) + ' / ' + fmtCount(total) + ' ' + esc(unit) + '</span></div>' +
      '<div class="bar"><div class="bar-fill' + cls + '" aria-hidden="true" style="width:' + Math.max(pct, 2).toFixed(1) + '%"></div></div>' +
      '</div>';
  }

  async function loadStatus() {
    var r = await fetch('/api/agent/status');
    if (r.status === 403) {
      fail('Нужна сессия владельца: войдите в кабинет и откройте эту страницу снова.');
      return false;
    }
    if (!r.ok) { fail('Сервер ответил ' + r.status + '.'); return false; }
    var s = await r.json();
    if (s.error) { fail(ERRORS[s.error] || s.error); return false; }

    var caps = s.caps || {};
    $('agent-error').textContent = '';
    $('agent-stats').hidden = false;
    $('agent-state-row').hidden = false;
    $('caps-block').hidden = false;
    $('s-balance').textContent = fmtMoney(s.balance_usdc);
    $('s-markets').textContent = fmtCount(s.markets_created);
    $('s-bets').textContent = fmtCount(s.bets_placed);
    $('s-errors').textContent = fmtCount(caps.consecutive_errors);

    var chip = $('s-cooldown');
    var cooling = !!caps.cooldown_active;
    chip.textContent = cooling ? 'пауза после ошибок' : 'работает';
    chip.className = 'chip ' + (cooling ? 'chip-cancelled' : 'chip-open');

    $('caps').innerHTML =
      capBar('Расход за сутки', caps.daily_spent_usdc, caps.daily_cap_usdc, 'USDC') +
      capBar('Действий в час', caps.actions_this_hour, caps.max_actions_per_hour, 'циклов');
    return true;
  }

  function entryRow(e) {
    var bet = e.bet_placed
      ? 'ставка ' + fmtMoney(e.bet_amount_usdc) + ' USDC'
      : 'без ставки';
    var conf = typeof e.confidence === 'number' ? ' · ' + Math.round(e.confidence * 100) + '%' : '';
    var question = e.question ? ' · ' + e.question : '';
    return '<div class="row">' +
      '<span class="row-k"><span class="mono">' + esc(fmtTime(e.ts)) + '</span>' +
      ' · #' + esc(String(e.market_id == null ? '—' : e.market_id)) + esc(question) + '</span>' +
      '<span class="row-v nowrap' + (e.bet_placed ? ' pos' : '') + '">' + esc(bet) + conf + '</span>' +
      '</div>';
  }

  async function loadAudit() {
    var r = await fetch('/api/agent/audit');
    $('audit-block').hidden = false;
    if (!r.ok) {
      $('audit').innerHTML = '';
      $('audit-empty').hidden = false;
      $('audit-empty').textContent = 'Журнал недоступен: сервер ответил ' + r.status + '.';
      return;
    }
    var entries = await r.json();
    var rows = entries.slice(-20).reverse().map(entryRow).join('');
    $('audit').innerHTML = rows;
    $('audit-empty').hidden = !!rows;
  }

  async function load() {
    try {
      if (await loadStatus()) await loadAudit();
    } catch (e) {
      fail('Сервер недоступен: ' + e.message);
    }
  }

  load();
  setInterval(load, REFRESH_MS);
})();
