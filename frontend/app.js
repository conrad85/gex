// Zeeverse GEX (Lite)
// - uses ONLY /api/market (no wallet, no LP)
// - shows price + reserves (depth)

const API_BASE = (() => {
  if (window.location.protocol.startsWith("http") && window.location.host) {
    return `${window.location.protocol}//${window.location.host}`;
  }
  return "http://127.0.0.1:8000";
})();

const API_MARKET = `${API_BASE}/api/market`;
const API_VEE_PRICE = `${API_BASE}/api/vee_price`;

const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf6 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 6, maximumFractionDigits: 6 });

let rows = [];
let sortKey = "item_name";
let sortAsc = true;

function fmt(n, digits = 2) {
  const x = Number(n);
  if (!isFinite(x)) return "-";
  return digits === 6 ? nf6.format(x) : nf2.format(x);
}

function setLastUpdated() {
  const el = document.getElementById("lastUpdated");
  const now = new Date();
  el.textContent = `Last updated: ${now.toLocaleString()}`;
}

async function loadVeePrice() {
  try {
    const res = await fetch(API_VEE_PRICE);
    const j = await res.json();
    const el = document.getElementById("veePrice");
    if (j && typeof j.vee_usd === "number") {
      el.textContent = `VEE: $${j.vee_usd.toFixed(8)}`;
    } else {
      el.textContent = "VEE: n/a";
    }
  } catch (e) {
    const el = document.getElementById("veePrice");
    el.textContent = "VEE: n/a";
  }
}

async function loadMarket() {
  const res = await fetch(API_MARKET);
  const data = await res.json();

  // Expected shape: array of objects
  rows = Array.isArray(data) ? data : (data && data.pairs ? data.pairs : []);

  render();
  setLastUpdated();
}

function getFilteredRows() {
  const q = (document.getElementById("q").value || "").trim().toLowerCase();
  if (!q) return rows.slice();

  return rows.filter(r => {
    const name = String(r.item_name || "").toLowerCase();
    const id = String(r.item_id || "").toLowerCase();
    return name.includes(q) || id.includes(q);
  });
}

function sortRows(list) {
  const key = sortKey;
  const asc = sortAsc ? 1 : -1;

  return list.sort((a, b) => {
    const va = a[key];
    const vb = b[key];

    if (typeof va === "number" && typeof vb === "number") return (va - vb) * asc;

    const sa = String(va ?? "");
    const sb = String(vb ?? "");
    return sa.localeCompare(sb) * asc;
  });
}

function render() {
  const tbody = document.querySelector("#marketTable tbody");
  tbody.innerHTML = "";

  const filtered = getFilteredRows();
  const sorted = sortRows(filtered);

  document.getElementById("row-count").textContent = `Rows: ${sorted.length}`;

  for (const r of sorted) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="left">${r.item_name || r.item_id}</td>
      <td>${fmt(r.price_vee, 6)}</td>
      <td>${fmt(r.reserve_vee, 2)}</td>
      <td>${fmt(r.reserve_item, 2)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function bindSort() {
  const headers = document.querySelectorAll("#marketTable thead th[data-key]");
  headers.forEach(th => {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-key");
      if (!key) return;
      if (sortKey === key) {
        sortAsc = !sortAsc;
      } else {
        sortKey = key;
        sortAsc = true;
      }
      render();
    });
  });
}

function bindUI() {
  document.getElementById("refreshBtn").addEventListener("click", async () => {
    await loadVeePrice();
    await loadMarket();
  });

  document.getElementById("q").addEventListener("input", () => render());
}

async function boot() {
  bindUI();
  bindSort();
  await loadVeePrice();
  await loadMarket();
}

boot();
