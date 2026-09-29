"""Reporte semanal XAUUSD — genera data.json para el dashboard.

Corre gratis en GitHub Actions. Sin IA ni claves: solo datos públicos y reglas fijas.
- Precios: Stooq (oro spot) con respaldo en Yahoo Finance (futuros GC=F); DXY, bono 10 años y Brent de Yahoo.
- Calendario: feed público semanal de Forex Factory.

Modo semanal (domingo o primera ejecución de la semana): calcula sesgo, niveles, escenarios y recap.
Modo diario (lunes a viernes): actualiza precio, rango de la semana, niveles tocados y calendario.
"""
import csv
import datetime as dt
import io
import json
import os
import time
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Guayaquil")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data.json")
HIST = os.path.join(ROOT, "history")
FIXTURES = os.environ.get("FIXTURES")  # carpeta con datos de prueba (solo para tests locales)
FORCE = os.environ.get("FORCE") == "1"
NOW = dt.datetime.fromisoformat(os.environ["NOW"]).replace(tzinfo=TZ) if os.environ.get("NOW") else dt.datetime.now(TZ)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      "Accept": "*/*"}
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_C = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------- utilidades
def log(*a):
    print(*a, flush=True)


def fetch(url, key):
    if FIXTURES:
        p = os.path.join(FIXTURES, key)
        if not os.path.exists(p):
            raise IOError("sin fixture " + key)
        with open(p, encoding="utf-8") as f:
            return f.read()
    last = None
    for intento in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (intento + 1))
    raise IOError(f"{url}: {last}")


def fmt(x, d=0):
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fecha_larga(d):
    return f"{d.day} de {MESES[d.month - 1]}"


def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


# ---------------------------------------------------------------- precios
def yahoo(symbol, rng="1y"):
    key = "yahoo_" + symbol.replace("=", "").replace("^", "").replace(".", "_") + ".json"
    last = None
    for host in ("query1", "query2"):
        try:
            raw = fetch(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d", key)
            res = json.loads(raw)["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            out = []
            for i, ts in enumerate(res["timestamp"]):
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c):
                    continue
                d = dt.datetime.fromtimestamp(ts, ZoneInfo(res["meta"].get("exchangeTimezoneName", "America/New_York"))).date()
                out.append({"d": d, "o": o, "h": h, "l": l, "c": c})
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last = e
    raise IOError(f"Yahoo {symbol}: {last}")


def stooq_spot():
    raw = fetch("https://stooq.com/q/d/l/?s=xauusd&i=d", "stooq_xauusd.csv")
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows or "Close" not in rows[0]:
        raise IOError("Stooq sin CSV")
    out = [{"d": dt.date.fromisoformat(r["Date"]), "o": float(r["Open"]), "h": float(r["High"]),
            "l": float(r["Low"]), "c": float(r["Close"])} for r in rows[-400:] if r.get("Close")]
    return out


def gold_series():
    try:
        s = stooq_spot()
        if (NOW.date() - s[-1]["d"]).days <= 5 and len(s) > 60:
            return s, "Oro spot (Stooq)"
    except Exception as e:  # noqa: BLE001
        log("Stooq falló:", e)
    return yahoo("GC=F"), "Futuros del oro GC (Yahoo). Suelen cotizar unos dólares por encima del spot de tu bróker"


def safe_yahoo(sym):
    try:
        return yahoo(sym, "6mo")
    except Exception as e:  # noqa: BLE001
        log("Sin datos", sym, e)
        return None


# ---------------------------------------------------------------- semanas
def week_monday(d):
    return d - dt.timedelta(days=d.weekday())


def target_monday():
    d = NOW.date()
    return d + dt.timedelta(days=7 - d.weekday()) if d.weekday() >= 5 else week_monday(d)


def weeks(bars):
    g = {}
    for b in bars:
        g.setdefault(week_monday(b["d"]), []).append(b)
    out = []
    for m in sorted(g):
        bs = g[m]
        out.append({"m": m, "o": bs[0]["o"], "c": bs[-1]["c"], "h": max(b["h"] for b in bs),
                    "l": min(b["l"] for b in bs), "bars": bs})
    return out


def wk_change(bars, monday):
    ws = [w for w in weeks(bars) if w["m"] < monday]
    if len(ws) < 2:
        return None, None
    return ws[-1]["c"], (ws[-1]["c"] / ws[-2]["c"] - 1) * 100


def atr(bars, n=14):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


# ---------------------------------------------------------------- niveles
def levels(bars, monday):
    ref = bars[-1]["c"]
    a = atr(bars)
    ws = [w for w in weeks(bars) if w["m"] < monday]
    ranges = [w["h"] - w["l"] for w in ws[-10:]]
    awr = sum(ranges) / len(ranges)
    cand = []

    def add(p, w, lab):
        cand.append((p, w, lab))

    pw = ws[-1]
    add(pw["h"], 3, "Máximo de la semana anterior")
    add(pw["l"], 3, "Mínimo de la semana anterior")
    add(pw["c"], 1, "Cierre de la semana anterior")
    if len(ws) > 1:
        add(ws[-2]["h"], 2, "Máximo de hace dos semanas")
        add(ws[-2]["l"], 2, "Mínimo de hace dos semanas")
    recent = bars[-90:]
    k = 3
    for i in range(k, len(recent) - k):
        win = recent[i - k:i + k + 1]
        dd = recent[i]["d"]
        tag = f"{dd.day:02d}/{dd.month:02d}"
        if recent[i]["h"] == max(b["h"] for b in win):
            add(recent[i]["h"], 2, f"Máximo de swing del {tag}")
        if recent[i]["l"] == min(b["l"] for b in win):
            add(recent[i]["l"], 2, f"Mínimo de swing del {tag}")
    m22 = bars[-22:]
    add(max(b["h"] for b in m22), 2, "Máximo del último mes")
    add(min(b["l"] for b in m22), 2, "Mínimo del último mes")
    base = int(ref // 50) * 50
    for p in range(base - 600, base + 650, 50):
        add(float(p), 2 if p % 100 == 0 else 1, "Número redondo")

    cand.sort()
    tol = max(8.0, a * 0.15)
    clusters, cur = [], [cand[0]]
    for c in cand[1:]:
        if c[0] - cur[-1][0] <= tol:
            cur.append(c)
        else:
            clusters.append(cur)
            cur = [c]
    clusters.append(cur)

    out = []
    for cl in clusters:
        lo, hi = min(c[0] for c in cl), max(c[0] for c in cl)
        score = sum(c[1] for c in cl) + 0.5 * (len(cl) - 1)
        labs = []
        for c in sorted(cl, key=lambda c: -c[1]):
            if c[2] not in labs:
                labs.append(c[2])
        mid = sum(c[0] * c[1] for c in cl) / sum(c[1] for c in cl)
        if hi - lo < 5:
            lo = hi = round(mid)
        out.append({"lo": round(lo, 1), "hi": round(hi, 1), "mid": round(mid, 1), "score": round(score, 1),
                    "motivo": " · ".join(labs[:3])})

    gap = a * 0.1
    res = [c for c in out if c["lo"] > ref + gap and c["mid"] - ref <= 1.6 * awr]
    sup = [c for c in out if c["hi"] < ref - gap and ref - c["mid"] <= 1.6 * awr]

    def pick(lst, nearest_first):
        if not lst:
            return []
        lst = sorted(lst, key=lambda c: c["mid"], reverse=not nearest_first)
        chosen = [lst[0]]
        for c in sorted(lst[1:], key=lambda c: -c["score"]):
            if len(chosen) >= 3:
                break
            chosen.append(c)
        return sorted(chosen, key=lambda c: c["mid"], reverse=not nearest_first)

    res = pick(res, True)   # de más cercana a más lejana
    sup = pick(sup, False)  # de más cercano a más lejano
    for c in res:
        c["tipo"] = "r"
    for c in sup:
        c["tipo"] = "s"
    for c in res + sup:
        c["alcanzable"] = abs(c["mid"] - ref) <= awr
        c["fuerza"] = 3 if c["score"] >= 6 else 2 if c["score"] >= 4 else 1
    nombres_r = ["Resistencia inmediata", "Resistencia", "Resistencia mayor"]
    nombres_s = ["Soporte inmediato", "Soporte", "Soporte mayor"]
    for i, c in enumerate(res):
        c["tag"] = nombres_r[i]
    for i, c in enumerate(sup):
        c["tag"] = nombres_s[i]
    return res, sup, ref, a, awr


# ---------------------------------------------------------------- sesgo
def bias(gold, dxy, tnx, brent, monday):
    g = [b for b in gold if b["d"] < monday]
    closes = [b["c"] for b in g]
    c, s20, s50 = closes[-1], sma(closes, 20), sma(closes, 50)
    f = []
    f.append({"k": "Precio vs media de 20 días", "v": 1 if c > s20 else -1,
              "d": f"{fmt(c)} frente a {fmt(s20)}"})
    f.append({"k": "Media de 20 vs media de 50", "v": 1 if s20 > s50 else -1,
              "d": "Tendencia de fondo " + ("alcista" if s20 > s50 else "bajista")})
    _, gch = wk_change(g, monday)
    f.append({"k": "Semana anterior del oro", "v": 0 if abs(gch) < 0.5 else (1 if gch > 0 else -1),
              "d": f"{'+' if gch >= 0 else ''}{fmt(gch, 1)} %"})
    macro = {}
    if dxy:
        v, ch = wk_change(dxy, monday)
        macro["dxy"] = {"v": v, "ch": ch}
        f.append({"k": "Dólar (DXY) en la semana", "v": -1 if ch > 0.3 else 1 if ch < -0.3 else 0,
                  "d": f"{fmt(v, 2)} ({'+' if ch >= 0 else ''}{fmt(ch, 2)} %)"})
    else:
        f.append({"k": "Dólar (DXY) en la semana", "v": 0, "d": "Sin datos"})
    if tnx:
        ws = [w for w in weeks(tnx) if w["m"] < monday]
        v, bp = ws[-1]["c"], (ws[-1]["c"] - ws[-2]["c"]) * 100
        macro["tnx"] = {"v": v, "bp": bp}
        f.append({"k": "Bono 10 años EE. UU.", "v": -1 if bp > 5 else 1 if bp < -5 else 0,
                  "d": f"{fmt(v, 2)} % ({'+' if bp >= 0 else ''}{fmt(bp)} pb)"})
    else:
        f.append({"k": "Bono 10 años EE. UU.", "v": 0, "d": "Sin datos"})
    if brent:
        v, ch = wk_change(brent, monday)
        macro["brent"] = {"v": v, "ch": ch}
        f.append({"k": "Petróleo Brent (vía inflación)", "v": -1 if ch > 3 else 1 if ch < -3 else 0,
                  "d": f"{fmt(v, 1)} $ ({'+' if ch >= 0 else ''}{fmt(ch, 1)} %)"})
    else:
        f.append({"k": "Petróleo Brent (vía inflación)", "v": 0, "d": "Sin datos"})
    macro["oro"] = {"v": c, "ch": gch}
    s = sum(x["v"] for x in f)
    if s >= 4:
        t, d, conv = "Alcista", "up", "alta" if s >= 5 else "media"
    elif s >= 2:
        t, d, conv = "Ligeramente alcista", "up", "baja" if s == 2 else "media"
    elif s <= -4:
        t, d, conv = "Bajista", "down", "alta" if s <= -5 else "media"
    elif s <= -2:
        t, d, conv = "Ligeramente bajista", "down", "baja" if s == -2 else "media"
    else:
        t, d, conv = "Neutral", "flat", "baja"
    return {"texto": t, "dir": d, "conviccion": conv, "puntos": s, "max": len(f), "factores": f}, macro


# ---------------------------------------------------------------- calendario
ES = [
    ("ADP Non-Farm Employment Change", "Empleo ADP"),
    ("Non-Farm Employment Change", "Nóminas no agrícolas"),
    ("Unemployment Rate", "Tasa de desempleo"),
    ("Average Hourly Earnings m/m", "Salario por hora m/m"),
    ("Unemployment Claims", "Subsidios por desempleo"),
    ("Core PCE Price Index m/m", "PCE subyacente m/m"),
    ("PCE Price Index m/m", "PCE m/m"),
    ("Core CPI m/m", "IPC subyacente m/m"),
    ("CPI m/m", "IPC m/m"),
    ("CPI y/y", "IPC a/a"),
    ("Core PPI m/m", "IPP subyacente m/m"),
    ("PPI m/m", "IPP m/m"),
    ("ISM Manufacturing PMI", "ISM manufacturero"),
    ("ISM Services PMI", "ISM servicios"),
    ("ISM Manufacturing Prices", "ISM manufacturero: precios"),
    ("ISM Services Prices", "ISM servicios: precios"),
    ("Flash Manufacturing PMI", "PMI manufacturero preliminar"),
    ("Flash Services PMI", "PMI servicios preliminar"),
    ("Final Manufacturing PMI", "PMI manufacturero final"),
    ("Final Services PMI", "PMI servicios final"),
    ("Core Retail Sales m/m", "Ventas minoristas subyacentes m/m"),
    ("Retail Sales m/m", "Ventas minoristas m/m"),
    ("Advance GDP q/q", "PIB avance t/t"),
    ("Prelim GDP q/q", "PIB preliminar t/t"),
    ("Final GDP q/q", "PIB final t/t"),
    ("JOLTS Job Openings", "Vacantes JOLTS"),
    ("CB Consumer Confidence", "Confianza del consumidor (CB)"),
    ("Prelim UoM Consumer Sentiment", "Confianza Michigan (prelim.)"),
    ("Revised UoM Consumer Sentiment", "Confianza Michigan (final)"),
    ("Prelim UoM Inflation Expectations", "Expectativas de inflación Michigan (prelim.)"),
    ("Revised UoM Inflation Expectations", "Expectativas de inflación Michigan (final)"),
    ("Core Durable Goods Orders m/m", "Bienes duraderos subyacentes m/m"),
    ("Durable Goods Orders m/m", "Bienes duraderos m/m"),
    ("Federal Funds Rate", "Decisión de tipos de la Fed"),
    ("FOMC Statement", "Comunicado de la Fed"),
    ("FOMC Press Conference", "Rueda de prensa de la Fed"),
    ("FOMC Meeting Minutes", "Actas de la Fed"),
    ("FOMC Economic Projections", "Proyecciones de la Fed"),
    ("Personal Spending m/m", "Gasto personal m/m"),
    ("Personal Income m/m", "Ingreso personal m/m"),
    ("New Home Sales", "Ventas de viviendas nuevas"),
    ("Pending Home Sales m/m", "Ventas pendientes de viviendas"),
    ("Existing Home Sales", "Ventas de viviendas usadas"),
    ("Building Permits", "Permisos de construcción"),
    ("Housing Starts", "Inicios de viviendas"),
    ("Empire State Manufacturing Index", "Índice Empire State"),
    ("Philly Fed Manufacturing Index", "Índice Fed de Filadelfia"),
    ("Crude Oil Inventories", "Inventarios de crudo"),
    ("Trade Balance", "Balanza comercial"),
    ("Employment Cost Index q/q", "Costes laborales t/t"),
    ("Prelim Nonfarm Productivity q/q", "Productividad t/t"),
    ("Empire State", "Índice Empire State"),
    ("Cash Rate", "Decisión de tipos"),
    ("Main Refinancing Rate", "Decisión de tipos del BCE"),
    ("Official Bank Rate", "Decisión de tipos del BoE"),
    ("BOJ Policy Rate", "Decisión de tipos del BoJ"),
    ("CPI Flash Estimate y/y", "IPC preliminar a/a"),
    ("Bank Holiday", "Festivo"),
]
PAISES = {"USD": "EE. UU.", "EUR": "Eurozona", "GBP": "Reino Unido", "JPY": "Japón", "CNY": "China",
          "AUD": "Australia", "CAD": "Canadá", "CHF": "Suiza", "NZD": "Nueva Zelanda", "All": "Global"}
MALO_SI_SUBE = ["non-farm", "employment change", "hourly earnings", "cpi", "ppi", "pce", "ism", "pmi",
                "retail sales", "gdp", "jolts", "confidence", "sentiment", "inflation expectations", "durable",
                "home sales", "permits", "housing starts", "empire state", "philly fed", "personal spending",
                "personal income", "employment cost", "productivity"]
BUENO_SI_SUBE = ["unemployment rate", "unemployment claims", "jobless claims"]
PRIORIDAD = ["federal funds rate", "non-farm employment change", "cpi", "pce", "unemployment rate",
             "hourly earnings", "ism", "pmi", "retail sales", "gdp", "jolts", "claims"]


def traducir(t):
    for en, es in ES:
        if t.startswith(en) or t == en:
            return es
    if "Speaks" in t or "Testifies" in t:
        quien = t.replace("Speaks", "").replace("Testifies", "").replace("FOMC Member", "").replace("Fed Chair", "Presidente de la Fed").strip()
        return f"Habla {quien}"
    return t


def lectura(title, fc, prev):
    t = title.lower()
    fcs = fc.replace(".", ",") if fc else ""
    if "federal funds rate" in t:
        return (f"Previsión {fcs}. Una subida mayor de lo previsto es muy negativa para el oro; una menor, muy favorable. "
                "Si coincide, manda el tono del comunicado.") if fc else "Manda el tono: duro presiona al oro, prudente lo apoya.", 0
    if "speaks" in t or "testifies" in t or "press conference" in t or "statement" in t or "minutes" in t:
        return "Tono duro (más subidas) presiona al oro. Tono prudente lo apoya.", 0
    if "crude oil inventories" in t:
        return "Caída fuerte de inventarios sube el petróleo y la inflación esperada: ligeramente negativo.", 0
    signo = 0
    if any(k in t for k in BUENO_SI_SUBE):
        signo = 1
    elif any(k in t for k in MALO_SI_SUBE):
        signo = -1
    if signo == 0:
        return "Impacto indirecto. Mirar la reacción del dólar.", 0
    if not fc:
        ref = f"el dato anterior ({prev.replace('.', ',')})" if prev else "lo esperado"
        return (f"Sin previsión publicada. Por encima de {ref}: {'favorable' if signo > 0 else 'negativo'} para el oro. "
                f"Por debajo: {'negativo' if signo > 0 else 'favorable'}."), signo
    if signo < 0:
        return f"Por encima de {fcs}: negativo para el oro. Por debajo: favorable.", signo
    return f"Por encima de {fcs}: favorable para el oro. Por debajo: negativo.", signo


def prioridad(title):
    t = title.lower()
    if "adp" in t:
        return 50
    for i, k in enumerate(PRIORIDAD):
        if k in t:
            return i
    return 99


def calendar(monday):
    raw = fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "ff_thisweek.json")
    evs = json.loads(raw)
    imp_n = {"High": 3, "Medium": 2, "Low": 1, "Holiday": 0}
    days = {monday + dt.timedelta(days=i): [] for i in range(5)}
    for e in evs:
        try:
            when = dt.datetime.fromisoformat(e["date"]).astimezone(TZ)
        except Exception:  # noqa: BLE001
            continue
        d = when.date()
        if d not in days:
            continue
        imp = imp_n.get(e.get("impact"), 1)
        cur = e.get("country", "")
        if imp == 0:
            days[d].append({"hora": "Todo el día", "pais": PAISES.get(cur, cur), "dato": "Festivo en " + PAISES.get(cur, cur),
                            "imp": 0, "prev": "", "ant": "", "lectura": "Menos liquidez: movimientos más irregulares.",
                            "usd": False, "orig": e.get("title", ""), "signo": 0})
            continue
        if not ((cur == "USD" and imp >= 2) or imp == 3):
            continue
        title = e.get("title", "")
        fc, prev = (e.get("forecast") or "").strip(), (e.get("previous") or "").strip()
        if cur == "USD":
            lec, signo = lectura(title, fc, prev)
        else:
            lec, signo = "Contexto global. Afecta al oro sobre todo a través del dólar.", 0
        days[d].append({"hora": when.strftime("%H:%M") if (when.hour, when.minute) != (0, 0) else "Todo el día",
                        "pais": PAISES.get(cur, cur),
                        "dato": traducir(title) if cur == "USD" else f"{traducir(title)} ({PAISES.get(cur, cur)})", "imp": imp,
                        "prev": fc.replace(".", ","), "ant": prev.replace(".", ","), "lectura": lec,
                        "usd": cur == "USD", "orig": title, "signo": signo})
    out = []
    for d, lst in days.items():
        lst.sort(key=lambda x: (x["hora"] == "Todo el día", x["hora"]))
        top = [x for x in lst if x["usd"] and x["imp"] == 3]
        titulo = " y ".join(dict.fromkeys(x["dato"] for x in sorted(top, key=lambda x: prioridad(x["orig"]))[:2])) \
            if top else (" y ".join(x["dato"] for x in lst if x["imp"] == 3)[:80] or
                         ("Datos secundarios" if lst else "Sin datos relevantes"))
        dirs = [x for x in top if x["signo"] != 0 and x["prev"]]
        if dirs:
            mejor = "; ".join(f"{x['dato']} {'por encima' if x['signo'] > 0 else 'por debajo'} de {x['prev']}" for x in dirs[:3])
            peor = "; ".join(f"{x['dato']} {'por debajo' if x['signo'] > 0 else 'por encima'} de {x['prev']}" for x in dirs[:3])
        else:
            mejor = "Dólar y rendimientos a la baja, petróleo cediendo."
            peor = "Dólar y rendimientos al alza, petróleo subiendo."
        regla = ""
        if len(top) >= 2:
            p = sorted(top, key=lambda x: prioridad(x["orig"]))[0]
            regla = f"Si salen mixtos, manda {p['dato']}. Después, confirma con la reacción del dólar y del bono a 10 años."
        elif not top:
            regla = "Día de contexto: el precio lo mueven el dólar, los rendimientos, el petróleo y los titulares."
        for x in lst:
            x.pop("orig", None)
        out.append({"fecha": d.isoformat(), "dia": f"{DIAS[d.weekday()]} {d.day}", "corto": DIAS_C[d.weekday()],
                    "imp": max([x["imp"] for x in lst], default=0), "titulo": titulo,
                    "eventos": lst, "mejor": mejor, "peor": peor, "regla": regla})
    return out


# ---------------------------------------------------------------- recap
def recap(prev, gold, prev_monday):
    bars = [b for b in gold if prev_monday <= b["d"] < prev_monday + dt.timedelta(days=7)]
    if not bars:
        return None
    before = [b for b in gold if b["d"] < prev_monday]
    pc = before[-1]["c"] if before else bars[0]["o"]
    items, last = [], pc
    for b in bars:
        ch = (b["c"] / last - 1) * 100
        items.append({"d": f"{DIAS_C[b['d'].weekday()]} {b['d'].day}",
                      "t": f"Cierre {fmt(b['c'], 1)} ({'+' if ch >= 0 else ''}{fmt(ch, 2)} %). Rango {fmt(b['l'])} – {fmt(b['h'])}."})
        last = b["c"]
    h, l, c = max(b["h"] for b in bars), min(b["l"] for b in bars), bars[-1]["c"]
    ch = (c / pc - 1) * 100
    r = {"items": items, "cierre": c, "max": h, "min": l, "cambio": ch, "veredicto": "", "niveles": []}
    if not prev:
        r["veredicto"] = "Primera semana del sistema: no hay un reporte anterior que evaluar."
        return r
    d = prev.get("sesgo", {}).get("dir")
    if d == "down":
        ok = ch < -0.3
    elif d == "up":
        ok = ch > 0.3
    else:
        ok = abs(ch) <= 1.0
    tocados = []
    for n in prev.get("niveles", []):
        if (n["tipo"] == "r" and h >= n["lo"]) or (n["tipo"] == "s" and l <= n["hi"]):
            tocados.append(n)
            r["niveles"].append({"txt": rango(n), "tipo": n["tipo"], "tocado": True})
        else:
            r["niveles"].append({"txt": rango(n), "tipo": n["tipo"], "tocado": False})
    txt = (f"El sesgo anterior era {prev['sesgo']['texto'].lower()}. La semana cerró "
           f"{'+' if ch >= 0 else ''}{fmt(ch, 2)} %, así que el sesgo {'acertó' if ok else 'no acertó'} en dirección. ")
    if tocados:
        txt += "Niveles alcanzados: " + ", ".join(rango(n) for n in tocados) + "."
    else:
        txt += "No se alcanzó ninguno de los niveles marcados."
    r["veredicto"] = txt
    r["acierto"] = ok
    return r


def rango(n):
    return fmt(n["lo"]) if n["lo"] == n["hi"] else f"{fmt(n['lo'])} – {fmt(n['hi'])}"


# ---------------------------------------------------------------- vivo (diario)
def vivo(gold, monday, niveles):
    wk = [b for b in gold if b["d"] >= monday]
    last = gold[-1]
    v = {"precio": last["c"], "fecha": last["d"].isoformat(), "semana_max": None, "semana_min": None}
    if wk:
        v["semana_max"] = max(b["h"] for b in wk)
        v["semana_min"] = min(b["l"] for b in wk)
        v["semana_apertura"] = wk[0]["o"]
    for n in niveles:
        n["tocado"] = bool(wk) and ((n["tipo"] == "r" and v["semana_max"] >= n["lo"]) or
                                   (n["tipo"] == "s" and v["semana_min"] <= n["hi"]))
    return v


# ---------------------------------------------------------------- main
def main():
    monday = target_monday()
    prev = None
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            prev = json.load(f)
    weekly = FORCE or not prev or prev.get("semana", {}).get("lunes") != monday.isoformat()

    gold, fuente = gold_series()
    log("Oro:", fuente, gold[-1])

    try:
        cal = calendar(monday)
        cal_ok = True
    except Exception as e:  # noqa: BLE001
        log("Calendario falló:", e)
        cal = prev.get("calendario") if (prev and not weekly) else []
        cal_ok = False

    if weekly:
        if prev and prev.get("semana", {}).get("lunes") and prev["semana"]["lunes"] != monday.isoformat():
            os.makedirs(HIST, exist_ok=True)
            with open(os.path.join(HIST, prev["semana"]["lunes"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False, indent=1)
        base = [b for b in gold if b["d"] < monday]
        dxy, tnx, brent = safe_yahoo("DX-Y.NYB"), safe_yahoo("^TNX"), safe_yahoo("BZ=F")
        res, sup, ref, a, awr = levels(base, monday)
        sesgo, macro = bias(gold, dxy, tnx, brent, monday)
        prev_monday = monday - dt.timedelta(days=7)
        prev_lunes = prev.get("semana", {}).get("lunes") if prev else None
        if prev_lunes == prev_monday.isoformat():
            prev_rep = prev
        elif prev_lunes == monday.isoformat():
            prev_rep = prev.get("_prev_report")  # re-ejecución forzada de la misma semana
        else:
            prev_rep = None
        rc = recap(prev_rep, gold, prev_monday)
        viernes = monday + dt.timedelta(days=4)
        data = {
            "version": 1,
            "semana": {"lunes": monday.isoformat(), "viernes": viernes.isoformat(),
                       "texto": f"{fecha_larga(monday)} – {fecha_larga(viernes)} de {viernes.year}"},
            "fuente": fuente,
            "ref": ref, "atr": a, "rango_semanal": awr,
            "sesgo": sesgo, "macro": macro,
            "niveles": res[::-1] + sup,  # de mayor a menor precio
            "recap": rc,
            "_prev_report": {k: prev_rep[k] for k in ("semana", "sesgo", "niveles")} if prev_rep else None,
        }
    else:
        data = prev

    data["calendario"] = cal
    data["calendario_ok"] = cal_ok
    data["vivo"] = vivo(gold, monday, data["niveles"])
    data["generado"] = NOW.isoformat(timespec="minutes")
    data["modo"] = "semanal" if weekly else "diario"
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    log("OK", data["modo"], data["semana"]["texto"], data["sesgo"]["texto"])


if __name__ == "__main__":
    main()
