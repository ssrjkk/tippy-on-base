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
