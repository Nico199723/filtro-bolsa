#!/usr/bin/env python3
"""Filtro de compra técnico: SPY, QQQ, VIX, medias móviles, RSI y MACD.
Solo biblioteca estándar. Genera docs/filtro.html y docs/filtro.json
DEMO=1 python filtro.py  -> datos sintéticos, sin red."""
import json, os, random, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape

# ---------- CONFIG (editá acá) ----------
INDICES = ["SPY", "QQQ"]      # definen el semáforo general
ACTIVOS = ["NVDA"]            # activos que querés mirar para comprar (ticker de EE.UU., no el CEDEAR)
MAS = [10, 20, 50, 200]       # medias móviles simples; 10 y 20 son las "cortas"
RSI_D, RSI_W, RSI_M = (14, 70, 30), (21, 60, 40), (21, 75, 25)   # (período, sobrecompra, sobreventa)
MACD_D, MACD_W = (12, 26, 9), (6, 13, 5)
VIX_WARN, VIX_BAD = 20, 30
# ----------------------------------------
DEMO = os.environ.get("DEMO") == "1"
UA = {"User-Agent": "Mozilla/5.0 (filtro-compra)"}


from functools import lru_cache


@lru_cache(maxsize=None)
def yahoo(sym):
    if DEMO:
        r = random.Random(sum(map(ord, sym)))
        px, out, d = 100.0, [], date.today() - timedelta(days=1900)
        while d <= date.today():
            if d.weekday() < 5:
                px *= 1 + r.gauss(0.0004 if sym != "^VIX" else 0, 0.011 if sym != "^VIX" else 0.04)
                out.append((d.isoformat(), 18 + px % 12 if sym == "^VIX" else px))
            d += timedelta(days=1)
        return out
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5y&interval=1d"
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
        res = json.loads(r.read())["chart"]["result"][0]
    out = [(datetime.fromtimestamp(t, timezone.utc).date().isoformat(), float(c))
           for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
    if len(out) < 60:
        raise ValueError("pocos datos")
    return out


def sma(v, n):
    if len(v) < n: raise ValueError(f"faltan datos para SMA {n}")
    return sum(v[-n:]) / n


def ema_series(v, n):
    if len(v) < n: return [None] * len(v)
    k, e = 2 / (n + 1), sum(v[:n]) / n
    out = [None] * (n - 1) + [e]
    for x in v[n:]:
        e = x * k + e * (1 - k); out.append(e)
    return out


def macd(v, f, s, g):
    ef, es = ema_series(v, f), ema_series(v, s)
    m = [a - b for a, b in zip(ef, es) if a is not None and b is not None]
    sg = [x for x in ema_series(m, g) if x is not None]
    if len(sg) < 2: raise ValueError("pocos datos para MACD")
    return dict(m=m[-1], s=sg[-1], h=m[-1] - sg[-1], hp=m[-2] - sg[-2])


def rsi(v, n):
    if len(v) < n + 1: raise ValueError("pocos datos para RSI")
    d = [b - a for a, b in zip(v, v[1:])]
    ag = sum(x for x in d[:n] if x > 0) / n
    al = sum(-x for x in d[:n] if x < 0) / n
    for x in d[n:]:
        ag = (ag * (n - 1) + max(x, 0)) / n
        al = (al * (n - 1) + max(-x, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def resample(series, key):
    out = {}
    for d, c in series:
        out[key(date.fromisoformat(d))] = c
    return list(out.values())


wk = lambda x: x.isocalendar()[:2]
mo = lambda x: (x.year, x.month)


def row(name, desc, value, reading, thr, st):
    return dict(name=name, desc=desc, value=value, reading=reading, thr=thr, st=st)


def rsi_row(tag, closes, cfg):
    n, ob, os_ = cfg
    v = rsi(closes, n)
    if v >= ob: st, rd = "warn", "sobrecompra: no conviene perseguir el precio"
    elif v <= os_: st, rd = "warn", "sobreventa: puede rebotar, pero la tendencia está débil"
    elif v >= 50: st, rd = "ok", "sobre 50: momentum alcista sin exceso"
    else: st, rd = "warn", "bajo 50: momentum débil"
    return row(f"RSI {n} {tag}", f"Fuerza relativa, {tag}", f"{v:.1f}", rd,
               f"{os_}-{ob}: rango normal · ≥{ob} sobrecompra · ≤{os_} sobreventa", st), v


def macd_row(tag, closes, cfg):
    f, s, g = cfg
    x = macd(closes, f, s, g)
    if x["m"] > x["s"]:
        st, rd = ("ok", "alcista y ganando fuerza") if x["h"] > x["hp"] else ("warn", "alcista pero perdiendo fuerza")
    else:
        st, rd = ("bad", "bajista, por debajo de cero") if x["m"] < 0 else ("warn", "bajista (señal por encima del MACD)")
    return row(f"MACD {f}/{s}/{g} {tag}", f"Tendencia y momentum, {tag}",
               f"MACD {x['m']:+.2f} · señal {x['s']:+.2f} · hist {x['h']:+.2f}", rd,
               "MACD sobre su señal y histograma creciendo: a favor", st), x


def analyze(sym):
    s = yahoo(sym)
    c = [x[1] for x in s]
    price, rows = c[-1], []
    facts = dict(price=price, date=s[-1][0])
    for n in MAS:
        try:
            m = sma(c, n)
            short = n <= 20
            st = "ok" if price > m else ("warn" if short else "bad")
            rows.append(row(f"Precio vs SMA {n}", "Media móvil " + ("corta" if short else "larga"),
                            f"{price:.2f} vs {m:.2f} ({(price / m - 1) * 100:+.1f}%)",
                            "precio por encima" if price > m else "precio por debajo",
                            "por encima: a favor · por debajo: " + ("precaución" if short else "en contra"), st))
            facts[f"sma{n}"] = m
        except Exception as e:
            rows.append(row(f"Precio vs SMA {n}", "", "—", str(e), "", "nd"))
    for fn, tag, closes, cfg, key in [(rsi_row, "diario", c, RSI_D, "rsi"), (macd_row, "diario", c, MACD_D, "macd"),
                                      (rsi_row, "semanal", resample(s, wk), RSI_W, "rsi_w"),
                                      (macd_row, "semanal", resample(s, wk), MACD_W, "macd_w"),
                                      (rsi_row, "mensual", resample(s, mo), RSI_M, "rsi_m")]:
        try:
            r, x = fn(tag, closes, cfg)
            rows.append(r); facts[key] = x
        except Exception as e:
            rows.append(row(f"{key.upper()} {tag}", "", "—", str(e), "", "nd"))
    return rows, facts


def verdict(vix, f):
    if vix is None or any(t not in f for t in INDICES):
        return "nd", "SIN DATOS SUFICIENTES", ["Faltan datos de algún índice o del VIX."]
    need = ["sma20", "sma50", "macd", "rsi"]
    if any(k not in f[t] for t in INDICES for k in need):
        return "nd", "SIN DATOS SUFICIENTES", ["Faltan indicadores de algún índice."]
    if vix >= VIX_BAD or all(f[t]["price"] < f[t]["sma50"] for t in INDICES):
        why = [f"VIX en {vix:.1f}"] if vix >= VIX_BAD else [f"{', '.join(INDICES)} por debajo de su SMA 50"]
        return "bad", "CONDICIONES EN CONTRA", why
    falta = []
    if vix >= VIX_WARN: falta.append(f"VIX en {vix:.1f} (verde: menor a {VIX_WARN})")
    for t in INDICES:
        x = f[t]
        if x["price"] <= x["sma20"]: falta.append(f"{t} debajo de su SMA 20")
        if x["price"] <= x["sma50"]: falta.append(f"{t} debajo de su SMA 50")
        if x["macd"]["m"] <= x["macd"]["s"]: falta.append(f"{t}: MACD diario bajista")
        if x["rsi"] >= RSI_D[1]: falta.append(f"{t}: RSI diario en sobrecompra ({x['rsi']:.0f})")
    return ("ok", "CONDICIONES A FAVOR", ["Todos los chequeos del filtro están en verde."]) if not falta \
        else ("warn", "CONDICIONES MIXTAS", falta)


CSS = """
:root{--bg:#070b10;--line:#16202c;--tx:#cfd8e3;--mut:#6b7c8f;--ok:#3ddc97;--warn:#f5a623;--bad:#ff6b6b}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;padding:20px 24px 40px;color:var(--tx);font:12px/1.45 ui-monospace,"SF Mono",Menlo,Consolas,monospace}
a{color:#4aa3ff}h1{font-size:15px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:16px}
h2{font-size:11px;color:#4aa3ff;margin:26px 0 6px;letter-spacing:.06em;text-transform:uppercase}
.ban{border:1px solid var(--line);border-left:4px solid;padding:12px 16px;margin:14px 0}
.ban.ok{border-color:var(--ok)}.ban.warn{border-color:var(--warn)}.ban.bad{border-color:var(--bad)}.ban.nd{border-color:var(--mut)}
.ban b{font-size:16px}.ban ul{margin:6px 0 0;padding-left:18px;color:var(--mut)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.nd{color:var(--mut)}
.row{display:grid;grid-template-columns:1.3fr 1.2fr 1.5fr 1.4fr 92px;gap:18px;align-items:center;padding:10px 0;border-top:1px solid var(--line)}
.n{font-weight:700;color:#e8eef5}.d,.s{color:var(--mut);font-size:10.5px}.v{font-weight:700}
.tag{font-size:10px;padding:3px 7px;border-radius:3px;text-align:center;font-weight:700}
.tag.ok{background:#0f2e24}.tag.warn{background:#33260c}.tag.bad{background:#3a1418}.tag.nd{background:#18212c}
@media(max-width:900px){.row{grid-template-columns:1fr 1fr;gap:6px 14px}}
"""
LBL = {"ok": "A FAVOR", "warn": "PRECAUCIÓN", "bad": "EN CONTRA", "nd": "SIN DATO"}


def block(title, rows):
    h = [f"<h2>{escape(title)}</h2>"]
    for r in rows:
        h.append(f"<div class=row><div><div class=n>{escape(r['name'])}</div><div class=d>{escape(r['desc'])}</div></div>"
                 f"<div class='v {r['st']}'>{escape(r['value'])}</div><div>{escape(r['reading'])}</div>"
                 f"<div class=s>{escape(r['thr'])}</div><div class='tag {r['st']}'>{LBL[r['st']]}</div></div>")
    return "".join(h)


def main():
    stamp = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y %H:%M")
    out, facts, errs = {}, {}, []
    for t in INDICES + ACTIVOS:
        try:
            out[t], facts[t] = analyze(t)
        except Exception as e:
            errs.append(f"{t}: {e}"); print("[aviso]", t, e)
    vix, vrow = None, None
    try:
        s = yahoo("^VIX"); vix = s[-1][1]
        st = "bad" if vix >= VIX_BAD else "warn" if vix >= VIX_WARN else "ok"
        vrow = row("VIX", "Volatilidad implícita del S&P 500", f"{vix:.2f}",
                   {"ok": "calma", "warn": "nerviosismo", "bad": "miedo alto"}[st],
                   f"menor a {VIX_WARN}: a favor · {VIX_WARN}-{VIX_BAD}: precaución · ≥{VIX_BAD}: en contra", st)
    except Exception as e:
        errs.append(f"VIX: {e}"); vrow = row("VIX", "Volatilidad implícita del S&P 500", "—", "sin dato", "", "nd")
    vc, vt, why = verdict(vix, facts)
    h = [f"<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<meta http-equiv=refresh content=3600><title>Filtro de compra</title><style>{CSS}</style>",
         f"<h1>Filtro de compra técnico</h1><div class=sub>Actualizado {stamp} (hora Argentina){' · MODO DEMO' if DEMO else ''}"
         f" · <a href=index.html>Tablero de estrés</a></div>",
         f"<div class='ban {vc}'><b class={vc}>{vt}</b><ul>{''.join(f'<li>{escape(w)}</li>' for w in why)}</ul></div>",
         block("Volatilidad", [vrow])]
    for t in INDICES + ACTIVOS:
        if t in out:
            h.append(block(f"{t} · {facts[t]['price']:.2f} · cierre {facts[t]['date']}"
                           + (" · activo a comprar" if t in ACTIVOS else ""), out[t]))
        else:
            h.append(block(f"{t} · sin dato", []))
    h.append("<p class=sub style='margin-top:28px'>El semáforo general usa solo " + ", ".join(INDICES) + " y el VIX. "
             "Verde: VIX bajo 20, ambos sobre SMA 20 y 50, MACD diario alcista y RSI diario sin sobrecompra. "
             "Rojo: VIX de 30 o más, o ambos bajo la SMA 50. Es una referencia técnica, no una recomendación; "
             "si entrás, hacelo en 2 o 3 tramos.</p></html>")
    os.makedirs("docs", exist_ok=True)
    open("docs/filtro.html", "w", encoding="utf-8").write("".join(h))
    json.dump(dict(actualizado=stamp, semaforo=vc, motivo=why, vix=vix, errores=errs,
                   tickers={t: [dict(n=r["name"], v=r["value"], st=r["st"]) for r in out[t]] for t in out}),
              open("docs/filtro.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("OK:", vt, "| errores:", errs or "ninguno")


if __name__ == "__main__":
    main()
