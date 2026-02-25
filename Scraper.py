"""
AutoMatch SI — Avto.net Scraper (Python)
─────────────────────────────────────────
Fetches real listings from avto.net and saves them to cars.json
which the main index.html app reads automatically.

HOW TO RUN:
    # Install dependencies (only needed once):
    pip install requests beautifulsoup4

    # Run the scraper:
    python scraper.py

    # Then open index.html in your browser.

The scraper respects avto.net by:
  - Adding 1.5s delays between requests
  - Only fetching publicly listed search pages
  - Max 5 pages per category
"""

import json
import time
import re
import sys
from datetime import datetime
from urllib.parse import urlencode

# ── Try to import dependencies, guide user if missing ──────────────────────
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌  Missing dependencies. Run this first:")
    print("    pip install requests beautifulsoup4")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
MAX_PAGES   = 5       # pages per category (25 listings each)
DELAY_SEC   = 1.5     # seconds between requests — be polite!
OUTPUT_FILE = "cars.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "sl-SI,sl;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# avto.net oblika (body style) parameter values
CATEGORIES = [
    {"type": "city",   "oblika": 11, "label": "Mali avto"},
    {"type": "sedan",  "oblika": 1,  "label": "Limuzina/Hatchback"},
    {"type": "wagon",  "oblika": 2,  "label": "Karavan"},
    {"type": "suv",    "oblika": 8,  "label": "SUV/Crossover"},
]

SI_KEYWORDS = [
    "slovensko poreklo", "1. lastnik", "uvožen iz slovenije",
    "si registracija", "slovenija"
]

MAINTENANCE = {
    "petrol":   {"insurance": 420, "registration": 110, "service": 280},
    "diesel":   {"insurance": 390, "registration": 130, "service": 310},
    "hybrid":   {"insurance": 440, "registration": 90,  "service": 220},
    "electric": {"insurance": 350, "registration": 60,  "service": 140},
    "lpg":      {"insurance": 380, "registration": 110, "service": 260},
}

# ══════════════════════════════════════════════════════════════════
# MODEL SPECS LOOKUP (length in m, trunk in L)
# Used as fallback when specs aren't in the listing HTML
# ══════════════════════════════════════════════════════════════════
MODEL_SPECS = {
    "yaris":          (3.94, 286),   "polo":           (4.05, 351),
    "fabia":          (4.11, 380),   "clio":           (4.05, 391),
    "ibiza":          (4.06, 355),   "208":            (4.06, 311),
    "i20":            (4.04, 352),   "corsa":          (4.06, 309),
    "corolla":        (4.37, 361),   "golf":           (4.28, 380),
    "308":            (4.37, 412),   "focus":          (4.37, 375),
    "megane":         (4.35, 388),   "civic":          (4.55, 519),
    "astra":          (4.37, 422),   "3 series":       (4.71, 480),
    "c-class":        (4.69, 455),   "a4":             (4.73, 460),
    "octavia":        (4.69, 590),   "passat":         (4.77, 650),
    "v60":            (4.76, 529),   "308 sw":         (4.64, 608),
    "rav4":           (4.60, 580),   "kodiaq":         (4.70, 720),
    "duster":         (4.34, 445),   "puma":           (4.19, 456),
    "tucson":         (4.50, 539),   "cx-5":           (4.55, 506),
    "tiguan":         (4.49, 615),   "qashqai":        (4.43, 504),
    "x1":             (4.50, 540),   "glc":            (4.67, 550),
    "cr-v":           (4.60, 589),   "forester":       (4.63, 505),
    "kuga":           (4.61, 566),   "sportage":       (4.52, 503),
    "t-roc":          (4.23, 445),   "2008":           (4.30, 434),
    "3008":           (4.45, 520),   "5008":           (4.64, 702),
    "touareg":        (4.88, 810),   "x5":             (4.92, 650),
}

def get_specs(model: str) -> tuple:
    """Look up length and trunk size by model name."""
    m = model.lower()
    for key, specs in MODEL_SPECS.items():
        if key in m:
            return specs
    return (4.40, 450)  # sensible default


# ══════════════════════════════════════════════════════════════════
# FETCH
# ══════════════════════════════════════════════════════════════════
def fetch_page(url: str) -> str | None:
    """Fetch a URL and return the HTML, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    except requests.RequestException as e:
        print(f"    ✗ Request failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# PARSE
# ══════════════════════════════════════════════════════════════════
def detect_fuel(text: str) -> str:
    t = text.lower()
    if "elektri" in t:          return "electric"
    if "hibrid" in t or "hybrid" in t: return "hybrid"
    if "diesel" in t or "dizel" in t:  return "diesel"
    if "lpg" in t or "plin" in t:      return "lpg"
    return "petrol"


def detect_si_origin(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SI_KEYWORDS)


def build_tags(make: str, fuel: str, owners: int, si_origin: bool, length: float, trunk: int) -> list:
    tags = []
    if make in ("Toyota", "Honda", "Mazda", "Subaru"):
        tags.append("reliable")
    if fuel in ("hybrid", "electric"):
        tags.append("eco")
    if fuel == "diesel":
        tags.append("fuel-efficient")
    if owners == 1:
        tags.append("1. lastnik")
    if si_origin:
        tags.append("🇸🇮 SI poreklo")
    if trunk > 550:
        tags.append("large-trunk")
    if length < 4.2:
        tags.append("small")
    if make in ("BMW", "Mercedes", "Audi", "Volvo", "Lexus", "Porsche"):
        tags.append("premium")
    return tags


def parse_listing(article, car_type: str) -> dict | None:
    """Parse a single listing <article> tag into a car dict."""
    text = article.get_text(" ", strip=True)

    # ── Title / make / model ──
    title_el = (
        article.find(class_=re.compile(r"GO-Results-Naziv", re.I))
        or article.find("h3")
        or article.find("h2")
    )
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    parts = title.split()
    if len(parts) < 2:
        return None
    make  = parts[0]
    model = " ".join(parts[1:3])

    # ── Price ──
    price_el = article.find(class_=re.compile(r"cena|price", re.I))
    price_text = price_el.get_text(strip=True) if price_el else text
    price_match = re.search(r"(\d[\d\.\s]{2,})\s*€", price_text)
    if not price_match:
        return None
    try:
        price = int(re.sub(r"[\s\.]", "", price_match.group(1)))
    except ValueError:
        return None
    if price < 500 or price > 200_000:
        return None

    # ── Year ──
    year_match = re.search(r"\b(20[01]\d|202[0-4])\b", text)
    year = int(year_match.group(1)) if year_match else 2018

    # ── Kilometres ──
    km_match = re.search(r"(\d[\d\.]+)\s*km", text, re.I)
    km = int(re.sub(r"\.", "", km_match.group(1))) if km_match else 0

    # ── Fuel ──
    fuel = detect_fuel(text)

    # ── Owners ──
    owner_match = re.search(r"(\d)\.\s*lastni", text, re.I)
    owners = int(owner_match.group(1)) if owner_match else 2

    # ── Slovenian origin ──
    si_origin = detect_si_origin(text)

    # ── Link ──
    link_el = article.find("a", href=re.compile(r"/Ads/details\.asp", re.I))
    if link_el:
        href = link_el["href"]
        link = href if href.startswith("http") else "https://www.avto.net" + href
    else:
        link = "https://www.avto.net"

    # ── Image ──
    img_el = article.find("img", src=re.compile(r"\.(jpg|jpeg|png|webp)", re.I))
    image = img_el["src"] if img_el else None

    # ── Specs from model lookup ──
    length, trunk = get_specs(model)

    # ── Maintenance ──
    m = MAINTENANCE.get(fuel, MAINTENANCE["petrol"]).copy()
    m["total"] = m["insurance"] + m["registration"] + m["service"]

    # ── Tags ──
    tags = build_tags(make, fuel, owners, si_origin, length, trunk)

    # ── Emoji icon ──
    icons = {"city": "🚗", "sedan": "🚘", "wagon": "🚐", "suv": "🛻"}
    if make in ("BMW", "Mercedes", "Audi", "Porsche"):
        icon = "🏎️"
    else:
        icon = icons.get(car_type, "🚗")

    car_id = re.sub(r"\s+", "-", f"{make}-{model}-{year}-{price}").lower()
    car_id = re.sub(r"[^a-z0-9\-]", "", car_id)

    return {
        "id":          car_id,
        "make":        make,
        "model":       model,
        "year":        year,
        "price":       price,
        "km":          km,
        "fuel":        fuel,
        "owners":      owners,
        "type":        car_type,
        "siOrigin":    si_origin,
        "length":      length,
        "trunk":       trunk,
        "img":         icon,
        "image":       image,
        "tags":        tags,
        "link":        link,
        "maintenance": m,
        "scrapedAt":   datetime.utcnow().isoformat() + "Z",
    }


def parse_page(html: str, car_type: str) -> list:
    """Parse all listings from a search results page."""
    soup = BeautifulSoup(html, "html.parser")

    # Try article tags first (avto.net uses <article class="GO-Results-Row ...">)
    articles = soup.find_all("article", class_=re.compile(r"GO-Results-Row", re.I))

    # Fallback: any div with that class
    if not articles:
        articles = soup.find_all("div", class_=re.compile(r"GO-Results-Row", re.I))

    cars = []
    for article in articles:
        try:
            car = parse_listing(article, car_type)
            if car:
                cars.append(car)
        except Exception:
            pass  # skip malformed listings silently

    return cars


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def build_url(oblika: int, page: int) -> str:
    params = {
        "znamka":         "",
        "model":          "",
        "tip":            "",
        "cenaMin":        3000,
        "cenaMax":        60000,
        "leto_od":        2015,
        "leto_do":        "",
        "bencin":         0,
        "kmMin":          0,
        "kmMax":          200000,
        "oblika":         oblika,
        "Strani":         page,
        "PrikazOglasov":  25,
        "order":          1,
    }
    return "https://www.avto.net/Ads/results.asp?" + urlencode(params)


def main():
    print("🚗  AutoMatch SI — Python Scraper")
    print("──────────────────────────────────")
    print("⚠️   Check avto.net/robots.txt before use in production.\n")

    all_cars = []

    for cat in CATEGORIES:
        print(f"📂  Category: {cat['label']} (type: {cat['type']})")

        for page in range(1, MAX_PAGES + 1):
            url = build_url(cat["oblika"], page)
            print(f"    Page {page}/{MAX_PAGES} … ", end="", flush=True)

            html = fetch_page(url)
            if html is None:
                print("skipped")
                time.sleep(DELAY_SEC * 2)
                continue

            cars = parse_page(html, cat["type"])
            print(f"✓  {len(cars)} listings")
            all_cars.extend(cars)
            time.sleep(DELAY_SEC)

        print()

    # Deduplicate by id
    seen = set()
    unique = []
    for car in all_cars:
        if car["id"] not in seen:
            seen.add(car["id"])
            unique.append(car)

    print(f"✅  Scraped {len(unique)} unique listings")

    output = {
        "cars":      unique,
        "scrapedAt": datetime.utcnow().isoformat() + "Z",
        "total":     len(unique),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"💾  Saved to {OUTPUT_FILE}")
    print(f"\n🎉  Done! Open index.html in your browser.")


if __name__ == "__main__":
    main()