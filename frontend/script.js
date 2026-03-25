const state = {
    symbol: "EURUSD",
    interval: "15m",
    source: "auto",
    widget: null,
};

function inferTradingViewSymbol(symbol) {
    const normalized = symbol.trim().toUpperCase();
    if (!normalized) {
        return "FX:EURUSD";
    }

    if (normalized.endsWith("USDT")) {
        return `BINANCE:${normalized}`;
    }

    const mapping = {
        EURUSD: "FX:EURUSD",
        GBPUSD: "FX:GBPUSD",
        USDJPY: "FX:USDJPY",
        AUDUSD: "FX:AUDUSD",
        USDCAD: "FX:USDCAD",
        XAUUSD: "OANDA:XAUUSD",
        USOIL: "OANDA:USOIL",
        SPX: "CAPITALCOM:US500",
        NAS100: "CAPITALCOM:US100",
        DJI: "CAPITALCOM:US30",
        GER40: "CAPITALCOM:GER40",
    };

    return mapping[normalized] || `FX:${normalized}`;
}

function renderTradingViewWidget(symbol) {
    const container = document.getElementById("tradingview-widget");
    container.innerHTML = "";
    const tvSymbol = inferTradingViewSymbol(symbol);
    document.getElementById("chart-caption").textContent = `TradingView symbol: ${tvSymbol}`;

    state.widget = new TradingView.widget({
        autosize: true,
        symbol: tvSymbol,
        interval: "15",
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        enable_publishing: false,
        hide_top_toolbar: false,
        allow_symbol_change: true,
        container_id: "tradingview-widget",
    });
}

function formatBiasClass(bias) {
    return String(bias || "neutral").toLowerCase().replace(/\s+/g, "-");
}

function formatNumber(value) {
    if (typeof value !== "number") {
        return "-";
    }
    return value.toFixed(4);
}

function renderBiasPanel(payload) {
    const finalBiasElement = document.getElementById("final-bias");
    finalBiasElement.textContent = payload.final_bias.toUpperCase();
    finalBiasElement.className = `final-bias ${formatBiasClass(payload.final_bias)}`;

    document.getElementById("confidence").textContent = `${payload.confidence}%`;
    document.getElementById("technical-bias").textContent = payload.technical_bias.toUpperCase();
    document.getElementById("news-bias").textContent = payload.news_bias.toUpperCase();
}

function renderSetups(setups) {
    const container = document.getElementById("setups-panel");
    if (!setups.length) {
        container.innerHTML = '<div class="empty-state">No active trade setup right now.</div>';
        return;
    }

    container.innerHTML = setups
        .map((setup) => `
            <article class="card">
                <span class="tag ${setup.signal.toLowerCase()}">${setup.signal}</span>
                <h3>${setup.signal} Setup</h3>
                <p>Entry: ${formatNumber(setup.entry)}</p>
                <p>SL: ${formatNumber(setup.stop_loss)}</p>
                <p>TP: ${formatNumber(setup.take_profit)}</p>
                <p>Zone: ${setup.zone_type.toUpperCase()}</p>
            </article>
        `)
        .join("");
}

function renderZones(zones) {
    const container = document.getElementById("zones-panel");
    if (!zones.length) {
        container.innerHTML = '<div class="empty-state">No supply or demand zones detected.</div>';
        return;
    }

    container.innerHTML = zones
        .map((zone) => `
            <article class="card">
                <span class="tag ${zone.type.toLowerCase()}">${zone.type}</span>
                <h3>${zone.symbol} ${zone.timeframe}</h3>
                <p>Start: ${formatNumber(zone.start_price)}</p>
                <p>End: ${formatNumber(zone.end_price)}</p>
                <p>Formed: ${zone.formed_at}</p>
            </article>
        `)
        .join("");
}

function renderAlerts(alerts) {
    const container = document.getElementById("alerts-panel");
    if (!alerts.length) {
        container.innerHTML = '<div class="empty-state">No active alerts right now.</div>';
        return;
    }

    container.innerHTML = alerts
        .map((alert) => `
            <article class="card">
                <span class="tag ${alert.type}">${alert.type.replace("_", " ")}</span>
                <h3>${alert.message}</h3>
            </article>
        `)
        .join("");
}

async function fetchDashboardData() {
    const params = new URLSearchParams({
        symbol: state.symbol,
        interval: state.interval,
        source: state.source,
    });

    const response = await fetch(`/data?${params.toString()}`);
    if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail || "Failed to load dashboard data.");
    }

    return response.json();
}

async function refreshDashboard() {
    state.symbol = document.getElementById("symbol-input").value.trim().toUpperCase() || "EURUSD";
    state.source = document.getElementById("source-select").value;

    if (!state.widget || document.getElementById("chart-caption").textContent.indexOf(state.symbol) === -1) {
        renderTradingViewWidget(state.symbol);
    }

    try {
        const payload = await fetchDashboardData();
        renderBiasPanel(payload);
        renderSetups(payload.setups || []);
        renderZones(payload.zones || []);
        renderAlerts(payload.alerts || []);
    } catch (error) {
        renderAlerts([{ type: "news", message: error.message }]);
    }
}

function bindEvents() {
    document.getElementById("refresh-button").addEventListener("click", () => {
        refreshDashboard();
    });

    document.getElementById("symbol-input").addEventListener("change", () => {
        renderTradingViewWidget(document.getElementById("symbol-input").value.trim().toUpperCase());
        refreshDashboard();
    });

    document.getElementById("source-select").addEventListener("change", () => {
        refreshDashboard();
    });
}

function startAutoRefresh() {
    setInterval(() => {
        refreshDashboard();
    }, 5000);
}

function initialize() {
    renderTradingViewWidget(state.symbol);
    bindEvents();
    refreshDashboard();
    startAutoRefresh();
}

window.addEventListener("DOMContentLoaded", initialize);
