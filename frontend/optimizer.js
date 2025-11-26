const WALLET_DEFAULT = "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0";

const API_BASE = (() => {
  if (window.location.protocol.startsWith("http") && window.location.host) {
    return `${window.location.protocol}//${window.location.host}`;
  }
  return "http://127.0.0.1:8000";
})();

const nf2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const nf4 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});
const pctFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatDays(d) {
  if (d == null || isNaN(d)) return "-";
  if (d < 1) return d.toFixed(2);
  return d.toFixed(1);
}

function formatPct(v) {
  if (v == null || isNaN(v)) return "-";
  return `${pctFmt.format(v)} %`;
}

function formatVee(v) {
  if (v == null || isNaN(v)) return "-";
  return nf4.format(v);
}

function formatUsd(v) {
  if (v == null || isNaN(v)) return "-";
  return "$" + nf2.format(v);
}

function classifyRec(row) {
  const net = row.net_effective_pct;
  if (net == null || isNaN(net)) {
    return { label: "N/A", cls: "pill-neutral" };
  }
  if (net < -10) {
    return { label: "EXIT", cls: "pill-bad" };
  }
  if (net < 0) {
    return { label: "Watch / weak", cls: "pill-warn" };
  }
  if (net < 20) {
    return { label: "OK / small", cls: "pill-ok" };
  }
  return { label: "Strong / overweight", cls: "pill-good" };
}

async function loadIL(wallet) {
  const status = document.getElementById("optimizer-status");
  const tbody = document.getElementById("optimizer-body");
  const summaryBox = document.getElementById("optimizer-summary");

  status.textContent = "Loading...";
  tbody.innerHTML = "";
  summaryBox.innerHTML = "";

  try {
    const res = await fetch(`${API_BASE}/api/lp/${wallet}/il`);
    if (!res.ok) {
      status.textContent = `HTTP error ${res.status}`;
      return;
    }
    const data = await res.json();
    if (!Array.isArray(data) || data.length === 0) {
      status.textContent = "No LP history for this wallet.";
      return;
    }

    // sortujemy od najlepszych netto
    data.sort((a, b) => {
      const na = a.net_effective_pct ?? -999999;
      const nb = b.net_effective_pct ?? -999999;
      return nb - na;
    });

    renderSummary(data);
    renderTable(data);
    status.textContent = `Loaded ${data.length} pairs.`;
  } catch (e) {
    console.error(e);
    status.textContent = "Error while loading data.";
  }
}

function renderSummary(data) {
  const box = document.getElementById("optimizer-summary");

  let totalLpVee = 0;
  let totalLpUsd = 0;
  let totalIlVee = 0;
  let totalIlUsd = 0;

  for (const r of data) {
    const lpV = r.lp_value_now_vee ?? r.value_lp_vee ?? 0;
    const lpU = r.lp_value_now_usd || 0;
    const ilV = r.il_vee || 0;
    const ilU = r.il_usd || 0;

    totalLpVee += lpV;
    totalLpUsd += lpU;
    totalIlVee += ilV;
    totalIlUsd += ilU;
  }

  const ilPctTotal =
    totalLpVee > 0 ? (totalIlVee / (totalLpVee - totalIlVee)) * 100.0 : null;

  box.innerHTML = `
    <div class="optimizer-card">
      <div class="card-title">Total LP value</div>
      <div class="card-main">${formatVee(totalLpVee)} VEE</div>
      <div class="card-sub">${formatUsd(totalLpUsd)}</div>
    </div>
    <div class="optimizer-card">
      <div class="card-title">Total IL vs HODL</div>
      <div class="card-main">${formatVee(totalIlVee)} VEE</div>
      <div class="card-sub">${formatUsd(totalIlUsd)} (${formatPct(ilPctTotal)})</div>
    </div>
    <div class="optimizer-card">
      <div class="card-title">Top net pair</div>
      <div class="card-main">${
        data[0].item_name || "?"
      }</div>
      <div class="card-sub">Net: ${formatPct(
        data[0].net_effective_pct
      )}, APR fees: ${formatPct(data[0].lp_apr)}</div>
    </div>
  `;
}

function renderTable(data) {
  const tbody = document.getElementById("optimizer-body");
  tbody.innerHTML = "";

  for (const r of data) {
    const tr = document.createElement("tr");

    const pairLink = `item.html?pair=${encodeURIComponent(r.pair_address)}`;
    const rec = classifyRec(r);
    const targetWeightPct =
      r.target_weight && !isNaN(r.target_weight)
        ? r.target_weight * 100.0
        : 0;

    tr.innerHTML = `
      <td>${r.item_name || "-"}</td>
      <td>
        <a href="${pairLink}" class="item-link">${r.pair_address}</a>
      </td>
      <td class="num">${formatDays(r.days_in_position)}</td>
      <td class="num">${formatVee(r.lp_value_now_vee)}</td>
      <td class="num">${formatUsd(r.lp_value_now_usd)}</td>
      <td class="num">${formatVee(r.il_vee)}</td>
      <td class="num">${formatPct(r.il_pct)}</td>
      <td class="num">${formatPct(r.il_annualized_pct)}</td>
      <td class="num">${formatPct(r.lp_apr)}</td>
      <td class="num">${formatPct(r.net_effective_pct)}</td>
      <td class="num">${targetWeightPct ? pctFmt.format(targetWeightPct) + " %" : "-"}</td>
      <td>
        <span class="pill ${rec.cls}">${rec.label}</span>
      </td>
    `;

    tbody.appendChild(tr);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("wallet-input");
  const btn = document.getElementById("load-btn");

  if (input && !input.value) {
    input.value = WALLET_DEFAULT;
  }

  btn.addEventListener("click", () => {
    const w = input.value.trim();
    if (!w) return;
    loadIL(w);
  });

  input.addEventListener("keyup", (e) => {
    if (e.key === "Enter") {
      const w = input.value.trim();
      if (!w) return;
      loadIL(w);
    }
  });

  // auto-load na starcie
  if (input.value.trim()) {
    loadIL(input.value.trim());
  }
});
