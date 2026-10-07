/* Workspace for / — drives the bot's full feature set from the browser.
 *
 * Auth is the same signed session cookie the Telegram Mini App gets, so every
 * /api/mini/* endpoint below enforces the same checks, throttles and limits as
 * the chat handlers. Limits are read from /api/info instead of being hardcoded,
 * so the numbers shown here are the numbers the server rejects against.
 */
(function () {
  'use strict';

  var MICRO = 1000000;
  var $ = function (id) { return document.getElementById(id); };
  var info = { limits: {}, fees: {}, smart_wallet_enabled: false, onchain_markets_enabled: false };
  var me = null;
  var state = null;
  var staged = null;      // staged withdrawal, awaiting confirm
  var stagedTimer = null;
  var pick = { trade: null, bet: null, oc: null };
  var createKind = 'market';

  /* ---------- helpers ---------- */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmt(x, dp) {
    var n = Number(x);
    if (!isFinite(n)) n = 0;
    return n.toLocaleString('ru-RU', {
      minimumFractionDigits: dp == null ? 2 : dp,
      maximumFractionDigits: dp == null ? 2 : dp,
    });
  }

  // Amounts already carry 6 decimals from the server; trailing zeros dropped.
  function exact(x) {
    return String(x).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  }

  function showError(id, text) {
    var el = $(id);
    if (!el) return;
    el.textContent = text;
    el.className = 'msg err';
  }

  function showOk(id, text) {
    var el = $(id);
    if (!el) return;
    el.textContent = text;
    el.className = 'msg ok';
  }

  function clearMsg(id) {
    var el = $(id);
    if (!el) return;
    el.textContent = '';
    el.className = 'msg';
  }

  function reason(err) {
    return err && (err.error || err.detail) || 'запрос не выполнен';
  }

  // Returns parsed JSON; throws Error(detail) on a non-2xx response. A 401
  // means the session cookie died (SECRET_KEY rotation), so fall back to connect.
  async function api(path, opts) {
    var r = await fetch(path, opts);
    var body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (r.status === 401) {
      signedOut();
      throw new Error('сессия истекла — войдите заново');
    }
    if (!r.ok) throw new Error(reason(body));
    return body || {};
  }

  function post(path, payload) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  function usd(x) { return fmt(x) + ' USDC'; }

  function num(id) {
    var v = parseFloat($(id).value);
    return isFinite(v) ? v : 0;
  }

  function micro(x) { return Math.floor(x * MICRO); }

  /* ---------- session ---------- */

  function signedOut() {
    me = null;
    state = null;
    $('workspace').hidden = true;
    $('connect').hidden = false;
    $('authArea').innerHTML = '<a class="btn btn-ghost btn-sm" href="#workspace-section">Войти</a>';
  }

  function renderAuthArea() {
    var name = (state && state.username) ? '@' + state.username : '#' + me.tg_id;
    $('authArea').innerHTML =
      '<a class="btn btn-ghost btn-sm" href="#workspace-section">' + esc(name) + '</a>';
  }

  // Inside the Telegram WebView initData is available but no cookie is set yet;
  // exchange it for the session cookie the rest of this file uses.  Returns true
  // only when the exchange ran, so boot() does not fire a second doomed
  // /api/me for an anonymous browser visitor.
  async function telegramAutoAuth() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.initData || !tg.initDataUnsafe || !tg.initDataUnsafe.user) return false;
    if (!/=/g.test(tg.initData)) return false;
    await post('/api/mini/auth', { initData: tg.initData });
    return true;
  }

  async function boot() {
    try {
      me = await api('/api/me');
    } catch (e) {
      try {
        if (!(await telegramAutoAuth())) throw new Error('no telegram session');
        me = await api('/api/me');
      } catch (e2) {
        signedOut();
        wireConnect();
        return;
      }
    }
    await afterSignIn();
    wireConnect();
    wireWorkspace();
    wireCreateOptions();
  }

  async function afterSignIn() {
    $('connect').hidden = true;
    $('workspace').hidden = false;
    renderAuthArea();
    await Promise.all([loadInfo(), loadState()]);
    renderPositions();
  }

  async function loadInfo() {
    try {
      var i = await api('/api/info');
      info.limits = i.limits || {};
      info.fees = i.fees || {};
      info.smart_wallet_enabled = !!i.smart_wallet_enabled;
      info.onchain_markets_enabled = !!i.onchain_markets_enabled;
      renderLimits();
    } catch (e) { /* hints stay blank rather than showing invented numbers */ }
  }

  function renderLimits() {
    var L = info.limits;
    if (L.max_tip_usdc) {
      $('tipLimit').textContent = 'Максимум за один перевод — ' + usd(L.max_tip_usdc) + '.';
    }
    if (L.max_trade_usdc) {
      $('tradeLimit').textContent = 'Максимум за одну сделку — ' + usd(L.max_trade_usdc) + '.';
    }
    if (L.max_bet_usdc) {
      $('betLimit').textContent = 'Максимум на одну ставку — ' + usd(L.max_bet_usdc) + '.';
    }
    var wd = [];
    if (L.min_withdraw_usdc) wd.push('не меньше ' + usd(L.min_withdraw_usdc));
    if (info.fees.withdraw_pct != null) wd.push('комиссия ' + fmt(info.fees.withdraw_pct, 2) + '% сверх суммы');
    if (L.max_withdraws_per_day) wd.push(L.max_withdraws_per_day + ' в сутки');
    if (wd.length) $('wdLimits').textContent = 'Вывод: ' + wd.join(', ') + '.';
    if (L.min_subsidy_usdc && L.max_subsidy_usdc) {
      $('subsidyLimits').textContent = 'От ' + usd(L.min_subsidy_usdc) + ' до ' + usd(L.max_subsidy_usdc) +
        ' — эта сумма идёт в ликвидность LMSR и не списывается с баланса сверх неё.';
    }
    amountChips('tipChips', 'tipAmount', [1, 5, 10, 25, 50].filter(function (v) {
      return !L.max_tip_usdc || v <= L.max_tip_usdc;
    }));
    amountChips('wdChips', 'wdAmount', [10, 50, 100, 250], true);
  }

  function amountChips(boxId, targetId, values, plusMax) {
    var box = $(boxId);
    if (!box) return;
    box.innerHTML = '';
    values.forEach(function (v) {
      var b = document.createElement('button');
      b.className = 'amount-chip';
      b.type = 'button';
      b.textContent = fmt(v, 0);
      b.dataset.act = 'chip';
      b.dataset.target = targetId;
      b.dataset.value = v;
      box.appendChild(b);
    });
    if (plusMax) {
      var m = document.createElement('button');
      m.className = 'amount-chip';
      m.type = 'button';
      m.textContent = 'всё';
      m.dataset.act = 'withdrawMax';
      box.appendChild(m);
    }
  }

  /* ---------- state ---------- */

  async function loadState() {
    try {
      state = await api('/api/mini/state');
    } catch (e) {
      showError('balanceMsg', 'Не удалось загрузить состояние: ' + e.message);
      return;
    }
    $('wsName').textContent = state.username ? '@' + state.username : 'Пользователь';
    $('wsId').textContent = 'id ' + state.tg_id;
    $('wsBalance').textContent = fmt(state.balance_usdc);
    $('balAvailable').textContent = usd(state.balance_usdc);
    $('depositAddress').textContent = state.deposit_address;
    $('depositQr').src = '/qr?size=220&data=' + encodeURIComponent(state.deposit_address);
    $('linkedAddress').textContent = state.linked_address || 'не привязан';
    renderMarkets();
    renderBets();
    renderOnchain();
    renderHistory();
    markLang();
    renderAuthArea();
    if (info.smart_wallet_enabled) loadSmartWallet();
  }

  function markLang() {
    document.querySelectorAll('[data-act="setLang"]').forEach(function (b) {
      b.classList.toggle('active', b.dataset.lang === state.lang);
    });
  }

  function renderMarkets() {
    fillSelect('tradeMarket', state.markets, 'Рынков с открытым торгом пока нет');
    pick.trade = null;
    renderTradeOptions();
  }

  function renderBets() {
    fillSelect('betPick', state.bets, 'Открытых ставок-пулов пока нет');
    pick.bet = null;
    renderBetOptions();
  }

  function fillSelect(id, rows, emptyLabel) {
    var sel = $(id);
    sel.innerHTML = '';
    if (!rows.length) {
      var o = document.createElement('option');
      o.textContent = emptyLabel;
      o.value = '';
      sel.appendChild(o);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    rows.forEach(function (r) {
      var opt = document.createElement('option');
      opt.value = r.id;
      opt.textContent = '#' + r.id + ' · ' + r.question;
      sel.appendChild(opt);
    });
  }

  function optionList(boxId, options, key) {
    var box = $(boxId);
    box.innerHTML = '';
    options.forEach(function (o) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'opt-btn';
      b.dataset.act = 'pick';
      b.dataset.pick = key;
      b.dataset.option = o.index;
      b.dataset.target = boxId;
      var price = o.price_pct != null ? o.price_pct
        : (o.chance_pct != null ? o.chance_pct : null);
      b.innerHTML = '<span class="opt-label">' + esc(o.label) + '</span>' +
        (price != null ? '<span class="opt-price">' + fmt(price, 1) + '%</span>' : '');
      box.appendChild(b);
    });
  }

  function tradeMarket() {
    var id = parseInt($('tradeMarket').value, 10);
    return (state.markets || []).filter(function (m) { return m.id === id; })[0] || null;
  }

  function renderTradeOptions() {
    var m = tradeMarket();
    pick.trade = null;
    if (!m) { $('tradeOptions').innerHTML = '<div class="empty">нет выбранного рынка</div>'; return; }
    optionList('tradeOptions', m.options, 'trade');
  }

  function betPick() {
    var id = parseInt($('betPick').value, 10);
    return (state.bets || []).filter(function (b) { return b.id === id; })[0] || null;
  }

  function renderBetOptions() {
    var b = betPick();
    pick.bet = null;
    if (!b) { $('betOptions').innerHTML = '<div class="empty">нет выбранной ставки</div>'; return; }
    optionList('betOptions', b.options, 'bet');
    $('betOptions').innerHTML += '';
    var pot = document.createElement('div');
    pot.className = 'hint';
    pot.textContent = 'Пул сейчас: ' + usd(b.pot_usdc);
    $('betOptions').appendChild(pot);
  }

  function ocMarkets() {
    return state.onchain_markets || [];
  }

  function ocMarket() {
    var id = parseInt($('ocMarket').value, 10);
    return ocMarkets().filter(function (m) { return m.id === id; })[0] || null;
  }

  function renderOnchain() {
    var rows = ocMarkets();
    fillSelect('ocMarket', rows, 'Ончейн-рынков пока нет');
    pick.oc = null;
    var box = $('ocOptions');
    var m = ocMarket();
    if (!m) { box.innerHTML = '<div class="empty">нет выбранного рынка</div>'; return; }
    optionList('ocOptions', m.options, 'oc');
    $('ocMsg').textContent = '';
  }

  var KIND_LABEL = {
    deposit: 'Депозит',
    withdraw: 'Вывод',
    tip: 'Чаевые',
    bet: 'Ставка',
    bet_win: 'Выигрыш ставки',
    bet_cancel: 'Возврат ставки',
    market_buy: 'Покупка долей',
    market_sell: 'Продажа долей',
    market_win: 'Выплата по рынку',
    market_cancel: 'Возврат по рынку',
    market_create: 'Создание рынка',
    paywall: 'Платный пост',
    paywall_earn: 'Доход с платного поста',
    channel_earn: 'Доход с канала',
    channel_pay: 'Оплата канала',
    x402: 'Оплата x402',
    fee: 'Комиссия',
  };

  function renderHistory() {
    var box = $('historyList');
    var rows = state.history || [];
    if (!rows.length) {
      box.innerHTML = '<div class="empty">Операций пока нет</div>';
      return;
    }
    box.innerHTML = rows.map(function (r) {
      var out = r.kind !== 'deposit' && r.kind !== 'tip' && r.kind !== 'bet_win'
        && r.kind !== 'market_win' && r.kind !== 'paywall_earn' && r.kind !== 'channel_earn';
      var label = KIND_LABEL[r.kind] || r.kind;
      var sub = [r.counterparty ? '@' + r.counterparty : '', r.note || '']
        .filter(Boolean).join(' — ');
      return '<div class="row"><span class="row-k">' + esc(label) +
        (sub ? ' <span class="hint">' + esc(sub) + '</span>' : '') +
        '<br><span class="hint">' + esc(String(r.created_at || '').replace('T', ' ').slice(0, 16)) + '</span></span>' +
        '<b class="row-v ' + (out ? 'neg' : 'pos') + '">' + (out ? '−' : '+') + fmt(r.amount) + '</b></div>';
    }).join('');
  }

  // Positions ride on /api/me, so the fetch and the render are split: boot
  // already has a fresh `me` and only needs to paint, while a bet must pull
  // the new position from the server first.
  async function loadPositions() {
    try {
      me = await api('/api/me');
    } catch (e) { return; }
    renderPositions();
  }

  function renderPositions() {
    var box = $('positionsList');
    var rows = (me && me.positions) || [];
    if (!rows.length) {
      box.innerHTML = '<div class="empty">Позиций по рынкам пока нет</div>';
      return;
    }
    box.innerHTML = rows.map(function (p) {
      var win = p.value_usdc - p.cost_usdc;
      return '<div class="row"><span class="row-k">#' + esc(p.market_id) + ' '
        + esc(p.question) + ' <span class="hint">→ ' + esc(p.option) + '</span></span>' +
        '<b class="row-v">' + fmt(p.value_usdc) +
        ' <span class="hint ' + (win >= 0 ? 'pos' : 'neg') + '">' +
        (win >= 0 ? '+' : '') + fmt(win) + '</span></b></div>';
    }).join('');
  }

  async function loadSmartWallet() {
    var box = $('smartWalletBox');
    try {
      var w = await api('/api/mini/smartwallet');
      box.hidden = false;
      $('swStatus').textContent = w.deployed ? 'развёрнут' : 'ещё не развёрнут';
      $('swAddress').textContent = w.address;
      $('swBalance').textContent = usd(w.balance_usdc);
      $('swHint').textContent = w.paymaster_sponsored
        ? 'Газ оплачивает paymaster — комиссия за транзакцию с вас не берётся.'
        : 'Газ оплачивает ваш кошелёк.';
    } catch (e) {
      box.hidden = true;   // 503: stack not configured — nothing to show
    }
  }

  /* ---------- actions ---------- */

  async function sendTip() {
    var to = $('tipTo').value.trim();
    var amount = num('tipAmount');
    if (!to) return showError('tipMsg', 'Укажите получателя.');
    if (amount <= 0) return showError('tipMsg', 'Сумма должна быть больше нуля.');
    clearMsg('tipMsg');
    try {
      var r = await post('/api/mini/tip', { to: to.replace(/^@/, ''), amount: amount });
      showOk('tipMsg', 'Отправлено ' + usd(amount) + ' → ' + to + '. Остаток: ' + usd(r.new_balance));
      $('tipAmount').value = '';
      await refreshBalance(r.new_balance);
    } catch (e) { showError('tipMsg', e.message); }
  }

  async function stageWithdraw() {
    var address = $('wdAddress').value.trim();
    var amount = num('wdAmount');
    if (!address) return showError('wdMsg', 'Укажите адрес получателя.');
    if (amount <= 0) return showError('wdMsg', 'Сумма должна быть больше нуля.');
    clearMsg('wdMsg');
    try {
      staged = await post('/api/mini/withdraw', { address: address, amount: amount });
      $('wdRvAmount').textContent = exact(staged.amount) + ' USDC';
      $('wdRvFee').textContent = exact(staged.fee) + ' USDC';
      $('wdRvTotal').textContent = exact(staged.total) + ' USDC';
      $('wdRvAfter').textContent = exact(staged.balance_after) + ' USDC';
      $('wdRvAddr').textContent = staged.address;
      $('wdReview').hidden = false;
      countdownTtl(staged.expires_in);
      showOk('wdMsg', 'Предложение зафиксировано. Проверьте сумму и подтвердите.');
    } catch (e) { showError('wdMsg', e.message); }
  }

  function countdownTtl(seconds) {
    var left = seconds | 0;
    clearInterval(stagedTimer);
    var paint = function () {
      $('wdRvTtl').textContent = left > 0
        ? Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0')
        : 'истекло';
    };
    paint();
    stagedTimer = setInterval(function () {
      left -= 1;
      paint();
      if (left <= 0) {
        clearInterval(stagedTimer);
        dropStaged('Время подтверждения вышло — оформите вывод заново.');
      }
    }, 1000);
  }

  function dropStaged(msg) {
    clearInterval(stagedTimer);
    staged = null;
    $('wdReview').hidden = true;
    if (msg) showError('wdMsg', msg);
  }

  async function confirmWithdraw(go) {
    if (!staged) return;
    var token = staged.token;
    clearMsg('wdMsg');
    try {
      var r = await post('/api/mini/withdraw/confirm', { token: token, confirm: go });
      dropStaged(null);
      if (r.cancelled) {
        showOk('wdMsg', 'Вывод отменён, средства не списаны.');
      } else {
        showOk('wdMsg', 'Вывод #' + r.withdraw_id + ' поставлен в очередь на '
          + exact(r.amount) + ' USDC (комиссия ' + exact(r.fee) + '). Остаток: ' + usd(r.new_balance));
      }
      await refreshBalance(r.new_balance);
      await loadState();
    } catch (e) {
      dropStaged(null);
      showError('wdMsg', e.message);
    }
  }

  // Fee is ceil(amount * pct), charged on top, so "всё" steps down until the
  // total fits the balance exactly — the same inequality the server checks.
  function withdrawMax() {
    var bal = micro(state ? state.balance_usdc : 0);
    var pct = (info.fees.withdraw_pct || 0) / 100;
    var a = Math.floor(bal / (1 + pct));
    while (a > 0 && a + Math.max(1, Math.ceil(a * pct)) > bal) a -= 1;
    $('wdAmount').value = (a / MICRO).toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }

  async function buyShares() {
    var m = tradeMarket();
    var amount = num('tradeAmount');
    if (!m) return showError('tradeMsg', 'Выберите рынок.');
    if (pick.trade == null) return showError('tradeMsg', 'Выберите исход.');
    if (amount <= 0) return showError('tradeMsg', 'Сумма должна быть больше нуля.');
    clearMsg('tradeMsg');
    try {
      var r = await post('/api/mini/trade', {
        market_id: m.id, option: pick.trade, amount: amount,
      });
      showOk('tradeMsg', 'Куплено долей по рынку #' + m.id + '. Новый баланс: ' + usd(r.new_balance));
      $('tradeAmount').value = '';
      await refreshBalance(r.new_balance);
      await Promise.all([loadState(), loadPositions()]);
    } catch (e) { showError('tradeMsg', e.message); }
  }

  async function placeBet() {
    var b = betPick();
    var amount = num('betAmount');
    if (!b) return showError('betMsg', 'Выберите ставку.');
    if (pick.bet == null) return showError('betMsg', 'Выберите вариант.');
    if (amount <= 0) return showError('betMsg', 'Сумма должна быть больше нуля.');
    clearMsg('betMsg');
    try {
      var r = await post('/api/mini/betplace', { bet_id: b.id, option: pick.bet, amount: amount });
      showOk('betMsg', 'Ставка принята в пул #' + b.id + '. Баланс: ' + usd(r.new_balance));
      $('betAmount').value = '';
      await refreshBalance(r.new_balance);
      await loadState();
    } catch (e) { showError('betMsg', e.message); }
  }

  async function createItem() {
    var question = $('newQuestion').value.trim();
    var options = optionValues().filter(Boolean);
    var hours = parseFloat($('newHours').value) || null;
    if (question.length < 5) return showError('createMsg', 'Вопрос короче 5 символов.');
    if (options.length < 2) return showError('createMsg', 'Нужно минимум 2 варианта.');
    clearMsg('createMsg');
    try {
      var payload = { kind: createKind, question: question, options: options, hours: hours };
      if (createKind === 'market') payload.subsidy_usdc = num('newSubsidy');
      var r = await post('/api/mini/create', payload);
      showOk('createMsg', 'Создано: #' + r.id + '. Обновляю списки.');
      $('newQuestion').value = '';
      await loadState();
      window.dispatchEvent(new CustomEvent('tippy:created'));
    } catch (e) { showError('createMsg', e.message); }
  }

  async function smartBuy() {
    var m = ocMarket();
    var shares = parseInt($('ocShares').value, 10);
    var maxCost = num('ocMaxCost');
    if (!m) return showError('ocMsg', 'Выберите ончейн-рынок.');
    if (pick.oc == null) return showError('ocMsg', 'Выберите исход.');
    if (!(shares > 0)) return showError('ocMsg', 'Долей должно быть больше нуля.');
    if (!(maxCost > 0)) return showError('ocMsg', 'Укажите максимальную цену.');
    clearMsg('ocMsg');
    try {
      var r = await post('/api/mini/smartbuy', {
        market_id: m.id, outcome: pick.oc, shares: shares, max_cost_usdc: maxCost,
      });
      showOk('ocMsg', 'Транзакция отправлена: ' + r.tx_hash.slice(0, 18) + '…');
    } catch (e) { showError('ocMsg', e.message); }
  }

  async function setLang(lang) {
    clearMsg('balanceMsg');
    try {
      var r = await post('/api/mini/lang', { lang: lang });
      state.lang = r.lang;
      markLang();
      showOk('balanceMsg', 'Язык сообщений бота: ' + r.lang);
    } catch (e) { showError('balanceMsg', e.message); }
  }

  async function refreshBalance(v) {
    if (typeof v === 'number') {
      $('wsBalance').textContent = fmt(v);
      $('balAvailable').textContent = usd(v);
      if (state) state.balance_usdc = v;
    }
  }

  async function copyText(targetId) {
    var text = $(targetId).textContent.trim();
    if (!text || text === 'загрузка…') return;
    try {
      await navigator.clipboard.writeText(text);
      showOk('balanceMsg', 'Скопировано в буфер обмена.');
    } catch (e) {
      showError('balanceMsg', 'Браузер не дал доступ к буферу — выделите адрес вручную.');
    }
  }

  /* ---------- create-form option rows ---------- */

  function optionValues() {
    return Array.prototype.map.call(
      document.querySelectorAll('#newOptions input'), function (i) { return i.value.trim(); });
  }

  function optionRow(value) {
    var div = document.createElement('div');
    div.className = 'opt-row';
    var input = document.createElement('input');
    input.className = 'input';
    input.maxLength = 64;
    input.placeholder = 'Вариант';
    input.value = value || '';
    div.appendChild(input);
    if (document.querySelectorAll('#newOptions .opt-row').length >= 2) {
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'btn btn-ghost btn-sm';
      rm.textContent = '×';
      rm.dataset.act = 'removeOption';
      div.appendChild(rm);
    }
    return div;
  }

  function refreshOptionRows() {
    var rows = document.querySelectorAll('#newOptions .opt-row');
    $('newOptions').querySelectorAll('[data-act="removeOption"]').forEach(function (b) {
      b.style.visibility = rows.length > 2 ? 'visible' : 'hidden';
    });
    var add = document.querySelector('[data-act="addOption"]');
    if (add) add.style.display = rows.length >= 4 ? 'none' : '';
  }

  function wireCreateOptions() {
    $('newOptions').innerHTML = '';
    ['Да', 'Нет'].forEach(function (v) { $('newOptions').appendChild(optionRow(v)); });
    refreshOptionRows();
  }

  function addOptionRow() {
    if (document.querySelectorAll('#newOptions .opt-row').length >= 4) return;
    $('newOptions').appendChild(optionRow(''));
    refreshOptionRows();
  }

  function removeOptionRow(btn) {
    if (document.querySelectorAll('#newOptions .opt-row').length <= 2) return;
    var row = btn.closest('.opt-row');
    if (row) row.remove();
    refreshOptionRows();
  }

  /* ---------- connect ---------- */

  function wireConnect() {
    // Nothing persistent: the wallet flow is a single signed message.
  }

  async function walletLogin() {
    if (!window.ethereum) {
      return showError('connectMsg', 'EVM-кошелёк не найден — установите MetaMask или войдите через Telegram.');
    }
    clearMsg('connectMsg');
    try {
      var accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
      var addr = accounts[0];
      if (!addr) throw new Error('кошелёк не вернул адрес');
      var nonceBytes = window.crypto.getRandomValues(new Uint8Array(16));
      var nonce = Array.prototype.map.call(nonceBytes, function (b) {
        return b.toString(16).padStart(2, '0');
      }).join('');
      var message = 'Tippy login\nAddress: ' + addr + '\nNonce: ' + nonce
        + '\nExpires: ' + Math.floor(Date.now() / 1000 + 600);
      var signature = await window.ethereum.request({
        method: 'personal_sign', params: [message, addr],
      });
      await post('/api/auth/wallet', { address: addr, message: message, signature: signature });
      showOk('connectMsg', 'Кошелёк подтверждён, загружаю кабинет.');
      await afterSignIn();
    } catch (e) {
      showError('connectMsg', e.code === 4001 ? 'Подпись отклонена в кошельке.' : e.message);
    }
  }

  async function logout() {
    try { await fetch('/logout', { method: 'POST' }); } catch (e) { /* cookie cleared server-side anyway */ }
    signedOut();
  }

  /* ---------- tabs & delegation (CSP blocks inline handlers) ---------- */

  function switchTab(name) {
    document.querySelectorAll('.tab').forEach(function (t) {
      t.classList.toggle('active', t.dataset.tab === name);
    });
    document.querySelectorAll('.panel').forEach(function (p) {
      p.hidden = p.id !== 'panel-' + name;
    });
  }

  function pickOption(btn) {
    var key = btn.dataset.pick;
    var box = $(btn.dataset.target);
    box.querySelectorAll('.opt-btn').forEach(function (b) { b.classList.remove('selected'); });
    btn.classList.add('selected');
    pick[key] = parseInt(btn.dataset.option, 10);
  }

  function wireWorkspace() {
    $('tradeMarket').addEventListener('change', renderTradeOptions);
    $('betPick').addEventListener('change', renderBetOptions);
    $('ocMarket').addEventListener('change', renderOnchain);

    document.addEventListener('click', function (e) {
      var el = e.target.closest('[data-act]');
      if (!el) return;
      var act = el.dataset.act;
      if (act === 'walletLogin') return walletLogin();
      if (act === 'toggleTheme') return;      // handled by the theme script
      if (!me) return;
      if (act === 'tab') return switchTab(el.dataset.tab);
      if (act === 'pick') return pickOption(el);
      if (act === 'chip') {
        $(el.dataset.target).value = el.dataset.value;
        document.querySelectorAll('#' + el.parentNode.id + ' .amount-chip').forEach(function (c) {
          c.classList.remove('active');
        });
        el.classList.add('active');
        return;
      }
      if (act === 'withdrawMax') return withdrawMax();
      if (act === 'sendTip') return sendTip();
      if (act === 'stageWithdraw') return stageWithdraw();
      if (act === 'confirmWithdraw') return confirmWithdraw(true);
      if (act === 'cancelWithdraw') return confirmWithdraw(false);
      if (act === 'buyShares') return buyShares();
      if (act === 'placeBet') return placeBet();
      if (act === 'createMarket') return createItem();
      if (act === 'createKind') {
        createKind = el.dataset.kind;
        document.querySelectorAll('[data-act="createKind"]').forEach(function (b) {
          b.classList.toggle('active', b === el);
        });
        $('subsidyField').hidden = createKind !== 'market';
        return;
      }
      if (act === 'addOption') return addOptionRow();
      if (act === 'removeOption') return removeOptionRow(el);
      if (act === 'smartBuy') return smartBuy();
      if (act === 'setLang') return setLang(el.dataset.lang);
      if (act === 'copy') return copyText(el.dataset.target);
      if (act === 'refreshState') return loadState();
      if (act === 'logout') return logout();
    });

    var enter = function (fn) {
      return function (e) { if (e.key === 'Enter') { e.preventDefault(); fn(); } };
    };
    $('tipAmount').addEventListener('keydown', enter(sendTip));
    $('tipTo').addEventListener('keydown', enter(sendTip));
    $('wdAmount').addEventListener('keydown', enter(stageWithdraw));
    $('wdAddress').addEventListener('keydown', enter(stageWithdraw));
    $('tradeAmount').addEventListener('keydown', enter(buyShares));
    $('betAmount').addEventListener('keydown', enter(placeBet));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
