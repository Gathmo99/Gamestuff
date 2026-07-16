const state = {
  tab: "discounts",
  data: { discounts: [], free: [], wishlist: [] },
  sort: {
    discounts: { col: "discount", dir: -1 },
    free: { col: "name", dir: 1 },
    wishlist: { col: "name", dir: 1 },
  },
  search: { discounts: "", free: "", wishlist: "" },
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
  ],
};

async function loadData() {
  const [discounts, free, wishlist, meta] = await Promise.all([
    fetchJson("data/discounts.json"),
    fetchJson("data/free.json"),
    fetchJson("data/wishlist.json"),
    fetchJson("data/meta.json"),
  ]);

  state.data.discounts = (discounts && discounts.items) || [];
  state.data.free = (free && free.items) || [];
  state.data.wishlist = (wishlist && wishlist.items) || [];

  document.getElementById("stat-discounts").textContent = state.data.discounts.length;
  document.getElementById("stat-discounts-total").textContent =
    discounts && discounts.total ? `von ${discounts.total} insgesamt` : "";
  document.getElementById("stat-free").textContent = state.data.free.length;
  document.getElementById("stat-wishlist").textContent = state.data.wishlist.length;

  if (meta && meta.updated) {
    const updated = new Date(meta.updated);
    document.getElementById("last-updated").textContent =
      "Zuletzt aktualisiert: " + updated.toLocaleString("de-DE");
  }

  render();
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
  if (items.length === 0) {
    return `<tr class="loading-row"><td colspan="${cols.length}">Keine Einträge gefunden.</td></tr>`;
  }
  return items
    .map((item) => {
      const cells = cols
        .map((c, i) => {
          const isNum = c.type !== "text";
          const nameCell = i === 0 ? "name-cell" : "";
          return `<td class="${isNum ? "num" : ""} ${nameCell}">${cellValue(item, c)}</td>`;
        })
        .join("");
      return `<tr data-appid="${item.appid}">${cells}</tr>`;
    })
    .join("");
}

function render() {
  const tab = state.tab;
  document.getElementById("table-head").innerHTML = renderTableHead(tab);
  document.getElementById("table-body").innerHTML = renderTableBody(tab);
  document.getElementById("result-count").textContent = `${filteredSorted(tab).length} Einträge`;

  document.getElementById("wishlist-help").style.display = tab === "wishlist" ? "block" : "none";

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.getElementById("search-input").value = state.search[tab];
}

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
    if (!th) return;
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
    const row = e.target.closest("tr[data-appid]");
    if (!row) return;
    window.open(`https://store.steampowered.com/app/${row.dataset.appid}/`, "_blank", "noopener");
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
}

setupEvents();
loadData();
