// API base (lokalnie: 127.0.0.1:8000, na VPS: host z przeglądarki)
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
const nf0 = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

const walletInput = document.getElementById("wallet-input");
const loadBtn = document.getElementById("load-btn");
const tbody = document.getElementById("lp-body");
const noHistoryEl = document.getElementById("no-history");
const veePriceLabel = document.getElementById("vee-price-label");
const summaryLabel = document.getElementById("summary-label");

function shortAddr(addr) {
  if (!addr) return "";
  const s = String(addr);
  return s.slice(0, 6) + "..." + s.slice(-4);
}

function fmtPct(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "-";
  return (x >= 0 ? "+" : "") + nf2.format(x) + " %";
}

function fmtVee(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "-";
  return nf2.format(x);
}

function fmtUsd(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "-";
  return "$" + nf4.format(x);
}

function renderTable(pairs) {
  if (!tbody) return;

  tbody.innerHTML = "";

  let totalLpVee = 0;
  let totalLpUsd = 0;

  for (const p of pairs) {
    totalLpVee += Number(p.value_lp_vee || 0);
    totalLpUsd += Number(p.value_lp_usd || 0);

    const tr = document.createElement("tr");

    const ilPct = p.il_pct;
    const ilPctAnn = p.il_annualized_pct;
    const netEff = p.net_effective_pct;
    const tw = (p.target_weight || 0) * 100;

    const ilClass = ilPct > 0 ? "pos" : ilPct < 0 ? "neg" : "";
    const netClass = netEff > 0 ? "pos" : netEff < 0 ? "neg" : "";

    tr.innerHTML = `
      <td>
        ${p.item_name || "-"}<br />
        <span class="pair-addr">${shortAddr(p.pair_address)}</span>
      </td>
      <td>${shortAddr(p.pair_address)}</td>
      <td>${p.days_in_position != null ? nf2.format(p.days_in_position) : "-"}</td>
      <td>${fmtVee(p.value_lp_vee)}</td>
      <td>${fmtUsd(p.value_lp_usd)}</td>
      <td class="${ilClass}">${fmtVee(p.il_vee)}</td>
      <td class="${ilClass}">${fmtPct(ilPct)}</td>
      <td class="${ilClass}">${fmtPct(ilPctAnn)}</td>
      <td>${fmtPct(p.lp_apr)}</td>
      <td class="${netClass}">${fmtPct(netEff)}</td>
      <td>${tw ? nf2.format(tw) + " %" : "-"}</td>
    `;

    tbody.appendChild(tr);
  }

  const best = pairs[0];
  if (best) {
    const bestName = best.item_name || shortAddr(best.pair_address);
    const netEff = best.net_effective_pct;
    summaryLabel.textContent =
      `Best: ${bestName} (` +
      (netEff != null ? fmtPct(netEff) : "-") +
      `), total LP: ${fmtVee(totalLpVee)} VEE` +
      (totalLpUsd ? ` (${fmtUsd(totalLpUsd)})` : "");
  } else {
    summaryLabel.textContent = "";
  }
}

async function loadIL(wallet) {
  const w = wallet.trim();
  if (!w) return;

  noHistoryEl.textContent = "Loading...";
  noHistoryEl.style.display = "inline";
  tbody.innerHTML = "";

  try {
    const res = await fetch(`${API_BASE}/api/lp/${encodeURIComponent(w)}/il`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();

    const veePrice = data.vee_usd_price;
    if (veePrice && veePrice > 0) {
      veePriceLabel.textContent = `VEE: ${fmtUsd(veePrice)}`;
    } else {
      veePriceLabel.textContent = "";
    }

    if (!data || !Array.isArray(data.pairs) || data.pairs.length === 0) {
      tbody.innerHTML = "";
      noHistoryEl.textContent = "No LP history for this wallet.";
      noHistoryEl.style.display = "inline";
      summaryLabel.textContent = "";
      return;
    }

    noHistoryEl.style.display = "none";
    renderTable(data.pairs);
  } catch (err) {
    console.error("Error loading LP IL:", err);
    tbody.innerHTML = "";
    noHistoryEl.textContent = "Error loading LP history (check console).";
    noHistoryEl.style.display = "inline";
    summaryLabel.textContent = "";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (loadBtn) {
    loadBtn.addEventListener("click", () => {
      loadIL(walletInput.value);
    });
  }

  if (walletInput) {
    walletInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        loadIL(walletInput.value);
      }
    });
  }

  // auto-load na starcie jeśli jest domyślny wallet
  if (walletInput && walletInput.value.trim()) {
    loadIL(walletInput.value.trim());
  }
});
