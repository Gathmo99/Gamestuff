const CONSENT_KEY = "steamSaleWatcher.consent";

// Public CORS proxies used only for live wishlist lookups (Steam/Xbox) - a static site can't
// call store.steampowered.com or storeedgefd.dsx.mp.microsoft.com directly, neither sends CORS
// headers. Tried in order; if the first is down/rate-limited, the next is used.
const PROXY_TEMPLATES = [
  (url) => "https://corsproxy.io/?url=" + encodeURIComponent(url),
  (url) => "https://api.allorigins.win/raw?url=" + encodeURIComponent(url),
];

const PLATFORMS = {
  steam: {
    label: "Steam",
    storeUrl: (id) => `https://store.steampowered.com/app/${id}/`,
    tabs: {
      discounts: {
        label: "Rabatte", style: "discount", file: "data/discounts.json",
        showGamepass: true, showSavings: true,
      },
      free: {
        label: "Aktuell kostenlos", style: "free", file: "data/free.json",
        showGamepass: true, priceLabel: "Regulärer Preis",
        note: "Nur Spiele, die zeitlich begrenzt kostenlos sind (nicht dauerhaft Free-to-Play).",
        emptyText: "Gerade keine zeitlich begrenzten Gratis-Spiele gefunden.",
      },
      wishlist: { label: "Wunschliste", style: "wishlist-live", provider: "steam", showGamepass: true },
    },
  },
  xbox: {
    label: "Xbox",
    storeUrl: (id) => `https://www.microsoft.com/store/productId/${id}`,
    tabs: {
      discounts: {
        label: "Rabatte", style: "discount", file: "data/xbox_discounts.json",
        showGamepass: true, showSavings: true,
        note: "Momentaufnahme der aktuellen Top-Deals (keine vollständige Liste wie bei Steam).",
      },
      free: {
        label: "Free Play Days", style: "free", file: "data/xbox_free.json",
        showGamepass: true, priceLabel: "Regulärer Preis",
        note: "Zeitlich begrenzte Gratis-Testphasen für Gold-/Game-Pass-Mitglieder.",
        emptyText: "Gerade keine aktiven Free Play Days.",
      },
      wishlist: { label: "Wunschliste", style: "wishlist-live", provider: "xbox", showGamepass: true },
    },
  },
  playstation: {
    label: "PlayStation",
    storeUrl: (id) => `https://store.playstation.com/de-de/product/${id}`,
    tabs: {
      discounts: {
        label: "Rabatte", style: "discount", file: "data/ps_discounts.json",
        showGamepass: false, showSavings: true,
      },
      free: {
        label: "PS Plus Monatsspiele", style: "free", file: "data/ps_free.json",
        showGamepass: false, priceLabel: "Regulärer Preis",
        note: "Benötigt aktives PS-Plus-Abo.",
        emptyText: "Keine PS-Plus-Monatsspiele gefunden.",
      },
      wishlist: {
        label: "Wunschliste", style: "wishlist-static", file: "data/ps_wishlist.json",
        showGamepass: false,
        configHelp: `PlayStations Store-API blockiert Anfragen von fremden Websites strikt (auch über Proxys) –
          eine Live-Suche direkt im Browser ist hier technisch nicht möglich. Um ein Spiel zu tracken,
          <code>ps-wishlist.json</code> im Repository bearbeiten
          (Format: <code>[{"appid": "EP4295-CUSA17368_00-...", "name": "Spielname"}]</code>, die
          App-ID steht in der PS-Store-URL) und pushen – die Preise werden beim nächsten
          automatischen Update übernommen. Die Desktop-App hat dafür ein "Spiel hinzufügen"-Fenster,
          in das du einfach den Store-Link einfügen kannst.`,
      },
    },
  },
  epic: {
    label: "Epic",
    storeUrl: (id, name) => `https://store.epicgames.com/de/browse?q=${encodeURIComponent(name || "")}&sortBy=relevancy`,
    tabs: {
      discounts: {
        label: "Rabatte", style: "discount", file: "data/epic_discounts.json",
        showGamepass: false, showSavings: true,
      },
      free: {
        label: "Aktuell kostenlos", style: "free", file: "data/epic_free.json",
        showGamepass: false, priceLabel: "Regulärer Preis",
        note: "Epics wöchentliche Gratis-Spiele.",
        emptyText: "Gerade keine aktiven Gratis-Spiele gefunden.",
      },
      wishlist: {
        label: "Wunschliste", style: "wishlist-static", file: "data/epic_wishlist.json",
        showGamepass: false,
        configHelp: `Epics Store-API blockiert Suchanfragen von fremden Websites (Referer-Prüfung,
          greift auch über Proxys) – eine Live-Suche direkt im Browser ist hier technisch nicht
          möglich. Um ein Spiel zu tracken, <code>epic-wishlist.json</code> im Repository bearbeiten
          (Format: <code>[{"appid": "371545146a944478ad5a87f9b581ac29", "name": "Spielname"}]</code>)
          und pushen – die Preise werden beim nächsten automatischen Update übernommen. Die
          Desktop-App hat dafür ein eigenes Such-Fenster.`,
      },
    },
  },
};

const WISHLIST_PROVIDERS = {
  steam: { storageKey: "steamSaleWatcher.wishlist", search: steamSearch, livePrice: steamLivePrice },
  xbox: { storageKey: "steamSaleWatcher.xboxWishlist", search: xboxSearch, livePrice: xboxLivePrice },
};

const state = {
  platform: "steam",
  tab: "discounts",
  data: {},
  gamepassCatalog: {},
  wishlistStatus: {},
  sort: {},
  search: {},
  addSearch: { term: "", status: "idle", results: [] },
};

function dataKey(platform, tab) {
  return `${platform}.${tab}`;
}

function currentTabConfig() {
  return PLATFORMS[state.platform].tabs[state.tab];
}

function ensureSortSearch(key, defaultSortCol) {
  if (!state.sort[key]) state.sort[key] = { col: defaultSortCol, dir: defaultSortCol === "discount" ? -1 : 1 };
  if (state.search[key] === undefined) state.search[key] = "";
}

function columnsFor(tabConfig) {
  const cols = [{ key: "name", label: "Name", type: "text" }];
  const isDiscountStyle = tabConfig.style === "discount" || tabConfig.style.startsWith("wishlist");
  if (isDiscountStyle) {
    cols.push({ key: "discount", label: "Rabatt", type: "discount" });
    cols.push({ key: "orig_cents", label: "Originalpreis", type: "price", priceKey: "orig_price_str" });
    cols.push({ key: "final_cents", label: "Preis jetzt", type: "price", priceKey: "final_price_str" });
    if (tabConfig.showSavings) cols.push({ key: "savings", label: "Ersparnis", type: "savings" });
  } else {
    cols.push({ key: "orig_cents", label: tabConfig.priceLabel || "Preis", type: "price", priceKey: "orig_price_str" });
  }
  if (tabConfig.showGamepass) cols.push({ key: "gamepass", label: "Game Pass", type: "gamepass" });
  if (tabConfig.style === "wishlist-live") cols.push({ key: "remove", label: "", type: "remove" });
  return cols;
}

// ---------- Wishlist storage (local to this browser only) ----------

function loadWishlistEntries(providerKey) {
  try {
    const raw = localStorage.getItem(WISHLIST_PROVIDERS[providerKey].storageKey);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    return [];
  }
}

function saveWishlistEntries(providerKey, entries) {
  localStorage.setItem(WISHLIST_PROVIDERS[providerKey].storageKey, JSON.stringify(entries));
}

function addToWishlist(providerKey, appid, name) {
  const entries = loadWishlistEntries(providerKey);
  if (entries.some((e) => String(e.appid) === String(appid))) return;
  entries.push({ appid, name });
  saveWishlistEntries(providerKey, entries);
  refreshWishlist(providerKey);
}

function removeFromWishlist(providerKey, appid) {
  const entries = loadWishlistEntries(providerKey).filter((e) => String(e.appid) !== String(appid));
  saveWishlistEntries(providerKey, entries);
  state.data[dataKey(providerKey, "wishlist")] =
    (state.data[dataKey(providerKey, "wishlist")] || []).filter((i) => String(i.appid) !== String(appid));
  render();
}

// ---------- Proxied requests (live wishlist search + price for Steam/Xbox only) ----------

async function proxiedFetchJson(targetUrl) {
  let lastError;
  for (const template of PROXY_TEMPLATES) {
    try {
      const res = await fetch(template(targetUrl));
      if (!res.ok) throw new Error("HTTP " + res.status);
      return await res.json();
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error("Alle Proxies fehlgeschlagen");
}

async function steamSearch(term) {
  const url = `https://store.steampowered.com/api/storesearch/?term=${encodeURIComponent(term)}&cc=de&l=german`;
  const data = await proxiedFetchJson(url);
  return (data.items || [])
    .filter((it) => it.type === "app")
    .map((it) => ({ appid: it.id, name: it.name, final_cents: it.price ? it.price.final : null }));
}

async function steamLivePrice(appid) {
  const url = `https://store.steampowered.com/api/appdetails?appids=${appid}&cc=de&l=german&filters=price_overview`;
  const data = await proxiedFetchJson(url);
  const entry = data[String(appid)];
  if (!entry || !entry.success) return null;
  const price = entry.data && !Array.isArray(entry.data) ? entry.data.price_overview : null;
  if (!price) {
    return { discount: 0, orig_cents: 0, final_cents: 0, orig_price_str: "Kostenlos/F2P", final_price_str: "Kostenlos/F2P" };
  }
  const origCents = price.initial ?? price.final ?? 0;
  const finalCents = price.final ?? 0;
  return {
    discount: price.discount_percent || 0, orig_cents: origCents, final_cents: finalCents,
    orig_price_str: formatCents(origCents), final_price_str: formatCents(finalCents),
  };
}

async function xboxSearch(term) {
  const url = (
    "https://storeedgefd.dsx.mp.microsoft.com/v9.0/pages/searchResults"
    + `?appVersion=22203.1401.0.0&market=DE&locale=de-DE&deviceFamily=windows.xbox`
    + `&query=${encodeURIComponent(term)}&mediaType=games`
  );
  const data = await proxiedFetchJson(url);
  const payload = (data[1] && data[1].Payload) || {};
  return (payload.SearchResults || []).map((item) => ({
    appid: item.ProductId, name: item.Title, final_cents: null,
  }));
}

async function xboxLivePrice(appid) {
  // displaycatalog.mp.microsoft.com sends real CORS headers - no proxy needed here.
  const url = `https://displaycatalog.mp.microsoft.com/v7.0/products?bigIds=${appid}&market=DE&languages=de-de`;
  const res = await fetch(url);
  if (!res.ok) return null;
  const data = await res.json();
  const product = (data.Products || [])[0];
  if (!product) return null;
  const localized = (product.LocalizedProperties || [{}])[0];
  const name = localized.ProductTitle;
  const price = (((product.DisplaySkuAvailabilities || [])[0] || {}).Availabilities || [])[0];
  const priceInfo = price && price.OrderManagementData && price.OrderManagementData.Price;
  if (!priceInfo || (!priceInfo.MSRP && !priceInfo.ListPrice)) {
    return { name, discount: 0, orig_cents: 0, final_cents: 0, orig_price_str: "Kostenlos/F2P", final_price_str: "Kostenlos/F2P" };
  }
  const origCents = Math.round((priceInfo.MSRP || priceInfo.ListPrice || 0) * 100);
  const finalCents = Math.round((priceInfo.ListPrice || 0) * 100);
  const discount = origCents ? Math.round((1 - finalCents / origCents) * 100) : 0;
  return {
    name, discount, orig_cents: origCents, final_cents: finalCents,
    orig_price_str: formatCents(origCents), final_price_str: formatCents(finalCents),
  };
}

async function refreshWishlist(providerKey) {
  const provider = WISHLIST_PROVIDERS[providerKey];
  const entries = loadWishlistEntries(providerKey);
  const key = dataKey(providerKey, "wishlist");
  if (entries.length === 0) {
    state.data[key] = [];
    state.wishlistStatus[providerKey] = "idle";
    render();
    return;
  }
  state.wishlistStatus[providerKey] = "loading";
  render();

  const items = await Promise.all(entries.map(async (entry) => {
    try {
      const price = await provider.livePrice(entry.appid);
      if (!price) {
        return {
          appid: entry.appid, name: entry.name, gamepass: gamepassLookup(entry.name),
          discount: 0, orig_cents: 0, final_cents: 0, orig_price_str: "?", final_price_str: "nicht gefunden",
        };
      }
      const name = price.name || entry.name;
      return {
        appid: entry.appid, name, gamepass: gamepassLookup(name),
        discount: price.discount, orig_cents: price.orig_cents, final_cents: price.final_cents,
        orig_price_str: price.orig_price_str, final_price_str: price.final_price_str,
      };
    } catch (err) {
      return {
        appid: entry.appid, name: entry.name, gamepass: gamepassLookup(entry.name),
        discount: 0, orig_cents: 0, final_cents: 0, orig_price_str: "?", final_price_str: "Fehler beim Laden",
      };
    }
  }));
  state.data[key] = items;
  state.wishlistStatus[providerKey] = "idle";
  render();
}

// ---------- Game Pass lookup (mirrors scripts/fetch_data.py normalize_title) ----------

function normalizeTitle(name) {
  if (!name) return "";
  let n = name.replace(/[™®©]/g, "");
  n = n.replace(/[\(\[][^)\]]*[\)\]]/g, " ");
  n = n.replace(/\s*-\s*windows\s*$/i, "");
  n = n.toLowerCase();
  n = n.replace(/[^a-z0-9]+/g, " ");
  return n.trim();
}

function gamepassLookup(name) {
  const platforms = state.gamepassCatalog[normalizeTitle(name)];
  if (!platforms || platforms.length === 0) return "";
  if (platforms.length === 2) return "PC + Xbox";
  return platforms[0];
}

// ---------- Data loading (static, refreshed every 6h by GitHub Actions) ----------

async function loadData() {
  const fileFetches = [];
  for (const [platformKey, platform] of Object.entries(PLATFORMS)) {
    for (const [tabKey, tabConfig] of Object.entries(platform.tabs)) {
      if (!tabConfig.file) continue;
      fileFetches.push(
        fetchJson(tabConfig.file).then((json) => {
          state.data[dataKey(platformKey, tabKey)] = (json && json.items) || [];
          if (json && typeof json.total === "number") {
            state.data[dataKey(platformKey, tabKey) + ".total"] = json.total;
          }
        })
      );
    }
  }

  const [gamepass, meta] = await Promise.all([
    fetchJson("data/gamepass.json"),
    fetchJson("data/meta.json"),
    ...fileFetches,
  ]);
  state.gamepassCatalog = gamepass || {};

  if (meta && meta.updated) {
    const updated = new Date(meta.updated);
    document.getElementById("last-updated").textContent =
      "Rabatte & Kostenlos-Tabs zuletzt aktualisiert: " + updated.toLocaleString("de-DE") +
      " (automatisch alle 6h) · Steam-/Xbox-Wunschliste lädt live bei jedem Besuch";
  }

  render();
  refreshWishlist("steam");
  refreshWishlist("xbox");
}

async function fetchJson(path) {
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    return null;
  }
}

// ---------- Rendering ----------

function gamepassBadge(label) {
  if (!label) return `<span class="badge-gp-none">-</span>`;
  if (label === "PC + Xbox") return `<span class="badge badge-gp-both">PC + Xbox</span>`;
  if (label === "PC") return `<span class="badge badge-gp-pc">PC</span>`;
  return `<span class="badge badge-gp-xbox">Xbox</span>`;
}

function discountBadge(discount) {
  if (!discount) return `<span class="badge-gp-none">-</span>`;
  let cls = "badge-discount-lo";
  if (discount >= 66) cls = "badge-discount-hi";
  else if (discount >= 33) cls = "badge-discount-mid";
  return `<span class="badge ${cls}">-${discount}%</span>`;
}

function formatCents(cents) {
  if (cents === null || cents === undefined) return "?";
  return (cents / 100).toFixed(2).replace(".", ",") + " €";
}

function cellValue(item, col) {
  switch (col.type) {
    case "text":
      return `<span>${escapeHtml(item.name)}</span>`;
    case "discount":
      return discountBadge(item.discount);
    case "price":
      return escapeHtml(item[col.priceKey] || formatCents(item[col.key]));
    case "savings":
      return formatCents((item.orig_cents || 0) - (item.final_cents || 0));
    case "gamepass":
      return gamepassBadge(item.gamepass);
    case "remove":
      return `<button class="remove-btn" data-remove="${item.appid}" title="Entfernen">✕</button>`;
    default:
      return "";
  }
}

function sortValue(item, col) {
  if (col.type === "savings") return (item.orig_cents || 0) - (item.final_cents || 0);
  if (col.key === "name") return (item.name || "").toLowerCase();
  return item[col.key] ?? 0;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function filteredSorted() {
  const key = dataKey(state.platform, state.tab);
  const tabConfig = currentTabConfig();
  const cols = columnsFor(tabConfig);
  ensureSortSearch(key, tabConfig.style === "discount" ? "discount" : "name");
  const sortCol = cols.find((c) => c.key === state.sort[key].col) || cols[0];
  const term = (state.search[key] || "").trim().toLowerCase();

  let items = state.data[key] || [];
  if (term) {
    items = items.filter((it) => it.name.toLowerCase().includes(term));
  }
  const dir = state.sort[key].dir;
  items = [...items].sort((a, b) => {
    const va = sortValue(a, sortCol);
    const vb = sortValue(b, sortCol);
    if (va < vb) return -1 * dir;
    if (va > vb) return 1 * dir;
    return 0;
  });
  return items;
}

function renderTableHead() {
  const key = dataKey(state.platform, state.tab);
  const cols = columnsFor(currentTabConfig());
  const sort = state.sort[key] || {};
  return cols
    .map((c) => {
      if (c.type === "remove") return `<th></th>`;
      const isNum = c.type !== "text";
      const sorted = c.key === sort.col;
      const arrow = sorted ? (sort.dir === 1 ? " ▲" : " ▼") : "";
      return `<th class="${sorted ? "sorted" : ""} ${isNum ? "num" : ""}" data-col="${c.key}">${c.label}${arrow}</th>`;
    })
    .join("");
}

function renderTableBody() {
  const tabConfig = currentTabConfig();
  const cols = columnsFor(tabConfig);
  const items = filteredSorted();
  const providerKey = tabConfig.provider;

  if (tabConfig.style === "wishlist-live" && items.length === 0 && state.wishlistStatus[providerKey] === "loading") {
    return `<tr class="loading-row"><td colspan="${cols.length}">Lade Preise...</td></tr>`;
  }
  if (items.length === 0) {
    const msg = tabConfig.style === "wishlist-live"
      ? "Noch keine Spiele auf der Wunschliste."
      : (tabConfig.emptyText || "Keine Einträge gefunden.");
    return `<tr class="loading-row"><td colspan="${cols.length}">${msg}</td></tr>`;
  }
  return items
    .map((item) => {
      const cells = cols
        .map((c, i) => {
          const isNum = c.type !== "text" && c.type !== "remove";
          const nameCell = i === 0 ? "name-cell" : "";
          return `<td class="${isNum ? "num" : ""} ${nameCell}">${cellValue(item, c)}</td>`;
        })
        .join("");
      return `<tr data-appid="${item.appid}" data-name="${escapeHtml(item.name)}">${cells}</tr>`;
    })
    .join("");
}

function renderAddPanel() {
  const tabConfig = currentTabConfig();
  const panel = document.getElementById("add-game-panel");
  const staticHelp = document.getElementById("static-wishlist-help");

  if (tabConfig.style === "wishlist-static") {
    panel.style.display = "none";
    staticHelp.innerHTML = tabConfig.configHelp || "";
    staticHelp.style.display = "block";
    return;
  }
  staticHelp.style.display = "none";

  if (tabConfig.style !== "wishlist-live") {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "block";

  const resultsEl = document.getElementById("add-game-results");
  if (state.addSearch.status === "loading") {
    resultsEl.innerHTML = `<div class="add-result-empty">Suche...</div>`;
  } else if (state.addSearch.status === "error") {
    resultsEl.innerHTML = `<div class="add-result-empty">Suche fehlgeschlagen (Proxy nicht erreichbar). Später erneut versuchen.</div>`;
  } else if (state.addSearch.results.length > 0) {
    resultsEl.innerHTML = state.addSearch.results
      .map((r) => {
        const price = r.final_cents === null || r.final_cents === undefined
          ? "Kostenlos/F2P"
          : formatCents(r.final_cents);
        return `<div class="add-result" data-add-appid="${r.appid}" data-add-name="${escapeHtml(r.name)}">
          <span>${escapeHtml(r.name)}</span><span class="add-result-price">${price}</span>
        </div>`;
      })
      .join("");
  } else {
    resultsEl.innerHTML = "";
  }
}

function updateStats() {
  const discountsKey = dataKey(state.platform, "discounts");
  const freeKey = dataKey(state.platform, "free");
  const wishlistKey = dataKey(state.platform, "wishlist");
  const freeTabConfig = PLATFORMS[state.platform].tabs.free;

  document.getElementById("stat-discounts").textContent = (state.data[discountsKey] || []).length;
  const total = state.data[discountsKey + ".total"];
  document.getElementById("stat-discounts-total").textContent = total ? `von ${total} insgesamt` : "";
  document.getElementById("stat-free").textContent = (state.data[freeKey] || []).length;
  document.getElementById("stat-free-label").textContent = freeTabConfig.label.toLowerCase();
  document.getElementById("stat-wishlist").textContent = (state.data[wishlistKey] || []).length;
}

function render() {
  const tabConfig = currentTabConfig();

  document.getElementById("table-head").innerHTML = renderTableHead();
  document.getElementById("table-body").innerHTML = renderTableBody();
  document.getElementById("result-count").textContent = `${filteredSorted().length} Einträge`;

  const noteEl = document.getElementById("tab-note");
  if (tabConfig.note) {
    noteEl.textContent = tabConfig.note;
    noteEl.style.display = "block";
  } else {
    noteEl.style.display = "none";
  }

  renderAddPanel();
  updateStats();

  document.querySelectorAll(".platform-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.platform === state.platform);
  });
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const key = btn.dataset.tab;
    btn.textContent = PLATFORMS[state.platform].tabs[key].label;
    btn.classList.toggle("active", key === state.tab);
  });
  const key = dataKey(state.platform, state.tab);
  document.getElementById("search-input").value = state.search[key] || "";
}

// ---------- Events ----------

function setupEvents() {
  document.querySelectorAll(".platform-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.platform = btn.dataset.platform;
      render();
    });
  });

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      render();
    });
  });

  document.getElementById("search-input").addEventListener("input", (e) => {
    const key = dataKey(state.platform, state.tab);
    ensureSortSearch(key, currentTabConfig().style === "discount" ? "discount" : "name");
    state.search[key] = e.target.value;
    render();
  });

  document.getElementById("table-head").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th || !th.dataset.col) return;
    const key = dataKey(state.platform, state.tab);
    const col = th.dataset.col;
    const sort = state.sort[key];
    if (sort.col === col) {
      sort.dir *= -1;
    } else {
      sort.col = col;
      sort.dir = ["discount", "orig_cents", "final_cents", "savings"].includes(col) ? -1 : 1;
    }
    render();
  });

  document.getElementById("table-body").addEventListener("click", (e) => {
    const removeBtn = e.target.closest("[data-remove]");
    if (removeBtn) {
      removeFromWishlist(state.platform, removeBtn.dataset.remove);
      return;
    }
    const row = e.target.closest("tr[data-appid]");
    if (!row) return;
    window.open(PLATFORMS[state.platform].storeUrl(row.dataset.appid, row.dataset.name), "_blank", "noopener");
  });

  const addToggle = document.getElementById("add-game-toggle");
  const addForm = document.getElementById("add-game-form");
  addToggle.addEventListener("click", () => {
    addForm.classList.toggle("open");
    if (addForm.classList.contains("open")) {
      document.getElementById("add-game-input").focus();
    }
  });

  const addInput = document.getElementById("add-game-input");
  const runSearch = async () => {
    const term = addInput.value.trim();
    if (!term) return;
    const provider = WISHLIST_PROVIDERS[currentTabConfig().provider];
    state.addSearch = { term, status: "loading", results: [] };
    renderAddPanel();
    try {
      const results = await provider.search(term);
      state.addSearch = { term, status: "done", results };
    } catch (err) {
      state.addSearch = { term, status: "error", results: [] };
    }
    renderAddPanel();
  };
  document.getElementById("add-game-search-btn").addEventListener("click", runSearch);
  addInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });

  document.getElementById("add-game-results").addEventListener("click", (e) => {
    const el = e.target.closest("[data-add-appid]");
    if (!el) return;
    addToWishlist(currentTabConfig().provider, el.dataset.addAppid, el.dataset.addName);
    addInput.value = "";
    state.addSearch = { term: "", status: "idle", results: [] };
    addForm.classList.remove("open");
  });

  const themeToggle = document.getElementById("theme-toggle");
  themeToggle.addEventListener("click", () => {
    const root = document.documentElement;
    const current = root.getAttribute("data-theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    themeToggle.textContent = next === "dark" ? "☀️" : "🌙";
  });

  const savedTheme = localStorage.getItem("theme");
  if (savedTheme) {
    document.documentElement.setAttribute("data-theme", savedTheme);
    themeToggle.textContent = savedTheme === "dark" ? "☀️" : "🌙";
  }

  setupConsentBanner();
}

function setupConsentBanner() {
  const banner = document.getElementById("consent-banner");
  if (localStorage.getItem(CONSENT_KEY)) {
    banner.style.display = "none";
    return;
  }
  banner.style.display = "flex";
  document.getElementById("consent-accept").addEventListener("click", () => {
    localStorage.setItem(CONSENT_KEY, "1");
    banner.style.display = "none";
  });
}

setupEvents();
loadData();
