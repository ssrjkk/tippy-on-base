from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:8000/", wait_until="networkidle")
    pg.wait_for_timeout(1200)
    res = pg.evaluate("""() => {
      const el = document.querySelector('.spot-meta span[data-close-at]');
      if (!el) return {found: false, meta: (document.querySelector('.spot-meta')||{}).textContent};
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {found: true, text: el.textContent, codepoints: [...el.textContent].map(c=>c.codePointAt(0).toString(16)),
              font: cs.fontFamily, transform: cs.textTransform, width: r.width};
    }""")
    print(res)
    w = pg.evaluate("""() => {
      const probe = (t) => { const s = document.createElement('span');
        s.style.cssText='position:absolute;font-family:'+getComputedStyle(document.querySelector('.spot-meta')).fontFamily+';font-size:'+getComputedStyle(document.querySelector('.spot-meta')).fontSize;
        s.textContent=t; document.body.appendChild(s); const w=s.getBoundingClientRect().width; s.remove(); return w; };
      return {plain: probe('14ч'), upper: probe('14Ч'), digit: probe('14'), box: probe('џ')};
    }""")
    print(w)
    b.close()
