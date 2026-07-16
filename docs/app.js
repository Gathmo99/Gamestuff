const STEAM_CC = "de";
const STEAM_LANG = "german";
const WISHLIST_KEY = "steamSaleWatcher.wishlist";
const CONSENT_KEY = "steamSaleWatcher.consent";

// Public CORS proxies used only for the Wunschliste tab (live per-game lookups on a static
// site can't call store.steampowered.com directly - it sends no CORS headers). Tried in
// order; if the first is down/rate-limited, the next is used.
const PROXY_TEMPLATES = [
  (url) => "https://corsproxy.io/?url=" + encodeURIComponent(url),
  (url) => "https://api.allorigins.win/raw?url=" + encodeURIComponent(url),
];

const state = {
  tab: "discounts",
  data: { discounts: [], free: [], wishlist: [] },
  gamepassCatalog: {},
  wishlistStatus: "idle", // idle | loading | error
  sort: {
    discounts: { col: "discount", dir: -1 },
    free: { col: "name", dir: 1 },
    wishlist: { col: "name", dir: 1 },
  },
  search: { discounts: "", free: "", wishlist: "" },
  addSearch: { term: "", status: "idle", results: [] },
};

const COLUMNS = {
  discounts: [
    { key: "name", label: "Name", type: "text" },
    { key: "discount", label: "Rabatt", type: "discount" },
    { key: "orig_cents", label: "Originalpreis", type: "price", priceKey: "orig_price_str" },
    { key: "final_cents", label: "Preis jetzt", type: "price", priceKey: "final_price_str" },
    { key: "savings", label: "Ersparnis", type: "savings" },
    { key: "gamepass", label: "Game Pass", type: "gamepass" },
  ],
  free: [
    { key: "name", label: "Name", type: "text" },
    { key: "orig_cents", label: "Regulärer Preis", type: "price", priceKey: "orig_price_str" },
    { key: "gamepass", label: "Game Pass", type: "gamepass" },
  ],
  wishlist: [
    { key: "name", label: "Name", type: "text" },
    { key: "discount", label: "Rabatt", type: "discount" },
    { key: "orig_cents", label: "Originalpreis", type: "price", priceKey: "orig_price_str" },
    { key: "final_cents", label: "Preis jetzt", type: "price", priceKey: "final_price_str" },
    { key: "gamepass", label: "Game Pass", type: "gamepass" },
    { key: "remove", label: "", type: "remove" },
  ],
};

// ---------- Wishlist storage (local to this browser only) ----------

function loadWishlistEntries() {
  try {
    const raw = localStorage.getItem(WISHLIST_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    return [];
  }
}

function saveWishlistEntries(entries) {
  localStorage.setItem(WISHLIST_KEY, JSON.stringify(entries));
}

function addToWishlist(appid, name) {
  appid = Number(appid);
  const entries = loadWishlistEntries();
  if (entries.some((e) => Number(e.appid) === appid)) return;
  entries.push({ appid, name });
  saveWishlistEntries(entries);
  refreshWishlist();
}

function removeFromWishlist(appid) {
  appid = Number(appid);
  const entries = loadWishlistEntries().filter((e) => Number(e.appid) !== appid);
  saveWishlistEntries(entries);
  state.data.wishlist = state.data.wishlist.filter((i) => Number(i.appid) !== appid);
  render();
}

// ---------- Proxied Steam requests (search + live price, wishlist only) ----------

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
  const url = `https://store.steampowered.com/api/storesearch/?term=${encodeURIComponent(term)}&cc=${STEAM_CC}&l=${STEAM_LANG}`;
  const data = await proxiedFetchJson(url);
  return (data.items || [])
    .filter((it) => it.type === "app")
    .map((it) => ({
      appid: it.id,
      name: it.name,
      final_cents: it.price ? it.price.final : null,
    }));
}

async function fetchLivePrice(appid) {
  const url = `https://store.steampowered.com/api/appdetails?appids=${appid}&cc=${STEAM_CC}&l=${STEAM_LANG}&filters=price_overview`;
  const data = await proxiedFetchJson(url);
  const entry = data[String(appid)];
  if (!entry || !entry.success) return null;
  const price = entry.data && !Array.isArray(entry.data) ? entry.data.price_overview : null;
  if (!price) {
    return {
      discount: 0, orig_cents: 0, final_cents: 0,
      orig_price_str: "Kostenlos/F2P", final_price_str: "Kostenlos/F2P",
    };
  }
  const origCents = price.initial ?? price.final ?? 0;
  const finalCents = price.final ?? 0;
  return {
    discount: price.discount_percent || 0,
    orig_cents: origCents,
    final_cents: finalCents,
    orig_price_str: formatCents(origCents),
    final_price_str: formatCents(finalCents),
  };
}

async function refreshWishlist() {
  const entries = loadWishlistEntries();
  document.getElementById("stat-wishlist").textContent = entries.length;
  if (entries.length === 0) {
    state.data.wishlist = [];
    state.wishlistStatus = "idle";
    render();
    return;
  }
  state.wishlistStatus = "loading";
  render();

  const items = await Promise.all(
    entries.map(async (entry) => {
      try {
        const price = await fetchLivePrice(entry.appid);
        const base = price || {
          discount: 0, orig_cents: 0, final_cents: 0,
          orig_price_str: "?", final_price_str: "nicht gefunden",
        };
        return { appid: entry.appid, name: entry.name, gamepass: gamepassLookup(entry.name), ...base };
      } catch (err) {
        return {
          appid: entry.appid, name: entry.name, gamepass: gamepassLookup(entry.name),
          discount: 0, orig_cents: 0, final_cents: 0,
          orig_price_str: "?", final_price_str: "Fehler beim Laden",
        };
      }
    })
  );
  state.data.wishlist = items;
  state.wishlistStatus = "idle";
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
  const [discounts, free, gamepass, meta] = await Promise.all([
    fetchJson("data/discounts.json"),
    fetchJson("data/free.json"),
    fetchJson("data/gamepass.json"),
    fetchJson("data/meta.json"),
  ]);

  state.data.discounts = (discounts && discounts.items) || [];
  state.data.free = (free && free.items) || [];
  state.gamepassCatalog = gamepass || {};

  document.getElementById("stat-discounts").textContent = state.data.discounts.length;
  document.getElementById("stat-discounts-total").textContent =
    discounts && discounts.total ? `von ${discounts.total} insgesamt` : "";
  document.getElementById("stat-free").textContent = state.data.free.length;

  if (meta && meta.updated) {
    const updated = new Date(meta.updated);
    document.getElementById("last-updated").textContent =
      "Rabatte & Gratis-Spiele zuletzt aktualisiert: " + updated.toLocaleString("de-DE") +
      " (automatisch alle 6h) · Wunschliste lädt live bei jedem Besuch";
  }

  render();
  refreshWishlist();
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

function filteredSorted(tab) {
  const cols = COLUMNS[tab];
  const sortCol = cols.find((c) => c.key === state.sort[tab].col) || cols[0];
  const term = state.search[tab].trim().toLowerCase();

  let items = state.data[tab];
  if (term) {
    items = items.filter((it) => it.name.toLowerCase().includes(term));
  }
  items = [...items].sort((a, b) => {
    const va = sortValue(a, sortCol);
    const vb = sortValue(b, sortCol);
    if (va < vb) return -1 * state.sort[tab].dir;
    if (va > vb) return 1 * state.sort[tab].dir;
    return 0;
  });
  return items;
}

function renderTableHead(tab) {
  const cols = COLUMNS[tab];
  const sort = state.sort[tab];
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

function renderTableBody(tab) {
  const cols = COLUMNS[tab];
  const items = filteredSorted(tab);

  if (tab === "wishlist" && items.length === 0 && state.wishlistStatus === "loading") {
    return `<tr class="loading-row"><td colspan="${cols.length}">Lade Preise...</td></tr>`;
  }
  if (items.length === 0) {
    const msg = tab === "wishlist"
      ? "Noch keine Spiele auf der Wunschliste."
      : "Keine Einträge gefunden.";
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
      return `<tr data-appid="${item.appid}">${cells}</tr>`;
    })
    .join("");
}

function renderAddPanel() {
  const panel = document.getElementById("add-game-panel");
  if (state.tab !== "wishlist") {
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

function render() {
  const tab = state.tab;
  document.getElementById("table-head").innerHTML = renderTableHead(tab);
  document.getElementById("table-body").innerHTML = renderTableBody(tab);
  document.getElementById("result-count").textContent = `${filteredSorted(tab).length} Einträge`;

  renderAddPanel();

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.getElementById("search-input").value = state.search[tab];
}

// ---------- Events ----------

function setupEvents() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      render();
    });
  });

  document.getElementById("search-input").addEventListener("input", (e) => {
    state.search[state.tab] = e.target.value;
    render();
  });

  document.getElementById("table-head").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th || !th.dataset.col) return;
    const col = th.dataset.col;
    const sort = state.sort[state.tab];
    if (sort.col === col) {
      sort.dir *= -1;
    } else {
      sort.col = col;
      sort.dir = col === "discount" || col === "orig_cents" || col === "final_cents" || col === "savings" ? -1 : 1;
    }
    render();
  });

  document.getElementById("table-body").addEventListener("click", (e) => {
    const removeBtn = e.target.closest("[data-remove]");
    if (removeBtn) {
      removeFromWishlist(removeBtn.dataset.remove);
      return;
    }
    const row = e.target.closest("tr[data-appid]");
    if (!row) return;
    window.open(`https://store.steampowered.com/app/${row.dataset.appid}/`, "_blank", "noopener");
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
    state.addSearch = { term, status: "loading", results: [] };
    renderAddPanel();
    try {
      const results = await steamSearch(term);
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
    addToWishlist(Number(el.dataset.addAppid), el.dataset.addName);
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
