import pathlib
d = {}
for line in pathlib.Path(".env").read_text(encoding="utf-8-sig").splitlines():
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip()
for k in ("SOLVENCY_ALERT_CHAT_ID", "ADMIN_TG_ID", "OWNER_ID"):
    v = d.get(k)
    state = "KEY ABSENT" if v is None else ("blank" if v == "" else "set, len=" + str(len(v)))
    num = v.strip().isdigit() and int(v.strip()) != 0 if v else False
    print(f"{k:24} {state:16} parses to a nonzero chat id: {num}")
