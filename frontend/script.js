const state = {
    symbol: "EURUSD",
    interval: "1h",
    source: "auto",
    widget: null,
    tradingViewSymbol: "FX:EURUSD",
    requestId: 0,
};

const APPROVED_SYMBOLS = new Set(["ETHUSDT", "GBPUSD", "EURUSD", "BTCUSDT", "XAUUSD", "NAS100", "USDCHF", "USDJPY"]);

function inferTradingViewSymbol(symbol) {
    if (String(symbol || "").includes(":")) {
        return String(symbol).trim().toUpperCase();
    }
    const normalized = String(symbol || "").trim().toUpperCase();
    if (!normalized) return "FX:EURUSD";
    if (normalized.endsWith("USDT")) return `BINANCE:${normalized}`;

    const mapping = {
        EURUSD: "FX:EURUSD",
        GBPUSD: "FX:GBPUSD",
        USDCHF: "FX:USDCHF",
        USDJPY: "FX:USDJPY",
        XAUUSD: "OANDA:XAUUSD",
        NAS100: "CAPITALCOM:US100",
        BTCUSDT: "BINANCE:BTCUSDT",
        ETHUSDT: "BINANCE:ETHUSDT",
    };
    return mapping[normalized] || `FX:${normalized}`;
}

function inferBackendFromTradingView(tvSymbol) {
    const normalized = String(tvSymbol || "").trim().toUpperCase();
    if (!normalized.includes(":")) return null;
    const [provider, rawSymbol] = normalized.split(":", 2);
    const compact = rawSymbol.replace(/\//g, "").replace(/_/g, "").replace(/-/g, "");

    const providerMap = {
        BINANCE: "binance",
        OANDA: "oanda",
        FX: "yfinance",
        CAPITALCOM: "yfinance",
        FOREXCOM: "yfinance",
        PEPPERSTONE: "yfinance",
    };
    const symbolMap = {
        US100: "NAS100",
        XAUUSD: "XAUUSD",
    };
    return {
        symbol: symbolMap[compact] || compact,
        source: compact.endsWith("USDT") ? "binance" : (providerMap[provider] || "auto"),
    };
}

function toTradingViewInterval(interval) {
    return interval === "30m" ? "30" : "60";
}

function renderTradingViewWidget(force = false) {
    const nextSymbol = inferTradingViewSymbol(state.tradingViewSymbol || state.symbol);
    const nextInterval = toTradingViewInterval(state.interval);

    if (!force && state.widget && state.tradingViewSymbol === nextSymbol) return;

    state.tradingViewSymbol = nextSymbol;
    const container = document.getElementById("tradingview-widget");
    container.innerHTML = "";
    document.getElementById("chart-caption").textContent = `TradingView symbol: ${nextSymbol}`;

    state.widget = new TradingView.widget({
        autosize: true,
        symbol: nextSymbol,
        interval: nextInterval,
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        enable_publishing: false,
        allow_symbol_change: true,
        container_id: "tradingview-widget",
    });
}

function formatNumber(value, digits = 4) {
    return typeof value === "number" ? value.toFixed(digits) : "-";
}

function createCard(title, lines, tagClass = "info", tagLabel = "INFO") {
    return `
        <article class="card">
            <span class="tag ${tagClass}">${tagLabel}</span>
            <h3>${title}</h3>
            ${lines.map((line) => `<p>${line}</p>`).join("")}
        </article>
    `;
}

function renderLoadingState() {
    document.getElementById("final-bias").textContent = "LOADING";
    document.getElementById("final-bias").className = "final-bias neutral";
    document.getElementById("htf-bias").textContent = "-";
    document.getElementById("news-bias").textContent = "-";
    document.getElementById("technical-bias").textContent = "-";
    document.getElementById("confidence").textContent = "...";
    document.getElementById("live-price-badge").textContent = "Price: ...";
    document.getElementById("setup-panel").innerHTML = '<div class="empty-state">Refreshing analysis...</div>';
    document.getElementById("setup-map-panel").innerHTML = '<div class="empty-state">Refreshing setup map...</div>';
    document.getElementById("htf-panel").innerHTML = '<div class="empty-state">Refreshing timeframes...</div>';
    document.getElementById("overlay-panel").innerHTML = '<div class="empty-state">Refreshing overlays...</div>';
    document.getElementById("alerts-panel").innerHTML = '<div class="empty-state">Refreshing alerts...</div>';
}

function renderErrorState(message) {
    renderBias({ final_bias: "NEUTRAL", daily_bias: "-", h1_bias: "-", status: "ERROR", confidence: "-", latest_price: null });
    document.getElementById("setup-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("setup-map-panel").innerHTML = '<div class="empty-state">Error fetching data.</div>';
    document.getElementById("htf-panel").innerHTML = '<div class="empty-state">Error fetching data.</div>';
    document.getElementById("overlay-panel").innerHTML = '<div class="empty-state">Error fetching data.</div>';
    document.getElementById("overlay-strip").innerHTML = '<span class="overlay-chip">Server error</span>';
    document.getElementById("alerts-panel").innerHTML = createCard("Error", [message || "Server error"], "sell", "error");
}

function renderBias(payload) {
    const finalBias = payload.final_bias || "NEUTRAL";
    const finalBiasElement = document.getElementById("final-bias");
    finalBiasElement.textContent = finalBias;
    finalBiasElement.className = `final-bias ${String(finalBias).toLowerCase()}`;
    document.getElementById("htf-bias").textContent = payload.daily_bias || "-";
    document.getElementById("news-bias").textContent = payload.h1_bias || "-";
    document.getElementById("technical-bias").textContent = payload.status || "-";
    document.getElementById("confidence").textContent = payload.confidence || "LOW";
    document.getElementById("live-price-badge").textContent = `Price: ${formatNumber(payload.latest_price)}`;
}

function renderSetup(payload) {
    const container = document.getElementById("setup-panel");
    if (payload.status === "ERROR") {
        container.innerHTML = createCard("Error fetching data", [payload.message || "Server error"], "sell", "error");
        renderSetupMap(payload, "ERROR");
        return;
    }

    if (payload.status === "NO TRADE") {
        container.innerHTML = createCard(
            "No valid setup available",
            [
                payload.message || "System running, no setups",
                `Missing: ${(payload.missing || []).join(", ") || "None"}`,
            ],
            "neutral",
            "no trade",
        );
        renderSetupMap(payload, "NO_TRADE");
        return;
    }

    container.innerHTML = createCard(
        `${(payload.strategies || []).join(" + ") || "Strategy"}`,
        [
            `Bias: ${payload.bias}`,
            `Entry: ${formatNumber(payload.entry)}`,
            `SL: ${formatNumber(payload.sl)}`,
            `TP: ${formatNumber(payload.tp)}`,
            `Target RR: 1:4`,
            `Confidence: ${payload.confidence}`,
            `Confluences: ${(payload.confluences || []).join(", ")}`,
        ],
        payload.bias === "BUY" ? "buy" : "sell",
        payload.status,
    );
    renderSetupMap(payload, "VALID_TRADE");
}

function renderSetupMap(payload, stateLabel) {
    const container = document.getElementById("setup-map-panel");
    const context = payload.analysis_context || {};

    if (stateLabel !== "VALID_TRADE") {
        const cards = [];
        if (context.order_block?.confirmed) {
            cards.push(createCard(
                "Order Block",
                [
                    `Zone: ${formatNumber(context.order_block.zone?.start_price)} -> ${formatNumber(context.order_block.zone?.end_price)}`,
                ],
                "info",
                "ob",
            ));
        }
        if (context.fvg?.confirmed) {
            cards.push(createCard(
                "FVG",
                [
                    `Zone: ${formatNumber(context.fvg.zone?.[0])} -> ${formatNumber(context.fvg.zone?.[1])}`,
                ],
                "info",
                "fvg",
            ));
        }
        if (context.inducement?.confirmed) {
            cards.push(createCard(
                "Inducement",
                [
                    `Level: ${formatNumber(context.inducement.level)}`,
                ],
                "info",
                "induce",
            ));
        }
        container.innerHTML = cards.join("") || '<div class="empty-state">No valid setup available.</div>';
        return;
    }

    const risk = Math.abs((payload.entry || 0) - (payload.sl || 0));
    const reward = Math.abs((payload.tp || 0) - (payload.entry || 0));
    const rr = risk > 0 ? reward / risk : 0;

    container.innerHTML = `
        <div class="setup-map">
            <div class="price-ladder">
                <div class="price-row tp"><span class="price-label">Take Profit</span><span class="price-value">${formatNumber(payload.tp)}</span></div>
                <div class="price-row entry"><span class="price-label">Entry</span><span class="price-value">${formatNumber(payload.entry)}</span></div>
                <div class="price-row live"><span class="price-label">Current Price</span><span class="price-value">${formatNumber(payload.latest_price)}</span></div>
                <div class="price-row sl"><span class="price-label">Stop Loss</span><span class="price-value">${formatNumber(payload.sl)}</span></div>
            </div>
            <div class="rr-strip">
                <div class="rr-card"><span class="metric-label">Risk</span><strong>${formatNumber(risk)}</strong></div>
                <div class="rr-card"><span class="metric-label">Reward</span><strong>${formatNumber(reward)}</strong></div>
                <div class="rr-card"><span class="metric-label">R:R</span><strong>${formatNumber(rr, 2)}</strong></div>
                <div class="rr-card"><span class="metric-label">Target RR</span><strong>4.00</strong></div>
            </div>
            <div class="confluence-grid">
                ${(payload.confluences || []).map((item) => `<span class="overlay-chip">${item}</span>`).join("")}
            </div>
        </div>
    `;
}

function renderTimeframes(payload) {
    const container = document.getElementById("htf-panel");
    const timeframes = payload.timeframes || {};
    const cards = [];
    if (timeframes.daily) {
        cards.push(createCard("Daily Bias", [`Bias: ${timeframes.daily.bias}`, `Price: ${formatNumber(timeframes.daily.latest_price)}`], "info", "1d"));
    }
    if (timeframes.h1) {
        cards.push(createCard("H1 Execution", [`Bias: ${timeframes.h1.bias}`, `Price: ${formatNumber(timeframes.h1.latest_price)}`], "info", "1h"));
    }
    if (timeframes.m30?.used) {
        cards.push(createCard("M30 Refinement", [`Trend: ${timeframes.m30.bias}`, `Price: ${formatNumber(timeframes.m30.latest_price)}`], "info", "30m"));
    }
    container.innerHTML = cards.join("") || '<div class="empty-state">No timeframe data.</div>';
}

function renderOverlays(payload) {
    const container = document.getElementById("overlay-panel");
    const overlays = payload.chart_overlays || {};
    const cards = [];
    if (overlays.order_block?.confirmed) {
        cards.push(createCard("Order Block", [`Zone: ${formatNumber(overlays.order_block.zone?.start_price)} -> ${formatNumber(overlays.order_block.zone?.end_price)}`], "info", "ob"));
    }
    if (overlays.fvg?.confirmed) {
        cards.push(createCard("FVG", [`Zone: ${formatNumber(overlays.fvg.zone?.[0])} -> ${formatNumber(overlays.fvg.zone?.[1])}`], "info", "fvg"));
    }
    if (overlays.inducement?.confirmed) {
        cards.push(createCard("Inducement", [`Level: ${formatNumber(overlays.inducement.level)}`], "info", "liq"));
    }
    if (overlays.trade_levels?.entry != null) {
        cards.push(createCard("Trade Levels", [
            `Entry: ${formatNumber(overlays.trade_levels.entry)}`,
            `SL: ${formatNumber(overlays.trade_levels.sl)}`,
            `TP: ${formatNumber(overlays.trade_levels.tp)}`,
        ], "info", "levels"));
    }
    container.innerHTML = cards.join("") || '<div class="empty-state">No active overlays.</div>';

    document.getElementById("overlay-strip").innerHTML = (payload.confluences || []).length
        ? payload.confluences.map((item) => `<span class="overlay-chip">${item}</span>`).join("")
        : '<span class="overlay-chip">No active confluence tags</span>';
}

function renderAlerts(alerts) {
    const container = document.getElementById("alerts-panel");
    if (!alerts?.length) {
        container.innerHTML = '<div class="empty-state">No recent alerts.</div>';
        return;
    }
    container.innerHTML = alerts.map((alert) =>
        createCard(
            String(alert.type || "alert").replace(/_/g, " ").toUpperCase(),
            [alert.message || "-"],
            alert.status === "LOSS" ? "sell" : "info",
            alert.type || "alert",
        )
    ).join("");
}

function renderJournal(entries) {
    const container = document.getElementById("journal-panel");
    if (!entries?.length) {
        container.innerHTML = '<div class="empty-state">No journal entries yet.</div>';
        return;
    }
    const grouped = new Map();
    entries.forEach((entry) => {
        const dateKey = String(entry.closed_at || entry.timestamp || "").slice(0, 10) || "Unknown date";
        if (!grouped.has(dateKey)) grouped.set(dateKey, []);
        grouped.get(dateKey).push(entry);
    });

    const sections = [];
    grouped.forEach((items, dateKey) => {
        sections.push(`<div class="journal-day">${dateKey}</div>`);
        items.forEach((entry) => {
            sections.push(
                createCard(
                    `${entry.symbol} ${entry.strategy}`,
                    [
                        `Status: ${entry.status || "-"}`,
                        `Result: ${entry.result || "-"}`,
                        `Missing: ${(entry.missing || []).join(", ") || "-"}`,
                        `RR: ${entry.rr_achieved != null ? entry.rr_achieved : "-"}`,
                    ],
                    String(entry.status || "").toLowerCase() === "loss" ? "sell" : String(entry.status || "").toLowerCase() === "win" ? "buy" : "neutral",
                    entry.status || "entry",
                )
            );
        });
    });
    container.innerHTML = sections.join("");
}

function renderPerformance(performance) {
    const container = document.getElementById("performance-panel");
    if (!performance || Object.keys(performance).length === 0) {
        container.innerHTML = '<div class="empty-state">Performance snapshot unavailable.</div>';
        return;
    }
    const cards = [
        createCard("Core Stats", [
            `Win rate: ${formatNumber(performance.win_rate, 2)}%`,
            `Profit factor: ${formatNumber(performance.profit_factor, 2)}`,
            `Total trades: ${performance.total_trades || 0}`,
            `Closed trades: ${performance.closed_trades || 0}`,
        ], "info", "stats"),
    ];
    container.innerHTML = cards.join("");
}

async function fetchDashboardData() {
    const params = new URLSearchParams({
        symbol: state.symbol,
        interval: state.interval,
        source: state.source,
    });
    const response = await fetch(`/data?${params.toString()}`);
    const rawText = await response.text();
    let payload = {};
    try {
        payload = rawText ? JSON.parse(rawText) : {};
    } catch {
        return { status: "ERROR", message: "Error fetching data" };
    }
    if (!response.ok) {
        return { status: "ERROR", message: payload.message || "Error fetching data" };
    }
    return payload;
}

function syncStateFromInputs() {
    const requestedSymbol = document.getElementById("symbol-input").value.trim().toUpperCase() || "EURUSD";
    state.symbol = APPROVED_SYMBOLS.has(requestedSymbol) ? requestedSymbol : "EURUSD";
    document.getElementById("symbol-input").value = state.symbol;
    state.source = document.getElementById("source-select").value;
    state.interval = document.getElementById("interval-select").value;
    state.tradingViewSymbol = document.getElementById("tv-symbol-input").value.trim() || inferTradingViewSymbol(state.symbol);
}

function syncBackendInputsFromTradingView() {
    const inferred = inferBackendFromTradingView(document.getElementById("tv-symbol-input").value.trim());
    if (!inferred) return;
    document.getElementById("symbol-input").value = APPROVED_SYMBOLS.has(inferred.symbol) ? inferred.symbol : "EURUSD";
    document.getElementById("source-select").value = inferred.source;
}

async function refreshDashboard(forceChart = false) {
    syncStateFromInputs();
    renderTradingViewWidget(forceChart);
    const requestId = ++state.requestId;

    if (forceChart) {
        renderLoadingState();
    }

    try {
        const payload = await fetchDashboardData();
        if (requestId !== state.requestId) return;

        renderBias(payload);
        renderSetup(payload);
        renderTimeframes(payload);
        renderOverlays(payload);
        renderAlerts(payload.alerts || []);
        renderJournal(payload.journal || []);
        renderPerformance(payload.performance || {});
    } catch {
        if (requestId !== state.requestId) return;
        renderErrorState("Error fetching data");
    }
}

function debounce(callback, waitMs) {
    let timeoutId = null;
    return (...args) => {
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(() => callback(...args), waitMs);
    };
}

function bindEvents() {
    const debouncedRefresh = debounce(() => refreshDashboard(true), 300);
    document.getElementById("refresh-button").addEventListener("click", () => refreshDashboard(true));
    document.getElementById("tv-symbol-input").addEventListener("change", () => {
        syncBackendInputsFromTradingView();
        refreshDashboard(true);
    });
    document.getElementById("tv-symbol-input").addEventListener("input", () => {
        syncBackendInputsFromTradingView();
        debouncedRefresh();
    });
    document.getElementById("symbol-input").addEventListener("change", () => refreshDashboard(true));
    document.getElementById("symbol-input").addEventListener("input", () => debouncedRefresh());
    document.getElementById("source-select").addEventListener("change", () => refreshDashboard(false));
    document.getElementById("interval-select").addEventListener("change", () => refreshDashboard(true));
}

window.addEventListener("DOMContentLoaded", () => {
    document.getElementById("tv-symbol-input").value = state.tradingViewSymbol;
    syncBackendInputsFromTradingView();
    bindEvents();
    refreshDashboard(true);
});
