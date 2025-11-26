// ===== CONFIG =====
// Jeśli chcesz LP: podaj adres; jeśli nie: pusty string.
const WALLET = "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0";
// Szacowany fee rate GEX (np. 5% = 0.05, 0.3% = 0.003)
const FEE_RATE = 0.05;

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
const pctFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
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
  hideZeroLP: false,
};

// ===== HELPERS APR / LABELS =====

function formatTs(ts) {
  try {
    const d = new Date(ts);
    if (!isNaN(d.getTime())) return dateFmt.format(d);
  } catch {}
  return ts;
}

function calcApr(row) {
  const earn7 = row.lp_earn_vee_7d || 0;
  const myVee = row.user_vee || 0;
  if (earn7 <= 0 || myVee <= 0) return null;
  const daily = earn7 / 7;
  const apr = (daily * 365) / myVee * 100;
  return apr;
}

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

        const merged = {
          ...r,
          lp_balance: prev.lp_balance ?? 0,
          lp_share: prev.lp_share ?? 0,
          user_item: prev.user_item ?? 0,
          user_vee: prev.user_vee ?? 0,
          lp_earn_vee_24h: prev.lp_earn_vee_24h ?? 0,
          lp_earn_vee_7d: prev.lp_earn_vee_7d ?? 0,
        };

        return merged;
      });

      // fallback: jeśli backend nie policzył fee, policz na froncie z volume + FEE_RATE
      rows = rows.map((r) => {
        const share = r.lp_share || 0;
        if (share <= 0) {
          const withFees = {
            ...r,
            lp_earn_vee_24h: r.lp_earn_vee_24h || 0,
            lp_earn_vee_7d: r.lp_earn_vee_7d || 0,
          };
          return {
            ...withFees,
            lp_apr: calcApr(withFees),
          };
        }

        const vol24 = r.volume_24h_est || r.volume_24h_vee || 0;
        const vol7 = r.volume_7d_vee || 0;

        const earn24 =
          r.lp_earn_vee_24h != null
            ? r.lp_earn_vee_24h
            : vol24 * FEE_RATE * share;
        const earn7 =
          r.lp_earn_vee_7d != null ? r.lp_earn_vee_7d : vol7 * FEE_RATE * share;

        const withFees = {
          ...r,
          lp_earn_vee_24h: earn24,
          lp_earn_vee_7d: earn7,
        };
        return {
          ...withFees,
          lp_apr: calcApr(withFees),
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

      const share = w.lp_share || r.lp_share || 0;
      const vol24 = r.volume_24h_est || r.volume_24h_vee || 0;
      const vol7 = r.volume_7d_vee || 0;

      const earn24 =
        w.lp_earn_vee_24h != null
          ? w.lp_earn_vee_24h
          : r.lp_earn_vee_24h != null
          ? r.lp_earn_vee_24h
          : vol24 * FEE_RATE * share;
      const earn7 =
        w.lp_earn_vee_7d != null
          ? w.lp_earn_vee_7d
          : r.lp_earn_vee_7d != null
          ? r.lp_earn_vee_7d
          : vol7 * FEE_RATE * share;

      const merged = {
        ...r,
        lp_balance: w.lp_balance,
        lp_share: share,
        user_item: w.user_item,
        user_vee: w.user_vee,
        lp_earn_vee_24h: earn24,
        lp_earn_vee_7d: earn7,
      };
      return {
        ...merged,
        lp_apr: calcApr(merged),
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

  filteredRows = rows.filter((r) => {
    if (state.hideZeroLP && (!r.lp_share || r.lp_share <= 0)) return false;
    if (f && !(r.item_name || "").toLowerCase().includes(f)) return false;
    return true;
  });

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
  renderLPSummary();
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

    // Item + pair (JAKO LINK)
    const tdItem = document.createElement("td");
    tdItem.innerHTML = `
      <a href="item.html?pair=${row.pair_address}" class="item-link">
        ${row.item_name || "?"}
      </a>
      <span class="pool-address">${row.pair_address || ""}</span>
    `;
    tr.appendChild(tdItem);

    // Price + Δ
    tr.appendChild(numCell(row.price_vee));
    tr.appendChild(
      numCell(
        row.price_change_24h_pct,
        "pctDelta",
        row.price_24h_ago
          ? `Prev 24h price: ${nf2.format(row.price_24h_ago)} VEE`
          : "No 24h history"
      )
    );
    tr.appendChild(
      numCell(
        row.price_change_7d_pct,
        "pctDelta",
        row.price_7d_ago
          ? `Prev 7d price: ${nf2.format(row.price_7d_ago)} VEE`
          : "No 7d history"
      )
    );

    // Volume + Δ
    const vol24 = row.volume_24h_vee || row.volume_24h_est || 0;
    const vol7 = row.volume_7d_vee || 0;

    tr.appendChild(numCell(vol24));
    tr.appendChild(
      numCell(
        row.volume_change_24h_pct,
        "pctDelta",
        row.volume_24h_prev_vee
          ? `Prev 24h vol: ${nf2.format(row.volume_24h_prev_vee)} VEE`
          : "No prev 24h window"
      )
    );
    tr.appendChild(numCell(vol7));
    tr.appendChild(
      numCell(
        row.volume_change_7d_pct,
        "pctDelta",
        row.volume_7d_prev_vee
          ? `Prev 7d vol: ${nf2.format(row.volume_7d_prev_vee)} VEE`
          : "No prev 7d window"
      )
    );

    // LP
    const lpPct = (row.lp_share || 0) * 100;
    tr.appendChild(numCell(lpPct, "pct"));

    // Fees
    tr.appendChild(numCell(row.lp_earn_vee_24h || 0));
    tr.appendChild(numCell(row.lp_earn_vee_7d || 0));

    // APR
    tr.appendChild(numCell(row.lp_apr, "pct"));

    // Updated (na końcu)
    const tdTs = document.createElement("td");
    tdTs.textContent = row.ts ? formatTs(row.ts) : "";
    tr.appendChild(tdTs);

    tbody.appendChild(tr);
  }

  const countLabel = document.getElementById("row-count");
  countLabel.textContent = `${filteredRows.length} items`;
}

function numCell(v, kind, tooltip) {
  const td = document.createElement("td");
  td.className = "num";

  if (tooltip) {
    td.classList.add("has-tooltip");
    td.setAttribute("data-tooltip", tooltip);
  }

  if (v == null) {
    td.textContent = "-";
    return td;
  }

  if (kind === "pctDelta") {
    const span = document.createElement("span");
    let cls = "delta-flat";
    if (v > 0) cls = "delta-up";
    else if (v < 0) cls = "delta-down";
    span.className = "delta " + cls;
    span.textContent = `${pctFmt.format(v)} %`;
    td.appendChild(span);
  } else if (kind === "pct") {
    td.textContent = pctFmt.format(v) + " %";
  } else {
    td.textContent = nf2.format(v);
  }
  return td;
}

// ===== LP SUMMARY =====
function renderLPSummary() {
  const container = document.getElementById("lp-summary");
  if (!container) return;

  const withLp = rows.filter((r) => (r.lp_share || 0) > 0);

  if (withLp.length === 0) {
    container.innerHTML = "<p>Brak pozycji w LP.</p>";
    return;
  }

  let totalVee = 0;
  let total24 = 0;
  let total7d = 0;

  for (const r of withLp) {
    totalVee += r.user_vee || 0;
    total24 += r.lp_earn_vee_24h || 0;
    total7d += r.lp_earn_vee_7d || 0;
  }

  const daily = total7d / 7;
  const apr = totalVee > 0 ? (daily * 365) / totalVee * 100 : 0;

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th colspan="4">My LP summary (estimated)</th>
        </tr>
        <tr>
          <th>Total VEE in LP</th>
          <th>Fees 24h (VEE)</th>
          <th>Fees 7d (VEE)</th>
          <th>APR (est)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="num">${nf2.format(totalVee)}</td>
          <td class="num">${nf2.format(total24)}</td>
          <td class="num">${nf2.format(total7d)}</td>
          <td class="num">${pctFmt.format(apr)} %</td>
        </tr>
      </tbody>
    </table>
  `;
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

// ===== HIDE ZERO LP TOGGLE =====
const hideZeroLpCheckbox = document.getElementById("hide-zero-lp");
if (hideZeroLpCheckbox) {
  hideZeroLpCheckbox.addEventListener("change", (e) => {
    state.hideZeroLP = e.target.checked;
    applyFilterAndSort();
  });
}

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
