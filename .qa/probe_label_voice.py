import re, sys
sys.path.insert(0, ".")
from tests.test_design_system import read, rule_selector_and_body

PROPS = ("font-size", "font-family", "letter-spacing", "font-weight", "color",
         "font-variant-numeric", "text-transform")

for path, text in (("style.css", read("style.css")), ("app.html", read("app.html"))):
    print("==", path)
    for sel, body in rule_selector_and_body(text):
        decls = dict(
            (m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip())
            for m in re.finditer(r"([a-z-]+)\s*:\s*([^;{}]+)", body)
        )
        if decls.get("text-transform") != "uppercase" or "letter-spacing" not in decls:
            continue
        print(" ", sel.replace("\n", " ")[:52], "|",
              " ".join(f"{p}={decls.get(p, '-')}" for p in PROPS if p != "text-transform"))
    # every rule whose selector carries a label-role class, uppercase or not
    print(" -- label-role classes, any styling")
    for sel, body in rule_selector_and_body(text):
        names = re.findall(r"\.([a-z][a-z0-9-]*)", sel)
        if not any(n.endswith(("label", "eyebrow", "caption")) or n in ("meta", "foot") for n in names):
            continue
        decls = dict(
            (m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip())
            for m in re.finditer(r"([a-z-]+)\s*:\s*([^;{}]+)", body)
        )
        print(" ", sel.replace("\n", " ")[:52], "|",
              " ".join(f"{p}={decls.get(p, '-')}" for p in PROPS))
