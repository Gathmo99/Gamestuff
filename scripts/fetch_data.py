"""Fetches Steam sale / Game Pass data and writes static JSON for the docs/ website.
Run headless (e.g. by the GitHub Actions workflow) or manually: `python scripts/fetch_data.py`.
"""

import html
import json
import os
import re
import sys
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

XBOX_DEALS_URL = "https://www.xbox.com/de-DE/games/browse/game-deals"
XBOX_FREE_PLAY_DAYS_URL = "https://www.xbox.com/de-DE/promotions/sales/free-play-days"
XBOX_DEALS_CHANNEL_KEY = "BROWSE_CHANNELID=GAME-DEALS_FILTERS="

PS_GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
PS_LOCALE = "de-de"
PS_SHA256 = {
    "categoryGridRetrieve": "4ce7d410a4db2c8b635a48c1dcec375906ff63b19dadd87e073f8fd0c0481d35",
    "metGetProductById": "a128042177bd93dd831164103d53b73ef790d56f51dae647064cb8f9d9fc9d1a",
    "metGetPricingDataByConceptId": "abcb311ea830e679fe2b697a27f755764535d825b24510ab1239a4ca3092bd09",
}
PS_CATEGORY_SALES = "803cee19-e5a1-4d59-a463-0b6b2701bf7c"
PS_CATEGORY_PS_PLUS = "038b4df3-bb4c-48f8-8290-3feb35f0f0fd"
PS_DISCOUNTS_LIMIT = 300

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "docs", "data")
PS_WISHLIST_CONFIG = os.path.join(REPO_ROOT, "ps-wishlist.json")

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


def http_get_json(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


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


# ---------- Xbox (scrapes the embedded __PRELOADED_STATE__ JSON of xbox.com pages) ----------

def fetch_xbox_state(url):
    try:
        page_html = http_get_text(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Xbox 404s pages like Free Play Days entirely when no promotion is currently
            # running, rather than rendering an empty page - that's a normal state, not an error.
            return None
        raise
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});", page_html, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


def parse_xbox_channel(state, channel_key):
    items = []
    channel = state["core2"]["channels"]["channelData"].get(channel_key)
    if not channel:
        return items
    availability = state["core2"]["products"]["availabilitySummaries"]
    summaries = state["core2"]["products"]["productSummaries"]
    for entry in channel["data"]["products"]:
        product_id = entry["productId"]
        title = (summaries.get(product_id) or {}).get("title")
        price = None
        for sku_entries in (availability.get(product_id) or {}).values():
            for avail_entry in sku_entries.values():
                price = avail_entry.get("price")
                if price:
                    break
            if price:
                break
        if not title or not price:
            continue
        discount = round(price.get("discountPercentage") or 0)
        if discount <= 0:
            continue
        final_cents = round((price.get("listPrice") or 0) * 100)
        orig_cents = round((price.get("msrp") or price.get("listPrice") or 0) * 100)
        items.append({
            "appid": product_id,
            "name": title,
            "discount": discount,
            "orig_cents": orig_cents,
            "final_cents": final_cents,
            "orig_price_str": format_cents(orig_cents),
            "final_price_str": format_cents(final_cents),
        })
    items.sort(key=lambda x: -x["discount"])
    return items


def fetch_xbox_deals():
    state = fetch_xbox_state(XBOX_DEALS_URL)
    if not state:
        return [], 0
    items = parse_xbox_channel(state, XBOX_DEALS_CHANNEL_KEY)
    total = state["core2"]["channels"]["channelData"].get(XBOX_DEALS_CHANNEL_KEY, {}) \
        .get("data", {}).get("totalItems", len(items))
    return items, total


def fetch_xbox_free_play_days():
    state = fetch_xbox_state(XBOX_FREE_PLAY_DAYS_URL)
    if not state:
        return []
    items = []
    for channel_key in state["core2"]["channels"]["channelData"]:
        items.extend(parse_xbox_channel(state, channel_key))
    return items


# ---------- PlayStation (Sony's storefront GraphQL - server-side only, Sony blocks
# browser/proxy requests from other origins with bot detection) ----------

def ps_graphql(operation_name, variables):
    url = (
        f"{PS_GRAPHQL_URL}?operationName={operation_name}"
        f"&variables={urllib.parse.quote(json.dumps(variables))}"
        f"&extensions={urllib.parse.quote(json.dumps({'persistedQuery': {'version': 1, 'sha256Hash': PS_SHA256[operation_name]}}))}"
    )
    return http_get_json(url, headers={
        "Content-Type": "application/json",
        "apollo-require-preflight": "true",
        "x-psn-store-locale-override": PS_LOCALE,
    })


def fetch_ps_category(category_id, limit):
    items = []
    total = 0
    offset = 0
    page_size = 24
    while len(items) < limit:
        data = ps_graphql("categoryGridRetrieve", {
            "id": category_id,
            "pageArgs": {"size": page_size, "offset": offset},
            "sortBy": {"name": "productReleaseDate", "isAscending": False},
            "filterBy": [],
            "facetOptions": [],
        })
        grid = data.get("data", {}).get("categoryGridRetrieve") or {}
        products = grid.get("products") or []
        total = (grid.get("pageInfo") or {}).get("totalCount", total)
        if not products:
            break
        for p in products:
            price = p.get("price") or {}
            base_cents = parse_price_to_cents(price.get("basePrice"))
            final_cents = parse_price_to_cents(price.get("discountedPrice"))
            if base_cents is None or final_cents is None or base_cents == final_cents:
                continue
            discount = round((base_cents - final_cents) / base_cents * 100)
            items.append({
                "appid": p["id"],
                "name": p["name"],
                "discount": discount,
                "orig_cents": base_cents,
                "final_cents": final_cents,
                "orig_price_str": format_cents(base_cents),
                "final_price_str": format_cents(final_cents),
            })
        offset += page_size
        log(f"  PlayStation: {min(len(items), limit)} geladen...")
        if offset >= total:
            break
    items.sort(key=lambda x: -x["discount"])
    return items[:limit], total


def fetch_ps_plus_monthly():
    # This category is already scoped to exactly this month's PS Plus games (Essential/Extra/
    # Premium tiers), no further filtering needed - unlike Steam there's no "discount=100%"
    # signal, so these are shown with their normal price alongside a "PS Plus" label on the site.
    items = []
    data = ps_graphql("categoryGridRetrieve", {
        "id": PS_CATEGORY_PS_PLUS,
        "pageArgs": {"size": 24, "offset": 0},
        "sortBy": {"name": "productReleaseDate", "isAscending": False},
        "filterBy": [],
        "facetOptions": [],
    })
    grid = data.get("data", {}).get("categoryGridRetrieve") or {}
    for p in grid.get("products") or []:
        price = p.get("price") or {}
        base_cents = parse_price_to_cents(price.get("basePrice"))
        items.append({
            "appid": p["id"],
            "name": p["name"],
            "orig_cents": base_cents or 0,
            "orig_price_str": price.get("basePrice") or "?",
        })
    return items


def fetch_ps_product_price(product_id):
    data = ps_graphql("metGetProductById", {"productId": product_id})
    product = (data.get("data") or {}).get("productRetrieve")
    if not product:
        return None
    name = product["name"]
    concept_id = (product.get("concept") or {}).get("id")
    if not concept_id:
        return name, None
    price_data = ps_graphql("metGetPricingDataByConceptId", {"conceptId": concept_id})
    default_product = ((price_data.get("data") or {}).get("conceptRetrieve") or {}).get("defaultProduct") or {}
    price = default_product.get("price")
    return name, price


def fetch_ps_wishlist():
    if not os.path.exists(PS_WISHLIST_CONFIG):
        return []
    with open(PS_WISHLIST_CONFIG, "r", encoding="utf-8") as f:
        entries = json.load(f)
    items = []
    for entry in entries:
        product_id = entry["appid"]
        try:
            name, price = fetch_ps_product_price(product_id)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            log(f"  PlayStation-Wunschliste: Fehler bei {entry.get('name', product_id)}: {exc}")
            items.append({
                "appid": product_id, "name": entry.get("name", product_id),
                "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "?", "final_price_str": "nicht gefunden",
            })
            continue
        if not price:
            items.append({
                "appid": product_id, "name": name or entry.get("name", product_id),
                "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "Kostenlos/F2P", "final_price_str": "Kostenlos/F2P",
            })
            continue
        base_cents = price.get("basePriceValue", 0)
        final_cents = price.get("discountedValue", base_cents)
        discount = round((base_cents - final_cents) / base_cents * 100) if base_cents else 0
        items.append({
            "appid": product_id, "name": name,
            "discount": discount,
            "orig_cents": base_cents, "final_cents": final_cents,
            "orig_price_str": price.get("basePrice") or format_cents(base_cents),
            "final_price_str": price.get("discountedPrice") or format_cents(final_cents),
        })
    return items


def write_json(filename, payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"Geschrieben: {path}")


PLATFORM_ERRORS = (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError)


def run_platform(label, fn, *args):
    # Each platform is independent (and PlayStation's persisted-query hash in particular can
    # break if Sony changes it) - one platform failing shouldn't blank out the others' data.
    try:
        return fn(*args)
    except PLATFORM_ERRORS as exc:
        log(f"  Fehler bei {label}: {exc}")
        return None


def main():
    log("Lade Game Pass Katalog...")
    catalog = fetch_gamepass_catalog()

    log("Lade Steam-Rabatte...")
    steam_discounts_result = run_platform("Steam-Rabatte", fetch_discounts, DISCOUNTS_LIMIT)
    if steam_discounts_result:
        discounts, total_discounts = steam_discounts_result
        enrich_with_gamepass(discounts, catalog)
        write_json("discounts.json", {"items": discounts, "total": total_discounts})

    log("Lade Steam Aktuell-kostenlos...")
    free_games = run_platform("Steam Aktuell-kostenlos", fetch_free_games)
    if free_games is not None:
        enrich_with_gamepass(free_games, catalog)
        write_json("free.json", {"items": free_games})

    log("Lade Xbox-Rabatte...")
    xbox_result = run_platform("Xbox-Rabatte", fetch_xbox_deals)
    if xbox_result:
        xbox_discounts, xbox_total = xbox_result
        enrich_with_gamepass(xbox_discounts, catalog)
        write_json("xbox_discounts.json", {"items": xbox_discounts, "total": xbox_total})

    log("Lade Xbox Free Play Days...")
    xbox_free = run_platform("Xbox Free Play Days", fetch_xbox_free_play_days)
    if xbox_free is not None:
        enrich_with_gamepass(xbox_free, catalog)
        write_json("xbox_free.json", {"items": xbox_free})

    log("Lade PlayStation-Rabatte...")
    ps_result = run_platform("PlayStation-Rabatte", fetch_ps_category, PS_CATEGORY_SALES, PS_DISCOUNTS_LIMIT)
    if ps_result:
        ps_discounts, ps_total = ps_result
        write_json("ps_discounts.json", {"items": ps_discounts, "total": ps_total})

    log("Lade PlayStation Plus Monatsspiele...")
    ps_plus = run_platform("PlayStation Plus Monatsspiele", fetch_ps_plus_monthly)
    if ps_plus is not None:
        write_json("ps_free.json", {"items": ps_plus})

    log("Lade PlayStation-Wunschliste...")
    ps_wishlist = run_platform("PlayStation-Wunschliste", fetch_ps_wishlist)
    if ps_wishlist is not None:
        write_json("ps_wishlist.json", {"items": ps_wishlist})

    # normalized title -> sorted platform list, used by the website to look up Game Pass
    # status for wishlist games (those are fetched live client-side, not pre-enriched here)
    gamepass_export = {title: sorted(platforms) for title, platforms in catalog.items()}
    write_json("gamepass.json", gamepass_export)
    write_json("meta.json", {"updated": datetime.now(timezone.utc).isoformat()})

    log("Fertig.")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        log(f"Fehler: {exc}")
        sys.exit(1)
