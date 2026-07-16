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

APP_TITLE = "Steam Sale Watcher"
STEAM_CC = "de"
STEAM_LANG = "german"
PAGE_SIZE = 100
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SteamSaleWatcher")
WISHLIST_FILE = os.path.join(APP_DIR, "wunschliste.json")

GAMEPASS_MARKET = "DE"
GAMEPASS_LANGUAGE = "de-de"
GAMEPASS_BATCH_SIZE = 200
GAMEPASS_SIGL_IDS = {
    "PC": "fdd9e2a7-0fee-49f6-ad69-4354098401ff",
    "Xbox": "f6f1f99f-9b49-4ccd-b3bf-4d9767a77f5e",
}

RE_APPID = re.compile(r'data-ds-appid="(\d+)"')
RE_TITLE = re.compile(r'<span class="title">(.*?)</span>', re.S)
RE_DISCOUNT = re.compile(r'data-discount="(\d+)"')
RE_ORIG_PRICE = re.compile(r'discount_original_price">([^<]*)</div>')
RE_FINAL_PRICE = re.compile(r'discount_final_price">([^<]*)</div>')


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


def fetch_search_page(start, sort_by):
    url = (
        "https://store.steampowered.com/search/results/"
        f"?query&start={start}&count={PAGE_SIZE}&dynamic_data="
        f"&sort_by={sort_by}&specials=1&category1=998"
        f"&cc={STEAM_CC}&l={STEAM_LANG}&infinite=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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


def fetch_discounted_games(limit, progress_queue):
    items = []
    start = 0
    total = 0
    while len(items) < limit:
        try:
            page_html, total = fetch_search_page(start, "_ASC")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            progress_queue.put(("error", str(exc)))
            return
        page_items = parse_search_html(page_html)
        if not page_items:
            break
        items.extend(page_items)
        start += PAGE_SIZE
        progress_queue.put(("progress", min(len(items), limit), min(total, limit) or limit))
        if start >= total:
            break
    progress_queue.put(("done", items[:limit], total))


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
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
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
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError):
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


def load_wishlist():
    if not os.path.exists(WISHLIST_FILE):
        return []
    try:
        with open(WISHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_wishlist(entries):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(WISHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def steam_search(term):
    url = (
        "https://store.steampowered.com/api/storesearch/"
        f"?term={urllib.parse.quote(term)}&cc={STEAM_CC}&l={STEAM_LANG}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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


def fetch_appdetails_price(appid):
    url = (
        f"https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc={STEAM_CC}&l={STEAM_LANG}&filters=price_overview"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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


def fetch_wishlist_prices(entries, progress_queue):
    items = []
    for entry in entries:
        try:
            price = fetch_appdetails_price(entry["appid"])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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


def fetch_free_games(progress_queue, max_pages=30):
    items = []
    start = 0
    for _ in range(max_pages):
        try:
            page_html, _total = fetch_search_page(start, "Price_ASC")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            progress_queue.put(("error", str(exc)))
            return
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
        progress_queue.put(("progress", len(items), None))
        if reached_end:
            break
        start += PAGE_SIZE
    progress_queue.put(("done", items, len(items)))


class DiscountsTab(SortableTreeMixin, ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.result_queue = queue.Queue()
        self.row_data = {}
        self._build_ui()
        GAMEPASS.on_ready(lambda: self.after(0, self._refresh_gamepass_column))

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="Aktualisieren", command=self.start_fetch).pack(side="left")

        ttk.Label(top, text="Anzahl:").pack(side="left", padx=(12, 4))
        self.limit_var = tk.StringVar(value="300")
        ttk.Combobox(
            top, textvariable=self.limit_var, values=("100", "300", "500", "1000", "2000"),
            width=6, state="readonly",
        ).pack(side="left")

        self.progress = ttk.Progressbar(top, mode="determinate", length=160)

        self.summary_label = ttk.Label(self, text="Noch nicht geladen.")
        self.summary_label.pack(fill="x", padx=8)

        columns = ("name", "discount", "orig", "final", "savings", "gamepass")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        self._column_labels = {
            "name": "Name",
            "discount": "Rabatt",
            "orig": "Originalpreis",
            "final": "Preis jetzt",
            "savings": "Ersparnis",
            "gamepass": "Game Pass",
        }
        for col, label in self._column_labels.items():
            self.tree.heading(col, text=label)
        self.tree.column("name", width=340)
        self.tree.column("discount", width=70, anchor="e")
        self.tree.column("orig", width=100, anchor="e")
        self.tree.column("final", width=100, anchor="e")
        self.tree.column("savings", width=100, anchor="e")
        self.tree.column("gamepass", width=90, anchor="center")
        self._init_sorting({
            "name": lambda d: d["name"].lower(),
            "discount": lambda d: d["discount"],
            "orig": lambda d: d["orig_cents"],
            "final": lambda d: d["final_cents"],
            "savings": lambda d: d["orig_cents"] - d["final_cents"],
            "gamepass": lambda d: d.get("gamepass", ""),
        })
        self.tree.bind("<Double-1>", self.open_store_page)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8))

        ttk.Label(
            self, text="Doppelklick öffnet die Store-Seite im Browser.", foreground="#666"
        ).pack(fill="x", padx=8, pady=(0, 4))

    def start_fetch(self):
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            limit = 300
        self.tree.delete(*self.tree.get_children())
        self.row_data.clear()
        self.summary_label.config(text="Lade Angebote...")
        self.progress["value"] = 0
        self.progress["maximum"] = limit
        self.progress.pack(side="left", padx=8)
        threading.Thread(
            target=fetch_discounted_games, args=(limit, self.result_queue), daemon=True
        ).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                kind, *payload = self.result_queue.get_nowait()
                if kind == "progress":
                    loaded, target = payload
                    self.progress["value"] = loaded
                    self.summary_label.config(text=f"Lade Angebote... {loaded}/{target}")
                elif kind == "error":
                    self.progress.pack_forget()
                    messagebox.showerror(APP_TITLE, f"Fehler beim Laden:\n{payload[0]}")
                    self.summary_label.config(text="Fehler beim Laden.")
                    return
                elif kind == "done":
                    items, total = payload
                    self.progress.pack_forget()
                    self._show_items(items, total)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_items(self, items, total):
        for item in items:
            savings = item["orig_cents"] - item["final_cents"]
            item["gamepass"] = GAMEPASS.lookup(item["name"])
            iid = self.tree.insert(
                "", "end",
                values=(
                    item["name"],
                    f"-{item['discount']}%",
                    item["orig_price_str"],
                    item["final_price_str"],
                    format_cents(savings),
                    item["gamepass"] or "-",
                ),
            )
            self.row_data[iid] = item
        self.summary_label.config(
            text=f"{len(items)} reduzierte Spiele geladen (insgesamt {total} auf Steam im Angebot)."
        )

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
            webbrowser.open(f"https://store.steampowered.com/app/{item['appid']}/")


class FreeGamesTab(SortableTreeMixin, ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.result_queue = queue.Queue()
        self.row_data = {}
        self._build_ui()
        GAMEPASS.on_ready(lambda: self.after(0, self._refresh_gamepass_column))

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="Aktualisieren", command=self.start_fetch).pack(side="left")
        self.progress = ttk.Progressbar(top, mode="indeterminate", length=160)

        self.summary_label = ttk.Label(self, text="Noch nicht geladen.")
        self.summary_label.pack(fill="x", padx=8)

        columns = ("name", "orig", "gamepass")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        self._column_labels = {
            "name": "Name",
            "orig": "Regulärer Preis",
            "gamepass": "Game Pass",
        }
        for col, label in self._column_labels.items():
            self.tree.heading(col, text=label)
        self.tree.column("name", width=350)
        self.tree.column("orig", width=120, anchor="e")
        self.tree.column("gamepass", width=90, anchor="center")
        self._init_sorting({
            "name": lambda d: d["name"].lower(),
            "orig": lambda d: d["orig_cents"],
            "gamepass": lambda d: d.get("gamepass", ""),
        })
        self.tree.bind("<Double-1>", self.open_store_page)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8))

        ttk.Label(
            self,
            text="Nur Spiele, die zeitlich begrenzt kostenlos sind (nicht dauerhaft Free-to-Play). "
                 "Doppelklick öffnet die Store-Seite.",
            foreground="#666",
        ).pack(fill="x", padx=8, pady=(0, 4))

    def start_fetch(self):
        self.tree.delete(*self.tree.get_children())
        self.row_data.clear()
        self.summary_label.config(text="Suche aktuell kostenlose Spiele...")
        self.progress.pack(side="left", padx=8)
        self.progress.start(10)
        threading.Thread(
            target=fetch_free_games, args=(self.result_queue,), daemon=True
        ).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                kind, *payload = self.result_queue.get_nowait()
                if kind == "progress":
                    loaded = payload[0]
                    self.summary_label.config(text=f"Suche aktuell kostenlose Spiele... ({loaded} gefunden)")
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
            item["gamepass"] = GAMEPASS.lookup(item["name"])
            iid = self.tree.insert(
                "", "end",
                values=(item["name"], item["orig_price_str"], item["gamepass"] or "-"),
            )
            self.row_data[iid] = item
        if items:
            self.summary_label.config(text=f"{len(items)} aktuell kostenlose Spiele gefunden.")
        else:
            self.summary_label.config(text="Gerade keine zeitlich begrenzten Gratis-Spiele gefunden.")

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
            webbrowser.open(f"https://store.steampowered.com/app/{item['appid']}/")


class SearchDialog(tk.Toplevel):
    def __init__(self, master, on_add):
        super().__init__(master)
        self.title("Spiel zur Wunschliste hinzufügen")
        self.geometry("420x360")
        self.transient(master)
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
            results = steam_search(term)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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
            price = format_cents(r["final_cents"]) if r["final_cents"] is not None else "Kostenlos/F2P"
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


class WishlistTab(SortableTreeMixin, ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.result_queue = queue.Queue()
        self.row_data = {}
        self.entries = load_wishlist()
        self._build_ui()
        GAMEPASS.on_ready(lambda: self.after(0, self._refresh_gamepass_column))

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="Spiel hinzufügen...", command=self.open_search_dialog).pack(side="left")
        ttk.Button(top, text="Entfernen", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Aktualisieren", command=self.start_fetch).pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(top, mode="determinate", length=160)

        self.summary_label = ttk.Label(self, text="Noch nicht geladen.")
        self.summary_label.pack(fill="x", padx=8)

        columns = ("name", "discount", "orig", "final", "gamepass")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        self._column_labels = {
            "name": "Name",
            "discount": "Rabatt",
            "orig": "Originalpreis",
            "final": "Preis jetzt",
            "gamepass": "Game Pass",
        }
        for col, label in self._column_labels.items():
            self.tree.heading(col, text=label)
        self.tree.column("name", width=340)
        self.tree.column("discount", width=70, anchor="e")
        self.tree.column("orig", width=110, anchor="e")
        self.tree.column("final", width=110, anchor="e")
        self.tree.column("gamepass", width=90, anchor="center")
        self._init_sorting({
            "name": lambda d: d["name"].lower(),
            "discount": lambda d: d["discount"],
            "orig": lambda d: d["orig_cents"],
            "final": lambda d: d["final_cents"],
            "gamepass": lambda d: d.get("gamepass", ""),
        })
        self.tree.bind("<Double-1>", self.open_store_page)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8))

        ttk.Label(
            self, text="Deine getrackten Spiele. Doppelklick öffnet die Store-Seite.",
            foreground="#666",
        ).pack(fill="x", padx=8, pady=(0, 4))

    def open_search_dialog(self):
        SearchDialog(self.winfo_toplevel(), self.add_game)

    def add_game(self, appid, name):
        if any(e["appid"] == appid for e in self.entries):
            messagebox.showinfo(APP_TITLE, f"'{name}' ist bereits auf der Wunschliste.")
            return
        self.entries.append({"appid": appid, "name": name})
        save_wishlist(self.entries)
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
        save_wishlist(self.entries)
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
            target=fetch_wishlist_prices, args=(self.entries, self.result_queue), daemon=True
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
                    items, total = payload
                    self.progress.pack_forget()
                    self._show_items(items)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_items(self, items):
        for item in items:
            item["gamepass"] = GAMEPASS.lookup(item["name"])
            iid = self.tree.insert(
                "", "end",
                values=(
                    item["name"],
                    f"-{item['discount']}%" if item["discount"] else "-",
                    item["orig_price_str"],
                    item["final_price_str"],
                    item["gamepass"] or "-",
                ),
            )
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
            webbrowser.open(f"https://store.steampowered.com/app/{item['appid']}/")


def main():
    GAMEPASS.start_loading()

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("820x560")

    try:
        style = ttk.Style()
        style.theme_use("vista")
    except tk.TclError:
        pass

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    discounts_tab = DiscountsTab(notebook)
    free_tab = FreeGamesTab(notebook)
    wishlist_tab = WishlistTab(notebook)
    notebook.add(discounts_tab, text="Rabatte")
    notebook.add(free_tab, text="Aktuell kostenlos")
    notebook.add(wishlist_tab, text="Wunschliste")

    root.after(200, discounts_tab.start_fetch)
    root.after(200, free_tab.start_fetch)
    root.after(200, wishlist_tab.start_fetch)

    root.mainloop()


if __name__ == "__main__":
    main()
