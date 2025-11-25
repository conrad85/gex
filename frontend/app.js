// ===== CONFIG =====
// Jeśli chcesz LP: podaj adres; jeśli nie: pusty string.
const WALLET = "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0";
const API_BASE = (() => {
  if (window.location.protocol.startsWith("http") && window.location.host) {
    return `${window.location.protocol}//${window.location.host}`;
  }
  return "http://127.0.0.1:8000";
})();
const API_URL_BASE = `${API_BASE}/api/market`;
const API_URL_WALLET = WALLET ? `${API_URL_BASE}/${WALLET}` : null;

// ===== STATE =====
let rows = [];
let filteredRows = [];
let baseRows = [];

const nf2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const dateFmt = new Intl.DateTimeFormat(undefined, {
  year: "2-digit",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const state = {
  sortKey: "item_name",
  sortDir: "asc",
  filter: "",
};

// ===== LOAD MARKET =====
async function loadMarketBase() {
  try {
    const res = await fetch(API_URL_BASE);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (Array.isArray(data)) {
      baseRows = data;

      // zachowujemy stare LP zanim nadpiszemy "rows"
      const prevMap = new Map(
        rows.map((r) => [String(r.pair_address).toLowerCase(), r])
      );

      rows = data.map((r) => {
        const key = String(r.pair_address).toLowerCase();
        const prev = prevMap.get(key) || {};

        return {
          ...r,
          lp_balance: prev.lp_balance ?? 0,
          lp_share: prev.lp_share ?? 0,
          user_item: prev.user_item ?? 0,
          user_vee: prev.user_vee ?? 0,
        };
      });

      applyFilterAndSort();
      updateUpdatedLabel();
    } else {
      console.warn("Unexpected response shape", data);
    }
  } catch (err) {
    console.error("Failed to load base market", err);
  }
}

async function loadMarketWallet() {
  if (!API_URL_WALLET) return;
  try {
    const res = await fetch(API_URL_WALLET);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!Array.isArray(data)) {
      console.warn("Unexpected wallet response shape", data);
      return;
    }
    const walletMap = new Map(
      data.map((r) => [String(r.pair_address).toLowerCase(), r])
    );
    rows = rows.map((r) => {
      const w = walletMap.get(String(r.pair_address).toLowerCase());
      if (!w) return r;
      return {
        ...r,
        lp_balance: w.lp_balance,
        lp_share: w.lp_share,
        user_item: w.user_item,
        user_vee: w.user_vee,
      };
    });
    applyFilterAndSort();
  } catch (err) {
    console.error("Failed to load wallet market", err);
  }
}

// ===== FILTER + SORT =====
function applyFilterAndSort() {
  const f = state.filter.trim().toLowerCase();

  filteredRows = rows.filter((r) =>
    !f ? true : (r.item_name || "").toLowerCase().includes(f)
  );

  const key = state.sortKey;
  const dir = state.sortDir === "asc" ? 1 : -1;

  filteredRows.sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "number" && typeof vb === "number") {
      return (va - vb) * dir;
    }
    return String(va).localeCompare(String(vb)) * dir;
  });

  renderTable();
}

// ===== RENDER TABLE =====
function renderTable() {
  const tbody = document.getElementById("market-body");
  tbody.innerHTML = "";

  for (const row of filteredRows) {
    const tr = document.createElement("tr");

    // podświetl LP, jeśli coś masz w tej puli
    if ((row.lp_share || 0) > 0) {
      tr.classList.add("has-lp");
    }

    // item
    const tdItem = document.createElement("td");
    tdItem.innerHTML = `
      <span>${row.item_name || "?"}</span>
      <span class="pool-address">${row.pair_address || ""}</span>
    `;
    tr.appendChild(tdItem);

    tr.appendChild(numCell(row.price_vee));
    tr.appendChild(numCell(row.reserve_vee));
    tr.appendChild(numCell(row.reserve_item));
    tr.appendChild(numCell(row.volume_24h_est));
    tr.appendChild(numCell(row.volume_7d_vee));

    const tdTs = document.createElement("td");
    tdTs.textContent = row.ts ? formatTs(row.ts) : "";
    tr.appendChild(tdTs);

    // LP COLUMNS
    tr.appendChild(numCell((row.lp_share || 0) * 100));
    tr.appendChild(numCell(row.user_item || 0));
    tr.appendChild(numCell(row.user_vee || 0));

    tbody.appendChild(tr);
  }

  const countLabel = document.getElementById("row-count");
  countLabel.textContent = `${filteredRows.length} items`;
}

function numCell(v) {
  const td = document.createElement("td");
  td.className = "num";
  td.textContent = v == null ? "-" : nf2.format(v);
  return td;
}

function formatTs(ts) {
  try {
    const d = new Date(ts);
    if (!isNaN(d.getTime())) return dateFmt.format(d);
  } catch {}
  return ts;
}

// ===== SORTING =====
document.addEventListener("click", (ev) => {
  const th = ev.target.closest("th[data-key]");
  if (!th) return;

  const key = th.getAttribute("data-key");

  if (state.sortKey === key) {
    state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
  } else {
    state.sortKey = key;
    state.sortDir = "asc";
  }

  document
    .querySelectorAll("th[data-key]")
    .forEach((el) => el.classList.remove("sorted"));
  th.classList.add("sorted");

  applyFilterAndSort();
});

// ===== SEARCH =====
document.getElementById("search").addEventListener("input", () => {
  state.filter = document.getElementById("search").value;
  applyFilterAndSort();
});

// ===== LABEL =====
function updateUpdatedLabel() {
  const el = document.getElementById("last-updated");
  const maxTs = Math.max(
    ...rows.map((r) => (r.ts ? new Date(r.ts).getTime() : 0))
  );
  if (maxTs > 0) {
    el.textContent = "Last update: " + dateFmt.format(new Date(maxTs));
  }
}

// ===== INIT =====
async function refreshAll() {
  await loadMarketBase(); 
  await loadMarketWallet();
}

refreshAll();
setInterval(refreshAll, 60_000);

