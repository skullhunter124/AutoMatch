"""
AutoMatch SI — server.py
────────────────────────
One file does everything:
  • Serves index.html and cars.json
  • Scrapes Avto.net in the background (starts immediately when server starts)
  • Streams live progress to the browser via /api/progress
  • Falls back gracefully if blocked

HOW TO RUN:
    pip install requests beautifulsoup4 flask cloudscraper
    python server.py
    → open http://localhost:5000

The scraper starts AS SOON as you run the server, while you
browse the quiz. By the time you hit "results", data is ready.
"""

import json, os, re, sys, time, threading, subprocess, random
from datetime import datetime, timezone
from urllib.parse import urlencode

# ── dependency check ─────────────────────────────────────────────────────────
missing = []
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    missing.append("requests beautifulsoup4")
try:
    from flask import Flask, Response, jsonify, send_from_directory
except ImportError:
    missing.append("flask")

if missing:
    print("Missing packages. Run:")
    print(f"    pip install {' '.join(missing)} cloudscraper")
    sys.exit(1)

# optional — makes bypassing bot detection much easier
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
MAX_PAGES   = 4
DELAY_SEC   = 2.5
CACHE_HOURS = 6
OUTPUT_FILE = "cars.json"
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))

CATEGORIES = [
    {"type": "city",  "oblika": 3,  "label": "Kombilimuzina / Hatchback"},
    {"type": "sedan", "oblika": 1,  "label": "Limuzina"},
    {"type": "wagon", "oblika": 2,  "label": "Karavan"},
    {"type": "suv",   "oblika": 8,  "label": "SUV"},
]

# Save first-page HTML to disk for debugging the real class names
DEBUG_HTML = True   # flip to False once parser is confirmed working

SI_KEYWORDS = ["slovensko poreklo", "1. lastnik", "uvožen iz slovenije"]

MAINTENANCE = {
    "petrol":   {"insurance": 420, "registration": 110, "service": 280},
    "diesel":   {"insurance": 390, "registration": 130, "service": 310},
    "hybrid":   {"insurance": 440, "registration": 90,  "service": 220},
    "electric": {"insurance": 350, "registration": 60,  "service": 140},
    "lpg":      {"insurance": 380, "registration": 110, "service": 260},
}

MODEL_SPECS = {
    "yaris": (3.94,286), "yaris cross": (4.18,270),
    "polo": (4.05,351),  "fabia": (4.11,380),
    "clio": (4.05,391),  "ibiza": (4.06,355),
    "208":  (4.06,311),  "i20":  (4.04,352),
    "corsa":(4.06,309),  "corolla":(4.37,361),
    "golf": (4.28,380),  "308":  (4.37,412),
    "focus":(4.37,375),  "megane":(4.35,388),
    "civic":(4.55,519),  "astra":(4.37,422),
    "3 series":(4.71,480), "c-class":(4.69,455),
    "a4":  (4.73,460),   "octavia":(4.69,590),
    "passat":(4.77,650), "v60": (4.76,529),
    "308 sw":(4.64,608), "rav4":(4.60,580),
    "kodiaq":(4.70,720), "duster":(4.34,445),
    "puma": (4.19,456),  "tucson":(4.50,539),
    "cx-5": (4.55,506),  "tiguan":(4.49,615),
    "qashqai":(4.43,504),"x1":  (4.50,540),
    "glc":  (4.67,550),  "cr-v":(4.60,589),
    "forester":(4.63,505),"kuga":(4.61,566),
    "sportage":(4.52,503),"t-roc":(4.23,445),
    "2008": (4.30,434),  "3008":(4.45,520),
    "5008": (4.64,702),  "x5":  (4.92,650),
}

# ═══════════════════════════════════════════════════════════════════
# SHARED STATE
# ═══════════════════════════════════════════════════════════════════
_state = {
    "running":  False,
    "done":     False,
    "progress": 0,
    "label":    "Cakam...",
    "log":      [],
    "total":    0,
}
_lock = threading.Lock()


def _log(msg, cls=""):
    with _lock:
        _state["log"].append({"t": msg, "c": cls})
    print(msg)


def _prog(pct, label):
    with _lock:
        _state["progress"] = pct
        _state["label"]    = label


# ═══════════════════════════════════════════════════════════════════
# FETCH  — tries 3 strategies
# ═══════════════════════════════════════════════════════════════════
_cs = None
if HAS_CLOUDSCRAPER:
    try:
        _cs = cloudscraper.create_scraper(
            browser={"browser":"chrome","platform":"windows","mobile":False}
        )
    except Exception:
        pass

_sess = requests.Session()
_sess.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "sl-SI,sl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua":       '"Chromium";v="124","Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Sec-Fetch-User":  "?1",
})


def _warm():
    for client in [_cs, _sess]:
        if client is None:
            continue
        try:
            r = client.get("https://www.avto.net/", timeout=15)
            if r.status_code == 200:
                return True
        except Exception:
            pass
    return False


def fetch(url, referer="https://www.avto.net/"):
    extra = {"Referer": referer}

    # A — cloudscraper
    if _cs:
        try:
            r = _cs.get(url, headers=extra, timeout=20)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
        except Exception:
            pass

    # B — requests session
    try:
        _sess.headers.update(extra)
        r = _sess.get(url, timeout=20)
        if r.status_code == 200 and len(r.text) > 500:
            r.encoding = "utf-8"
            return r.text
        if r.status_code == 403:
            time.sleep(4)
            r = _sess.get(url, timeout=20)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
    except Exception:
        pass

    # C — system curl (uses OS proxy/cert settings — often works through corporate firewalls)
    try:
        result = subprocess.run(
            ["curl","-sL","--max-time","25","--compressed",
             "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0",
             "-H", "Accept: text/html,*/*;q=0.8",
             "-H", "Accept-Language: sl-SI,sl;q=0.9",
             "-H", f"Referer: {referer}",
             "-H", "Connection: keep-alive",
             url],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            errors="replace"
        )
        if result.returncode == 0 and len(result.stdout) > 500:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


# ═══════════════════════════════════════════════════════════════════
# PARSE
# ═══════════════════════════════════════════════════════════════════
def get_specs(model):
    m = model.lower()
    for k, v in MODEL_SPECS.items():
        if k in m:
            return v
    return (4.40, 450)


def detect_fuel(text):
    t = text.lower()
    if "elektri" in t:                    return "electric"
    if "hibrid" in t or "hybrid" in t:    return "hybrid"
    if "diesel" in t or "dizel" in t:     return "diesel"
    if "lpg" in t or "plin" in t:         return "lpg"
    return "petrol"


def build_tags(make, fuel, owners, si, length, trunk):
    tags = []
    if make in ("Toyota","Honda","Mazda","Subaru"):    tags.append("reliable")
    if fuel in ("hybrid","electric"):                  tags.append("eco")
    if fuel == "diesel":                               tags.append("fuel-efficient")
    if owners == 1:                                    tags.append("1. lastnik")
    if si:                                             tags.append("SI poreklo")
    if trunk > 550:                                    tags.append("large-trunk")
    if length < 4.2:                                   tags.append("small")
    if make in ("BMW","Mercedes","Audi","Volvo","Lexus","Porsche"): tags.append("premium")
    return tags


def parse_listing(el, car_type):
    text = el.get_text(" ", strip=True)

    # ── Make / Model — try multiple approaches ──────────────────
    title_el = (
        el.find(class_=re.compile(r"naziv|title|name|make|model|GO-Results-Naziv", re.I))
        or el.find("h3") or el.find("h2") or el.find("h4") or el.find("strong")
    )

    # Fallback: pull title from the <a> href or alt text
    if not title_el:
        link_el2 = el.find("a", href=re.compile(r"/Ads/details\.asp", re.I))
        if link_el2:
            title_el = link_el2   # use the link text itself
        elif el.find("img", alt=re.compile(r"\w+ \w+", re.I)):
            alt = el.find("img", alt=True)["alt"]
            parts = alt.strip().split()
            if len(parts) >= 2:
                make  = parts[0]
                model = " ".join(parts[1:3])
                title_el = None  # skip title parse below
            else:
                return None
        else:
            return None

    if title_el is not None:
        raw = title_el.get_text(strip=True)
        parts = raw.split()
        if len(parts) < 2:
            return None
        make  = parts[0]
        model = " ".join(parts[1:3])

    # Sanity: make should look like a car brand
    if not re.match(r"^[A-ZŠĐŽČĆ][a-zA-ZŠĐŽČĆšđžčć\-]+$", make):
        return None

    # ── Price ──────────────────────────────────────────────────
    price_el = (
        el.find(class_=re.compile(r"cena|price|vrednost|cost", re.I))
        or el.find(attrs={"data-price": True})
    )
    price_text = price_el.get_text() if price_el else text
    pm = re.search(r"(\d[\d\.\s]{2,})\s*€", price_text)
    if not pm:
        # try reversed format: € 12.500
        pm = re.search(r"€\s*(\d[\d\.\s]{2,})", price_text)
    if not pm:
        return None
    try:
        price = int(re.sub(r"[\s\.]", "", pm.group(1)))
    except ValueError:
        return None
    if not (500 < price < 200_000):
        return None

    # ── Year & km ──────────────────────────────────────────────
    ym   = re.search(r"\b(20[01]\d|202[0-4])\b", text)
    year = int(ym.group(1)) if ym else 2018
    km_m = re.search(r"(\d[\d\.]+)\s*km", text, re.I)
    km   = int(re.sub(r"\.", "", km_m.group(1))) if km_m else 0

    # ── Other fields ───────────────────────────────────────────
    fuel   = detect_fuel(text)
    om     = re.search(r"(\d)\.\s*lastni", text, re.I)
    owners = int(om.group(1)) if om else 2
    si     = any(kw in text.lower() for kw in SI_KEYWORDS)

    link_el = el.find("a", href=re.compile(r"/Ads/details\.asp", re.I))
    href    = link_el["href"] if link_el else ""
    link    = href if href.startswith("http") else "https://www.avto.net" + href

    img_el = el.find("img", src=re.compile(r"(avto\.net|\.jpg|\.jpeg|\.png|\.webp)", re.I))
    image  = img_el["src"] if img_el else None

    length, trunk = get_specs(model)
    maint = MAINTENANCE.get(fuel, MAINTENANCE["petrol"]).copy()
    maint["total"] = maint["insurance"] + maint["registration"] + maint["service"]
    tags  = build_tags(make, fuel, owners, si, length, trunk)
    icons = {"city": "🚗", "sedan": "🚘", "wagon": "🚐", "suv": "🛻"}
    img   = "🏎️" if make in ("BMW","Mercedes","Audi","Porsche") else icons.get(car_type, "🚗")
    car_id = re.sub(r"[^a-z0-9\-]", "",
                    re.sub(r"\s+", "-", f"{make}-{model}-{year}-{price}").lower())

    return dict(
        id=car_id, make=make, model=model, year=year, price=price, km=km,
        fuel=fuel, owners=owners, type=car_type, siOrigin=si,
        length=length, trunk=trunk, img=img, image=image,
        tags=tags, link=link, maintenance=maint,
        scrapedAt=datetime.now(timezone.utc).isoformat(),
    )


def parse_html(html, car_type, debug_label=""):
    """
    Try every known Avto.net listing selector.
    If DEBUG_HTML=True, saves HTML snippet so you can inspect real class names.
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── Debug: save raw HTML on first attempt ──────────────────────
    if DEBUG_HTML and debug_label:
        debug_path = os.path.join(BASE_DIR, f"debug_{debug_label}.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html[:80_000])   # first 80k chars is enough
        _log(f"  [debug] HTML saved → {debug_path}", "info")

    # ── Try every selector we know about ──────────────────────────
    # We cast a wide net: avto.net has redesigned multiple times
    els = []

    # Modern avto.net (2023+)
    for sel in [
        {"class": re.compile(r"GO-Results-Row",        re.I)},
        {"class": re.compile(r"oglas-row",             re.I)},
        {"class": re.compile(r"ads-list-item",         re.I)},
        {"class": re.compile(r"result-item",           re.I)},
        {"class": re.compile(r"listing-item",          re.I)},
        {"class": re.compile(r"vehicle-item",          re.I)},
        {"class": re.compile(r"car-item",              re.I)},
        {"class": re.compile(r"ad-item",               re.I)},
        {"class": re.compile(r"ArticleList",           re.I)},
        {"class": re.compile(r"GO-Results",            re.I)},
    ]:
        found = soup.find_all(["article", "div", "li", "section"], **sel)
        if found:
            els = found
            _log(f"  Selector matched: {list(sel.values())[0].pattern} → {len(found)} elements", "info")
            break

    # Nuclear fallback: any <a> that goes to /Ads/details.asp
    # Each such link IS a car listing — walk up to find its container
    if not els:
        links = soup.find_all("a", href=re.compile(r"/Ads/details\.asp", re.I))
        _log(f"  Fallback: found {len(links)} detail links", "info" if links else "err")
        seen_parents = set()
        for lnk in links:
            # Walk up 3 levels to find the listing container
            parent = lnk.parent
            for _ in range(3):
                if parent and id(parent) not in seen_parents:
                    parent = parent.parent
            if parent and id(parent) not in seen_parents:
                seen_parents.add(id(parent))
                els.append(parent)

    if not els:
        # Log what tags/classes DO exist so we can fix the selector
        all_classes = set()
        for tag in soup.find_all(True):
            for c in (tag.get("class") or []):
                if any(kw in c.lower() for kw in ["result","oglas","ad","list","item","car","vehicle","row"]):
                    all_classes.add(c)
        if all_classes:
            _log(f"  Candidate classes in HTML: {', '.join(sorted(all_classes)[:15])}", "warn")
        else:
            _log("  No listing containers found — page may need JS or is blocked", "err")
        return []

    out = []
    for el in els:
        try:
            c = parse_listing(el, car_type)
            if c:
                out.append(c)
        except Exception:
            pass
    return out


def build_url(oblika, page):
    # Use the exact param format from real Avto.net search URLs
    return "https://www.avto.net/Ads/results.asp?" + urlencode({
        "znamka":  "0",
        "model":   "0",
        "modelID": "0",
        "tip":     "0",
        "znamka2": "0",
        "model2":  "0",
        "tip2":    "0",
        "cenaMin": "3000",
        "cenaMax": "99999",
        "leto_od": "2015",
        "leto_do": "",
        "bencin":  "0",
        "kmMin":   "0",
        "kmMax":   "999999",
        "oblika":  str(oblika),
        "Strani":  str(page),
        "PrikazOglasov": "25",
        "order":   "1",
        "redirect": "true",
    })


# ═══════════════════════════════════════════════════════════════════
# SCRAPE THREAD
# ═══════════════════════════════════════════════════════════════════
TOTAL_PAGES = len(CATEGORIES) * MAX_PAGES


def run_scrape():
    with _lock:
        if _state["running"]:
            return
        _state["running"] = True
        _state["done"]    = False

    try:
        _log("AutoMatch SI scraper started", "info")
        _prog(2, "Vzpostavljam sejo...")

        ok = _warm()
        _log("Povezava vzpostavljena" if ok else "Opozorilo: homepage ni dosegljiv",
             "ok" if ok else "warn")

        all_cars   = []
        pages_done = 0
        referer    = "https://www.avto.net/"

        for cat in CATEGORIES:
            _log(f"Kategorija: {cat['label']}", "info")

            for page in range(1, MAX_PAGES + 1):
                url  = build_url(cat["oblika"], page)
                html = fetch(url, referer=referer)
                referer = url

                if html is None:
                    _log(f"  Stran {page} - ni podatkov", "err")
                else:
                    debug_label = f"{cat['type']}_p{page}" if DEBUG_HTML and page == 1 else ""
                    cars = parse_html(html, cat["type"], debug_label)
                    _log(f"  Stran {page}/{MAX_PAGES} - {len(cars)} avtov",
                         "ok" if cars else "warn")
                    all_cars.extend(cars)

                pages_done += 1
                pct = 5 + int(pages_done / TOTAL_PAGES * 90)
                _prog(pct, f"{pages_done}/{TOTAL_PAGES} strani - {len(all_cars)} avtov")
                time.sleep(DELAY_SEC + random.uniform(0, 0.8))

        seen, unique = set(), []
        for c in all_cars:
            if c["id"] not in seen:
                seen.add(c["id"])
                unique.append(c)

        if unique:
            out = {
                "cars":      unique,
                "scrapedAt": datetime.now(timezone.utc).isoformat(),
                "total":     len(unique),
            }
            path = os.path.join(BASE_DIR, OUTPUT_FILE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            _log(f"Shranjenih {len(unique)} avtomobilov", "ok")
            _prog(100, f"{len(unique)} avtomobilov naloženih")
            with _lock:
                _state["total"] = len(unique)
        else:
            _log("Ni podatkov - preverite internet/firewall", "err")
            _prog(100, "Ni podatkov - uporabljam demo")

    except Exception as e:
        _log(f"Napaka: {e}", "err")
        _prog(100, "Napaka pri scrapanju")
    finally:
        with _lock:
            _state["running"] = False
            _state["done"]    = True


def is_fresh():
    path = os.path.join(BASE_DIR, OUTPUT_FILE)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        dt  = datetime.fromisoformat(d["scrapedAt"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return age < CACHE_HOURS
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# FLASK
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__, static_folder=BASE_DIR)


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/cars.json")
def cars_route():
    path = os.path.join(BASE_DIR, OUTPUT_FILE)
    if os.path.exists(path):
        return send_from_directory(BASE_DIR, OUTPUT_FILE)
    return jsonify({"cars": [], "total": 0}), 200


@app.route("/api/debug")
def api_debug():
    """Shows saved debug HTML files and candidate class names — helps fix the parser."""
    import glob
    files = glob.glob(os.path.join(BASE_DIR, "debug_*.html"))
    if not files:
        return "<pre>No debug files yet. Run the scraper first.</pre>"

    report = ["<h2>AutoMatch SI — Debug Report</h2>"]
    for fpath in sorted(files):
        fname = os.path.basename(fpath)
        report.append(f"<h3>{fname}</h3>")
        try:
            with open(fpath, encoding="utf-8") as f:
                html = f.read()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Count detail links
            links = soup.find_all("a", href=re.compile(r"/Ads/details\.asp", re.I))
            report.append(f"<p>Detail links (/Ads/details.asp): <b>{len(links)}</b></p>")

            # Show all classes that look listing-related
            all_classes = {}
            for tag in soup.find_all(True):
                for c in (tag.get("class") or []):
                    all_classes[c] = all_classes.get(c, 0) + 1

            interesting = {c: n for c, n in all_classes.items()
                           if any(kw in c.lower() for kw in
                                  ["result","oglas","ad","list","item","car","vehicle","row","naziv","cena","price"])}
            if interesting:
                rows = "".join(f"<tr><td>{c}</td><td>{n}</td></tr>"
                               for c, n in sorted(interesting.items(), key=lambda x: -x[1]))
                report.append(f"<table border=1 cellpadding=4><tr><th>Class</th><th>Count</th></tr>{rows}</table>")
            else:
                report.append("<p style='color:red'>No relevant classes found — site may require JavaScript</p>")

            # Show first 3 detail link parents
            report.append("<h4>First 3 listing containers (parent of detail link):</h4>")
            for lnk in links[:3]:
                container = lnk.parent
                for _ in range(2):
                    if container:
                        container = container.parent
                if container:
                    snippet = str(container)[:800].replace("<","&lt;").replace(">","&gt;")
                    report.append(f"<pre style='background:#eee;padding:8px;font-size:11px'>{snippet}...</pre>")

        except Exception as e:
            report.append(f"<p>Error reading file: {e}</p>")

    return "\n".join(report)


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "running":  _state["running"],
            "done":     _state["done"],
            "progress": _state["progress"],
            "label":    _state["label"],
            "total":    _state["total"],
            "fresh":    is_fresh(),
        })


@app.route("/api/progress")
def api_progress():
    """SSE endpoint — streams log lines and progress percentages."""
    def stream():
        sent = 0
        while True:
            with _lock:
                logs    = list(_state["log"])
                pct     = _state["progress"]
                label   = _state["label"]
                done    = _state["done"]

            while sent < len(logs):
                entry = logs[sent]
                yield f"data: {json.dumps({'type':'log','t':entry['t'],'c':entry['c']})}\n\n"
                sent += 1

            yield f"data: {json.dumps({'type':'progress','pct':pct,'label':label})}\n\n"

            if done and sent >= len(logs):
                yield f"data: {json.dumps({'type':'done','total':_state['total']})}\n\n"
                break

            time.sleep(0.5)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("  AutoMatch SI")
    print("  ────────────")
    if not HAS_CLOUDSCRAPER:
        print("  TIP: pip install cloudscraper  (helps bypass 403 errors)")
        print()

    if is_fresh():
        print(f"  cars.json is fresh (under {CACHE_HOURS}h old) - skipping scrape")
        with _lock:
            _state["done"]     = True
            _state["progress"] = 100
            _state["label"]    = "Podatki so svezi"
    else:
        print("  Starting background scrape now...")
        print("  Open http://localhost:5000 while it runs\n")
        t = threading.Thread(target=run_scrape, daemon=True)
        t.start()

    print("  http://localhost:5000")
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)