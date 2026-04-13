const state = {
    symbol: "EURUSD",
    interval: "1h",
    source: "auto",
    widget: null,
    tradingViewSymbol: "FX:EURUSD",
    requestId: 0,
    journalFilters: {
        pair: "",
        result: "",
        quality: "",
        month: "",
    },
};

const APPROVED_SYMBOLS = new Set([
    "ETHUSDT", "BTCUSDT", "XAUUSD",
    "NAS100", "SP500", "US30", "GER40", "UK100", "JP225",
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPNZD", "GBPCAD",
    "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF",
    "NZDJPY", "NZDCAD", "NZDCHF",
    "CADJPY", "CADCHF", "CHFJPY",
]);

const SYMBOL_CLUSTERS = [
    { name: "Crypto Cluster", symbols: ["BTCUSDT", "ETHUSDT"] },
    { name: "US Index Cluster", symbols: ["NAS100", "SP500", "US30"] },
    { name: "JPY Cross Cluster", symbols: ["AUDJPY", "NZDJPY", "USDJPY", "GBPJPY", "CHFJPY", "CADJPY"] },
    { name: "AUD/NZD Cluster", symbols: ["AUDUSD", "NZDUSD", "AUDCAD", "AUDNZD"] },
    { name: "EUR/GBP/USD Cluster", symbols: ["EURUSD", "GBPUSD", "EURGBP"] },
];

function inferTradingViewSymbol(symbol) {
    if (String(symbol || "").includes(":")) {
        return String(symbol).trim().toUpperCase();
    }
    const normalized = String(symbol || "").trim().toUpperCase();
    if (!normalized) return "FX:EURUSD";
    if (normalized.endsWith("USDT")) return `BINANCE:${normalized}`;

    const mapping = {
        AUDUSD: "FX:AUDUSD",
        AUDCAD: "FX:AUDCAD",
        AUDCHF: "FX:AUDCHF",
        AUDJPY: "FX:AUDJPY",
        AUDNZD: "FX:AUDNZD",
        CADCHF: "FX:CADCHF",
        CADJPY: "FX:CADJPY",
        CHFJPY: "FX:CHFJPY",
        EURAUD: "FX:EURAUD",
        EURCAD: "FX:EURCAD",
        EURCHF: "FX:EURCHF",
        EURGBP: "FX:EURGBP",
        EURJPY: "FX:EURJPY",
        EURNZD: "FX:EURNZD",
        EURUSD: "FX:EURUSD",
        GBPAUD: "FX:GBPAUD",
        GBPCAD: "FX:GBPCAD",
        GBPCHF: "FX:GBPCHF",
        GBPNZD: "FX:GBPNZD",
        GBPUSD: "FX:GBPUSD",
        GBPJPY: "FX:GBPJPY",
        NZDCAD: "FX:NZDCAD",
        NZDCHF: "FX:NZDCHF",
        NZDUSD: "FX:NZDUSD",
        NZDJPY: "FX:NZDJPY",
        USDCAD: "FX:USDCAD",
        USDCHF: "FX:USDCHF",
        USDJPY: "FX:USDJPY",
        XAUUSD: "OANDA:XAUUSD",
        NAS100: "CAPITALCOM:US100",
        SP500: "CAPITALCOM:US500",
        US30: "CAPITALCOM:US30",
        GER40: "CAPITALCOM:DE40",
        UK100: "CAPITALCOM:UK100",
        JP225: "CAPITALCOM:JPN225",
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
        US500: "SP500",
        US30: "US30",
        DE40: "GER40",
        UK100: "UK100",
        JPN225: "JP225",
        XAUUSD: "XAUUSD",
    };
    return {
        symbol: symbolMap[compact] || compact,
        source: compact.endsWith("USDT") ? "binance" : (providerMap[provider] || "auto"),
    };
}

function toTradingViewInterval(interval) {
    return interval === "15m" ? "15" : "60";
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

function humanizeReason(reason) {
    const value = String(reason || "").trim();
    const map = {
        "Bias mismatch": "Timeframes are not aligned yet.",
        "Daily/H4 bias mismatch": "Daily and H4 are not aligned yet.",
        "Weekly/Daily bias mismatch": "Weekly and Daily are not aligned yet.",
        "Unfavorable regime": "Market is too choppy or not trending clearly enough.",
        "Insufficient target range": "Target is too close for the minimum reward requirement.",
        "No H1 pullback": "H1 has not pulled back into a useful structure area yet.",
        "No LTF break and retest": "Lower timeframe confirmation has not formed yet.",
        "No continuation or reversal zone": "No clean higher-timeframe zone is active yet.",
        "No H4/H1 confirmation": "H4 and H1 have not confirmed the move yet.",
        "H1 not aligned": "H1 flow is not aligned with the higher-timeframe bias.",
        "Setup invalidated": "Price moved too far or structure changed enough to cancel the idea.",
        "News lock": "A nearby high-impact news event is blocking entries for now.",
    };
    return map[value] || value;
}

function humanizeReasons(reasons) {
    const items = Array.isArray(reasons) ? reasons : [];
    return items.map(humanizeReason);
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
    document.getElementById("checklist-panel").innerHTML = '<div class="empty-state">Refreshing checklist...</div>';
    document.getElementById("live-setups-panel").innerHTML = '<div class="empty-state">Refreshing live setups...</div>';
    document.getElementById("cluster-panel").innerHTML = '<div class="empty-state">Refreshing cluster alignment...</div>';
    document.getElementById("opportunities-panel").innerHTML = '<div class="empty-state">Refreshing top opportunities...</div>';
    document.getElementById("scan-report-panel").innerHTML = '<div class="empty-state">Refreshing broader scan...</div>';
    document.getElementById("setup-map-panel").innerHTML = '<div class="empty-state">Refreshing setup map...</div>';
    document.getElementById("htf-panel").innerHTML = '<div class="empty-state">Refreshing timeframes...</div>';
    document.getElementById("overlay-panel").innerHTML = '<div class="empty-state">Refreshing overlays...</div>';
    document.getElementById("alerts-panel").innerHTML = '<div class="empty-state">Refreshing alerts...</div>';
    document.getElementById("journal-panel").innerHTML = '<div class="empty-state">Refreshing journal...</div>';
    document.getElementById("news-panel").innerHTML = '<div class="empty-state">Refreshing news...</div>';
    document.getElementById("digital-twin-panel").innerHTML = '<div class="empty-state">Refreshing digital twin...</div>';
    document.getElementById("fractal-panel").innerHTML = '<div class="empty-state">Refreshing fractal context...</div>';
}

function renderErrorState(message) {
    renderBias({ final_bias: "NEUTRAL", daily_bias: "-", h1_bias: "-", status: "ERROR", confidence: "-", latest_price: null });
    document.getElementById("setup-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("checklist-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("live-setups-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("cluster-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("opportunities-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("scan-report-panel").innerHTML = createCard("Error fetching data", [message || "Server error"], "sell", "error");
    document.getElementById("setup-map-panel").innerHTML = '<div class="empty-state">Error fetching data.</div>';
    document.getElementById("htf-panel").innerHTML = '<div class="empty-state">Error fetching data.</div>';
    document.getElementById("overlay-panel").innerHTML = '<div class="empty-state">Error fetching data.</div>';
    document.getElementById("overlay-strip").innerHTML = '<span class="overlay-chip">Server error</span>';
    document.getElementById("alerts-panel").innerHTML = createCard("Error", [message || "Server error"], "sell", "error");
    document.getElementById("news-panel").innerHTML = createCard("Error", [message || "Server error"], "sell", "error");
    document.getElementById("digital-twin-panel").innerHTML = createCard("Error", [message || "Server error"], "sell", "error");
    document.getElementById("fractal-panel").innerHTML = createCard("Error", [message || "Server error"], "sell", "error");
}

function renderBias(payload) {
    const finalBias = payload.final_bias || "NEUTRAL";
    const finalBiasElement = document.getElementById("final-bias");
    finalBiasElement.textContent = finalBias;
    finalBiasElement.className = `final-bias ${String(finalBias).toLowerCase()}`;
    document.getElementById("htf-bias").textContent = payload.daily_bias || "-";
    document.getElementById("news-bias").textContent = payload.h1_bias || "-";
    const lifecycle = payload.lifecycle ? ` | ${payload.lifecycle}` : "";
    document.getElementById("technical-bias").textContent = `${payload.status || "-"}${lifecycle}`;
    document.getElementById("confidence").textContent = payload.confidence || "LOW";
    document.getElementById("live-price-badge").textContent = `Price: ${formatNumber(payload.latest_price)}`;
}

function renderSetup(payload) {
    const container = document.getElementById("setup-panel");
    if (payload.status === "ERROR") {
        container.innerHTML = createCard("Error fetching data", [payload.message || "Server error"], "sell", "error");
        renderSetupMap(payload, "ERROR");
        renderChecklist(payload);
        return;
    }

    if (payload.status === "NO TRADE") {
        container.innerHTML = createCard(
            "No valid setup available",
            [
                payload.message || "System running, no setups",
                `Lifecycle: ${payload.lifecycle || "-"}`,
                payload.stalker ? `Stalker: ${payload.stalker.state} (${payload.stalker.score})` : "",
                `Missing: ${humanizeReasons(payload.missing || []).join(" | ") || "None"}`,
                `Why: ${(payload.analysis_context?.news?.news_lock?.locked ? "News lock active" : "Waiting for full confluence")}`,
            ],
            "neutral",
            "no trade",
        );
        renderSetupMap(payload, "NO_TRADE");
        renderChecklist(payload);
        return;
    }

    if (payload.status === "WAIT_CONFIRMATION") {
        const confirmation = payload.details?.confirmation_entry || {};
        const planZone = payload.details?.plan_zone || {};
        const targetRr = payload.risk_reward_ratio != null ? `1:${payload.risk_reward_ratio}` : "-";
        const session = payload.analysis_context?.session?.session || "-";
        const regime = payload.analysis_context?.regime?.regime || "-";
        container.innerHTML = createCard(
            `${(payload.strategies || []).join(" + ") || "Strategy"} - Wait For Confirmation`,
            [
                `Bias: ${payload.bias || "-"}`,
                `Lifecycle: ${payload.lifecycle || "-"}`,
                `Session: ${session}`,
                `Regime: ${regime}`,
                `Plan Zone: ${formatNumber(planZone.start_price)} -> ${formatNumber(planZone.end_price)}`,
                `Projected Entry Zone: ${formatNumber(payload.entry)}`,
                `Projected SL Zone: ${formatNumber(payload.sl)}`,
                `Projected TP Zone: ${formatNumber(payload.tp)}`,
                `Target RR: ${targetRr}`,
                `Confirmation Needed: ${(confirmation.required || []).join(", ") || "H1 confirmation"}`,
                `Why: ${payload.message || "Wait for H1 confirmation before entering."}`,
                `Confluences: ${(payload.confluences || []).join(", ")}`,
            ],
            "info",
            "wait confirmation",
        );
        renderSetupMap(payload, "WAIT_CONFIRMATION");
        renderChecklist(payload);
        return;
    }

    const targetRr = payload.risk_reward_ratio != null ? `1:${payload.risk_reward_ratio}` : "-";
    const session = payload.analysis_context?.session?.session || "-";
    const regime = payload.analysis_context?.regime?.regime || "-";
    container.innerHTML = createCard(
        `${(payload.strategies || []).join(" + ") || "Strategy"}`,
        [
            `Bias: ${payload.bias}`,
            `Lifecycle: ${payload.lifecycle || "-"}`,
            `Session: ${session}`,
            `Regime: ${regime}`,
            `Entry: ${formatNumber(payload.entry)}`,
            `SL: ${formatNumber(payload.sl)}`,
            `TP: ${formatNumber(payload.tp)}`,
            `Target RR: ${targetRr}`,
            `Confidence: ${payload.confidence}`,
            `Confluences: ${(payload.confluences || []).join(", ")}`,
            payload.analysis_context?.news?.news_lock?.locked ? "News lock: active" : "News lock: clear",
        ],
        payload.bias === "BUY" ? "buy" : "sell",
        payload.status,
    );
    renderSetupMap(payload, "VALID_TRADE");
    renderChecklist(payload);
}

function renderChecklist(payload) {
    const container = document.getElementById("checklist-panel");
    const items = payload.analysis_context?.checklist || [];
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No checklist data available.</div>';
        return;
    }

    container.innerHTML = items.map((item) =>
        createCard(
            item.name || "Check",
            [
                `Status: ${item.ok ? "CONFIRMED" : "MISSING"}`,
                item.detail ? `Detail: ${item.detail}` : "",
            ].filter(Boolean),
            item.ok ? "buy" : "sell",
            item.ok ? "ok" : "missing",
        )
    ).join("");
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
    const targetRr = payload.risk_reward_ratio != null ? Number(payload.risk_reward_ratio).toFixed(2) : "-";

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
                <div class="rr-card"><span class="metric-label">Target RR</span><strong>${targetRr}</strong></div>
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
    if (timeframes.ltf?.used) {
        cards.push(createCard("LTF Refinement", [`Trend: ${timeframes.ltf.bias}`, `Price: ${formatNumber(timeframes.ltf.latest_price)}`, `TF: ${timeframes.ltf.timeframe || "15m"}`], "info", "15m"));
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
            [
                alert.message || "-",
                alert.why_this_matters ? `Why: ${alert.why_this_matters}` : "",
                alert.tier ? `Tier: ${alert.tier}` : "",
                alert.risk_reward_ratio != null ? `RR: 1:${alert.risk_reward_ratio}` : "",
            ].filter(Boolean),
            alert.status === "LOSS" ? "sell" : "info",
            alert.type || "alert",
        )
    ).join("");
}

function renderJournal(payload) {
    const container = document.getElementById("journal-panel");
    const entries = payload?.entries || [];
    if (!entries?.length) {
        container.innerHTML = '<div class="empty-state">No journal entries yet.</div>';
        return;
    }
    const summary = payload?.summary || {};
    const grouped = new Map();
    entries.forEach((entry) => {
        const dateKey = String(entry.closed_at || entry.timestamp || "").slice(0, 10) || "Unknown date";
        if (!grouped.has(dateKey)) grouped.set(dateKey, []);
        grouped.get(dateKey).push(entry);
    });

    const sections = [];
    sections.push(createCard(
        "Journal Summary",
        [
            `Entries: ${summary.count ?? entries.length}`,
            `Closed: ${summary.closed ?? 0}`,
            `Wins: ${summary.wins ?? 0} | Losses: ${summary.losses ?? 0}`,
            `Win rate: ${summary.win_rate != null ? `${summary.win_rate}%` : "-"}`,
        ],
        "info",
        "summary",
    ));
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
                        `Quality: ${entry.quality || "-"}`,
                        entry.snapshot_path ? `Snapshot: ${entry.snapshot_path}` : "",
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
    const edge = performance.edge_control || {};
    const validation = performance.validation || {};
    const calibration = performance.calibration || {};
    const shadow = performance.shadow_tracking || {};
    const scan = performance.scan_diagnostics || {};
    const scannerHealth = performance.scanner_health || {};
    const cards = [
        createCard("Core Stats", [
            `Win rate: ${formatNumber(performance.win_rate, 2)}%`,
            `Profit factor: ${formatNumber(performance.profit_factor, 2)}`,
            `Expectancy: ${formatNumber(performance.expectancy_r, 2)}R`,
            `Total trades: ${performance.total_trades || 0}`,
            `Closed trades: ${performance.closed_trades || 0}`,
            `Open: ${performance.open_trades || 0} | Pending: ${performance.pending_open_trades || 0}`,
            `Stale hidden: ${performance.stale_open_trades || 0}`,
        ], "info", "stats"),
    ];
    cards.push(createCard("Validation Mode", [
        `Triggered closed trades: ${validation.validated_closed_trades || 0}`,
        `Adjusted net R: ${formatNumber(validation.adjusted_net_r, 2)}`,
        `Adjusted PF: ${formatNumber(validation.adjusted_profit_factor, 2)}`,
        `Adjusted win rate: ${formatNumber(validation.adjusted_win_rate, 2)}%`,
        `Adjusted expectancy: ${formatNumber(validation.adjusted_expectancy_r, 2)}R`,
        `Auto-disabled: ${(validation.underperforming_symbols || []).join(", ") || "None"}`,
    ], "info", "validation"));
    cards.push(createCard("Calibration", [
        `Pending changes: ${calibration.pending_changes?.length || 0}`,
        `Recommended sessions: ${(calibration.recommended_sessions || []).join(", ") || "-"}`,
        `Shadow ready: ${(calibration.shadow_sessions_ready || []).join(", ") || "None"}`,
        `Recommended grade: ${calibration.recommended_minimum_setup_grade || "-"}`,
        `Promote: ${(calibration.promoted_symbols || []).join(", ") || "None"}`,
        `Demote: ${(calibration.demoted_symbols || []).join(", ") || "None"}`,
        calibration.last_application?.applied_at
            ? `Last applied: ${calibration.last_application.applied_at}`
            : `Recent adjusted R: ${formatNumber(calibration.recent_adjusted_net_r, 2)} | Expectancy: ${formatNumber(calibration.recent_adjusted_expectancy_r, 2)}R`,
    ], calibration.can_apply ? "info" : "neutral", "calibration"));
    cards.push(createCard("Edge Control", [
        `Locked: ${edge.locked ? "YES" : "NO"}`,
        `Symbol mode: ${edge.symbol_filter_mode || "score_only"}`,
        `Session mode: ${edge.session_filter_mode || "score_only"}`,
        `Session whitelist: ${(edge.allowed_sessions || []).join(", ") || "OPEN"}`,
        `Shadow sessions: ${(edge.shadow_sessions || []).join(", ") || "None"}`,
        `Min grade: ${edge.minimum_setup_grade || "-"}`,
        `Whitelist mode: ${edge.symbol_mode || "open"}`,
        `Allowed symbols: ${(edge.allowed_symbols || []).slice(0, 5).join(", ") || "OPEN"}`,
        `Calibration blocks: ${(edge.calibration_blocked_symbols || []).slice(0, 5).join(", ") || "None"}`,
        `Validation blocks: ${(edge.validation_blocked_symbols || []).slice(0, 5).join(", ") || "None"}`,
        `Open tracked: ${edge.open_positions?.total || 0} | Stale ignored: ${edge.open_positions?.stale || 0}`,
        edge.lock_reasons?.length
            ? `Why locked: ${edge.lock_reasons.join(" | ")}`
            : `Daily R: ${formatNumber(edge.daily?.net_rr, 2)} | Weekly R: ${formatNumber(edge.weekly?.net_rr, 2)}`,
    ], edge.locked ? "sell" : "info", "edge"));
    cards.push(createCard("Shadow Track", [
        `Sessions: ${(shadow.sessions || []).join(", ") || "None"}`,
        `Validated closed trades: ${shadow.validated_closed_trades || 0}`,
        `Ready for live: ${(shadow.ready_sessions || []).join(", ") || "None"}`,
        `Adjusted net R: ${formatNumber(shadow.adjusted_net_r, 2)}`,
        `Adjusted win rate: ${formatNumber(shadow.adjusted_win_rate, 2)}%`,
        `Adjusted expectancy: ${formatNumber(shadow.adjusted_expectancy_r, 2)}R`,
    ], (shadow.validated_closed_trades || 0) > 0 ? "neutral" : "info", "shadow"));
    cards.push(createCard("Live Scan", [
        `Health: ${String(scannerHealth.status || "unknown").toUpperCase()}`,
        `Progress: ${scannerHealth.completed_symbols || 0}/${scannerHealth.total_symbols || 0} | Current: ${scannerHealth.current_symbol || "-"}`,
        `Last completed: ${scannerHealth.last_completed_symbol || "-"}`,
        `Last progress: ${scannerHealth.last_progress_at || "-"}`,
        `Last successful scan: ${scannerHealth.last_successful_scan || "-"}`,
        `Evaluated: ${scan.evaluated_symbols || 0} | Valid: ${scan.valid_candidates || 0}`,
        `Selected: ${scan.selected_count || 0} | Blocked: ${scan.blocked_count || 0} | Rejected: ${scan.rejected_count || 0}`,
        `Selected now: ${(scan.selected_candidates || []).map((item) => item.symbol).join(", ") || "None"}`,
        `Blocked now: ${(scan.blocked_candidates || []).slice(0, 3).map((item) => `${item.symbol} (${(item.reasons || []).join("/") || item.block_type || "blocked"})`).join(" | ") || "None"}`,
        `Rejected now: ${(scan.rejected_candidates || []).slice(0, 3).map((item) => `${item.symbol} (${(item.missing || []).join("/") || item.message || item.status || "no trade"})`).join(" | ") || "None"}`,
        scannerHealth.reasons?.length ? `Why: ${scannerHealth.reasons.join(" | ")}` : "",
    ], scannerHealth.status === "stalled" || scannerHealth.status === "stale" ? "sell" : (scan.selected_count || 0) > 0 ? "buy" : "neutral", "scan"));

    (validation.top_symbols || []).slice(0, 3).forEach((stats) => {
        cards.push(createCard(`${stats.symbol} Validated`, [
            `Closed trades: ${stats.closed_trades || 0}`,
            `Adj. win rate: ${formatNumber(stats.adjusted_win_rate, 2)}%`,
            `Adj. net R: ${formatNumber(stats.adjusted_net_r, 2)} | Expectancy: ${formatNumber(stats.adjusted_expectancy_r, 2)}R`,
            `Avg cost: ${formatNumber(stats.avg_cost_r, 2)}R`,
        ], "info", "pair"));
    });
    (validation.bottom_symbols || []).slice(0, 2).forEach((stats) => {
        cards.push(createCard(`${stats.symbol} Underperforming`, [
            `Closed trades: ${stats.closed_trades || 0}`,
            `Adj. win rate: ${formatNumber(stats.adjusted_win_rate, 2)}%`,
            `Adj. net R: ${formatNumber(stats.adjusted_net_r, 2)} | Expectancy: ${formatNumber(stats.adjusted_expectancy_r, 2)}R`,
        ], "sell", "risk"));
    });
    (shadow.session_reviews || []).slice(0, 2).forEach((review) => {
        cards.push(createCard(`${String(review.session || "").toUpperCase()} Shadow`, [
            `Closed trades: ${review.closed_trades || 0}`,
            `Adj. win rate: ${formatNumber(review.adjusted_win_rate, 2)}% | PF: ${formatNumber(review.adjusted_profit_factor, 2)}`,
            `Adj. net R: ${formatNumber(review.adjusted_net_r, 2)} | Expectancy: ${formatNumber(review.adjusted_expectancy_r, 2)}R`,
            `Ready for live: ${review.ready_for_live ? "YES" : "NO"}`,
            `Why: ${(review.reasons || []).join(" | ") || "Meets promotion gate"}`,
        ], review.ready_for_live ? "buy" : "neutral", "shadow-review"));
    });
    container.innerHTML = cards.join("");
}

function renderDigitalTwin(payload) {
    const container = document.getElementById("digital-twin-panel");
    const twin = payload?.digital_twin || payload;
    if (!twin || Object.keys(twin).length === 0) {
        container.innerHTML = '<div class="empty-state">Digital twin not available yet.</div>';
        return;
    }

    const account = twin.account || {};
    const summary = twin.summary || {};
    const cards = [
        createCard("Virtual Account", [
            `Balance: $${formatNumber(account.balance ?? 0, 2)}`,
            `Equity: $${formatNumber(account.equity ?? 0, 2)}`,
            `Risk per trade: $${formatNumber(account.risk_per_trade ?? 0, 2)}`,
            `Open positions: ${account.open_positions ?? 0}`,
            `Drawdown: ${formatNumber(account.drawdown_percent ?? 0, 2)}%`,
        ], "info", "twin"),
        createCard("Twin Summary", [
            `Closed trades: ${summary.closed_count ?? 0}`,
            `Wins: ${summary.wins ?? 0} | Losses: ${summary.losses ?? 0}`,
            `Win rate: ${formatNumber(summary.win_rate ?? 0, 2)}%`,
            `Realized PnL: $${formatNumber(summary.realized_pnl ?? 0, 2)}`,
            `News lock active: ${summary.news_overview?.news_lock_active ? "YES" : "NO"}`,
        ], "info", "summary"),
    ];

    (summary.strategy_leaderboard || []).slice(0, 3).forEach((item) => {
        cards.push(createCard(`${item.strategy}`, [
            `Observations: ${item.observations ?? 0}`,
            `Valid: ${item.valid_signals ?? 0} | Near-valid: ${item.near_valid ?? 0}`,
            `Win rate: ${formatNumber(item.win_rate ?? 0, 2)}% | Avg RR: ${formatNumber(item.avg_rr ?? 0, 2)}`,
            `Realized PnL: $${formatNumber(item.realized_pnl ?? 0, 2)}`,
            `News-locked obs: ${item.news_locked_observations ?? 0}`,
        ], "info", "strategy"));
    });

    (twin.open_trades || []).slice(0, 3).forEach((trade) => {
        cards.push(createCard(`${trade.symbol} Twin`, [
            `Status: ${trade.status}`,
            `Bias: ${trade.bias} | Strategy: ${trade.primary_strategy}`,
            `Entry: ${formatNumber(trade.entry)} | SL: ${formatNumber(trade.stop_loss)} | TP: ${formatNumber(trade.take_profit)}`,
            `Unrealized: $${formatNumber(trade.unrealized_pnl ?? 0, 2)} | Risk: $${formatNumber(trade.risk_amount ?? 0, 2)}`,
            `News lock at entry: ${trade.news_context?.news_lock_active ? "YES" : "NO"}`,
        ], trade.bias === "BUY" ? "buy" : "sell", "open"));
    });

    container.innerHTML = cards.join("");
}

function renderFractal(payload) {
    const container = document.getElementById("fractal-panel");
    const fractal = payload?.fractal || payload;
    if (!fractal || fractal.status === "ERROR") {
        container.innerHTML = createCard("Fractal context unavailable", [fractal?.message || "Unable to load fractal context."], "sell", "error");
        return;
    }
    if (!fractal.current_box || Object.keys(fractal.current_box).length === 0) {
        container.innerHTML = createCard("Fractal context unavailable", [fractal?.message || "Not enough history yet."], "neutral", "fractal");
        return;
    }

    const scenario = fractal.scenario || {};
    const currentBox = fractal.current_box || {};
    const validation = fractal.validation || {};
    const motifs = currentBox.motifs || [];
    const cards = [
        createCard("Current Box", [
            `Type: ${currentBox.box_type || "-"}`,
            `Trend: ${currentBox.trend || "-"}`,
            `Rotations: ${currentBox.rotations ?? "-"}`,
            `Range: ${formatNumber(currentBox.range_low)} -> ${formatNumber(currentBox.range_high)}`,
            `Location: ${currentBox.premium_discount || "-"}`,
            `Motifs: ${motifs.length ? motifs.map((item) => item.label).join(" | ") : "None detected"}`,
        ], "info", "box"),
        createCard("Path Bias", [
            `Bias: ${scenario.path_bias || "neutral"}`,
            `Breakdown probability: ${formatNumber(scenario.breakdown_probability ?? 0, 2)}%`,
            `Breakout probability: ${formatNumber(scenario.breakout_probability ?? 0, 2)}%`,
            `Range probability: ${formatNumber(scenario.range_probability ?? 0, 2)}%`,
            `Confidence: ${formatNumber(scenario.confidence ?? 0, 2)}%`,
            `${scenario.dominant_theme || fractal.message || ""}`,
        ], scenario.path_bias === "bearish" ? "sell" : scenario.path_bias === "bullish" ? "buy" : "neutral", "bias"),
        createCard("Validation", [
            `Samples: ${validation.sample_count ?? 0}`,
            `Directional accuracy: ${formatNumber(validation.directional_accuracy ?? 0, 2)}%`,
            `Avg top similarity: ${formatNumber(validation.avg_top_similarity ?? 0, 2)}`,
            `Hits: BULL ${validation.bullish_hits ?? 0} | BEAR ${validation.bearish_hits ?? 0} | RANGE ${validation.range_hits ?? 0}`,
        ], "info", "validation"),
    ];

    (fractal.analogs || []).slice(0, 3).forEach((item, index) => {
        cards.push(createCard(`Analog ${index + 1}`, [
            `Window: ${String(item.window_start || "").slice(0, 10)} -> ${String(item.window_end || "").slice(0, 10)}`,
            `Similarity: ${formatNumber(item.similarity_score ?? 0, 2)}`,
            `Type: ${item.box_type || "-"} | Trend: ${item.trend || "-"}`,
            `Future outcome: ${item.future_outcome?.label || "-"}`,
            `Close move: ${formatNumber(item.future_outcome?.close_move_percent ?? 0, 2)}%`,
        ], "info", "analog"));
    });

    motifs.slice(0, 2).forEach((motif, index) => {
        cards.push(createCard(`Motif ${index + 1}`, [
            `Label: ${motif.label || "-"}`,
            `Bias: ${motif.bias || "-"}`,
            `Confidence: ${formatNumber(motif.confidence ?? 0, 2)}%`,
            motif.description || "",
        ], motif.bias === "bearish" ? "sell" : motif.bias === "bullish" ? "buy" : "info", "motif"));
    });

    if (fractal.short_term && Object.keys(fractal.short_term).length) {
        cards.push(createCard("Short-Term Context", [
            `H4 trend: ${fractal.short_term.h4_trend || "-"}`,
            `Recent high: ${formatNumber(fractal.short_term.recent_high)}`,
            `Recent low: ${formatNumber(fractal.short_term.recent_low)}`,
            `Recent close: ${formatNumber(fractal.short_term.recent_close)}`,
        ], "info", "h4"));
    }

    container.innerHTML = cards.join("");
}

function renderWatchlist(payload) {
    const container = document.getElementById("watchlist-panel");
    const items = payload?.symbols || [];
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No watchlist data available.</div>';
        return;
    }

    const grouped = {
        Ready: [],
        "Near Valid": [],
        Developing: [],
        Skip: [],
    };
    items.forEach((item) => {
        if (item.status === "VALID_TRADE") {
            grouped.Ready.push(item);
        } else if (item.stalker?.state === "near_valid") {
            grouped["Near Valid"].push(item);
        } else if (item.stalker?.state === "developing") {
            grouped.Developing.push(item);
        } else {
            grouped.Skip.push(item);
        }
    });

    const renderItem = (item) => {
        const isActive = item.symbol === state.symbol;
        const entryLabel = item.entry != null ? formatNumber(item.entry) : "-";
        const rrLabel = item.risk_reward_ratio != null ? `1:${item.risk_reward_ratio}` : "-";
        return `
            <article class="card watchlist-item ${isActive ? "active" : ""}" data-symbol="${item.symbol}" data-source="${item.source}">
                <div class="watchlist-head">
                    <span class="watchlist-symbol">${item.symbol}</span>
                    <span class="watchlist-status ${String(item.status || "").toLowerCase()}">${String(item.status || "").replace("_", " ")}</span>
                </div>
                <div class="watchlist-meta">
                    <span class="watchlist-price">Price: ${formatNumber(item.latest_price)}</span>
                    <span class="watchlist-price">Entry: ${entryLabel}</span>
                </div>
                <div class="watchlist-sub">Daily: ${item.daily_bias} | H1: ${item.h1_bias}</div>
                <div class="watchlist-sub">Lifecycle: ${item.lifecycle || "-"} | Tier: ${item.tier || "-"} | Rank: ${item.rank || "-"}</div>
                <div class="watchlist-sub">Stalker: ${item.stalker?.state || "-"} | Score: ${item.stalker?.score ?? "-"}</div>
                <div class="watchlist-sub">Score: ${item.ranking_score ?? "-"}</div>
                <div class="watchlist-sub">Confidence: ${item.confidence} | RR: ${rrLabel} | Source: ${item.source}</div>
            </article>
        `;
    };

    container.innerHTML = Object.entries(grouped).map(([label, groupItems]) => {
        if (!groupItems.length) return "";
        return `
            <section class="watchlist-group">
                <div class="journal-day">${label}</div>
                ${groupItems.map(renderItem).join("")}
            </section>
        `;
    }).join("");

    container.querySelectorAll(".watchlist-item").forEach((element) => {
        element.addEventListener("click", () => {
            document.getElementById("symbol-input").value = element.dataset.symbol || "EURUSD";
            document.getElementById("source-select").value = element.dataset.source || "auto";
            document.getElementById("tv-symbol-input").value = inferTradingViewSymbol(element.dataset.symbol || "EURUSD");
            refreshDashboard(true);
        });
    });
}

function renderTopOpportunities(payload) {
    const container = document.getElementById("opportunities-panel");
    const items = (payload?.symbols || [])
        .filter((item) => item.symbol !== state.symbol && item.status !== "ERROR")
        .slice(0, 5);

    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No ranked opportunities available right now.</div>';
        return;
    }

    container.innerHTML = items.map((item) => {
        const failedChecks = (item.analysis_context?.checklist || [])
            .filter((check) => !check.ok)
            .map((check) => check.name);
        const rawReasons = failedChecks.length ? failedChecks : (item.missing || []);
        const explanation = item.status === "VALID_TRADE"
            ? "Ready now."
            : (humanizeReasons(rawReasons).join(" | ") || item.message || "Market is building but not fully ready yet.");
        return `
            <article class="card watchlist-item" data-symbol="${item.symbol}" data-source="${item.source}">
                <span class="tag ${item.status === "VALID_TRADE" ? "buy" : "info"}">${item.tier || item.stalker?.state || item.status}</span>
                <h3>${item.rank || "-"} • ${item.symbol}</h3>
                <p>Bias: ${item.bias || item.daily_bias} | Daily: ${item.daily_bias} | H1: ${item.h1_bias}</p>
                <p>Lifecycle: ${item.lifecycle || "-"} | Score: ${item.ranking_score ?? "-"} | RR: ${item.risk_reward_ratio != null ? `1:${item.risk_reward_ratio}` : "-"}</p>
                <p>Why now: ${explanation || "Market is one of the stronger candidates right now."}</p>
            </article>
        `;
    }).join("");

    container.querySelectorAll(".watchlist-item").forEach((element) => {
        element.addEventListener("click", () => {
            document.getElementById("symbol-input").value = element.dataset.symbol || "EURUSD";
            document.getElementById("source-select").value = element.dataset.source || "auto";
            document.getElementById("tv-symbol-input").value = inferTradingViewSymbol(element.dataset.symbol || "EURUSD");
            refreshDashboard(true);
        });
    });
}

function renderLiveSetups(payload) {
    const container = document.getElementById("live-setups-panel");
    const items = (payload?.symbols || []).filter((item) => (item.status === "VALID_TRADE" || item.stalker?.state === "near_valid") && item.symbol !== state.symbol);
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No other active setups on the watchlist right now.</div>';
        return;
    }

    container.innerHTML = items.map((item) => {
        const context = item.analysis_context || {};
        const ob = context.order_block?.zone;
        const fvg = context.fvg?.zone;
        const inducement = context.inducement;
        return `
            <article class="card watchlist-item" data-symbol="${item.symbol}" data-source="${item.source}">
                <span class="tag ${String(item.bias || "").toLowerCase() === "buy" ? "buy" : "sell"}">${item.bias || "SETUP"}</span>
                <h3>${item.symbol}</h3>
                <p>Entry: ${formatNumber(item.entry)} | SL: ${formatNumber(item.sl)} | TP: ${formatNumber(item.tp)}</p>
                <p>Daily: ${item.daily_bias} | H1: ${item.h1_bias} | Lifecycle: ${item.lifecycle || "-"} </p>
                <p>Stalker: ${item.stalker?.state || "-"} | Score: ${item.stalker?.score ?? "-"}</p>
                <p>Confidence: ${item.confidence} | RR: ${item.risk_reward_ratio != null ? `1:${item.risk_reward_ratio}` : "-"} | Tier: ${item.tier || "-"}</p>
                <p>Rank: ${item.rank || "-"} | Score: ${item.ranking_score ?? "-"}</p>
                <p>OB: ${ob ? `${formatNumber(ob.start_price)} -> ${formatNumber(ob.end_price)}` : "-"}</p>
                <p>FVG: ${Array.isArray(fvg) ? `${formatNumber(fvg[0])} -> ${formatNumber(fvg[1])}` : "-"}</p>
                <p>Inducement: ${inducement?.confirmed ? formatNumber(inducement.level) : "-"}</p>
            </article>
        `;
    }).join("");

    container.querySelectorAll(".watchlist-item").forEach((element) => {
        element.addEventListener("click", () => {
            document.getElementById("symbol-input").value = element.dataset.symbol || "EURUSD";
            document.getElementById("source-select").value = element.dataset.source || "auto";
            document.getElementById("tv-symbol-input").value = inferTradingViewSymbol(element.dataset.symbol || "EURUSD");
            refreshDashboard(true);
        });
    });
}

function renderClusterPanel(payload) {
    const container = document.getElementById("cluster-panel");
    const items = payload?.symbols || [];
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No cluster data available right now.</div>';
        return;
    }

    const bySymbol = new Map(items.map((item) => [item.symbol, item]));
    const cards = SYMBOL_CLUSTERS.map((cluster) => {
        const members = cluster.symbols
            .map((symbol) => bySymbol.get(symbol))
            .filter(Boolean);
        if (!members.length) return "";

        const validMembers = members.filter((item) => item.status === "VALID_TRADE");
        const buyCount = validMembers.filter((item) => String(item.bias || "").toUpperCase() === "BUY").length;
        const sellCount = validMembers.filter((item) => String(item.bias || "").toUpperCase() === "SELL").length;
        const activeMembers = validMembers.length ? validMembers : members.filter((item) => item.stalker?.state === "near_valid" || item.stalker?.state === "developing");
        const avgScore = activeMembers.length
            ? activeMembers.reduce((sum, item) => sum + Number(item.ranking_score || 0), 0) / activeMembers.length
            : 0;

        let clusterBias = "MIXED";
        let tagClass = "neutral";
        let tagLabel = "mixed";
        if (buyCount > 0 && sellCount === 0) {
            clusterBias = "BULLISH";
            tagClass = "buy";
            tagLabel = "aligned";
        } else if (sellCount > 0 && buyCount === 0) {
            clusterBias = "BEARISH";
            tagClass = "sell";
            tagLabel = "aligned";
        } else if (!validMembers.length && activeMembers.length) {
            clusterBias = "BUILDING";
            tagClass = "info";
            tagLabel = "building";
        }

        const strongest = [...members].sort((left, right) => {
            const leftValid = left.status === "VALID_TRADE" ? 1 : 0;
            const rightValid = right.status === "VALID_TRADE" ? 1 : 0;
            if (rightValid !== leftValid) return rightValid - leftValid;
            return Number(right.ranking_score || 0) - Number(left.ranking_score || 0);
        })[0];

        const memberLine = members
            .slice(0, 6)
            .map((item) => {
                const stateLabel = item.status === "VALID_TRADE"
                    ? `${item.symbol} ${item.bias}`
                    : `${item.symbol} ${item.stalker?.state || item.status}`;
                return stateLabel;
            })
            .join(" | ");

        return `
            <div class="cluster-card" data-symbol="${strongest?.symbol || ""}" data-source="${strongest?.source || "auto"}">
                <div class="cluster-leader">Leader: ${strongest?.symbol || "-"}</div>
                ${createCard(
            cluster.name,
            [
                `Cluster bias: ${clusterBias}`,
                `Focus: ${strongest?.symbol || "-"} | Status: ${strongest?.status || "-"}`,
                `Valid members: ${validMembers.length} | Building: ${members.filter((item) => item.stalker?.state === "near_valid" || item.stalker?.state === "developing").length}`,
                `Average score: ${formatNumber(avgScore, 2)}`,
                `Members: ${memberLine || "-"}`,
            ],
            tagClass,
            tagLabel,
                )}
            </div>
        `;
    }).filter(Boolean);

    container.innerHTML = cards.join("") || '<div class="empty-state">No aligned clusters right now.</div>';
    container.querySelectorAll(".cluster-card").forEach((element) => {
        element.addEventListener("click", () => {
            const symbol = element.dataset.symbol;
            if (!symbol) return;
            document.getElementById("symbol-input").value = symbol;
            document.getElementById("source-select").value = element.dataset.source || "auto";
            document.getElementById("tv-symbol-input").value = inferTradingViewSymbol(symbol);
            refreshDashboard(true);
        });
    });
}

function renderScanReport(payload) {
    const container = document.getElementById("scan-report-panel");
    const items = payload?.valid?.length ? payload.valid : payload?.near_valid?.length ? payload.near_valid : payload?.developing || [];
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No broader candidates available right now.</div>';
        return;
    }

    container.innerHTML = items.slice(0, 8).map((item) => `
        <article class="card watchlist-item" data-symbol="${item.symbol}" data-source="${item.source}">
            <span class="tag ${item.status === "VALID_TRADE" ? "buy" : "neutral"}">${item.status === "VALID_TRADE" ? "ready" : (item.stalker?.state || "scan")}</span>
            <h3>${item.symbol}</h3>
            <p>Daily: ${item.daily_bias} | H1: ${item.h1_bias}</p>
            <p>Lifecycle: ${item.lifecycle || "-"} | Tier: ${item.tier || "-"}</p>
            <p>RR: ${item.risk_reward_ratio != null ? `1:${item.risk_reward_ratio}` : "-"} | Confidence: ${item.confidence || "-"}</p>
            <p>Missing: ${(item.missing || []).join(", ") || "None"}</p>
            <p>Stalker: ${item.stalker?.state || "-"} | Score: ${item.stalker?.score ?? "-"}</p>
        </article>
    `).join("");

    container.querySelectorAll(".watchlist-item").forEach((element) => {
        element.addEventListener("click", () => {
            document.getElementById("symbol-input").value = element.dataset.symbol || "EURUSD";
            document.getElementById("source-select").value = element.dataset.source || "auto";
            document.getElementById("tv-symbol-input").value = inferTradingViewSymbol(element.dataset.symbol || "EURUSD");
            refreshDashboard(true);
        });
    });
}

function renderNews(payload) {
    const container = document.getElementById("news-panel");
    if (!payload || payload.status === "ERROR") {
        container.innerHTML = createCard("News error", [payload?.message || "Unable to load news."], "sell", "error");
        return;
    }
    if (!payload.configured) {
        container.innerHTML = createCard("News feed unavailable", [payload.message || "No news provider configured."], "neutral", "news");
        return;
    }

    const cards = [
        createCard(
            `${payload.symbol} News Bias`,
            [
                `Pair bias: ${payload.pair_news_bias || "NEUTRAL"}`,
                `Currencies: ${(payload.currencies || []).join(" / ")}`,
                `News lock: ${payload.news_lock?.locked ? "ACTIVE" : "CLEAR"}`,
                payload.message || "No relevant market-moving news in range.",
            ],
            "info",
            "news",
        ),
    ];

    (payload.events || []).slice(0, 6).forEach((event) => {
        cards.push(
            createCard(
                `${event.currency} ${event.event_name}`,
                [
                    `Relevance: ${event.relevance_score ?? "-"}`,
                    `Impact: ${String(event.impact || "").toUpperCase()}`,
                    `Time: ${String(event.time || "").replace("T", " ").slice(0, 16)}`,
                    `Forecast: ${event.forecast ?? "n/a"} | Previous: ${event.previous ?? "n/a"} | Actual: ${event.actual ?? "n/a"}`,
                ],
                event.market_moving ? "buy" : "info",
                event.market_moving ? "moving" : "calendar",
            )
        );
    });

    (payload.news_lock?.events || []).forEach((event) => {
        cards.push(
            createCard(
                `News Lock: ${event.currency} ${event.event_name}`,
                [
                    `Time: ${String(event.time || "").replace("T", " ").slice(0, 16)}`,
                    `Minutes from now: ${event.minutes_from_now}`,
                ],
                "sell",
                "locked",
            )
        );
    });

    (payload.headlines || []).slice(0, 5).forEach((headline) => {
        cards.push(
            createCard(
                headline.title || "Live headline",
                [
                    `Source: ${headline.source || "Live feed"}`,
                    `Published: ${headline.published || "-"}`,
                    `Relevance: ${headline.relevance_score ?? "-"}`,
                ],
                "info",
                "headline",
            )
        );
    });

    if (!payload.events?.length) {
        cards.push(createCard("No relevant news", ["No market-moving events found for this pair right now."], "neutral", "news"));
    }

    container.innerHTML = cards.join("");
}

async function fetchDashboardData() {
    const params = new URLSearchParams({
        symbol: state.symbol,
        interval: state.interval,
        source: state.source,
        lite: "true",
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

async function fetchAlertsData() {
    const response = await fetch("/alerts?limit=30");
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : { entries: [] };
    } catch {
        return { entries: [] };
    }
}

async function fetchPerformanceData() {
    const response = await fetch("/performance");
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : {};
    } catch {
        return {};
    }
}

async function fetchDigitalTwinData() {
    const response = await fetch("/digital-twin");
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : {};
    } catch {
        return {};
    }
}

async function fetchWatchlistData() {
    const response = await fetch(`/watchlist?source=${encodeURIComponent(state.source)}&fast=true`);
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : { symbols: [] };
    } catch {
        return { symbols: [] };
    }
}

async function fetchNewsData() {
    const response = await fetch(`/news?symbol=${encodeURIComponent(state.symbol)}`);
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : { configured: false, events: [] };
    } catch {
        return { configured: false, events: [], message: "Error fetching news" };
    }
}

async function fetchJournalData() {
    const params = new URLSearchParams({ limit: "50" });
    if (state.journalFilters.pair) params.set("pair", state.journalFilters.pair);
    if (state.journalFilters.result) params.set("result", state.journalFilters.result);
    if (state.journalFilters.quality) params.set("quality", state.journalFilters.quality);
    if (state.journalFilters.month) params.set("month", state.journalFilters.month);
    const response = await fetch(`/journal?${params.toString()}`);
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : { entries: [], summary: {} };
    } catch {
        return { entries: [], summary: {} };
    }
}

async function fetchScanReportData() {
    const response = await fetch(`/scan-report?source=${encodeURIComponent(state.source)}&broader=true&fast=true`);
    const rawText = await response.text();
    try {
        return rawText ? JSON.parse(rawText) : { valid: [], near_valid: [], developing: [] };
    } catch {
        return { valid: [], near_valid: [], developing: [] };
    }
}

function syncStateFromInputs() {
    const requestedSymbol = document.getElementById("symbol-input").value.trim().toUpperCase() || "EURUSD";
    state.symbol = APPROVED_SYMBOLS.has(requestedSymbol) ? requestedSymbol : "EURUSD";
    document.getElementById("symbol-input").value = state.symbol;
    state.source = document.getElementById("source-select").value;
    state.interval = document.getElementById("interval-select").value;
    state.tradingViewSymbol = document.getElementById("tv-symbol-input").value.trim() || inferTradingViewSymbol(state.symbol);
    state.journalFilters = {
        pair: document.getElementById("journal-pair-filter").value,
        result: document.getElementById("journal-result-filter").value,
        quality: document.getElementById("journal-quality-filter").value,
        month: document.getElementById("journal-month-filter").value,
    };
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
        renderFractal(payload.fractal || {});

        fetchAlertsData()
            .then((alertsPayload) => {
                if (requestId !== state.requestId) return;
                renderAlerts(alertsPayload.entries || []);
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("alerts-panel").innerHTML = '<div class="empty-state">Alerts unavailable right now.</div>';
            });

        fetchPerformanceData()
            .then((performancePayload) => {
                if (requestId !== state.requestId) return;
                renderPerformance(performancePayload || {});
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("performance-panel").innerHTML = '<div class="empty-state">Performance unavailable right now.</div>';
            });

        fetchDigitalTwinData()
            .then((digitalTwinPayload) => {
                if (requestId !== state.requestId) return;
                renderDigitalTwin(digitalTwinPayload || {});
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("digital-twin-panel").innerHTML = '<div class="empty-state">Digital twin unavailable right now.</div>';
            });

        fetchWatchlistData()
            .then((watchlistPayload) => {
                if (requestId !== state.requestId) return;
                renderWatchlist(watchlistPayload);
                renderLiveSetups(watchlistPayload);
                renderClusterPanel(watchlistPayload);
                renderTopOpportunities(watchlistPayload);
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("watchlist-panel").innerHTML = '<div class="empty-state">Watchlist unavailable right now.</div>';
                document.getElementById("live-setups-panel").innerHTML = '<div class="empty-state">Live setups unavailable right now.</div>';
                document.getElementById("cluster-panel").innerHTML = '<div class="empty-state">Cluster alignment unavailable right now.</div>';
                document.getElementById("opportunities-panel").innerHTML = '<div class="empty-state">Top opportunities unavailable right now.</div>';
            });

        fetchNewsData()
            .then((newsPayload) => {
                if (requestId !== state.requestId) return;
                renderNews(newsPayload);
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("news-panel").innerHTML = '<div class="empty-state">News unavailable right now.</div>';
            });

        fetchJournalData()
            .then((journalPayload) => {
                if (requestId !== state.requestId) return;
                renderJournal(journalPayload);
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("journal-panel").innerHTML = '<div class="empty-state">Journal unavailable right now.</div>';
            });

        fetchScanReportData()
            .then((scanReportPayload) => {
                if (requestId !== state.requestId) return;
                renderScanReport(scanReportPayload);
            })
            .catch(() => {
                if (requestId !== state.requestId) return;
                document.getElementById("scan-report-panel").innerHTML = '<div class="empty-state">Broader scan unavailable right now.</div>';
            });
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
    document.getElementById("journal-pair-filter").addEventListener("change", () => refreshDashboard(false));
    document.getElementById("journal-result-filter").addEventListener("change", () => refreshDashboard(false));
    document.getElementById("journal-quality-filter").addEventListener("change", () => refreshDashboard(false));
    document.getElementById("journal-month-filter").addEventListener("change", () => refreshDashboard(false));
}

window.addEventListener("DOMContentLoaded", () => {
    document.getElementById("tv-symbol-input").value = state.tradingViewSymbol;
    syncBackendInputsFromTradingView();
    bindEvents();
    refreshDashboard(true);
});
