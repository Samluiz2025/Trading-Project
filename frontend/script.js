const state = {
    symbol: "EURUSD",
    interval: "15m",
    source: "auto",
    widget: null,
    tradingViewSymbol: "FX:EURUSD",
};

function inferTradingViewSymbol(symbol) {
    if (String(symbol || "").includes(":")) {
        return String(symbol).trim().toUpperCase();
    }

    const normalized = String(symbol || "").trim().toUpperCase();
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
        UK100: "CAPITALCOM:UK100",
    };

    return mapping[normalized] || `FX:${normalized}`;
}

function inferBackendFromTradingView(tvSymbol) {
    const normalized = String(tvSymbol || "").trim().toUpperCase();
    if (!normalized.includes(":")) {
        return null;
    }

    const [provider, rawSymbol] = normalized.split(":", 2);
    const compact = rawSymbol.replace(/\//g, "").replace(/_/g, "").replace(/-/g, "");

    const providerSourceMap = {
        BINANCE: "binance",
        BYBIT: "binance",
        KUCOIN: "binance",
        OANDA: "oanda",
        FX: "yfinance",
        FOREXCOM: "yfinance",
        PEPPERSTONE: "yfinance",
        CAPITALCOM: "yfinance",
        BLACKBULL: "yfinance",
        SAXO: "yfinance",
    };

    const symbolMap = {
        XAUUSD: "XAUUSD",
        USOIL: "USOIL",
        UKOIL: "BRENT",
        WTI: "USOIL",
        US500: "SPX",
        SPX500: "SPX",
        SPX: "SPX",
        US100: "NAS100",
        NAS100: "NAS100",
        USTEC: "NAS100",
        US30: "DJI",
        DJI: "DJI",
        GER40: "GER40",
        DE40: "GER40",
        DAX: "GER40",
        UK100: "UK100",
        JPN225: "JP225",
        JP225: "JP225",
        EURUSD: "EURUSD",
        GBPUSD: "GBPUSD",
        USDJPY: "USDJPY",
        AUDUSD: "AUDUSD",
        USDCAD: "USDCAD",
    };

    let backendSymbol = symbolMap[rawSymbol] || symbolMap[compact] || compact;
    let backendSource = providerSourceMap[provider] || "auto";

    if (compact.endsWith("USDT")) {
        backendSymbol = compact;
        backendSource = "binance";
    }

    return {
        symbol: backendSymbol,
        source: backendSource,
    };
}

function toTradingViewInterval(interval) {
    return interval === "5m" ? "5" : "15";
}

function renderTradingViewWidget(force = false) {
    const nextSymbol = inferTradingViewSymbol(state.tradingViewSymbol || state.symbol);
    const nextInterval = toTradingViewInterval(state.interval);

    if (!force && state.tradingViewSymbol === nextSymbol && state.widget) {
        return;
    }

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
        hide_top_toolbar: false,
        allow_symbol_change: true,
        studies: [
            "MASimple@tv-basicstudies",
            "RSI@tv-basicstudies",
        ],
        container_id: "tradingview-widget",
    });
}

function biasClass(value) {
    return String(value || "neutral").toLowerCase().replace(/\s+/g, "-");
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

function renderBias(payload) {
    const finalBiasElement = document.getElementById("final-bias");
    finalBiasElement.textContent = String(payload.final_bias || "neutral").toUpperCase();
    finalBiasElement.className = `final-bias ${biasClass(payload.final_bias)}`;

    document.getElementById("htf-bias").textContent = String(payload.htf?.bias || "-").toUpperCase();
    document.getElementById("news-bias").textContent = String(payload.news_bias || "-").toUpperCase();
    document.getElementById("technical-bias").textContent = String(payload.technical_bias || "-").toUpperCase();
    document.getElementById("confidence").textContent = `${payload.confidence || 0}%`;
    document.getElementById("live-price-badge").textContent = `Price: ${formatNumber(payload.latest_price)}`;
}

function renderSetup(payload) {
    const container = document.getElementById("setup-panel");
    if (!payload.active_strategy || payload.entry == null) {
        container.innerHTML = '<div class="empty-state">No aligned high-confidence setup right now.</div>';
        renderSetupMap(payload, false);
        return;
    }

    container.innerHTML = createCard(
        payload.active_strategy,
        [
            `Entry: ${formatNumber(payload.entry)}`,
            `SL: ${formatNumber(payload.sl)}`,
            `TP: ${formatNumber(payload.tp)}`,
            `Ranking score: ${formatNumber(payload.ranking_score, 2)}`,
            `Historical win rate: ${formatNumber(payload.historical_win_rate, 2)}%`,
            `Confluences: ${(payload.confluences || []).join(", ") || "None"}`,
        ],
        biasClass(payload.final_bias),
        payload.final_bias || "neutral",
    );
    renderSetupMap(payload, true);
}

function renderSetupMap(payload, hasSetup) {
    const container = document.getElementById("setup-map-panel");
    if (!hasSetup || payload.entry == null) {
        container.innerHTML = '<div class="empty-state">No live setup map yet. When a valid setup appears, entry, stop, target, and confluences will show here.</div>';
        return;
    }

    const risk = Math.abs((payload.entry || 0) - (payload.sl || 0));
    const reward = Math.abs((payload.tp || 0) - (payload.entry || 0));
    const rr = risk > 0 ? reward / risk : 0;
    const distanceToEntry = payload.latest_price != null && payload.entry != null
        ? Math.abs(payload.latest_price - payload.entry)
        : null;

    container.innerHTML = `
        <div class="setup-map">
            <div class="price-ladder">
                <div class="price-row tp">
                    <span class="price-label">Take Profit</span>
                    <span class="price-value">${formatNumber(payload.tp)}</span>
                </div>
                <div class="price-row entry">
                    <span class="price-label">Entry</span>
                    <span class="price-value">${formatNumber(payload.entry)}</span>
                </div>
                <div class="price-row live">
                    <span class="price-label">Current Price</span>
                    <span class="price-value">${formatNumber(payload.latest_price)}</span>
                </div>
                <div class="price-row sl">
                    <span class="price-label">Stop Loss</span>
                    <span class="price-value">${formatNumber(payload.sl)}</span>
                </div>
            </div>
            <div class="rr-strip">
                <div class="rr-card">
                    <span class="metric-label">Risk</span>
                    <strong>${formatNumber(risk)}</strong>
                </div>
                <div class="rr-card">
                    <span class="metric-label">Reward</span>
                    <strong>${formatNumber(reward)}</strong>
                </div>
                <div class="rr-card">
                    <span class="metric-label">R:R</span>
                    <strong>${formatNumber(rr, 2)}</strong>
                </div>
            </div>
            <div class="rr-card">
                <span class="metric-label">Distance To Entry</span>
                <strong>${distanceToEntry != null ? formatNumber(distanceToEntry) : "-"}</strong>
            </div>
            <div class="confluence-grid">
                ${(payload.confluences || []).map((item) => `<span class="overlay-chip">${item}</span>`).join("") || '<span class="overlay-chip">No confluences</span>'}
            </div>
        </div>
    `;
}

function renderHtfAndOverlays(payload) {
    const htfContainer = document.getElementById("htf-panel");
    const overlayContainer = document.getElementById("overlay-panel");
    const strip = document.getElementById("overlay-strip");
    const htfTimeframes = payload.htf?.timeframes || [];

    if (!htfTimeframes.length) {
        htfContainer.innerHTML = '<div class="empty-state">No HTF data available.</div>';
    } else {
        htfContainer.innerHTML = htfTimeframes
            .map((timeframe) =>
                createCard(
                    `${timeframe.timeframe} Bias`,
                    [
                        `Bias: ${String(timeframe.bias).toUpperCase()}`,
                        `Price: ${formatNumber(timeframe.latest_price)}`,
                        `Zones: ${(timeframe.zones || []).length}`,
                    ],
                    biasClass(timeframe.bias),
                    timeframe.timeframe,
                )
            )
            .join("");
    }

    const overlays = payload.chart_overlays || {};
    const cards = [];

    if (overlays.htf_zones?.length) {
        cards.push(
            createCard(
                "HTF Zones",
                overlays.htf_zones.map((zone) => `${zone.type.toUpperCase()}: ${formatNumber(zone.start_price)} -> ${formatNumber(zone.end_price)}`),
                "zone",
                "zone",
            )
        );
    }

    if (overlays.latest_fvg) {
        cards.push(
            createCard(
                "Latest FVG",
                [
                    `${overlays.latest_fvg.signal} at ${formatNumber(overlays.latest_fvg.entry)}`,
                    `SL: ${formatNumber(overlays.latest_fvg.stop_loss)}`,
                    `TP: ${formatNumber(overlays.latest_fvg.take_profit)}`,
                ],
                "info",
                "FVG",
            )
        );
    }

    if (overlays.order_block) {
        cards.push(
            createCard(
                "Order Block",
                [
                    `${overlays.order_block.signal} at ${formatNumber(overlays.order_block.entry)}`,
                    `SL: ${formatNumber(overlays.order_block.stop_loss)}`,
                    `TP: ${formatNumber(overlays.order_block.take_profit)}`,
                ],
                "info",
                "OB",
            )
        );
    }

    if (overlays.liquidity?.length) {
        cards.push(
            createCard(
                "Liquidity / Confirmation",
                overlays.liquidity.map((item) => item.timeframe ? `${item.timeframe} ${item.type} ${item.signal}` : `${item.name} ${item.signal} @ ${formatNumber(item.entry)}`),
                "info",
                "LTF",
            )
        );
    }

    if (overlays.trade_levels?.entry != null) {
        cards.push(
            createCard(
                "Trade Levels",
                [
                    `Entry: ${formatNumber(overlays.trade_levels.entry)}`,
                    `SL: ${formatNumber(overlays.trade_levels.sl)}`,
                    `TP: ${formatNumber(overlays.trade_levels.tp)}`,
                ],
                "info",
                "levels",
            )
        );
    }

    overlayContainer.innerHTML = cards.join("") || '<div class="empty-state">No active filtered overlays.</div>';

    const chips = [];
    if (payload.htf?.bias) {
        chips.push(`<span class="overlay-chip">HTF ${String(payload.htf.bias).toUpperCase()}</span>`);
    }
    (payload.confluences || []).slice(0, 6).forEach((item) => {
        chips.push(`<span class="overlay-chip">${item}</span>`);
    });
    strip.innerHTML = chips.join("") || '<span class="overlay-chip">No active confluence tags</span>';
}

function renderAlerts(alerts) {
    const container = document.getElementById("alerts-panel");
    if (!alerts?.length) {
        container.innerHTML = '<div class="empty-state">No recent alerts.</div>';
        return;
    }

    container.innerHTML = alerts
        .map((alert) =>
            createCard(
                String(alert.type || "alert").replace(/_/g, " ").toUpperCase(),
                [alert.message || "-"],
                alert.type === "setup" ? "buy" : alert.type === "bias_change" ? "sell" : "news",
                alert.type || "alert",
            )
        )
        .join("");
}

function renderJournal(entries) {
    const container = document.getElementById("journal-panel");
    if (!entries?.length) {
        container.innerHTML = '<div class="empty-state">No journal entries yet.</div>';
        return;
    }

    container.innerHTML = entries
        .map((entry) =>
            createCard(
                `${entry.symbol} ${entry.strategy}`,
                [
                    `Entry: ${formatNumber(entry.entry)}`,
                    `Result: ${entry.result || entry.status || "OPEN"}`,
                    `RR: ${entry.rr_achieved != null ? entry.rr_achieved : "-"}`,
                    `Confidence: ${entry.confidence}%`,
                ],
                biasClass((entry.result || entry.status || "OPEN").toLowerCase()),
                entry.result || entry.status || "OPEN",
            )
        )
        .join("");
}

function renderPerformance(performance) {
    const container = document.getElementById("performance-panel");
    if (!performance) {
        container.innerHTML = '<div class="empty-state">Performance snapshot unavailable.</div>';
        return;
    }

    const topStrategies = performance.best_strategies || [];
    const conceptSummary = (performance.best_concepts || [])
        .map((concept) => `${concept.name} (${concept.count})`)
        .join(", ") || "None";

    const cards = [
        createCard(
            "Core Stats",
            [
                `Win rate: ${formatNumber(performance.win_rate, 2)}%`,
                `Profit factor: ${formatNumber(performance.profit_factor, 2)}`,
                `Total trades: ${performance.total_trades || 0}`,
                `Closed trades: ${performance.closed_trades || 0}`,
            ],
            "info",
            "stats",
        ),
        createCard(
            "Best Concepts",
            [conceptSummary],
            "info",
            "concepts",
        ),
    ];

    topStrategies.slice(0, 3).forEach((strategy) => {
        cards.push(
            createCard(
                strategy.name || strategy.strategy || "Strategy",
                [
                    `Score: ${formatNumber(strategy.ranking_score, 2)}`,
                    `Win rate: ${formatNumber(strategy.win_rate, 2)}%`,
                    `Profit factor: ${formatNumber(strategy.profit_factor, 2)}`,
                ],
                "info",
                "ranked",
            )
        );
    });

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
    let payload = null;
    try {
        payload = rawText ? JSON.parse(rawText) : {};
    } catch (error) {
        throw new Error(`Backend returned non-JSON data: ${rawText.slice(0, 180)}`);
    }

    if (!response.ok) {
        throw new Error(payload.detail || "Failed to load platform data.");
    }
    return payload;
}

function syncStateFromInputs() {
    state.symbol = document.getElementById("symbol-input").value.trim().toUpperCase() || "EURUSD";
    state.source = document.getElementById("source-select").value;
    state.interval = document.getElementById("interval-select").value;
    state.tradingViewSymbol = document.getElementById("tv-symbol-input").value.trim() || inferTradingViewSymbol(state.symbol);
}

function syncBackendInputsFromTradingView() {
    const tvValue = document.getElementById("tv-symbol-input").value.trim();
    const inferred = inferBackendFromTradingView(tvValue);
    if (!inferred) {
        return;
    }

    document.getElementById("symbol-input").value = inferred.symbol;
    document.getElementById("source-select").value = inferred.source;
}

async function refreshDashboard(forceChart = false) {
    syncStateFromInputs();
    renderTradingViewWidget(forceChart);

    try {
        const payload = await fetchDashboardData();
        renderBias(payload);
        renderSetup(payload);
        renderHtfAndOverlays(payload);
        renderAlerts(payload.alerts || []);
        renderJournal(payload.journal || []);
        renderPerformance(payload.performance || {});
    } catch (error) {
        renderAlerts([{ type: "news", message: error.message }]);
    }
}

function bindEvents() {
    document.getElementById("refresh-button").addEventListener("click", () => refreshDashboard(true));
    document.getElementById("tv-symbol-input").addEventListener("change", () => {
        syncBackendInputsFromTradingView();
        refreshDashboard(true);
    });
    document.getElementById("symbol-input").addEventListener("change", () => refreshDashboard(true));
    document.getElementById("source-select").addEventListener("change", () => refreshDashboard(false));
    document.getElementById("interval-select").addEventListener("change", () => refreshDashboard(true));
}

function startAutoRefresh() {
    setInterval(() => {
        refreshDashboard(false);
    }, 5000);
}

window.addEventListener("DOMContentLoaded", () => {
    document.getElementById("tv-symbol-input").value = state.tradingViewSymbol;
    syncBackendInputsFromTradingView();
    bindEvents();
    refreshDashboard(true);
    startAutoRefresh();
});
