from pathlib import Path

p = Path("web/static/app.html")
t = p.read_text(encoding="utf-8")

EDITS: list[tuple[str, str]] = []

# E1 - one micro-label voice, defined once.
EDITS.append((
"""   one voice spelled two ways at two letter-spacings. */
.eyebrow{display:block;font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:var(--t-chip);font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);margin:0 0 10px}""",
"""   one voice spelled two ways at two letter-spacings. It is now also the only voice
   over a labelled thing anywhere in the app: the landing names a field with the
   same eleven pixels of tracked mono and the same 8px beneath it, while
   `.input-label` drew that label in the interface face at 12px — one job, two
   grammars, inside one form. */
.eyebrow,.input-label,.mini-label{display:block;font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:var(--t-chip);font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);margin:0 0 8px}"""))

# E2 - the override that equalled the base.
EDITS.append((
""".balance{padding:4px 0 6px}
.balance .eyebrow{margin-bottom:8px}
""",
""".balance{padding:4px 0 6px}
"""))

# E3 - the field takes the ground of its layer, not the band's fill.
EDITS.append((
"""/* The field takes the smaller corner of the radius scale and the sheet's own
   fill: a 16px radius on a 44px input is a lozenge, and the landing's fields
   read as rectangles inside a pill-shaped page. */
.input{width:100%;background:var(--card2);""",
"""/* The field takes the smaller corner of the radius scale and the ground of the
   layer it sits on, told apart from it by the strong hairline: a 16px radius on a
   44px input is a lozenge, and a field painted in its band's own fill is a field
   whose edges nothing but the border states. */
.input{width:100%;background:var(--bg);"""))

# E4 - the hint is the reading size, and the old label rule is gone into E1.
EDITS.append((
""".input-label{font-size:var(--t-meta);color:var(--text3);margin-bottom:6px;display:block;font-weight:500}
.hint{max-width:var(--measure);font-size:var(--t-meta);color:var(--text3);line-height:1.5}""",
"""/* A line that says why a sum was refused is a sentence, not a unit: the landing
   sets `.hint` at the small reading size, and 12px here is what a figure's
   suffix costs. */
.hint{max-width:var(--measure);font-size:var(--t-small);color:var(--text3);line-height:1.5}"""))

# E5 - the button: the landing's gap and horizontal padding.
EDITS.append((
""".btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border-radius:var(--pill);font-weight:500;font-size:var(--t-ui);padding:12px 20px;""",
""".btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border-radius:var(--pill);font-weight:500;font-size:var(--t-lead);padding:12px 22px;"""))

# E6 - a key/value row is a cell of a frame, the landing's `.row`.
EDITS.append((
""".wd-row{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:var(--t-small);color:var(--text3);padding:6px 0}
.wd-row b{color:var(--text);font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-weight:500}""",
"""/* A row of a read is a cell, not a floating pair of words: the landing's `.row`
   is a full-height cell of one frame — the frame paints the hairline, the cell
   paints the padding, the key is the quiet grey and the figure is the mono weight
   set right against it. Four `padding:6px 0` lines stacked in a sheet used to read
   as four separate thoughts where the sheet has one thing to say. */
.wd-row{display:flex;justify-content:space-between;align-items:baseline;gap:14px;font-size:var(--t-ui);color:var(--text3);background:var(--bg);padding:12px 14px}
.wd-row b{color:var(--text);font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-weight:500;text-align:right;overflow-wrap:anywhere}"""))

# E7 - the address plate is one step of fill from the ground under it.
EDITS.append((
"""/* The address is a plate: the money face, the quiet grey, a filled well with a
   hairline, exactly the landing's `.wallet-addr`. A 42-character hex printed flat
   on the card is the one thing a user needs to read twice. */
.deposit-addr{font-size:var(--t-meta);color:var(--text3);word-break:break-all;flex:1;min-width:0;user-select:all;font-family:var(--font-mono);font-variant-numeric:tabular-nums;background:var(--bg);""",
"""/* The address is a plate: the money face, the quiet grey, a filled well with a
   hairline, exactly the landing's `.wallet-addr`. A 42-character hex printed flat
   on the card is the one thing a user needs to read twice. The rule behind the
   fill is one step away from whatever it sits on: the sheet is the canvas, so this
   plate is the surface, and `.sw-addr`, which sits inside a surface card, is the
   canvas. A plate the colour of its container is only a border. */
.deposit-addr{font-size:var(--t-meta);color:var(--text3);word-break:break-all;flex:1;min-width:0;user-select:all;font-family:var(--font-mono);font-variant-numeric:tabular-nums;background:var(--card2);"""))

# E8 - the sheet is the landing's drawer: canvas ground, strong edge.
EDITS.append((
""".modal-sheet{background:var(--card);border-top:1px solid var(--border);""",
""".modal-sheet{background:var(--bg);border-top:1px solid var(--border-strong);"""))

# E9 - an option tile is the landing's `.opt-btn`.
EDITS.append((
""".detail-opt{background:var(--card2);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;display:block;width:100%;text-align:left}""",
""".detail-opt{background:var(--bg);border:1px solid var(--border-strong);border-radius:var(--radius-lg);padding:16px;display:block;width:100%;text-align:left}"""))

EDITS.append((
""".detail-opt.pickable{cursor:pointer;transition:border-color .2s var(--ease),background-color .2s var(--ease)}
.detail-opt.pickable:active{border-color:var(--border-strong);background:var(--card3)}""",
""".detail-opt.pickable{cursor:pointer;transition:border-color .2s var(--ease),background-color .2s var(--ease)}
/* The affordance is the border, as it is on the landing's `.opt-btn`: a pressed
   tile that fills itself grey reads as disabled, which is the one state this
   control never has. */
.detail-opt.pickable:hover,.detail-opt.pickable:active{border-color:var(--text)}"""))

# E10 - a remove control is chrome, so it wears no state colour.
EDITS.append((
""".create-opt-rm{width:32px;height:32px;border-radius:var(--pill);background:var(--danger-bg);display:flex;align-items:center;justify-content:center;color:var(--danger);cursor:pointer;flex-shrink:0}
.create-opt-rm .icon{width:14px;height:14px}""",
"""/* This button deletes a row the user has not finished writing, not money. A red
   disc says "irreversible" in the one language this interface reserves for a
   figure that moved, and every other surface keeps --danger on amounts. */
.create-opt-rm{width:32px;height:32px;border-radius:var(--pill);border:1px solid var(--border-strong);display:flex;align-items:center;justify-content:center;color:var(--text3);cursor:pointer;flex-shrink:0;transition:border-color .2s var(--ease),color .2s var(--ease)}
.create-opt-rm:hover,.create-opt-rm:active{border-color:var(--text);color:var(--text)}
.create-opt-rm .icon{width:14px;height:14px}"""))

# E11 - `.mini-label` is now the E1 rule; only the later gap is its own.
EDITS.append((
"""/* The label above a field inside a sheet speaks with the same mono, tracked
   uppercase voice as every other micro-label on the surface. */
.mini-label{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:var(--t-chip);font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);margin:0 0 6px}
.mini-label.late{margin:12px 0 6px}""",
""".mini-label.late{margin:12px 0 8px}"""))

# E12/E13 - the assistant is prose, and its opening line is a hint.
EDITS.append((
""".ai-chat{max-height:200px;overflow-y:auto;margin-bottom:10px;font-size:var(--t-small);color:var(--text2)}""",
""".ai-chat{max-height:200px;overflow-y:auto;margin-bottom:10px;font-size:var(--t-ui);color:var(--text2)}"""))

EDITS.append((
"""/* The assistant is the one card that renders as an empty box until someone types, so it says what it answers. */
.ai-hint{color:var(--text3);font-size:var(--t-small);margin-bottom:6px}""",
"""/* The assistant is the one card that renders as an empty box until someone types,
   so it says what it answers — in the one hint voice every other sheet uses. */"""))

EDITS.append((
'<p class="ai-hint" data-i18n="ai_hint">',
'<p class="hint" data-i18n="ai_hint">'))

EDITS.append((
"chat.querySelector('.ai-hint')?.remove();",
"chat.querySelector('.hint')?.remove();"))

# E14 - the frame builder, and the three panels that paint rows.
EDITS.append((
"""// One row, one meaning, on every panel that spends: what this account holds.
function availableLine() {
  const micro = balanceMicro();
  const figure = micro === null ? '--' : usdc(micro / 1e6) + ' USDC';
  return `<div class="wd-row"><span>${t('available')}</span><b>${figure}</b></div>`;
}""",
"""// A frame is only ever as good as the cells inside it: a `.list-group` with none
// is one rounded rule, so the group is built from the rows that answered.
function rowFrame(rows) {
  return rows ? `<div class="list-group">${rows}</div>` : '';
}

// One row, one meaning, on every panel that spends: what this account holds.
function availableLine() {
  const micro = balanceMicro();
  const figure = micro === null ? '--' : usdc(micro / 1e6) + ' USDC';
  return rowFrame(`<div class="wd-row"><span>${t('available')}</span><b>${figure}</b></div>`);
}"""))

EDITS.append((
"""    <div class="input-group">
      ${infoRow(t('dep_network'), INFO.chain_name)}
      ${INFO.usdc_address ? infoRow(t('dep_asset'), 'USDC') : ''}
      ${infoRow(t('dep_confirmations'), LIMITS.deposit_confirmations)}
      ${contractRow()}
      ${explorerLink()}
    </div>""",
"""    <div class="gap-top-sm">
      ${rowFrame(infoRow(t('dep_network'), INFO.chain_name)
        + (INFO.usdc_address ? infoRow(t('dep_asset'), 'USDC') : '')
        + infoRow(t('dep_confirmations'), LIMITS.deposit_confirmations))}
    </div>
    ${contractRow()}
    ${explorerLink()}"""))

EDITS.append((
"""function showWithdrawConfirm(d) {
  const tok = esc(d.token);
  openModal(`
    <div class="modal-title">${t('wd_confirm_title')}</div>
    <div class="input-group">
      <div class="wd-row"><span>${t('amount')}</span><b>${esc(d.amount)} USDC</b></div>
      <div class="wd-row"><span>${t('wd_fee')}</span><b>${esc(d.fee)} USDC</b></div>
      <div class="wd-row"><span>${t('wd_total')}</span><b>${esc(d.total)} USDC</b></div>
      <div class="wd-row"><span>${t('wd_after')}</span><b>${esc(d.balance_after)} USDC</b></div>
      <div class="mini-label late">${t('address')}</div>
      <div class="deposit-addr">${esc(d.address)}</div>
    </div>""",
"""function showWithdrawConfirm(d) {
  const tok = esc(d.token);
  // The four figures of one settlement are one read, so they are four cells of one
  // frame, and each goes through the row builder no row is written outside.
  const rows = [[t('amount'), d.amount], [t('wd_fee'), d.fee],
                [t('wd_total'), d.total], [t('wd_after'), d.balance_after]]
    .map(([label, value]) => infoRow(label, `${esc(String(value))} USDC`)).join('');
  openModal(`
    <div class="modal-title">${t('wd_confirm_title')}</div>
    ${rowFrame(rows)}
    <div class="mini-label late">${t('address')}</div>
    <div class="deposit-addr">${esc(d.address)}</div>"""))

for i, (old, new) in enumerate(EDITS, 1):
    n = t.count(old)
    assert n == 1, f"edit #{i} matched {n} times:\n{old[:90]}"
    t = t.replace(old, new)

assert ".ai-hint" not in t, "the dead .ai-hint voice survived"
p.write_bytes(t.encode("utf-8"))
print("applied", len(EDITS), "edits")
