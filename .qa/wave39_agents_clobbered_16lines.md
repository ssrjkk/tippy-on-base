  the page's own chip reads «недостаточно» whenever reserves fall short.
- **The one alert chat is the owner's, and a dropped notification is now an event.** Insolvency,
 low margin and the AML flag all read `config.SOLVENCY_ALERT_CHAT_ID`, and that chain used to end
 at `0`: `.env` on this host leaves the knob and `ADMIN_TG_ID` blank and names only `OWNER_ID`, so
 the watcher measured `solvent: false` on `/api/solvency` and answered itself with `no
 SOLVENCY_ALERT_CHAT_ID set, cannot send solvency alert` in `data/runtime/origin.err.log`, while
 `_notify_aml` in `bot/handlers/wallet.py` dropped a sanctioned-counterparty flag without a word.
 The chain now ends at `OWNER_ID` and the wallet logs the flag it cannot deliver.
 `test_the_alert_chat_falls_back_to_the_owner_rather_than_dropping_the_alert` reads the knob out of
 a fresh interpreter — the fallback is import-time arithmetic in `bot/config.py`, and reloading
 that module in this process would rewrite the knobs every other test of the session reads — and
 holds four shapes: the owner alone, an admin beating the owner, an explicit chat beating both,
 and three blanks still naming no recipient. Two mutations were proven to fire: the `OWNER_ID`
 clause removed from the chain, and the wallet's log line removed. Neither law sees delivery: a
 chat the bot cannot write to fails the way a missing one did, and only the first real alert
 proves the address is good.
