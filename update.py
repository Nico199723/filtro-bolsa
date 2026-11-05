#!/usr/bin/env python3
"""Tablero de estrés de mercado. Solo biblioteca estándar, sin API keys.
Uso: python update.py   (genera docs/index.html y docs/data.json)
     DEMO=1 python update.py   (usa valores de ejemplo, sin red)"""
import csv, io, json, os, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape

DEMO = os.environ.get("DEMO") == "1"
UA = {"User-Agent": "Mozilla/5.0 (tablero-estres)"}


def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fred(series, days=1200):
    start = (date.today() - timedelta(days=days)).isoformat()
    txt = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}")
    out = []
    for row in csv.DictReader(io.StringIO(txt)):
        k = list(row.keys())
        if row[k[1]] not in ("", "."):
            out.append((row[k[0]], float(row[k[1]])))
    if not out:
        raise ValueError(f"FRED {series} sin datos")
    return out


def yahoo(symbol, rng="5y"):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d"
    res = json.loads(get(u))["chart"]["result"][0]
    ind = res["indicators"]
    closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    out = [(datetime.fromtimestamp(t, timezone.utc).date().isoformat(), float(c))
           for t, c in zip(res["timestamp"], closes) if c is not None]
    if not out:
        raise ValueError(f"Yahoo {symbol} sin datos")
    return out


def diff(a, b, scale=1.0):
    bm = dict(b)
    return [(d, (v - bm[d]) * scale) for d, v in a if d in bm]


def scaled(series, k):
    return [(d, v * k) for d, v in series]


def tga():
    try:
        u = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance"
             "?filter=account_type:eq:Treasury%20General%20Account%20(TGA)%20Closing%20Balance"
             "&sort=-record_date&page%5Bsize%5D=10")
        rows = json.loads(get(u))["data"]
        out = []
        for r in rows:
            v = r.get("open_today_bal") or r.get("close_today_bal")
            if v not in (None, "null", ""):
                out.append((r["record_date"], float(v) / 1000))
        if out:
            return sorted(out)
    except Exception:
        pass
    return scaled(fred("WTREGEN"), 1 / 1000)  # respaldo semanal, en miles de millones


def vixeq():
    try:
        rows = list(csv.reader(io.StringIO(get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIXEQ_History.csv"))))
        out = []
        for r in rows[1:]:
            try:
                out.append((datetime.strptime(r[0], "%m/%d/%Y").date().isoformat(), float(r[-1])))
            except Exception:
                continue
        if out:
            return out
    except Exception:
        pass
    return yahoo("^VIXEQ", "1mo")


def rel_return(months):
    def run():
        a, b = yahoo("RSP"), yahoo("SPY")
        bm = dict(b)
        pairs = [(d, v, bm[d]) for d, v in a if d in bm]
        last = pairs[-1]
        target = (date.fromisoformat(last[0]) - timedelta(days=int(months * 30.4375))).isoformat()
        base = next((p for p in reversed(pairs) if p[0] <= target), None)
        if not base:
            raise ValueError("historia insuficiente")
        return [(last[0], ((last[1] / base[1]) - (last[2] / base[2])) * 100)]
    return run


# --- formatos ---
pb = lambda v: f"{v:+.0f} pb"
pct = lambda v: f"{v:.2f}%"
pp = lambda v: f"{v:+.1f} pp"
num = lambda v: f"{v:.2f}"
trill = lambda v: f"${v:.2f}T"
def bill(v):
    if v < 1:
        return f"${v * 1000:.0f}M"
    return f"${v:.2f}B" if v < 10 else f"${v:,.0f}B"

L, C, K, V, A = ("Liquidez y fondeo en dólares (repo)", "Curva de rendimientos", "Crédito",
                 "Volatilidad", "Amplitud de mercado (S&P 500 vs. equal weight)")

IND = [
    dict(g=L, name="SOFR - IORB", desc="Costo del repo overnight vs. la tasa que la Fed paga por reservas",
         src="NY Fed vía FRED", get=lambda: diff(fred("SOFR"), fred("IORB"), 100), fmt=pb,
         normal="-12 a 0 pb: con reservas abundantes el SOFR cotiza por debajo del IORB",
         thr="0 a +10 pb: atención · > +10 pb: estrés", dir="high_bad", warn=0, stress=10, lo=-20, hi=20, demo=7),
    dict(g=L, name="EFFR - IORB", desc="Fed funds efectiva vs. la tasa sobre reservas",
         src="NY Fed vía FRED", get=lambda: diff(fred("EFFR"), fred("IORB"), 100), fmt=pb,
         normal="-10 a -4 pb: acercarse a 0 es señal de escasez",
         thr="-4 a 0 pb: atención · ≥ 0 pb: estrés", dir="high_bad", warn=-4, stress=0, lo=-15, hi=5, demo=-2),
    dict(g=L, name="Reservas bancarias", desc="Dinero de los bancos depositado en la Fed",
         src="Fed H.4.1 vía FRED (semanal)", get=lambda: scaled(fred("WRESBAL"), 1e-6), fmt=trill, stale=12,
         normal="≥ ~$3.0T (no hay piso fijo)", thr="$2.8-3.0T: atención · < $2.8T: estrés",
         dir="low_bad", warn=3.0, stress=2.8, lo=2.4, hi=3.4, demo=2.89),
    dict(g=L, name="TGA (cuenta del Tesoro)", desc="Caja del Tesoro en la Fed; cuando sube, drena reservas",
         src="Treasury (DTS)", get=tga, fmt=bill, normal="≈ $600-900B (referencia aproximada)",
         thr="$900B-1.05T: atención · > $1.05T: estrés", dir="high_bad", warn=900, stress=1050, lo=500, hi=1200, demo=938),
    dict(g=L, name="ON RRP (reverse repo)", desc="Colchón de liquidez que absorbe shocks",
         src="NY Fed vía FRED", get=lambda: fred("RRPONTSYD"), fmt=bill, normal="colchón > ~$50B",
         thr="≈ $0: sin amortiguador (no es estrés por sí solo)", dir="low_bad", warn=50, stress=None, lo=0, hi=200, demo=0.23),
    dict(g=L, name="Uso del Standing Repo Facility", desc="Bancos pidiéndole cash prestado a la Fed",
         src="NY Fed vía FRED", get=lambda: fred("RPONTSYD"), fmt=bill, normal="≈ $0 fuera de cierres de mes/trimestre",
         thr="$1-100B: atención · > $100B: estrés", dir="high_bad", warn=1, stress=100, lo=0, hi=150, demo=0.031),
    dict(g=C, name="Spread 10Y - 2Y", desc="Pendiente de la curva del Tesoro",
         src="FRED (T10Y2Y)", get=lambda: scaled(fred("T10Y2Y"), 100), fmt=pb, normal="+50 a +200 pb",
         thr="0 a +50 pb: atención (plana) · < 0: invertida", dir="low_bad", warn=50, stress=0, lo=-50, hi=250, demo=34),
    dict(g=C, name="Spread 10Y - 3M", desc="Pendiente entre el bono a 10 años y la letra a 3 meses",
         src="FRED (T10Y3M)", get=lambda: scaled(fred("T10Y3M"), 100), fmt=pb, normal="+100 a +250 pb",
         thr="0 a +100 pb: atención (plana) · < 0: invertida (señal de recesión)", dir="low_bad", warn=100, stress=0, lo=-100, hi=300, demo=74),
    dict(g=K, name="Spread High Yield (OAS)", desc="Prima de riesgo de bonos corporativos basura sobre el Tesoro",
         src="ICE BofA vía FRED", get=lambda: fred("BAMLH0A0HYM2"), fmt=pct, normal="≈ 3.0-4.5%",
         thr="4.5-6%: atención · > 6%: estrés (> 8%: crisis)", dir="high_bad", warn=4.5, stress=6, lo=2, hi=9, demo=3.40),
    dict(g=K, name="Spread Investment Grade (OAS)", desc="Prima de riesgo de bonos corporativos grado de inversión",
         src="ICE BofA vía FRED", get=lambda: fred("BAMLC0A0CM"), fmt=pct, normal="≈ 0.8-1.3%",
         thr="1.3-1.8%: atención · > 1.8%: estrés", dir="high_bad", warn=1.3, stress=1.8, lo=0.5, hi=2.5, demo=0.93),
    dict(g=K, name="Spread CCC y menores (OAS)", desc="Prima del tramo más débil del crédito",
         src="ICE BofA vía FRED", get=lambda: fred("BAMLH0A3HYC"), fmt=pct, normal="≈ 6.5-10% (referencia aproximada)",
         thr="10-15%: atención · > 15%: estrés", dir="high_bad", warn=10, stress=15, lo=5, hi=20, demo=13.16),
    dict(g=V, name="VIX", desc="Volatilidad implícita del S&P 500 (30 días)", src="FRED (VIXCLS)",
         get=lambda: fred("VIXCLS"), fmt=num, normal="12-20", thr="20-30: atención · > 30: estrés",
         dir="high_bad", warn=20, stress=30, lo=10, hi=40, demo=20.83),
    dict(g=V, name="MOVE", desc="Volatilidad implícita de los bonos del Tesoro", src="Yahoo Finance (^MOVE)",
         get=lambda: yahoo("^MOVE", "1mo"), fmt=num, normal="≈ 60-100", thr="100-130: atención · > 130: estrés",
         dir="high_bad", warn=100, stress=130, lo=50, hi=150, demo=None),
    dict(g=V, name="VIXEQ (vol. de acciones individuales)", desc="Vol. implícita a 30 días de ~86 acciones líquidas del S&P 500",
         src="Cboe", get=vixeq, fmt=num, normal="≈ 25-35 (promedio largo plazo 31.6)",
         thr="35-45: atención · > 45: estrés", dir="high_bad", warn=35, stress=45, lo=15, hi=55, demo=42.80),
    dict(g=A, name="Equal weight - cap-weight, 12 meses", desc="Rendimiento relativo de RSP vs. SPY (retorno total)",
         src="Yahoo Finance (RSP, SPY)", get=rel_return(12), fmt=pp, normal="-5 a +8 pp: el equal weight acompaña",
         thr="-10 a -5 pp: atención (mercado angosto) · < -10 pp: estrés", dir="low_bad", warn=-5, stress=-10, lo=-15, hi=12, demo=6.5),
    dict(g=A, name="Equal weight - cap-weight, 3 años", desc="Mismo cálculo a 36 meses", src="Yahoo Finance (RSP, SPY)",
         get=rel_return(36), fmt=pp, normal="-9.6 a +30 pp",
         thr="-15 a -9.6 pp: atención (decil más estrecho desde 1929) · < -15 pp: estrés",
         dir="low_bad", warn=-9.6, stress=-15, lo=-20, hi=35, demo=-9.7),
]


def status(v, i):
    w, s = i.get("warn"), i.get("stress")
    if i["dir"] == "high_bad":
        if s is not None and v >= s: return "estres"
        if w is not None and v > w: return "atencion"
    else:
        if s is not None and v <= s: return "estres"
        if w is not None and v < w: return "atencion"
    return "normal"


def gradient(i):
    lo, hi = i["lo"], i["hi"]
    p = lambda x: max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))
    G, A_, R = "#1f7a5c", "#8a6d1f", "#8a3a3a"
    pw = p(i["warn"]); ps = p(i["stress"]) if i.get("stress") is not None else None
    if i["dir"] == "high_bad":
        st = [(G, 0, pw), (A_, pw, ps if ps is not None else 100)] + ([(R, ps, 100)] if ps is not None else [])
    else:
        st = ([(R, 0, ps)] if ps is not None else []) + [(A_, ps or 0, pw), (G, pw, 100)]
    return "linear-gradient(90deg," + ",".join(f"{c} {a:.1f}%,{c} {b:.1f}%" for c, a, b in st) + ")"


def collect():
    out = []
    for i in IND:
        r = dict(i=i, value=None, date=None, status="sin_dato", err=None)
        try:
            if DEMO:
                if i["demo"] is None: raise ValueError("sin dato")
                d, v = date.today().isoformat(), i["demo"]
            else:
                d, v = i["get"]()[-1]
            r.update(value=v, date=d, status=status(v, i))
        except Exception as e:
            r["err"] = f"{type(e).__name__}: {e}"
            print(f"[aviso] {i['name']}: {r['err']}")
        out.append(r)
    return out


CSS = """
:root{--bg:#070b10;--line:#16202c;--tx:#cfd8e3;--mut:#6b7c8f;--ok:#3ddc97;--warn:#f5a623;--bad:#ff6b6b}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;padding:20px 24px 40px;color:var(--tx);font:12px/1.45 ui-monospace,"SF Mono",Menlo,Consolas,monospace}
h1{font-size:15px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:16px}
.sum b{margin-right:14px}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.nd{color:var(--mut)}
h2{font-size:11px;color:#4aa3ff;margin:26px 0 6px;letter-spacing:.06em;text-transform:uppercase}
.row{display:grid;grid-template-columns:1.25fr .8fr 1.5fr 1.1fr 1.1fr 78px;gap:18px;align-items:center;
 padding:12px 0;border-top:1px solid var(--line)}
.n{font-weight:700;color:#e8eef5}.d,.s,.nm{color:var(--mut);font-size:10.5px}
.v{font-size:19px;font-weight:700}.v.ok{color:var(--ok)}.v.warn{color:var(--warn)}.v.bad{color:var(--bad)}.v.nd{color:var(--mut)}
.stale{color:var(--warn);font-size:10.5px}.nm b{color:var(--ok);font-weight:700}
.bar{position:relative;height:6px;border-radius:3px}.bar i{position:absolute;top:-4px;width:2px;height:14px;background:#fff}
.tag{font-size:10px;padding:3px 7px;border-radius:3px;text-align:center;font-weight:700}
.tag.ok{background:#0f2e24}.tag.warn{background:#33260c}.tag.bad{background:#3a1418}.tag.nd{background:#18212c}
@media(max-width:900px){.row{grid-template-columns:1fr 1fr;gap:8px 14px}.row .bar{grid-column:1/-1}}
"""
CLS = {"normal": ("ok", "NORMAL"), "atencion": ("warn", "ATENCIÓN"), "estres": ("bad", "ESTRÉS"), "sin_dato": ("nd", "SIN DATO")}


def render(res, stamp):
    cnt = {k: sum(r["status"] == k for r in res) for k in CLS}
    h = [f"<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<meta http-equiv=refresh content=3600><title>Tablero de estrés de mercado</title><style>{CSS}</style>",
         "<h1>Tablero de estrés de mercado</h1>",
         f"<div class=sub>Actualizado {escape(stamp)} (hora Argentina){' · MODO DEMO' if DEMO else ''}</div>",
         f"<div class=sum><b class=bad>{cnt['estres']} en estrés</b><b class=warn>{cnt['atencion']} en atención</b>"
         f"<b class=ok>{cnt['normal']} normales</b><b class=nd>{cnt['sin_dato']} sin dato</b></div>"]
    cur = None
    for r in res:
        i = r["i"]
        if i["g"] != cur:
            cur = i["g"]; h.append(f"<h2>{escape(cur)}</h2>")
        c, label = CLS[r["status"]]
        if r["value"] is None:
            val, meta, pos = "—", f"<div class=d>sin dato · {escape(i['src'])}</div>", None
        else:
            age = (date.today() - date.fromisoformat(r["date"])).days
            stale = f"<div class=stale>dato de hace {age} días</div>" if age > i.get("stale", 5) else ""
            val, meta = i["fmt"](r["value"]), f"<div class=d>{r['date']} · {escape(i['src'])}</div>{stale}"
            pos = max(0, min(100, (r["value"] - i["lo"]) / (i["hi"] - i["lo"]) * 100))
        bar = (f"<div class=bar style='background:{gradient(i)}'><i style='left:calc({pos:.1f}% - 1px)'></i></div>"
               if pos is not None else "<div></div>")
        h.append(f"<div class=row><div><div class=n>{escape(i['name'])}</div><div class=d>{escape(i['desc'])}</div></div>"
                 f"<div><div class='v {c}'>{escape(val)}</div>{meta}</div>"
                 f"<div class=nm><b>Normal:</b> {escape(i['normal'])}</div><div class=s>{escape(i['thr'])}</div>"
                 f"{bar}<div class='tag {c}'>{label}</div></div>")
    h.append("<p class=sub style='margin-top:28px'>Los umbrales son referencias aproximadas, no señales de trading. "
             "Editalos en la lista IND de update.py.</p></html>")
    return "".join(h)


def main():
    res = collect()
    art = timezone(timedelta(hours=-3))
    stamp = datetime.now(art).strftime("%d/%m/%Y %H:%M")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(render(res, stamp))
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump({"actualizado": stamp, "indicadores": [
            {"nombre": r["i"]["name"], "valor": r["value"], "fecha": r["date"], "estado": r["status"], "error": r["err"]}
            for r in res]}, f, ensure_ascii=False, indent=1)
    print("OK:", ", ".join(f"{k}={sum(r['status'] == k for r in res)}" for k in CLS))


if __name__ == "__main__":
    main()
