/* The chain name, the USDC address and the explorer come from /api/info —
 * the same config the bot signs transactions with — so a testnet deployment
 * cannot advertise a mainnet it is not connected to.
 *
 * Pages that already hold the payload call applyNetInfo(); renderers that need
 * a value mid-template call netInfo(), which fetches once and fills the same
 * cache, so two sources can never disagree. */
const NET = { chain_name: "", chain_id: null, explorer: "", usdc_address: "" };

function shortAddr(a) {
  const s = String(a || "");
  return s.length > 14 ? s.slice(0, 6) + "…" + s.slice(-4) : s;
}

function applyNetInfo(info) {
  if (!info) return NET;
  NET.chain_name = info.chain_name || NET.chain_name;
  NET.chain_id = info.chain_id === undefined ? NET.chain_id : info.chain_id;
  NET.explorer = info.explorer || NET.explorer;
  NET.usdc_address = info.usdc_address || NET.usdc_address;

  const texts = {
    chain: NET.chain_name,
    usdc: NET.usdc_address ? "USDC " + shortAddr(NET.usdc_address) : null,
  };
  for (const key of Object.keys(texts)) {
    const value = texts[key];
    if (!value) continue;
    document.querySelectorAll('[data-net="' + key + '"]').forEach((el) => {
      el.textContent = value;
    });
  }
  if (NET.explorer) {
    document.querySelectorAll("[data-net-explorer]").forEach((el) => {
      el.href = NET.explorer;
      el.target = "_blank";
      el.rel = "noopener";
    });
  }
  return NET;
}

async function netInfo() {
  if (!NET.chain_name) {
    try {
      applyNetInfo(await (await fetch("/api/info")).json());
    } catch (e) { /* labels keep their fallback */ }
  }
  return NET;
}

/* One state per market row, named in one place. `status` is not the state: a
 * pool whose deadline has passed and that nobody has resolved is no longer
 * open, and the four surfaces that paint a chip each derived the answer from
 * whichever of the two fields it happened to have — so the landing hero, /m/{id}
 * and /oc/{id} all printed «дедлайн прошёл» in the same line as «открыт».
 * "awaiting" is the word the bot already uses for it (истёк — /cancel). */
function marketState(row) {
  if (!row) return "cancelled";
  const status = row.status
    || (row.cancelled ? "cancelled" : row.resolved ? "resolved" : "open");
  if (status !== "open") return status;
  const closed = row.expired === true
    || (row.close_at ? Number(row.close_at) * 1000 <= Date.now() : false);
  return closed ? "awaiting" : "open";
}

/* A percentage bar is the printed number drawn as geometry. The row says
 * «0,4 %» and the fill under it owes the visitor 0,4 % of the track; a floor
 * that thickens a small share into a visible bar — the `Math.max(pct, 2)` this
 * replaced, on every option row of the site and the Mini App — paints a
 * quantity no label claims, and on a two-option market it stands a 0 % row
 * beside a 100 % one and asks the eye to compare them. The clamp is to the
 * interval a percentage lives in, not to a legibility preference, and it is one
 * function because a bar is one mechanism however many pages draw it. */
function barWidth(value) {
  const pct = Number(value);
  if (!Number.isFinite(pct)) return 0;
  const notBelowTheTrack = Math.max(pct, 0);
  return Math.min(notBelowTheTrack, 100);
}

/* What a pool whose deadline passed still owes the reader. The sentence lives
 * beside the derivation that names the state, because every surface that paints
 * «ждёт решения» must answer «и что теперь?» with the same words: the landing
 * promised «можно вернуть деньги» and /m/{id} promised «ставки можно вернуть»
 * for one fact, and a reader who saw both could not tell which money moves.
 * It names the bot, because after the deadline the bot is where the refund is
 * claimed (`/markets` -> «Отменить») and this page has no control to offer. */
const REFUND_NOTE = "ставки можно вернуть в боте";

/* The countdown is the only deadline text, and once the deadline is gone it has
 * nothing to count: the chip says that. Returning "" here rather than a
 * "прошёл" string is what makes a contradictory line impossible to build. */
function deadlineLine(row) {
  if (!row || marketState(row) !== "open" || !row.close_at) return "";
  const left = Math.max(0, Math.floor(Number(row.close_at)) - Math.floor(Date.now() / 1000));
  const d = Math.floor(left / 86400);
  const h = Math.floor((left % 86400) / 3600);
  const m = Math.floor((left % 3600) / 60);
  if (d) return "осталось " + d + "д " + h + "ч";
  if (h) return "осталось " + h + "ч " + m + "м";
  return "осталось " + m + "м";
}
