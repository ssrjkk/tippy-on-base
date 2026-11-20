import importlib.util, re
spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec); spec.loader.exec_module(tds)
FRAMES, CELLS = set(tds.frame_classes(tds.CSS)), set(tds.cell_classes(tds.CSS))
ID_OF = re.compile(r"""\$\(\s*['"]#?([\w-]+)|getElementById\(\s*['"]([\w-]+)|querySelector(?:All)?\(\s*['"]#([\w-]+)""")
WRITE = re.compile(r"\.(?:innerHTML|outerHTML)\s*=\s*")

def elements(markup):
    stack = []
    for tag in tds.MARKUP_TAG_RE.finditer(markup):
        closing, name, rest = tag.group(1), tag.group(2).lower(), tag.group(3)
        classes = tds._classes_on(rest)
        m = re.search(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""", rest)
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == name:
                    del stack[i:]
                    break
            continue
        if m:
            yield (m.group(1) or m.group(2), classes,
                   frozenset(c for _n, cs in stack for c in (cs & FRAMES)) | (classes & FRAMES))
        if name not in tds.VOID_TAGS and not rest.rstrip().endswith("/"):
            stack.append((name, classes))

loaders = {p: [s.rsplit('/', 1)[-1] for s in tds.page_scripts(p)] for p in tds.PAGES}
for page in tds.PAGES:
    markup = tds.markup_outside_code(tds.read(page))
    framed_by_id = {i: f for i, _c, f in elements(markup)}
    own_ids = {i: c for i, c, _f in elements(markup)}
    sources = {page: tds.code_only(tds.read(page))}
    for script in loaders[page]:
        sources[script] = tds.read(script)
    for source, code in sources.items():
        for name, body in sorted(tds.js_functions(code).items()):
            for w in WRITE.finditer(body):
                tail = body[w.end():]
                value = tail[:tail.find(";")] if ";" in tail else tail
                cells = tds.writes_a_cell(value, CELLS)
                if not cells:
                    continue
                m = ID_OF.search(body[max(0, w.start() - 90):w.start()])
                target = next((g for g in m.groups() if g), "?") if m else "?"
                framed_value = bool(frozenset(FRAMES) & tds._classes_of_markup(value)) if hasattr(tds, "_classes_of_markup") else bool(tds.frame_classes_in(value)) if hasattr(tds, "frame_classes_in") else any(tds._paints_class(value, f) for f in FRAMES)
                state = framed_value or bool(framed_by_id.get(target))
                print(f"{'ok   ' if state else 'LOOSE'} {page:<14} {source:<10} {name + '()' :<18} #{target:<14} {cells} in-frame-elem={sorted(framed_by_id.get(target, []))} own={sorted(own_ids.get(target, []))}")
