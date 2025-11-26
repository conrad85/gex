const API_BASE = (() => {
  if (window.location.protocol.startsWith("http") && window.location.host) {
    return `${window.location.protocol}//${window.location.host}`;
  }
  return "http://127.0.0.1:8000";
})();

const WALLET = "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0";
const API_URL_BASE = `${API_BASE}/api/market`;
const API_URL_WALLET = `${API_BASE}/api/market/${WALLET}`;
const API_URL_HISTORY = `${API_BASE}/api/history`;

const nf2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const pct = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function qs(name) {
  return new URLSearchParams(window.location.search).get(name);
}

async function loadItem() {
  const pair = qs("pair");
  if (!pair) {
    document.getElementById("item-title").textContent = "Invalid pair address";
    return;
  }

  // snapshot + LP z tego samego setupu co index
  const [baseData, walletData, historyData] = await Promise.all([
    fetch(API_URL_BASE).then((r) => r.json()),
    fetch(API_URL_WALLET).then((r) => r.json()),
    fetch(`${API_URL_HISTORY}/${pair}`).then((r) => r.json()),
  ]);

  const row = walletData.find(
    (r) => String(r.pair_address).toLowerCase() === pair.toLowerCase()
  ) ||
  baseData.find(
    (r) => String(r.pair_address).toLowerCase() === pair.toLowerCase()
  );

  if (!row) {
    document.getElementById("item-title").textContent = "Item not found";
    return;
  }

  document.getElementById("item-title").textContent =
    row.item_name || "Unknown item";

  const infoBox = document.getElementById("item-info");
  infoBox.innerHTML = `
    <div><strong>Pair:</strong> ${row.pair_address}</div>
    <div><strong>Item address:</strong> ${row.item_address}</div>
    <div><strong>VEE address:</strong> ${row.vee_address}</div>
  `;

  const tbody = document.getElementById("details-body");
  tbody.innerHTML = "";

  const fields = {
    "Price (VEE)": row.price_vee,
    "Reserve VEE": row.reserve_vee,
    "Reserve Item": row.reserve_item,
    "Volume 24h (VEE)": row.volume_24h_vee ?? row.volume_24h_est,
    "Volume 7d (VEE)": row.volume_7d_vee,
    "My LP %": (row.lp_share || 0) * 100,
    "My items in LP": row.user_item,
    "My VEE in LP": row.user_vee,
    "My fees 24h (VEE)": row.lp_earn_vee_24h,
    "My fees 7d (VEE)": row.lp_earn_vee_7d,
    "My APR (est)": row.lp_apr,
    "Updated": row.ts,
  };

  Object.entries(fields).forEach(([label, val]) => {
    const tr = document.createElement("tr");
    let text;
    if (val == null) {
      text = "-";
    } else if (label.includes("%")) {
      text = pct.format(val) + " %";
    } else if (typeof val === "number") {
      text = nf2.format(val);
    } else {
      text = String(val);
    }
    tr.innerHTML = `
      <td>${label}</td>
      <td>${text}</td>
    `;
    tbody.appendChild(tr);
  });

  buildCharts(historyData);
}

function buildCharts(history) {
  const snaps = history.snapshots || [];
  const vols = history.daily_volume || [];

  const priceCtx = document.getElementById("price-chart").getContext("2d");
  const volCtx = document.getElementById("volume-chart").getContext("2d");

  const priceLabels = snaps.map((s) => s.ts);
  const priceData = snaps.map((s) => s.price_vee || 0);

  new Chart(priceCtx, {
    type: "line",
    data: {
      labels: priceLabels,
      datasets: [
        {
          label: "Price (VEE)",
          data: priceData,
          fill: false,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      scales: {
        x: {
          ticks: {
            callback: (v) => {
              const raw = priceLabels[v];
              return raw ? raw.slice(5, 16) : "";
            },
          },
        },
        y: {
          beginAtZero: false,
        },
      },
    },
  });

  const volLabels = vols.map((v) => v.day);
  const volData = vols.map((v) => v.volume_vee || 0);

  new Chart(volCtx, {
    type: "bar",
    data: {
      labels: volLabels,
      datasets: [
        {
          label: "Daily volume (VEE)",
          data: volData,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      scales: {
        x: {
          ticks: {
            callback: (v) => volLabels[v]?.slice(5) || "",
          },
        },
        y: {
          beginAtZero: true,
        },
      },
    },
  });
}

loadItem();
