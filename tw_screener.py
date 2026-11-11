#!/usr/bin/env python3
"""
台股選股儀表板（上市公司）
  條件 1：季報三率三升（毛利率、營益率、淨利率）＋ 近 N 個月月營收 YoY 成長
  條件 2：EPS 持續成長（近 K 季單季 EPS 皆高於去年同季，且最新一季 > 0）
  條件 3：三大法人低調建倉（近 20 日外資＋投信累計買超佔股本比、買超天數、股價漲幅未大漲）
  條件 4：預估殖利率 > 5%（證交所殖利率 x 近四季EPS / 去年全年EPS；無季報時用證交所殖利率）

資料來源：臺灣證券交易所（www.twse.com.tw、openapi.twse.com.tw）、公開資訊觀測站（mopsov.twse.com.tw）

用法：
  pip install requests pandas lxml
  python tw_screener.py
  python tw_screener.py --days 20 --months 3
  python tw_screener.py --demo

所有下載結果快取在 ./cache，重跑只補抓缺的部分。證交所有流量限制，預設每次請求間隔 3 秒。
"""
from __future__ import annotations
import argparse, datetime as dt, io, json, random, re, sys, time
from pathlib import Path

CACHE = Path("cache")
SLEEP = 3.0
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
MOPS_HOSTS = ["https://mopsov.twse.com.tw", "https://mops.twse.com.tw"]
CODE_RE = re.compile(r"^\d{4}$")

INDUSTRY = {
    "01":"水泥工業","02":"食品工業","03":"塑膠工業","04":"紡織纖維","05":"電機機械",
    "06":"電器電纜","08":"玻璃陶瓷","09":"造紙工業","10":"鋼鐵工業","11":"橡膠工業",
    "12":"汽車工業","14":"建材營造","15":"航運業","16":"觀光餐旅","17":"金融保險",
    "18":"貿易百貨","19":"綜合","20":"其他","21":"化學工業","22":"生技醫療業",
    "23":"油電燃氣業","24":"半導體業","25":"電腦及週邊設備業","26":"光電業",
    "27":"通信網路業","28":"電子零組件業","29":"電子通路業","30":"資訊服務業",
    "31":"其他電子業","35":"綠能環保","36":"數位雲端","37":"運動休閒","38":"居家生活",
}

def log(*a):
    print(*a, file=sys.stderr, flush=True)

_last_req = 0.0

def fetch(url, *, params=None, data=None, encoding="utf-8", key=None, retries=3):
    import requests
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (key or re.sub(r"[^\w.-]+", "_", url + json.dumps(params or data or {}))[:180])
    if path.exists():
        return path.read_text(encoding="utf-8")
    global _last_req
    for i in range(retries):
        wait = SLEEP - (time.time() - _last_req)
        if wait > 0:
            time.sleep(wait)
        _last_req = time.time()
        try:
            if data is not None:
                r = requests.post(url, data=data, headers=UA, timeout=300)
            else:
                r = requests.get(url, params=params, headers=UA, timeout=300)
            if r.status_code == 200 and r.content:
                text = r.content.decode(encoding, errors="replace")
                if "PAGE CAN NOT BE ACCESSED" in text or "查詢過於頻繁" in text:
                    log("  ! 被網站防火牆擋下，稍後再跑")
                    return None
                path.write_text(text, encoding="utf-8")
                return text
            log(f"  HTTP {r.status_code}: {url}")
            if r.status_code in (307, 403):
                log("  ! 被網站防火牆擋下（多半是請求太頻繁或 IP 被擋），稍後再跑；已抓的資料都在快取裡")
                return None
        except Exception as e:
            log(f"  第 {i+1} 次失敗 {url}: {e}")
            time.sleep(SLEEP * (i + 2) * 2)
    return None

def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace(" ", "").strip()
    if s in ("", "--", "-", "N/A", "nan", "None", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None

def norm(s):
    return re.sub(r"\s+", "", str(s))

def read_tables(html):
    import pandas as pd
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return []
    out = []
    for t in tables:
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = [norm(c[-1]) for c in t.columns]
        else:
            t.columns = [norm(c) for c in t.columns]
        out.append(t)
    return out

def pick_col(cols, *must, exclude=()):
    for c in cols:
        if all(m in c for m in must) and not any(e in c for e in exclude):
            return c
    return None

def fetch_company_info():
    txt = fetch("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", key=f"company_{dt.date.today():%Y%m}.json")
    info = {}
    if not txt:
        log("! 無法取得上市公司基本資料（股本、產業別）")
        return info
    for r in json.loads(txt):
        code = str(r.get("公司代號", "")).strip()
        if not CODE_RE.match(code):
            continue
        shares = None
        for k, v in r.items():
            if "已發行普通股數" in k:
                shares = num(v)
        ind = str(r.get("產業別", "")).strip().zfill(2)
        info[code] = {"name": r.get("公司簡稱") or r.get("公司名稱"), "ind": INDUSTRY.get(ind, ind), "shares": shares}
    return info

def fetch_month_revenue(y, m):
    roc = y - 1911
    html = None
    for host in MOPS_HOSTS:
        html = fetch(f"{host}/nas/t21/sii/t21sc03_{roc}_{m}_0.html", encoding="cp950", key=f"rev_{y}{m:02d}.html")
        if html:
            break
    if not html:
        return {}
    out = {}
    for t in read_tables(html):
        cols = list(t.columns)
        c_code = pick_col(cols, "公司代號")
        c_yoy = pick_col(cols, "去年同月增減")
        c_rev = pick_col(cols, "當月營收")
        c_name = pick_col(cols, "公司名稱")
        if not (c_code and c_yoy and c_rev):
            continue
        for _, r in t.iterrows():
            code = str(r[c_code]).strip()
            if CODE_RE.match(code):
                out[code] = {"name": r[c_name] if c_name else None, "rev": num(r[c_rev]), "yoy": num(r[c_yoy])}
    return out

def latest_revenue_month(today):
    y, m = today.year, today.month - (1 if today.day > 10 else 2)
    while m <= 0:
        m += 12
        y -= 1
    return y, m

def fetch_income_cum(y, s):
    roc = y - 1911
    payload = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y", "TYPEK": "sii", "year": roc, "season": f"{s:02d}"}
    html = None
    for host in MOPS_HOSTS:
        html = fetch(f"{host}/mops/web/ajax_t163sb04", data=payload, key=f"is_{y}Q{s}.html")
        if html and "公司代號" in html:
            break
        html = None
    if not html:
        return {}
    out = {}
    for t in read_tables(html):
        cols = list(t.columns)
        c_code = pick_col(cols, "公司代號")
        if not c_code:
            continue
        c_rev = pick_col(cols, "營業收入") or pick_col(cols, "收益") or pick_col(cols, "淨收益")
        c_gp = pick_col(cols, "營業毛利", exclude=("已實現", "未實現", "淨額")) or pick_col(cols, "營業毛利")
        c_op = pick_col(cols, "營業利益")
        c_ni = pick_col(cols, "本期淨利", exclude=("繼續", "停業", "歸屬")) or pick_col(cols, "本期稅後淨利") or pick_col(cols, "淨利", "本期", exclude=("繼續", "停業", "歸屬"))
        c_eps = pick_col(cols, "基本每股盈餘")
        for _, r in t.iterrows():
            code = str(r[c_code]).strip()
            if not CODE_RE.match(code):
                continue
            out[code] = {k: (num(r[c]) if c else None) for k, c in (("rev", c_rev), ("gp", c_gp), ("op", c_op), ("ni", c_ni), ("eps", c_eps))}
    return out

def quarter_deadline(y, s):
    return {1: dt.date(y, 5, 15), 2: dt.date(y, 8, 14), 3: dt.date(y, 11, 14), 4: dt.date(y + 1, 3, 31)}[s]

def latest_quarter(today):
    y, s = today.year, 4
    while quarter_deadline(y, s) >= today:
        s -= 1
        if s == 0:
            y, s = y - 1, 4
    return y, s

def prev_q(y, s):
    return (y, s - 1) if s > 1 else (y - 1, 4)

def build_quarters(today, n=8):
    qs = [latest_quarter(today)]
    while len(qs) < n:
        qs.insert(0, prev_q(*qs[0]))
    years = sorted({y for y, _ in qs})
    cum = {}
    for y in years:
        for s in range(1, 5):
            if (y, s) > qs[-1]:
                break
            log(f"  季報 {y}Q{s}")
            cum[(y, s)] = fetch_income_cum(y, s)
    def single(code, y, s):
        cur = cum.get((y, s), {}).get(code)
        if not cur:
            return None
        if s == 1:
            return cur
        prev = cum.get((y, s - 1), {}).get(code)
        if not prev:
            return None
        return {k: (cur[k] - prev[k]) if cur[k] is not None and prev[k] is not None else None for k in cur}
    codes = set().union(*[set(v) for v in cum.values()]) if cum else set()
    result = {}
    for code in codes:
        gm, om, nm, eps = [], [], [], []
        for y, s in qs:
            d = single(code, y, s)
            rev = d and d["rev"]
            ok = bool(rev) and rev > 0
            def ratio(k, d=d, rev=rev, ok=ok):
                return round(d[k] / rev * 100, 2) if ok and d[k] is not None else None
            gm.append(ratio("gp"))
            om.append(ratio("op"))
            nm.append(ratio("ni"))
            eps.append(round(d["eps"], 2) if d and d["eps"] is not None else None)
        result[code] = {"gm": gm, "om": om, "nm": nm, "eps": eps}
    return [f"{y}Q{s}" for y, s in qs], result

def fetch_t86(day):
    txt = fetch("https://www.twse.com.tw/rwd/zh/fund/T86", params={"date": f"{day:%Y%m%d}", "selectType": "ALLBUT0999", "response": "json"}, key=f"t86_{day:%Y%m%d}.json")
    if not txt:
        return None
    j = json.loads(txt)
    if j.get("stat") != "OK" or not j.get("data"):
        return {}
    f = [norm(x) for x in j["fields"]]
    i_code = f.index("證券代號")
    i_for = next(i for i, c in enumerate(f) if "外陸資買賣超" in c)
    i_tru = next(i for i, c in enumerate(f) if "投信買賣超" in c)
    i_dea = next(i for i, c in enumerate(f) if c.startswith("自營商買賣超股數"))
    out = {}
    for r in j["data"]:
        code = str(r[i_code]).strip()
        if CODE_RE.match(code):
            out[code] = (num(r[i_for]) or 0, num(r[i_tru]) or 0, num(r[i_dea]) or 0)
    return out

def fetch_close(day):
    txt = fetch("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", params={"date": f"{day:%Y%m%d}", "type": "ALLBUT0999", "response": "json"}, key=f"close_{day:%Y%m%d}.json")
    if not txt:
        return {}
    j = json.loads(txt)
    tables = list(j.get("tables") or [])
    for k, v in j.items():
        if k.startswith("fields") and isinstance(v, list):
            tables.append({"fields": v, "data": j.get("data" + k[6:], [])})
    for t in tables:
        f = [norm(x) for x in t.get("fields", [])]
        if "證券代號" in f and "收盤價" in f:
            ic, ip = f.index("證券代號"), f.index("收盤價")
            return {str(r[ic]).strip(): num(r[ip]) for r in t.get("data", []) if CODE_RE.match(str(r[ic]).strip())}
    return {}

def build_institutional(today, n_days=20):
    days, flows = [], []
    d = today
    tries = 0
    while len(days) < n_days and tries < n_days * 2 + 20:
        tries += 1
        if d.weekday() < 5:
            r = fetch_t86(d)
            if r is None:
                log("  法人資料中斷，以已取得的交易日計算")
                break
            if r:
                days.insert(0, d)
                flows.insert(0, r)
                log(f"  法人 {d}")
        d -= dt.timedelta(days=1)
    if not days:
        return None, {}
    p0, p1 = fetch_close(days[0]), fetch_close(days[-1])
    agg = {}
    for code in set().union(*flows):
        f = t = de = 0.0
        buy_days = 0
        for day_flow in flows:
            a, b, c = day_flow.get(code, (0, 0, 0))
            f, t, de = f + a, t + b, de + c
            buy_days += (a + b) > 0
        pchg = None
        if p0.get(code) and p1.get(code):
            pchg = round((p1[code] / p0[code] - 1) * 100, 2)
        agg[code] = {"f": f, "t": t, "d": de, "buy_days": buy_days, "pchg": pchg, "close": p1.get(code)}
    meta = {"start": days[0].isoformat(), "end": days[-1].isoformat(), "days": len(days)}
    return meta, agg

def fetch_yield(today):
    txt = fetch("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", key=f"bwibbu_{today:%Y%m%d}.json")
    out = {}
    for r in json.loads(txt) if txt else []:
        code = str(r.get("Code") or r.get("證券代號") or "").strip()
        if CODE_RE.match(code):
            out[code] = {"y": num(r.get("DividendYield") or r.get("殖利率(%)")), "pe": num(r.get("PEratio") or r.get("本益比")), "pb": num(r.get("PBratio") or r.get("股價淨值比"))}
    return out

def est_yield(twse_y, q, quarters):
    if twse_y is None or not q:
        return None
    e = q["eps"]
    last_year = int(quarters[-1][:4]) - 1
    prev = [e[i] for i, qq in enumerate(quarters) if qq.startswith(str(last_year))]
    ttm = e[-4:]
    if len(prev) != 4 or any(x is None for x in prev + ttm) or sum(prev) <= 0 or sum(ttm) <= 0:
        return None
    return round(twse_y * sum(ttm) / sum(prev), 2)

DEFAULTS = {"rev_n": 3, "rev_min": 0, "margin_base": "qoq", "eps_k": 3, "inst_ratio": 0.3, "inst_days_ratio": 0.55, "inst_pchg": 10, "yield_min": 5, "yield_basis": "est"}

def screen(s, meta, p=DEFAULTS):
    rev = [x for x in s["rev"][-p["rev_n"]:]]
    c1_rev = len(rev) == p["rev_n"] and all(x is not None and x > p["rev_min"] for x in rev)
    q = s.get("q")
    c1_m = c2 = False
    if q:
        off = 1 if p["margin_base"] == "qoq" else 4
        c1_m = all(q[k][-1] is not None and q[k][-1 - off] is not None and q[k][-1] > q[k][-1 - off] for k in ("gm", "om", "nm"))
        e = q["eps"]
        c2 = e[-1] is not None and e[-1] > 0 and all(e[-i] is not None and e[-i - 4] is not None and e[-i] > e[-i - 4] for i in range(1, p["eps_k"] + 1))
    i = s.get("inst")
    c3 = bool(i) and i["ratio"] is not None and (i["f"] + i["t"]) > 0 and i["ratio"] >= p["inst_ratio"] and i["buy_days"] >= round(meta["inst"]["days"] * p["inst_days_ratio"]) and i["pchg"] is not None and i["pchg"] < p["inst_pchg"]
    y = s.get("yld") or {}
    yv = y.get("est") if p["yield_basis"] == "est" and y.get("est") is not None else y.get("twse")
    c4 = yv is not None and yv > p["yield_min"]
    return c1_rev and c1_m, c2, c3, c4

def collect(today, n_months, n_days):
    log("1/5 公司基本資料")
    info = fetch_company_info()
    log("2/5 月營收")
    y, m = latest_revenue_month(today)
    months = []
    for _ in range(max(n_months, 3)):
        months.insert(0, (y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    revs = []
    for y, m in months:
        log(f"  月營收 {y}-{m:02d}")
        revs.append(fetch_month_revenue(y, m))
    while revs and len(revs[-1]) < 100:
        log(f"  {months[-1]} 資料不足，改用前一月")
        months.pop()
        revs.pop()
    log("3/5 季報")
    quarters, qdata = build_quarters(today)
    log("4/5 殖利率")
    ylds = fetch_yield(today)
    log("5/5 三大法人與股價")
    inst_meta, inst = build_institutional(today, n_days)
    codes = set(info) | set(revs[-1] if revs else {}) | set(qdata)
    stocks = []
    for code in sorted(codes):
        base = info.get(code, {})
        name = base.get("name") or next((r[code]["name"] for r in revs if code in r), None)
        if not name:
            continue
        ins = inst.get(code)
        if ins:
            shares = base.get("shares")
            ins = dict(ins, ratio=round((ins["f"] + ins["t"]) / shares * 100, 3) if shares else None)
        stocks.append({"c": code, "n": str(name).strip(), "ind": base.get("ind", ""), "rev": [r.get(code, {}).get("yoy") for r in revs], "q": qdata.get(code), "inst": ins, "yld": {"twse": ylds.get(code, {}).get("y"), "pe": ylds.get(code, {}).get("pe"), "est": est_yield(ylds.get(code, {}).get("y"), qdata.get(code), quarters)}})
    meta = {"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "rev_months": [f"{y}-{m:02d}" for y, m in months], "quarters": quarters, "inst": inst_meta or {"days": 0}, "demo": False, "defaults": DEFAULTS}
    return meta, stocks

def demo_data():
    rnd = random.Random(7)
    meta = {"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "rev_months": ["2026-07", "2026-08", "2026-09"], "quarters": ["2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"], "inst": {"start": "2026-10-02", "end": "2026-10-30", "days": 20}, "demo": True, "defaults": DEFAULTS}
    inds = list(INDUSTRY.values())
    stocks = []
    for k in range(240):
        trend = rnd.uniform(-1, 1.4)
        gm0, om0 = rnd.uniform(10, 50), rnd.uniform(2, 25)
        q = {"gm": [], "om": [], "nm": [], "eps": []}
        e = rnd.uniform(0.2, 4)
        for i in range(8):
            g = trend * i * 0.4 + rnd.gauss(0, 1)
            q["gm"].append(round(gm0 + g, 2))
            q["om"].append(round(om0 + g, 2))
            q["nm"].append(round(om0 * 0.8 + g, 2))
            q["eps"].append(round(e * (1 + trend * 0.08) ** i + rnd.gauss(0, 0.15), 2))
        shares = rnd.uniform(1e8, 3e9)
        buy = rnd.gauss(trend * 0.3, 0.5) / 100 * shares
        stocks.append({"c": str(1101 + k * 37 % 8000), "n": f"示範{k:03d}", "ind": rnd.choice(inds), "rev": [round(rnd.gauss(trend * 12, 15), 2) for _ in range(3)], "q": q, "inst": {"f": buy * 0.7, "t": buy * 0.3, "d": rnd.gauss(0, 1e6), "buy_days": max(0, min(20, int(rnd.gauss(10 + trend * 4, 3)))), "pchg": round(rnd.gauss(4, 9), 2), "close": round(rnd.uniform(15, 900), 1), "ratio": round(buy / shares * 100, 3)}})
        ty = round(max(0, rnd.gauss(3.8, 2)), 2)
        stocks[-1]["yld"] = {"twse": ty, "pe": round(rnd.uniform(8, 30), 2), "est": est_yield(ty, q, meta["quarters"])}
    return meta, stocks

def write_outputs(meta, stocks, out_html, out_csv):
    import csv
    with out_csv.open("w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["代號", "名稱", "產業", *[f"營收YoY {m}" for m in meta["rev_months"]], "毛利率", "營益率", "淨利率", "最新季EPS", "法人買超佔股本%", "買超天數", "期間漲幅%", "殖利率%", "預估殖利率%", "條件1", "條件2", "條件3", "條件4", "符合數"])
        for s in stocks:
            c = screen(s, meta)
            q = s.get("q") or {}
            i = s.get("inst") or {}
            last = lambda k: (q.get(k) or [None])[-1]
            w.writerow([s["c"], s["n"], s["ind"], *s["rev"], last("gm"), last("om"), last("nm"), last("eps"), i.get("ratio"), i.get("buy_days"), i.get("pchg"), (s.get("yld") or {}).get("twse"), (s.get("yld") or {}).get("est"), *map(int, c), sum(c)])
    tpl = (Path(__file__).with_name("dashboard_template.html")).read_text(encoding="utf-8")
    payload = json.dumps({"meta": meta, "stocks": stocks}, ensure_ascii=False, separators=(",", ":"))
    out_html.write_text(tpl.replace("/*__DATA__*/null", payload.replace("</", "<\\/")), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--date")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--csv", default="screen_result.csv")
    ap.add_argument("--json")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    meta, stocks = demo_data() if a.demo else collect(today, a.months, a.days)
    if a.json:
        Path(a.json).write_text(json.dumps({"meta": meta, "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    write_outputs(meta, stocks, Path(a.out), Path(a.csv))
    hits = [s for s in stocks if all(screen(s, meta))]
    log(f"完成：{len(stocks)} 檔，四條件全符合 {len(hits)} 檔 -> {a.out}、{a.csv}")
    for s in hits:
        log(f"  {s['c']} {s['n']}")

if __name__ == "__main__":
    main()
