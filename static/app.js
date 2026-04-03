/* TraderBot Dashboard — app.js */

const API = '';
let charts = {};
let tradeOffset = 0;

// ── Navigation ──

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => showPage(item.dataset.page));
});

function showPage(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('page-' + page).classList.add('active');
    document.querySelector(`.nav-item[data-page="${page}"]`).classList.add('active');

    if (page === 'overview') loadOverview();
    if (page === 'portfolio') loadPortfolio();
    if (page === 'bots') loadBots();
    if (page === 'trades') { tradeOffset = 0; loadTrades(); }
    if (page === 'backtest') { updateBtParams(); loadPastBacktests(); }
    if (page === 'risk') loadRisk();
    if (page === 'settings') loadSettings();
}

// ── API Helpers ──

async function api(path, opts = {}) {
    try {
        const res = await fetch(API + path, {
            headers: { 'Content-Type': 'application/json' },
            ...opts,
            body: opts.body ? JSON.stringify(opts.body) : undefined
        });
        return await res.json();
    } catch (e) {
        console.error('API error:', path, e);
        return null;
    }
}

function $(id) { return document.getElementById(id); }

function pnlClass(val) { return val > 0 ? 'positive' : val < 0 ? 'negative' : 'neutral'; }
function pnlSign(val) { return val > 0 ? '+' : ''; }
function fmt(val, dec = 2) { return '$' + Number(val || 0).toFixed(dec); }
function fmtTime(dt) {
    if (!dt) return '-';
    const d = new Date(dt);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
           d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

// ── Overview ──

async function loadOverview() {
    const [portfolio, summary, trades] = await Promise.all([
        api('/api/portfolio'),
        api('/api/trades/summary'),
        api('/api/trades?limit=10')
    ]);

    if (portfolio) {
        $('totalValue').textContent = fmt(portfolio.total_value);
        $('todayPnl').textContent = pnlSign(portfolio.today_pnl) + fmt(portfolio.today_pnl);
        $('todayPnl').className = 'stat-value ' + pnlClass(portfolio.today_pnl);
        $('activeBots').textContent = portfolio.mode === 'paper' ?
            Object.keys(portfolio.positions || {}).length + ' pos' : '0';

        const badge = $('modeBadge');
        badge.textContent = portfolio.mode.toUpperCase();
        badge.className = 'mode-badge mode-' + portfolio.mode;
        $('modeToggle').textContent = portfolio.mode === 'paper' ? 'Switch to Live' : 'Switch to Paper';
    }

    if (summary) {
        $('winRate').textContent = summary.win_rate.toFixed(1) + '%';
        $('activeBots').textContent = summary.total_trades;
        $('activeBots').nextElementSibling.textContent = 'Total Trades';
    }

    if (trades) {
        $('recentTrades').innerHTML = trades.length === 0 ?
            '<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">No trades yet. Create and start a bot!</td></tr>' :
            trades.map(t => `<tr>
                <td>${fmtTime(t.created_at)}</td>
                <td>${t.bot_id || '-'}</td>
                <td>${t.symbol}</td>
                <td><span class="${t.side === 'buy' ? 'positive' : 'negative'}">${t.side.toUpperCase()}</span></td>
                <td>${Number(t.quantity).toFixed(6)}</td>
                <td>${fmt(t.price, 4)}</td>
                <td class="${pnlClass(t.pnl)}">${t.pnl != null ? pnlSign(t.pnl) + fmt(t.pnl, 4) : '-'}</td>
            </tr>`).join('');
    }

    loadOverviewChart();
    loadAllocationChart();
}

async function loadOverviewChart() {
    const data = await api('/api/portfolio/history?period=1w');
    if (!data || data.length === 0) {
        renderChart('overviewChart', { labels: ['Now'], values: [30000] }, 'Portfolio Value');
        return;
    }
    const labels = data.map(d => fmtTime(d.snapshot_at));
    const values = data.map(d => d.total_value);
    renderChart('overviewChart', { labels, values }, 'Portfolio Value');
}

async function loadAllocationChart() {
    const data = await api('/api/portfolio/breakdown');
    if (!data) return;

    const ctx = $('allocationChart').getContext('2d');
    if (charts['allocation']) charts['allocation'].destroy();

    const markets = data.by_market || {};
    const labels = Object.keys(markets).map(m => m.charAt(0).toUpperCase() + m.slice(1));
    const values = Object.values(markets).map(m => m.total);

    charts['allocation'] = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data: values,
                backgroundColor: ['#58a6ff', '#3fb950', '#d29922'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom', labels: { color: '#e6edf3', padding: 16 } }
            }
        }
    });
}

// ── Portfolio ──

async function loadPortfolio() {
    const [portfolio, breakdown] = await Promise.all([
        api('/api/portfolio'),
        api('/api/portfolio/breakdown')
    ]);

    if (portfolio) {
        $('portCash').textContent = fmt(portfolio.cash_balance);
        $('portPositions').textContent = fmt(portfolio.positions_value);
        $('portUnrealized').textContent = pnlSign(portfolio.unrealized_pnl) + fmt(portfolio.unrealized_pnl);
        $('portUnrealized').className = 'stat-value ' + pnlClass(portfolio.unrealized_pnl);
    }

    if (breakdown) {
        $('marketBreakdown').innerHTML = Object.entries(breakdown.by_market || {}).map(([m, d]) =>
            `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
                <span style="text-transform:capitalize">${m}</span>
                <span>${fmt(d.total)} <small style="color:var(--text-dim)">(${d.position_count} positions)</small></span>
            </div>`
        ).join('');

        $('botBreakdown').innerHTML = Object.entries(breakdown.by_bot || {}).length === 0 ?
            '<p style="color:var(--text-dim)">No bots created yet</p>' :
            Object.entries(breakdown.by_bot).map(([id, d]) =>
                `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
                    <span>${id} <small style="color:var(--text-dim)">${d.type}</small></span>
                    <span class="${pnlClass(d.realized_pnl)}">${pnlSign(d.realized_pnl)}${fmt(d.realized_pnl)}</span>
                </div>`
            ).join('');
    }

    loadPortfolioHistory('1w');
}

async function loadPortfolioHistory(period, btn) {
    if (btn) {
        btn.parentElement.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }
    const data = await api('/api/portfolio/history?period=' + period);
    if (!data || data.length === 0) {
        renderChart('equityChart', { labels: ['Start'], values: [30000] }, 'Equity');
        return;
    }
    const labels = data.map(d => fmtTime(d.snapshot_at));
    const values = data.map(d => d.total_value);
    renderChart('equityChart', { labels, values }, 'Equity');
}

// ── Bots ──

async function loadBots() {
    const bots = await api('/api/bots');
    if (!bots || bots.length === 0) {
        $('botCards').innerHTML = `<div class="card" style="grid-column:1/-1;text-align:center;padding:40px">
            <p style="color:var(--text-dim);margin-bottom:16px">No bots created yet</p>
            <button class="btn btn-primary" onclick="openCreateBot()">+ Create Your First Bot</button>
        </div>`;
        return;
    }

    $('botCards').innerHTML = bots.map(b => {
        const pnl = b.live_status ? b.live_status.realized_pnl : 0;
        const trades = b.live_status ? b.live_status.trade_count : 0;
        const wr = b.live_status ? b.live_status.win_rate : 0;
        return `<div class="bot-card">
            <div class="bot-card-header">
                <div>
                    <div class="bot-card-name">${b.id}</div>
                    <div class="bot-card-symbol">${b.bot_type} / ${b.symbol}</div>
                </div>
                <span class="badge badge-${b.status}">${b.status}</span>
            </div>
            <div class="bot-card-pnl ${pnlClass(pnl)}">${pnlSign(pnl)}${fmt(pnl)}</div>
            <div class="bot-card-meta">
                <span>${trades} trades</span>
                <span>Win: ${wr}%</span>
                <span>${b.market}</span>
            </div>
            <div style="margin-top:12px;display:flex;gap:6px">
                ${b.status !== 'running' ?
                    `<button class="btn btn-success btn-sm" onclick="botAction('${b.id}','start')">Start</button>` :
                    `<button class="btn btn-warning btn-sm" onclick="botAction('${b.id}','pause')">Pause</button>`}
                <button class="btn btn-danger btn-sm" onclick="botAction('${b.id}','stop')">Stop</button>
                <button class="btn btn-sm" onclick="deleteBot('${b.id}')" style="margin-left:auto">Delete</button>
            </div>
        </div>`;
    }).join('');
}

async function botAction(id, action) {
    await api(`/api/bots/${id}/${action}`, { method: 'POST' });
    loadBots();
}

async function deleteBot(id) {
    if (!confirm('Delete bot ' + id + '?')) return;
    await api(`/api/bots/${id}`, { method: 'DELETE' });
    loadBots();
}

function openCreateBot() {
    $('createBotModal').classList.add('active');
    updateNewBotParams();
}

function closeModal(id) { $(id).classList.remove('active'); }

function updateNewBotParams() {
    const type = $('newBotType').value;
    const params = BOT_PARAMS[type] || [];
    $('newBotParams').innerHTML = params.map(p =>
        `<div class="form-group">
            <label>${p.label}</label>
            <input type="number" id="nbp_${p.key}" value="${p.default}" step="${p.step || 1}">
        </div>`
    ).join('');
}

const BOT_PARAMS = {
    grid: [
        { key: 'upper_price', label: 'Upper Price ($)', default: 70000, step: 100 },
        { key: 'lower_price', label: 'Lower Price ($)', default: 60000, step: 100 },
        { key: 'grid_count', label: 'Grid Levels', default: 10 },
        { key: 'investment_amount', label: 'Investment ($)', default: 100, step: 10 }
    ],
    dca_momentum: [
        { key: 'base_amount', label: 'Base Buy Amount ($)', default: 10, step: 1 },
        { key: 'dca_multiplier', label: 'DCA Multiplier', default: 1.5, step: 0.1 },
        { key: 'rsi_buy_threshold', label: 'RSI Buy (oversold)', default: 30 },
        { key: 'rsi_sell_threshold', label: 'RSI Sell (overbought)', default: 70 }
    ],
    funding_arb: [
        { key: 'position_size', label: 'Position Size ($)', default: 100, step: 10 },
        { key: 'min_funding_rate', label: 'Min Funding Rate (%)', default: 0.01, step: 0.001 },
        { key: 'exit_funding_rate', label: 'Exit Funding Rate (%)', default: 0.005, step: 0.001 }
    ],
    mean_reversion: [
        { key: 'bb_period', label: 'Bollinger Period', default: 20 },
        { key: 'bb_std', label: 'Std Deviations', default: 2, step: 0.5 },
        { key: 'position_size', label: 'Position Size ($)', default: 50, step: 10 },
        { key: 'take_profit_pct', label: 'Take Profit (%)', default: 2, step: 0.5 },
        { key: 'stop_loss_pct', label: 'Stop Loss (%)', default: 1.5, step: 0.5 }
    ]
};

async function createBot() {
    const type = $('newBotType').value;
    const paramDefs = BOT_PARAMS[type] || [];
    const params = {};
    paramDefs.forEach(p => {
        const el = $('nbp_' + p.key);
        if (el) params[p.key] = parseFloat(el.value);
    });

    const body = {
        bot_id: $('newBotId').value,
        bot_type: type,
        market: $('newBotMarket').value,
        symbol: $('newBotSymbol').value,
        params
    };

    if (!body.bot_id) { alert('Enter a bot name/ID'); return; }

    await api('/api/bots', { method: 'POST', body });
    closeModal('createBotModal');
    loadBots();
}

// ── Trades ──

async function loadTrades() {
    const [trades, summary] = await Promise.all([
        api(`/api/trades?limit=50&offset=${tradeOffset}`),
        api('/api/trades/summary')
    ]);

    if (summary) {
        $('trTotalTrades').textContent = summary.total_trades;
        $('trWinRate').textContent = summary.win_rate.toFixed(1) + '%';
        $('trWeekPnl').textContent = pnlSign(summary.week_pnl) + fmt(summary.week_pnl);
        $('trWeekPnl').className = 'stat-value ' + pnlClass(summary.week_pnl);
        $('trMonthPnl').textContent = pnlSign(summary.month_pnl) + fmt(summary.month_pnl);
        $('trMonthPnl').className = 'stat-value ' + pnlClass(summary.month_pnl);
    }

    if (trades) {
        const html = trades.length === 0 ?
            '<tr><td colspan="9" style="text-align:center;color:var(--text-dim)">No trades yet</td></tr>' :
            trades.map(t => `<tr>
                <td>${fmtTime(t.created_at)}</td>
                <td>${t.bot_id || '-'}</td>
                <td>${t.market}</td>
                <td>${t.symbol}</td>
                <td class="${t.side === 'buy' ? 'positive' : 'negative'}">${t.side.toUpperCase()}</td>
                <td>${Number(t.quantity).toFixed(6)}</td>
                <td>${fmt(t.price, 4)}</td>
                <td>${fmt(t.fee, 4)}</td>
                <td class="${pnlClass(t.pnl)}">${t.pnl != null ? pnlSign(t.pnl) + fmt(t.pnl, 4) : '-'}</td>
            </tr>`).join('');

        if (tradeOffset === 0) {
            $('allTrades').innerHTML = html;
        } else {
            $('allTrades').innerHTML += html;
        }
    }
}

function loadMoreTrades() {
    tradeOffset += 50;
    loadTrades();
}

// ── Backtester ──

function updateBtParams() {
    const type = $('btStrategy').value;
    const params = BOT_PARAMS[type] || [];
    $('btParams').innerHTML = params.map(p =>
        `<div class="form-group">
            <label>${p.label}</label>
            <input type="number" id="btp_${p.key}" value="${p.default}" step="${p.step || 1}">
        </div>`
    ).join('');
}

async function runBacktest() {
    const btn = $('btRunBtn');
    btn.textContent = 'Running...';
    btn.disabled = true;

    const type = $('btStrategy').value;
    const paramDefs = BOT_PARAMS[type] || [];
    const params = {};
    paramDefs.forEach(p => {
        const el = $('btp_' + p.key);
        if (el) params[p.key] = parseFloat(el.value);
    });

    const result = await api('/api/backtest', {
        method: 'POST',
        body: {
            bot_type: type,
            symbol: $('btSymbol').value,
            market: 'crypto',
            params
        }
    });

    btn.textContent = 'Run Backtest';
    btn.disabled = false;

    if (!result || result.error) {
        $('btResults').innerHTML = `<p style="color:var(--red)">${result?.error || 'Backtest failed'}</p>`;
        return;
    }

    $('btResults').innerHTML = `
        <div class="grid-2" style="gap:10px">
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.total_trades}</div><div class="stat-label">Trades</div></div>
            <div class="stat"><div class="stat-value ${pnlClass(result.total_return)}" style="font-size:20px">${result.total_return}%</div><div class="stat-label">Return</div></div>
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.win_rate}%</div><div class="stat-label">Win Rate</div></div>
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.profit_factor}</div><div class="stat-label">Profit Factor</div></div>
            <div class="stat"><div class="stat-value negative" style="font-size:20px">${result.max_drawdown}%</div><div class="stat-label">Max Drawdown</div></div>
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.sharpe_ratio}</div><div class="stat-label">Sharpe Ratio</div></div>
        </div>
        <div style="margin-top:12px;font-size:13px;color:var(--text-dim)">
            Final Balance: <strong class="${pnlClass(result.total_return)}">${fmt(result.final_balance)}</strong> |
            Best: <span class="positive">${fmt(result.best_trade)}</span> |
            Worst: <span class="negative">${fmt(result.worst_trade)}</span>
        </div>
    `;

    // Load equity curve if results file exists
    if (result.results_file) {
        $('btChartCard').style.display = 'block';
        // We'd need an endpoint to serve the file, for now use placeholder
    }

    loadPastBacktests();
}

async function loadPastBacktests() {
    const results = await api('/api/backtest/results');
    if (!results || results.length === 0) {
        $('pastBacktests').innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-dim)">No backtests run yet</td></tr>';
        return;
    }
    $('pastBacktests').innerHTML = results.map(r => `<tr>
        <td>${fmtTime(r.created_at)}</td>
        <td>${r.bot_type}</td>
        <td>${r.symbol}</td>
        <td>${r.total_trades}</td>
        <td>${r.win_rate}%</td>
        <td class="${pnlClass(r.total_return)}">${r.total_return}%</td>
        <td class="negative">${r.max_drawdown}%</td>
        <td>${r.sharpe_ratio}</td>
    </tr>`).join('');
}

// ── Risk ──

async function loadRisk() {
    const [risk, events] = await Promise.all([
        api('/api/risk'),
        api('/api/risk/events')
    ]);

    if (risk) {
        $('riskDailyUsed').textContent = fmt(risk.daily_loss);
        $('riskDailyLimit').textContent = '/ ' + fmt(risk.daily_limit);
        $('riskDailyBar').style.width = Math.min(risk.daily_pct, 100) + '%';
        $('riskDailyBar').className = 'progress-fill ' + (risk.daily_pct > 80 ? 'progress-red' : risk.daily_pct > 50 ? 'progress-yellow' : 'progress-green');

        $('riskWeeklyUsed').textContent = fmt(risk.weekly_loss);
        $('riskWeeklyLimit').textContent = '/ ' + fmt(risk.weekly_limit);
        $('riskWeeklyBar').style.width = Math.min(risk.weekly_pct, 100) + '%';
        $('riskWeeklyBar').className = 'progress-fill ' + (risk.weekly_pct > 80 ? 'progress-red' : risk.weekly_pct > 50 ? 'progress-yellow' : 'progress-green');

        $('riskDrawdown').textContent = risk.drawdown_pct.toFixed(1) + '%';
        $('riskPerTrade').value = risk.per_trade_pct;
        $('riskKelly').value = risk.kelly_fraction;
    }

    if (events) {
        $('riskEvents').innerHTML = events.length === 0 ?
            '<tr><td colspan="3" style="text-align:center;color:var(--text-dim)">No risk events</td></tr>' :
            events.map(e => `<tr>
                <td>${fmtTime(e.created_at)}</td>
                <td>${e.event_type}</td>
                <td>${e.bot_id || '-'}</td>
            </tr>`).join('');
    }
}

async function saveRiskSettings() {
    await api('/api/risk/settings', {
        method: 'POST',
        body: {
            per_trade_pct: parseFloat($('riskPerTrade').value),
            daily_loss_pct: parseFloat($('riskDailyPct').value),
            weekly_loss_pct: parseFloat($('riskWeeklyPct').value),
            kelly_fraction: parseFloat($('riskKelly').value)
        }
    });
    loadRisk();
}

// ── Settings ──

async function loadSettings() {
    const config = await api('/api/config');
    if (!config) return;
    $('cfgCryptoExchange').value = config.crypto_exchange || 'binance';
    $('cfgPaperBalance').value = config.paper_starting_balance || 10000;
    $('cfgAlpacaPaper').checked = config.alpaca_paper !== false;
    $('cfgOandaPractice').checked = config.oanda_practice !== false;
    $('cfgDiscordWebhook').value = config.notifications?.discord_webhook || '';
}

async function saveSettings() {
    await api('/api/config', {
        method: 'POST',
        body: {
            crypto_exchange: $('cfgCryptoExchange').value,
            crypto_api_key: $('cfgCryptoKey').value || undefined,
            crypto_api_secret: $('cfgCryptoSecret').value || undefined,
            alpaca_api_key: $('cfgAlpacaKey').value || undefined,
            alpaca_api_secret: $('cfgAlpacaSecret').value || undefined,
            alpaca_paper: $('cfgAlpacaPaper').checked,
            oanda_account_id: $('cfgOandaAccount').value || undefined,
            oanda_api_token: $('cfgOandaToken').value || undefined,
            oanda_practice: $('cfgOandaPractice').checked,
            paper_starting_balance: parseFloat($('cfgPaperBalance').value),
            notifications: {
                discord_webhook: $('cfgDiscordWebhook').value
            }
        }
    });
    alert('Settings saved!');
}

// ── Mode Toggle ──

async function toggleMode() {
    const current = $('modeBadge').textContent.toLowerCase();
    const newMode = current === 'paper' ? 'live' : 'paper';
    if (newMode === 'live' && !confirm('Switch to LIVE trading? Real money will be at risk!')) return;
    await api('/api/mode', { method: 'POST', body: { mode: newMode } });
    refreshAll();
}

// ── Chart Helper ──

function renderChart(canvasId, data, label) {
    const ctx = $(canvasId).getContext('2d');
    if (charts[canvasId]) charts[canvasId].destroy();

    const isProfit = data.values.length > 1 && data.values[data.values.length - 1] >= data.values[0];

    charts[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.labels,
            datasets: [{
                label,
                data: data.values,
                borderColor: isProfit ? '#3fb950' : '#f85149',
                backgroundColor: isProfit ? 'rgba(63,185,80,0.1)' : 'rgba(248,81,73,0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: ctx => '$' + ctx.raw.toFixed(2) }
                }
            },
            scales: {
                x: {
                    display: true,
                    ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 11 } },
                    grid: { color: 'rgba(48,54,61,0.5)' }
                },
                y: {
                    display: true,
                    ticks: {
                        color: '#8b949e',
                        font: { size: 11 },
                        callback: val => '$' + val.toLocaleString()
                    },
                    grid: { color: 'rgba(48,54,61,0.5)' }
                }
            },
            interaction: { intersect: false, mode: 'index' }
        }
    });
}

// ── Refresh ──

function refreshAll() {
    const activePage = document.querySelector('.page.active')?.id?.replace('page-', '');
    if (activePage) showPage(activePage);
}

// Auto-refresh every 30 seconds
setInterval(refreshAll, 30000);

// Initial load
loadOverview();
