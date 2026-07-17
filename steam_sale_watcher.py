import html
import json
import os
import queue
import re
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

APP_TITLE = "Sale Watcher"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

STEAM_CC = "de"
STEAM_LANG = "german"
STEAM_PAGE_SIZE = 100

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SteamSaleWatcher")

GAMEPASS_MARKET = "DE"
GAMEPASS_LANGUAGE = "de-de"
GAMEPASS_BATCH_SIZE = 200
GAMEPASS_SIGL_IDS = {
    "PC": "fdd9e2a7-0fee-49f6-ad69-4354098401ff",
    "Xbox": "f6f1f99f-9b49-4ccd-b3bf-4d9767a77f5e",
}

XBOX_MARKET = "DE"
XBOX_LOCALE = "de-DE"
XBOX_DEALS_URL = f"https://www.xbox.com/{XBOX_LOCALE}/games/browse/game-deals"
XBOX_FREE_PLAY_DAYS_URL = f"https://www.xbox.com/{XBOX_LOCALE}/promotions/sales/free-play-days"
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


# ---------- Shared helpers ----------

class SortableTreeMixin:
    """Adds click-to-sort headings. Requires self.tree, self.row_data (iid -> dict),
    and self._column_labels (col -> heading text) to be set before use."""

    def _init_sorting(self, sort_keys):
        self._sort_keys = sort_keys
        self._sort_state = {"col": None, "reverse": False}
        for col in sort_keys:
            self.tree.heading(col, command=lambda c=col: self._sort_by(c))

    def _sort_by(self, col):
        if self._sort_state["col"] == col:
            reverse = not self._sort_state["reverse"]
        else:
            reverse = False
        self._apply_sort(col, reverse)

    def _apply_sort(self, col, reverse):
        if col is None:
            return
        key_func = self._sort_keys[col]
        items = list(self.tree.get_children(""))
        items.sort(key=lambda iid: key_func(self.row_data[iid]), reverse=reverse)
        for index, iid in enumerate(items):
            self.tree.move(iid, "", index)
        self._sort_state = {"col": col, "reverse": reverse}
        self._update_heading_arrows()

    def _update_heading_arrows(self):
        active_col = self._sort_state["col"]
        reverse = self._sort_state["reverse"]
        for col, label in self._column_labels.items():
            if col == active_col:
                arrow = " ▼" if reverse else " ▲"
                self.tree.heading(col, text=label + arrow)
            else:
                self.tree.heading(col, text=label)


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


def load_wishlist(filename):
    path = os.path.join(APP_DIR, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_wishlist(filename, entries):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(os.path.join(APP_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError)


# ---------- Steam ----------

RE_APPID = re.compile(r'data-ds-appid="(\d+)"')
RE_TITLE = re.compile(r'<span class="title">(.*?)</span>', re.S)
RE_DISCOUNT = re.compile(r'data-discount="(\d+)"')
RE_ORIG_PRICE = re.compile(r'discount_original_price">([^<]*)</div>')
RE_FINAL_PRICE = re.compile(r'discount_final_price">([^<]*)</div>')


def steam_search_page(start, sort_by):
    url = (
        "https://store.steampowered.com/search/results/"
        f"?query&start={start}&count={STEAM_PAGE_SIZE}&dynamic_data="
        f"&sort_by={sort_by}&specials=1&category1=998"
        f"&cc={STEAM_CC}&l={STEAM_LANG}&infinite=1"
    )
    data = http_get_json(url)
    return data.get("results_html", ""), data.get("total_count", 0)


def parse_steam_search_html(page_html):
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


def steam_discounts_worker(progress_queue, limit):
    items = []
    start = 0
    total = 0
    while len(items) < limit:
        try:
            page_html, total = steam_search_page(start, "_ASC")
        except NETWORK_ERRORS as exc:
            progress_queue.put(("error", str(exc)))
            return
        page_items = parse_steam_search_html(page_html)
        if not page_items:
            break
        items.extend(page_items)
        start += STEAM_PAGE_SIZE
        progress_queue.put(("progress", min(len(items), limit), min(total, limit) or limit))
        if start >= total:
            break
    progress_queue.put(("done", items[:limit], total))


def steam_free_worker(progress_queue, _limit=None, max_pages=30):
    items = []
    start = 0
    for _ in range(max_pages):
        try:
            page_html, _total = steam_search_page(start, "Price_ASC")
        except NETWORK_ERRORS as exc:
            progress_queue.put(("error", str(exc)))
            return
        page_items = parse_steam_search_html(page_html)
        if not page_items:
            break
        reached_end = False
        for it in page_items:
            if it["discount"] >= 100 and it["final_cents"] == 0:
                items.append(it)
            else:
                reached_end = True
                break
        progress_queue.put(("progress", len(items), None))
        if reached_end:
            break
        start += STEAM_PAGE_SIZE
    progress_queue.put(("done", items, len(items)))


def steam_search(term):
    url = (
        "https://store.steampowered.com/api/storesearch/"
        f"?term={urllib.parse.quote(term)}&cc={STEAM_CC}&l={STEAM_LANG}"
    )
    data = http_get_json(url)
    results = []
    for item in data.get("items", []):
        if item.get("type") != "app":
            continue
        price = item.get("price") or {}
        results.append({
            "appid": item["id"],
            "name": item["name"],
            "final_cents": price.get("final"),
        })
    return results


def steam_appdetails_price(appid):
    url = (
        f"https://store.steampowered.com/api/appdetails"
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


def steam_wishlist_prices_worker(entries, progress_queue):
    items = []
    for entry in entries:
        try:
            price = steam_appdetails_price(entry["appid"])
        except NETWORK_ERRORS as exc:
            progress_queue.put(("error", str(exc)))
            return
        if price is None:
            items.append({
                "appid": entry["appid"], "name": entry["name"],
                "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "?", "final_price_str": "nicht gefunden",
            })
        else:
            items.append({"appid": entry["appid"], "name": entry["name"], **price})
        progress_queue.put(("progress", len(items), len(entries)))
    progress_queue.put(("done", items, len(items)))


def steam_store_url(appid):
    return f"https://store.steampowered.com/app/{appid}/"


# ---------- Game Pass catalog (used to cross-reference Steam and Xbox listings) ----------

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


class GamePassCatalog:
    """Loads the PC/Xbox Game Pass catalogs once in the background and answers title lookups."""

    def __init__(self):
        self.ready = False
        self._catalog = {}
        self._lock = threading.Lock()
        self._listeners = []

    def start_loading(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        catalog = {}
        try:
            for platform, sigl_id in GAMEPASS_SIGL_IDS.items():
                ids = fetch_gamepass_ids(sigl_id)
                for start in range(0, len(ids), GAMEPASS_BATCH_SIZE):
                    batch = ids[start:start + GAMEPASS_BATCH_SIZE]
                    for title in fetch_gamepass_titles(batch):
                        norm = normalize_title(title)
                        if norm:
                            catalog.setdefault(norm, set()).add(platform)
        except NETWORK_ERRORS:
            pass
        with self._lock:
            self._catalog = catalog
            self.ready = True
            listeners = list(self._listeners)
        for callback in listeners:
            callback()

    def on_ready(self, callback):
        with self._lock:
            if self.ready:
                run_now = True
            else:
                self._listeners.append(callback)
                run_now = False
        if run_now:
            callback()

    def lookup(self, name):
        norm = normalize_title(name)
        with self._lock:
            platforms = self._catalog.get(norm)
        if not platforms:
            return ""
        if platforms == {"PC"}:
            return "PC"
        if platforms == {"Xbox"}:
            return "Xbox"
        return "PC + Xbox"


GAMEPASS = GamePassCatalog()


# ---------- Xbox ----------

def fetch_xbox_state(url):
    try:
        page_html = http_get_text(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Xbox 404s pages like Free Play Days entirely when no promotion is currently
            # running, rather than rendering an empty page - a normal state, not an error.
            return None
        raise
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});", page_html, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


def parse_xbox_channel(state, channel_key, require_discount):
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
        if not title:
            continue
        discount = round((price or {}).get("discountPercentage") or 0)
        if require_discount and discount <= 0:
            continue
        final_cents = round(((price or {}).get("listPrice") or 0) * 100)
        orig_cents = round(((price or {}).get("msrp") or (price or {}).get("listPrice") or 0) * 100)
        items.append({
            "appid": product_id,
            "name": title,
            "discount": discount,
            "orig_cents": orig_cents,
            "final_cents": final_cents,
            "orig_price_str": format_cents(orig_cents) if orig_cents else "?",
            "final_price_str": format_cents(final_cents) if final_cents else "?",
        })
    items.sort(key=lambda x: -x["discount"])
    return items


def xbox_discounts_worker(progress_queue, _limit=None):
    try:
        state = fetch_xbox_state(XBOX_DEALS_URL)
    except NETWORK_ERRORS as exc:
        progress_queue.put(("error", str(exc)))
        return
    if not state:
        progress_queue.put(("done", [], 0))
        return
    items = parse_xbox_channel(state, XBOX_DEALS_CHANNEL_KEY, require_discount=True)
    total = state["core2"]["channels"]["channelData"].get(XBOX_DEALS_CHANNEL_KEY, {}) \
        .get("data", {}).get("totalItems", len(items))
    progress_queue.put(("done", items, total))


def xbox_free_worker(progress_queue, _limit=None):
    try:
        state = fetch_xbox_state(XBOX_FREE_PLAY_DAYS_URL)
    except NETWORK_ERRORS as exc:
        progress_queue.put(("error", str(exc)))
        return
    if not state:
        progress_queue.put(("done", [], 0))
        return
    items = []
    for channel_key in state["core2"]["channels"]["channelData"]:
        items.extend(parse_xbox_channel(state, channel_key, require_discount=False))
    progress_queue.put(("done", items, len(items)))


def xbox_search(term):
    url = (
        "https://storeedgefd.dsx.mp.microsoft.com/v9.0/pages/searchResults"
        f"?appVersion=22203.1401.0.0&market={XBOX_MARKET}&locale={XBOX_LOCALE}"
        f"&deviceFamily=windows.xbox&query={urllib.parse.quote(term)}&mediaType=games"
    )
    data = http_get_json(url)
    payload = data[1]["Payload"] if len(data) > 1 else {}
    results = []
    for item in payload.get("SearchResults", []):
        results.append({
            "appid": item["ProductId"],
            "name": item["Title"],
            "final_cents": None,
        })
    return results


def xbox_product_price(product_id):
    url = (
        "https://displaycatalog.mp.microsoft.com/v7.0/products"
        f"?bigIds={product_id}&market={GAMEPASS_MARKET}&languages={GAMEPASS_LANGUAGE}"
    )
    data = http_get_json(url)
    products = data.get("Products") or []
    if not products:
        return None
    product = products[0]
    localized = (product.get("LocalizedProperties") or [{}])[0]
    name = localized.get("ProductTitle")
    price = ((product.get("DisplaySkuAvailabilities") or [{}])[0]
             .get("Availabilities", [{}])[0]
             .get("OrderManagementData", {})
             .get("Price"))
    if not price:
        return {"name": name, "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "Kostenlos/F2P", "final_price_str": "Kostenlos/F2P"}
    orig_cents = round((price.get("MSRP") or price.get("ListPrice") or 0) * 100)
    final_cents = round((price.get("ListPrice") or 0) * 100)
    discount = round((1 - final_cents / orig_cents) * 100) if orig_cents else 0
    return {
        "name": name, "discount": discount,
        "orig_cents": orig_cents, "final_cents": final_cents,
        "orig_price_str": format_cents(orig_cents), "final_price_str": format_cents(final_cents),
    }


def xbox_wishlist_prices_worker(entries, progress_queue):
    items = []
    for entry in entries:
        try:
            price = xbox_product_price(entry["appid"])
        except NETWORK_ERRORS as exc:
            progress_queue.put(("error", str(exc)))
            return
        if price is None:
            items.append({
                "appid": entry["appid"], "name": entry["name"],
                "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "?", "final_price_str": "nicht gefunden",
            })
        else:
            items.append({"appid": entry["appid"], "name": price.get("name") or entry["name"], **{
                k: v for k, v in price.items() if k != "name"
            }})
        progress_queue.put(("progress", len(items), len(entries)))
    progress_queue.put(("done", items, len(items)))


def xbox_store_url(appid):
    return f"https://www.microsoft.com/store/productId/{appid}"


# ---------- PlayStation ----------

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


def ps_category_page(category_id, offset, page_size=24):
    data = ps_graphql("categoryGridRetrieve", {
        "id": category_id,
        "pageArgs": {"size": page_size, "offset": offset},
        "sortBy": {"name": "productReleaseDate", "isAscending": False},
        "filterBy": [],
        "facetOptions": [],
    })
    grid = data.get("data", {}).get("categoryGridRetrieve") or {}
    return grid.get("products") or [], (grid.get("pageInfo") or {}).get("totalCount", 0)


def ps_discounts_worker(progress_queue, limit):
    items = []
    offset = 0
    total = 0
    page_size = 24
    while len(items) < limit:
        try:
            products, total = ps_category_page(PS_CATEGORY_SALES, offset, page_size)
        except NETWORK_ERRORS as exc:
            progress_queue.put(("error", str(exc)))
            return
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
                "appid": p["id"], "name": p["name"], "discount": discount,
                "orig_cents": base_cents, "final_cents": final_cents,
                "orig_price_str": format_cents(base_cents), "final_price_str": format_cents(final_cents),
            })
        offset += page_size
        progress_queue.put(("progress", min(len(items), limit), min(total, limit) or limit))
        if offset >= total:
            break
    items.sort(key=lambda x: -x["discount"])
    progress_queue.put(("done", items[:limit], total))


def ps_plus_worker(progress_queue, _limit=None):
    try:
        products, _total = ps_category_page(PS_CATEGORY_PS_PLUS, 0)
    except NETWORK_ERRORS as exc:
        progress_queue.put(("error", str(exc)))
        return
    items = []
    for p in products:
        price = p.get("price") or {}
        base_cents = parse_price_to_cents(price.get("basePrice"))
        items.append({
            "appid": p["id"], "name": p["name"],
            "orig_cents": base_cents or 0,
            "orig_price_str": price.get("basePrice") or "?",
        })
    progress_queue.put(("done", items, len(items)))


def ps_product_price(product_id):
    data = ps_graphql("metGetProductById", {"productId": product_id})
    product = (data.get("data") or {}).get("productRetrieve")
    if not product:
        return None
    name = product["name"]
    concept_id = (product.get("concept") or {}).get("id")
    if not concept_id:
        return {"name": name, "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "?", "final_price_str": "?"}
    price_data = ps_graphql("metGetPricingDataByConceptId", {"conceptId": concept_id})
    default_product = ((price_data.get("data") or {}).get("conceptRetrieve") or {}).get("defaultProduct") or {}
    price = default_product.get("price")
    if not price:
        return {"name": name, "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "Kostenlos/F2P", "final_price_str": "Kostenlos/F2P"}
    base_cents = price.get("basePriceValue", 0)
    final_cents = price.get("discountedValue", base_cents)
    discount = round((base_cents - final_cents) / base_cents * 100) if base_cents else 0
    return {
        "name": name, "discount": discount,
        "orig_cents": base_cents, "final_cents": final_cents,
        "orig_price_str": price.get("basePrice") or format_cents(base_cents),
        "final_price_str": price.get("discountedPrice") or format_cents(final_cents),
    }


PS_PRODUCT_ID_RE = re.compile(r"/product/([A-Za-z0-9_-]+)")


def parse_ps_product_id(text):
    text = text.strip()
    m = PS_PRODUCT_ID_RE.search(text)
    if m:
        return m.group(1)
    return text or None


def ps_wishlist_prices_worker(entries, progress_queue):
    items = []
    for entry in entries:
        try:
            price = ps_product_price(entry["appid"])
        except NETWORK_ERRORS as exc:
            progress_queue.put(("error", str(exc)))
            return
        if price is None:
            items.append({
                "appid": entry["appid"], "name": entry["name"],
                "discount": 0, "orig_cents": 0, "final_cents": 0,
                "orig_price_str": "?", "final_price_str": "nicht gefunden",
            })
        else:
            items.append({"appid": entry["appid"], "name": price.get("name") or entry["name"], **{
                k: v for k, v in price.items() if k != "name"
            }})
        progress_queue.put(("progress", len(items), len(entries)))
    progress_queue.put(("done", items, len(items)))


def ps_store_url(appid):
    return f"https://store.playstation.com/{PS_LOCALE}/product/{appid}"


# ---------- Generic UI: discounts-style tab (sortable table + optional limit selector) ----------

class GenericDiscountsTab(SortableTreeMixin, ttk.Frame):
    def __init__(self, master, platform_name, worker_fn, store_url_fn,
                 show_limit=True, limit_options=("100", "300", "500"), default_limit="300",
                 show_gamepass=True, show_savings=True, hint_text="Doppelklick öffnet die Store-Seite im Browser."):
        super().__init__(master)
        self.platform_name = platform_name
        self.worker_fn = worker_fn
        self.store_url_fn = store_url_fn
        self.show_limit = show_limit
        self.show_gamepass = show_gamepass
        self.show_savings = show_savings
        self.hint_text = hint_text
        self.result_queue = queue.Queue()
        self.row_data = {}
        self._build_ui(limit_options, default_limit)
        if show_gamepass:
            GAMEPASS.on_ready(lambda: self.after(0, self._refresh_gamepass_column))

    def _build_ui(self, limit_options, default_limit):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="Aktualisieren", command=self.start_fetch).pack(side="left")

        if self.show_limit:
            ttk.Label(top, text="Anzahl:").pack(side="left", padx=(12, 4))
            self.limit_var = tk.StringVar(value=default_limit)
            ttk.Combobox(
                top, textvariable=self.limit_var, values=limit_options, width=6, state="readonly",
            ).pack(side="left")

        self.progress = ttk.Progressbar(top, mode="determinate" if self.show_limit else "indeterminate", length=160)

        self.summary_label = ttk.Label(self, text="Noch nicht geladen.")
        self.summary_label.pack(fill="x", padx=8)

        columns = ["name", "discount", "orig", "final"]
        self._column_labels = {
            "name": "Name", "discount": "Rabatt", "orig": "Originalpreis", "final": "Preis jetzt",
        }
        sort_keys = {
            "name": lambda d: d["name"].lower(),
            "discount": lambda d: d["discount"],
            "orig": lambda d: d["orig_cents"],
            "final": lambda d: d["final_cents"],
        }
        if self.show_savings:
            columns.append("savings")
            self._column_labels["savings"] = "Ersparnis"
            sort_keys["savings"] = lambda d: d["orig_cents"] - d["final_cents"]
        if self.show_gamepass:
            columns.append("gamepass")
            self._column_labels["gamepass"] = "Game Pass"
            sort_keys["gamepass"] = lambda d: d.get("gamepass", "")

        self.tree = ttk.Treeview(self, columns=tuple(columns), show="headings", selectmode="browse")
        for col, label in self._column_labels.items():
            self.tree.heading(col, text=label)
        self.tree.column("name", width=320)
        self.tree.column("discount", width=70, anchor="e")
        self.tree.column("orig", width=100, anchor="e")
        self.tree.column("final", width=100, anchor="e")
        if self.show_savings:
            self.tree.column("savings", width=100, anchor="e")
        if self.show_gamepass:
            self.tree.column("gamepass", width=90, anchor="center")
        self._init_sorting(sort_keys)
        self.tree.bind("<Double-1>", self.open_store_page)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8))

        ttk.Label(self, text=self.hint_text, foreground="#666").pack(fill="x", padx=8, pady=(0, 4))

    def start_fetch(self):
        self.tree.delete(*self.tree.get_children())
        self.row_data.clear()
        self.summary_label.config(text="Lade Angebote...")
        if self.show_limit:
            try:
                limit = int(self.limit_var.get())
            except ValueError:
                limit = 300
            self.progress["value"] = 0
            self.progress["maximum"] = limit
        else:
            limit = None
            self.progress.start(10)
        self.progress.pack(side="left", padx=8)
        threading.Thread(target=self.worker_fn, args=(self.result_queue, limit), daemon=True).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                kind, *payload = self.result_queue.get_nowait()
                if kind == "progress":
                    loaded, target = payload
                    if self.show_limit:
                        self.progress["value"] = loaded
                    self.summary_label.config(text=f"Lade Angebote... {loaded}/{target}" if target else f"Lade Angebote... {loaded}")
                elif kind == "error":
                    if self.show_limit:
                        self.progress.pack_forget()
                    else:
                        self.progress.stop()
                        self.progress.pack_forget()
                    messagebox.showerror(APP_TITLE, f"Fehler beim Laden:\n{payload[0]}")
                    self.summary_label.config(text="Fehler beim Laden.")
                    return
                elif kind == "done":
                    items, total = payload
                    if self.show_limit:
                        self.progress.pack_forget()
                    else:
                        self.progress.stop()
                        self.progress.pack_forget()
                    self._show_items(items, total)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_items(self, items, total):
        for item in items:
            if self.show_gamepass:
                item["gamepass"] = GAMEPASS.lookup(item["name"])
            values = [item["name"], f"-{item['discount']}%", item["orig_price_str"], item["final_price_str"]]
            if self.show_savings:
                values.append(format_cents(item["orig_cents"] - item["final_cents"]))
            if self.show_gamepass:
                values.append(item["gamepass"] or "-")
            iid = self.tree.insert("", "end", values=tuple(values))
            self.row_data[iid] = item
        if total and total > len(items):
            self.summary_label.config(
                text=f"{len(items)} reduzierte Spiele geladen (insgesamt {total} bei {self.platform_name} im Angebot)."
            )
        else:
            self.summary_label.config(text=f"{len(items)} reduzierte Spiele geladen.")

    def _refresh_gamepass_column(self):
        for iid, item in self.row_data.items():
            item["gamepass"] = GAMEPASS.lookup(item["name"])
            self.tree.set(iid, "gamepass", item["gamepass"] or "-")

    def open_store_page(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.row_data.get(selection[0])
        if item:
            webbrowser.open(self.store_url_fn(item["appid"]))


# ---------- Generic UI: "currently free" style tab (name + regular price, no discount%) ----------

class GenericFreeTab(SortableTreeMixin, ttk.Frame):
    def __init__(self, master, worker_fn, store_url_fn, show_gamepass=True,
                 hint_text="Doppelklick öffnet die Store-Seite.",
                 empty_text="Gerade keine Angebote gefunden."):
        super().__init__(master)
        self.worker_fn = worker_fn
        self.store_url_fn = store_url_fn
        self.show_gamepass = show_gamepass
        self.empty_text = empty_text
        self.result_queue = queue.Queue()
        self.row_data = {}
        self._build_ui(hint_text)
        if show_gamepass:
            GAMEPASS.on_ready(lambda: self.after(0, self._refresh_gamepass_column))

    def _build_ui(self, hint_text):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="Aktualisieren", command=self.start_fetch).pack(side="left")
        self.progress = ttk.Progressbar(top, mode="indeterminate", length=160)

        self.summary_label = ttk.Label(self, text="Noch nicht geladen.")
        self.summary_label.pack(fill="x", padx=8)

        columns = ["name", "orig"] + (["gamepass"] if self.show_gamepass else [])
        self.tree = ttk.Treeview(self, columns=tuple(columns), show="headings", selectmode="browse")
        self._column_labels = {"name": "Name", "orig": "Regulärer Preis"}
        sort_keys = {"name": lambda d: d["name"].lower(), "orig": lambda d: d["orig_cents"]}
        if self.show_gamepass:
            self._column_labels["gamepass"] = "Game Pass"
            sort_keys["gamepass"] = lambda d: d.get("gamepass", "")
        for col, label in self._column_labels.items():
            self.tree.heading(col, text=label)
        self.tree.column("name", width=350)
        self.tree.column("orig", width=120, anchor="e")
        if self.show_gamepass:
            self.tree.column("gamepass", width=90, anchor="center")
        self._init_sorting(sort_keys)
        self.tree.bind("<Double-1>", self.open_store_page)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8))

        ttk.Label(self, text=hint_text, foreground="#666").pack(fill="x", padx=8, pady=(0, 4))

    def start_fetch(self):
        self.tree.delete(*self.tree.get_children())
        self.row_data.clear()
        self.summary_label.config(text="Lade...")
        self.progress.pack(side="left", padx=8)
        self.progress.start(10)
        threading.Thread(target=self.worker_fn, args=(self.result_queue, None), daemon=True).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                kind, *payload = self.result_queue.get_nowait()
                if kind == "progress":
                    loaded = payload[0]
                    self.summary_label.config(text=f"Lade... ({loaded} gefunden)")
                elif kind == "error":
                    self.progress.stop()
                    self.progress.pack_forget()
                    messagebox.showerror(APP_TITLE, f"Fehler beim Laden:\n{payload[0]}")
                    self.summary_label.config(text="Fehler beim Laden.")
                    return
                elif kind == "done":
                    items, _total = payload
                    self.progress.stop()
                    self.progress.pack_forget()
                    self._show_items(items)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_items(self, items):
        for item in items:
            if self.show_gamepass:
                item["gamepass"] = GAMEPASS.lookup(item["name"])
            values = [item["name"], item["orig_price_str"]]
            if self.show_gamepass:
                values.append(item["gamepass"] or "-")
            iid = self.tree.insert("", "end", values=tuple(values))
            self.row_data[iid] = item
        if items:
            self.summary_label.config(text=f"{len(items)} Spiele gefunden.")
        else:
            self.summary_label.config(text=self.empty_text)

    def _refresh_gamepass_column(self):
        for iid, item in self.row_data.items():
            item["gamepass"] = GAMEPASS.lookup(item["name"])
            self.tree.set(iid, "gamepass", item["gamepass"] or "-")

    def open_store_page(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.row_data.get(selection[0])
        if item:
            webbrowser.open(self.store_url_fn(item["appid"]))


# ---------- Generic UI: wishlist tab (search-to-add, used by Steam and Xbox) ----------

class SearchDialog(tk.Toplevel):
    def __init__(self, master, search_fn, on_add):
        super().__init__(master)
        self.title("Spiel zur Wunschliste hinzufügen")
        self.geometry("420x360")
        self.transient(master)
        self.search_fn = search_fn
        self.on_add = on_add
        self.result_queue = queue.Queue()
        self.results = []
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)
        self.term_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.term_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.search())
        ttk.Button(top, text="Suchen", command=self.search).pack(side="left", padx=(8, 0))

        self.listbox = tk.Listbox(self)
        self.listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.listbox.bind("<Double-1>", lambda _e: self.add_selected())

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bottom, text="Hinzufügen", command=self.add_selected).pack(side="left")
        ttk.Button(bottom, text="Abbrechen", command=self.destroy).pack(side="right")

        entry.focus_set()

    def search(self):
        term = self.term_var.get().strip()
        if not term:
            return
        self.listbox.delete(0, "end")
        self.listbox.insert("end", "Suche...")
        threading.Thread(target=self._search_worker, args=(term,), daemon=True).start()
        self.after(100, self._poll)

    def _search_worker(self, term):
        try:
            results = self.search_fn(term)
        except NETWORK_ERRORS as exc:
            self.result_queue.put(("error", str(exc)))
            return
        self.result_queue.put(("done", results))

    def _poll(self):
        try:
            kind, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self.listbox.delete(0, "end")
        if kind == "error":
            self.listbox.insert("end", f"Fehler: {payload}")
            return
        self.results = payload
        if not self.results:
            self.listbox.insert("end", "Keine Treffer.")
            return
        for r in self.results:
            price = format_cents(r["final_cents"]) if r["final_cents"] is not None else "?"
            self.listbox.insert("end", f"{r['name']} — {price}")

    def add_selected(self):
        selection = self.listbox.curselection()
        if not selection or not self.results:
            return
        idx = selection[0]
        if idx >= len(self.results):
            return
        item = self.results[idx]
        self.on_add(item["appid"], item["name"])
        self.destroy()


class PasteIdDialog(tk.Toplevel):
    """Add-by-URL/ID dialog, used for PlayStation (no confirmed live search API)."""

    def __init__(self, master, parse_fn, resolve_fn, on_add):
        super().__init__(master)
        self.title("Spiel zur Wunschliste hinzufügen")
        self.geometry("420x160")
        self.transient(master)
        self.parse_fn = parse_fn
        self.resolve_fn = resolve_fn
        self.on_add = on_add
        self.result_queue = queue.Queue()
        self._build_ui()

    def _build_ui(self):
        ttk.Label(
            self, text="Store-Link oder Produkt-ID einfügen\n(z. B. store.playstation.com/.../product/EP...)",
            justify="left",
        ).pack(fill="x", padx=8, pady=(8, 4))
        self.entry_var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self.entry_var)
        entry.pack(fill="x", padx=8)
        entry.bind("<Return>", lambda _e: self.resolve())
        entry.focus_set()

        self.status_label = ttk.Label(self, text="")
        self.status_label.pack(fill="x", padx=8, pady=4)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=8)
        ttk.Button(bottom, text="Hinzufügen", command=self.resolve).pack(side="left")
        ttk.Button(bottom, text="Abbrechen", command=self.destroy).pack(side="right")

    def resolve(self):
        product_id = self.parse_fn(self.entry_var.get())
        if not product_id:
            self.status_label.config(text="Keine gültige ID/URL erkannt.")
            return
        self.status_label.config(text="Suche...")
        threading.Thread(target=self._worker, args=(product_id,), daemon=True).start()
        self.after(100, self._poll)

    def _worker(self, product_id):
        try:
            price = self.resolve_fn(product_id)
        except NETWORK_ERRORS as exc:
            self.result_queue.put(("error", str(exc)))
            return
        self.result_queue.put(("done", product_id, price))

    def _poll(self):
        try:
            payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        kind = payload[0]
        if kind == "error":
            self.status_label.config(text=f"Fehler: {payload[1]}")
            return
        _kind, product_id, price = payload
        if not price or not price.get("name"):
            self.status_label.config(text="Spiel nicht gefunden.")
            return
        self.on_add(product_id, price["name"])
        self.destroy()


class WishlistTab(SortableTreeMixin, ttk.Frame):
    def __init__(self, master, wishlist_file, price_worker_fn, store_url_fn,
                 open_add_dialog, show_gamepass=True):
        super().__init__(master)
        self.wishlist_file = wishlist_file
        self.price_worker_fn = price_worker_fn
        self.store_url_fn = store_url_fn
        self.open_add_dialog = open_add_dialog
        self.show_gamepass = show_gamepass
        self.result_queue = queue.Queue()
        self.row_data = {}
        self.entries = load_wishlist(wishlist_file)
        self._build_ui()
        if show_gamepass:
            GAMEPASS.on_ready(lambda: self.after(0, self._refresh_gamepass_column))

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="Spiel hinzufügen...", command=lambda: self.open_add_dialog(self.add_game)).pack(side="left")
        ttk.Button(top, text="Entfernen", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Aktualisieren", command=self.start_fetch).pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(top, mode="determinate", length=160)

        self.summary_label = ttk.Label(self, text="Noch nicht geladen.")
        self.summary_label.pack(fill="x", padx=8)

        columns = ["name", "discount", "orig", "final"] + (["gamepass"] if self.show_gamepass else [])
        self.tree = ttk.Treeview(self, columns=tuple(columns), show="headings", selectmode="browse")
        self._column_labels = {"name": "Name", "discount": "Rabatt", "orig": "Originalpreis", "final": "Preis jetzt"}
        sort_keys = {
            "name": lambda d: d["name"].lower(),
            "discount": lambda d: d["discount"],
            "orig": lambda d: d["orig_cents"],
            "final": lambda d: d["final_cents"],
        }
        if self.show_gamepass:
            self._column_labels["gamepass"] = "Game Pass"
            sort_keys["gamepass"] = lambda d: d.get("gamepass", "")
        for col, label in self._column_labels.items():
            self.tree.heading(col, text=label)
        self.tree.column("name", width=320)
        self.tree.column("discount", width=70, anchor="e")
        self.tree.column("orig", width=110, anchor="e")
        self.tree.column("final", width=110, anchor="e")
        if self.show_gamepass:
            self.tree.column("gamepass", width=90, anchor="center")
        self._init_sorting(sort_keys)
        self.tree.bind("<Double-1>", self.open_store_page)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8))

        ttk.Label(
            self, text="Deine getrackten Spiele. Doppelklick öffnet die Store-Seite.", foreground="#666",
        ).pack(fill="x", padx=8, pady=(0, 4))

    def add_game(self, appid, name):
        if any(e["appid"] == appid for e in self.entries):
            messagebox.showinfo(APP_TITLE, f"'{name}' ist bereits auf der Wunschliste.")
            return
        self.entries.append({"appid": appid, "name": name})
        save_wishlist(self.wishlist_file, self.entries)
        self.start_fetch()

    def remove_selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst ein Spiel auswählen.")
            return
        item = self.row_data.get(selection[0])
        if not item:
            return
        self.entries = [e for e in self.entries if e["appid"] != item["appid"]]
        save_wishlist(self.wishlist_file, self.entries)
        self.tree.delete(selection[0])
        del self.row_data[selection[0]]
        if self.entries:
            self.summary_label.config(text=f"{len(self.entries)} Spiele auf der Wunschliste.")
        else:
            self.summary_label.config(text="Noch keine Spiele auf der Wunschliste.")

    def start_fetch(self):
        if not self.entries:
            self.summary_label.config(text="Noch keine Spiele auf der Wunschliste.")
            return
        self.tree.delete(*self.tree.get_children())
        self.row_data.clear()
        self.summary_label.config(text="Lade Preise...")
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.entries)
        self.progress.pack(side="left", padx=8)
        threading.Thread(
            target=self.price_worker_fn, args=(self.entries, self.result_queue), daemon=True
        ).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                kind, *payload = self.result_queue.get_nowait()
                if kind == "progress":
                    loaded, target = payload
                    self.progress["value"] = loaded
                    self.summary_label.config(text=f"Lade Preise... {loaded}/{target}")
                elif kind == "error":
                    self.progress.pack_forget()
                    messagebox.showerror(APP_TITLE, f"Fehler beim Laden:\n{payload[0]}")
                    self.summary_label.config(text="Fehler beim Laden.")
                    return
                elif kind == "done":
                    items, _total = payload
                    self.progress.pack_forget()
                    self._show_items(items)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_items(self, items):
        for item in items:
            if self.show_gamepass:
                item["gamepass"] = GAMEPASS.lookup(item["name"])
            values = [item["name"], f"-{item['discount']}%" if item["discount"] else "-",
                      item["orig_price_str"], item["final_price_str"]]
            if self.show_gamepass:
                values.append(item["gamepass"] or "-")
            iid = self.tree.insert("", "end", values=tuple(values))
            self.row_data[iid] = item
        self.summary_label.config(text=f"{len(items)} Spiele auf der Wunschliste.")

    def _refresh_gamepass_column(self):
        for iid, item in self.row_data.items():
            item["gamepass"] = GAMEPASS.lookup(item["name"])
            self.tree.set(iid, "gamepass", item["gamepass"] or "-")

    def open_store_page(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.row_data.get(selection[0])
        if item:
            webbrowser.open(self.store_url_fn(item["appid"]))


def main():
    GAMEPASS.start_loading()

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("880x600")

    try:
        style = ttk.Style()
        style.theme_use("vista")
    except tk.TclError:
        pass

    platforms_notebook = ttk.Notebook(root)
    platforms_notebook.pack(fill="both", expand=True)

    to_start = []

    # --- Steam ---
    steam_frame = ttk.Frame(platforms_notebook)
    steam_notebook = ttk.Notebook(steam_frame)
    steam_notebook.pack(fill="both", expand=True)
    steam_discounts = GenericDiscountsTab(
        steam_notebook, "Steam", steam_discounts_worker, steam_store_url,
        limit_options=("100", "300", "500", "1000", "2000"), default_limit="300",
    )
    steam_free = GenericFreeTab(
        steam_notebook, steam_free_worker, steam_store_url,
        hint_text="Nur Spiele, die zeitlich begrenzt kostenlos sind (nicht dauerhaft Free-to-Play). Doppelklick öffnet die Store-Seite.",
        empty_text="Gerade keine zeitlich begrenzten Gratis-Spiele gefunden.",
    )
    steam_wishlist = WishlistTab(
        steam_notebook, "wunschliste.json", steam_wishlist_prices_worker, steam_store_url,
        open_add_dialog=lambda on_add: SearchDialog(root, steam_search, on_add),
    )
    steam_notebook.add(steam_discounts, text="Rabatte")
    steam_notebook.add(steam_free, text="Aktuell kostenlos")
    steam_notebook.add(steam_wishlist, text="Wunschliste")
    platforms_notebook.add(steam_frame, text="Steam")
    to_start += [steam_discounts.start_fetch, steam_free.start_fetch, steam_wishlist.start_fetch]

    # --- Xbox ---
    xbox_frame = ttk.Frame(platforms_notebook)
    xbox_notebook = ttk.Notebook(xbox_frame)
    xbox_notebook.pack(fill="both", expand=True)
    xbox_discounts = GenericDiscountsTab(
        xbox_notebook, "Xbox", xbox_discounts_worker, xbox_store_url, show_limit=False,
        hint_text="Zeigt eine Momentaufnahme der aktuellen Top-Deals (keine vollständige Liste). Doppelklick öffnet die Store-Seite.",
    )
    xbox_free = GenericFreeTab(
        xbox_notebook, xbox_free_worker, xbox_store_url,
        hint_text="Xbox Free Play Days (zeitlich begrenzte Gratis-Testphasen für Gold/Game-Pass-Mitglieder). Doppelklick öffnet die Store-Seite.",
        empty_text="Gerade keine aktiven Free Play Days.",
    )
    xbox_wishlist = WishlistTab(
        xbox_notebook, "xbox_wunschliste.json", xbox_wishlist_prices_worker, xbox_store_url,
        open_add_dialog=lambda on_add: SearchDialog(root, xbox_search, on_add),
    )
    xbox_notebook.add(xbox_discounts, text="Rabatte")
    xbox_notebook.add(xbox_free, text="Free Play Days")
    xbox_notebook.add(xbox_wishlist, text="Wunschliste")
    platforms_notebook.add(xbox_frame, text="Xbox")
    to_start += [xbox_discounts.start_fetch, xbox_free.start_fetch, xbox_wishlist.start_fetch]

    # --- PlayStation ---
    ps_frame = ttk.Frame(platforms_notebook)
    ps_notebook = ttk.Notebook(ps_frame)
    ps_notebook.pack(fill="both", expand=True)
    ps_discounts = GenericDiscountsTab(
        ps_notebook, "PlayStation", ps_discounts_worker, ps_store_url,
        limit_options=("100", "300", "500"), default_limit="300", show_gamepass=False,
    )
    ps_free = GenericFreeTab(
        ps_notebook, ps_plus_worker, ps_store_url, show_gamepass=False,
        hint_text="PlayStation Plus Spiele des Monats (benötigt aktives PS-Plus-Abo). Doppelklick öffnet die Store-Seite.",
        empty_text="Keine PS-Plus-Monatsspiele gefunden.",
    )
    ps_wishlist = WishlistTab(
        ps_notebook, "ps_wunschliste.json", ps_wishlist_prices_worker, ps_store_url,
        open_add_dialog=lambda on_add: PasteIdDialog(root, parse_ps_product_id, ps_product_price, on_add),
        show_gamepass=False,
    )
    ps_notebook.add(ps_discounts, text="Rabatte")
    ps_notebook.add(ps_free, text="PS Plus Monatsspiele")
    ps_notebook.add(ps_wishlist, text="Wunschliste")
    platforms_notebook.add(ps_frame, text="PlayStation")
    to_start += [ps_discounts.start_fetch, ps_free.start_fetch, ps_wishlist.start_fetch]

    for i, fn in enumerate(to_start):
        root.after(200 + i * 50, fn)

    root.mainloop()


if __name__ == "__main__":
    main()
