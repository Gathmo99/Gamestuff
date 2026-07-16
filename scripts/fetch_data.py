"""Fetches Steam sale / Game Pass data and writes static JSON for the docs/ website.
Run headless (e.g. by the GitHub Actions workflow) or manually: `python scripts/fetch_data.py`.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STEAM_CC = "de"
STEAM_LANG = "german"
PAGE_SIZE = 100
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
DISCOUNTS_LIMIT = 500

GAMEPASS_MARKET = "DE"
GAMEPASS_LANGUAGE = "de-de"
GAMEPASS_BATCH_SIZE = 200
GAMEPASS_SIGL_IDS = {
    "PC": "fdd9e2a7-0fee-49f6-ad69-4354098401ff",
    "Xbox": "f6f1f99f-9b49-4ccd-b3bf-4d9767a77f5e",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "docs", "data")
WISHLIST_CONFIG = os.path.join(REPO_ROOT, "wishlist.json")

RE_APPID = re.compile(r'data-ds-appid="(\d+)"')
RE_TITLE = re.compile(r'<span class="title">(.*?)</span>', re.S)
RE_DISCOUNT = re.compile(r'data-discount="(\d+)"')
RE_ORIG_PRICE = re.compile(r'discount_original_price">([^<]*)</div>')
RE_FINAL_PRICE = re.compile(r'discount_final_price">([^<]*)</div>')


def log(message):
    print(message, flush=True)


def parse_price_to_cents(price_str):
    digits = re.sub(r"[^\d,\.]", "", price_str or "")
    if not digits:
        return None
    digits = digits.replace(".", "").replace(",", ".")
    try:
        return round(float(digits) * 100)
    except ValueError:
        return None


def format_cents(cents):
    if cents is None:
        return "?"
    return f"{cents / 100:.2f} €".replace(".", ",")


def http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_search_page(start, sort_by):
    url = (
        "https://store.steampowered.com/search/results/"
        f"?query&start={start}&count={PAGE_SIZE}&dynamic_data="
        f"&sort_by={sort_by}&specials=1&category1=998"
        f"&cc={STEAM_CC}&l={STEAM_LANG}&infinite=1"
    )
    data = http_get_json(url)
    return data.get("results_html", ""), data.get("total_count", 0)


def parse_search_html(page_html):
    items = []
    # Split right before each item's own data-ds-appid attribute so appid stays paired with
    # that item's title/price (the item's own <a href> appid appears in the *previous* chunk).
    for block in re.split(r'(?=data-ds-appid=")', page_html)[1:]:
        appid_m = RE_APPID.search(block)
        title_m = RE_TITLE.search(block)
        discount_m = RE_DISCOUNT.search(block)
        orig_m = RE_ORIG_PRICE.search(block)
        final_m = RE_FINAL_PRICE.search(block)
        if not (appid_m and title_m and discount_m and orig_m and final_m):
            continue
        orig_cents = parse_price_to_cents(orig_m.group(1))
        final_cents = parse_price_to_cents(final_m.group(1))
        if orig_cents is None or final_cents is None:
            continue
        items.append({
            "appid": appid_m.group(1),
            "name": html.unescape(title_m.group(1).strip()),
            "discount": int(discount_m.group(1)),
            "orig_price_str": orig_m.group(1).strip(),
            "final_price_str": final_m.group(1).strip(),
            "orig_cents": orig_cents,
            "final_cents": final_cents,
        })
    return items


def fetch_discounts(limit):
    items = []
    start = 0
    total = 0
    while len(items) < limit:
        page_html, total = fetch_search_page(start, "_ASC")
        page_items = parse_search_html(page_html)
        if not page_items:
            break
        items.extend(page_items)
        start += PAGE_SIZE
        log(f"  Rabatte: {min(len(items), limit)} geladen...")
        if start >= total:
            break
    return items[:limit], total


def fetch_free_games(max_pages=30):
    items = []
    start = 0
    for _ in range(max_pages):
        page_html, _total = fetch_search_page(start, "Price_ASC")
        page_items = parse_search_html(page_html)
        if not page_items:
            break
        reached_end = False
        for it in page_items:
            if it["discount"] >= 100 and it["final_cents"] == 0:
                items.append(it)
            else:
                reached_end = True
                break
        if reached_end:
            break
        start += PAGE_SIZE
    return items


def fetch_appdetails_price(appid):
    url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc={STEAM_CC}&l={STEAM_LANG}&filters=price_overview"
    )
    data = http_get_json(url)
    entry = data.get(str(appid)) or {}
    if not entry.get("success"):
        return None
    entry_data = entry.get("data")
    price = entry_data.get("price_overview") if isinstance(entry_data, dict) else None
    if not price:
        return {
            "discount": 0, "orig_cents": 0, "final_cents": 0,
            "orig_price_str": "Kostenlos/F2P", "final_price_str": "Kostenlos/F2P",
        }
    orig_cents = price.get("initial", price.get("final", 0))
    final_cents = price.get("final", 0)
    return {
        "discount": price.get("discount_percent", 0),
        "orig_cents": orig_cents,
        "final_cents": final_cents,
        "orig_price_str": format_cents(orig_cents),
        "final_price_str": format_cents(final_cents),
    }


def fetch_wishlist():
    if not os.path.exists(WISHLIST_CONFIG):
        return []
    with open(WISHLIST_CONFIG, "r", encoding="utf-8") as f:
        entries = json.load(f)
    items = []
    for entry in entries:
        appid = entry["appid"]
        name = entry["name"]
        try:
            price = fetch_appdetails_price(appid)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log(f"  Wunschliste: Fehler bei {name} ({appid}): {exc}")
            price = None
        if price is None:
            items.append({
                "appid": appid, "name": name,
                "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "?", "final_price_str": "nicht gefunden",
            })
        else:
            items.append({"appid": appid, "name": name, **price})
        time.sleep(0.2)
    return items


def normalize_title(name):
    if not name:
        return ""
    name = re.sub(r"[™®©]", "", name)
    name = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", name)
    name = re.sub(r"(?i)\s*-\s*windows\s*$", "", name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return name.strip()


def fetch_gamepass_ids(sigl_id):
    url = f"https://catalog.gamepass.com/sigls/v2?id={sigl_id}&language={GAMEPASS_LANGUAGE}&market={GAMEPASS_MARKET}"
    try:
        data = http_get_json(url, timeout=15)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return [entry["id"] for entry in data if "id" in entry]


def fetch_gamepass_titles(ids_batch):
    url = (
        "https://displaycatalog.mp.microsoft.com/v7.0/products"
        f"?bigIds={','.join(ids_batch)}&market={GAMEPASS_MARKET}&languages={GAMEPASS_LANGUAGE}"
    )
    data = http_get_json(url, timeout=20)
    titles = []
    for product in data.get("Products", []):
        localized = product.get("LocalizedProperties") or [{}]
        title = localized[0].get("ProductTitle")
        if title:
            titles.append(title)
    return titles


def fetch_gamepass_catalog():
    catalog = {}
    for platform, sigl_id in GAMEPASS_SIGL_IDS.items():
        ids = fetch_gamepass_ids(sigl_id)
        log(f"  Game Pass {platform}: {len(ids)} Spiele im Katalog")
        for start in range(0, len(ids), GAMEPASS_BATCH_SIZE):
            batch = ids[start:start + GAMEPASS_BATCH_SIZE]
            for title in fetch_gamepass_titles(batch):
                norm = normalize_title(title)
                if norm:
                    catalog.setdefault(norm, set()).add(platform)
    return catalog


def gamepass_label(name, catalog):
    platforms = catalog.get(normalize_title(name))
    if not platforms:
        return ""
    if platforms == {"PC"}:
        return "PC"
    if platforms == {"Xbox"}:
        return "Xbox"
    return "PC + Xbox"


def enrich_with_gamepass(items, catalog):
    for item in items:
        item["gamepass"] = gamepass_label(item["name"], catalog)
    return items


def write_json(filename, payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"Geschrieben: {path}")


def main():
    log("Lade Game Pass Katalog...")
    catalog = fetch_gamepass_catalog()

    log("Lade Rabatte...")
    discounts, total_discounts = fetch_discounts(DISCOUNTS_LIMIT)
    enrich_with_gamepass(discounts, catalog)

    log("Lade aktuell kostenlose Spiele...")
    free_games = fetch_free_games()
    enrich_with_gamepass(free_games, catalog)

    log("Lade Wunschliste...")
    wishlist = fetch_wishlist()
    enrich_with_gamepass(wishlist, catalog)

    write_json("discounts.json", {"items": discounts, "total": total_discounts})
    write_json("free.json", {"items": free_games})
    write_json("wishlist.json", {"items": wishlist})
    write_json("meta.json", {"updated": datetime.now(timezone.utc).isoformat()})

    log("Fertig.")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        log(f"Fehler: {exc}")
        sys.exit(1)
