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
    if (page === 'backtest') { loadPastBacktests(); }
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
            '<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">No trades yet. Go to Bots and create one!</td></tr>' :
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
    loadActivityFeed();
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

// ── Activity Feed ──

const ACTION_LABELS = {
    watching: 'Watching',
    buy: 'Bought',
    sell: 'Sold',
    profit: 'Profit',
    loss: 'Loss',
    started: 'Started',
    stopped: 'Stopped',
    paused: 'Paused',
    error: 'Error',
    signal: 'Signal',
    waiting: 'Waiting'
};

async function loadActivityFeed() {
    const data = await api('/api/activity?limit=30');
    if (!data || data.length === 0) {
        $('activityFeed').innerHTML = '<p class="loading">No bot activity yet. Create and start a bot!</p>';
        return;
    }

    $('activityFeed').innerHTML = data.map(a => {
        const ago = formatAgo(a.time);
        const dot = a.action || 'watching';
        return `<div class="activity-item">
            <div class="activity-dot dot-${dot}"></div>
            <div class="activity-content">
                <span class="activity-bot">${a.bot_id}</span>
                <span class="activity-detail">${a.details || ACTION_LABELS[a.action] || a.action}</span>
                ${a.price ? `<span class="activity-price"> @ $${Number(a.price).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>` : ''}
            </div>
            <span class="activity-time">${ago}</span>
        </div>`;
    }).join('');
}

function formatAgo(ts) {
    const sec = Math.floor(Date.now() / 1000 - ts);
    if (sec < 5) return 'just now';
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
    return Math.floor(sec / 86400) + 'd ago';
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
        const ls = b.live_status || {};
        const pnl = ls.realized_pnl || 0;
        const trades = ls.trade_count || 0;
        const wr = ls.win_rate || 0;
        const openPos = ls.open_positions || 0;
        const curPrice = ls.current_price;
        const lastAction = ls.last_action;
        const lastDetail = ls.last_detail;
        const lastTime = ls.last_time;

        // Build the status bar for running bots
        let statusBlock = '';
        if (b.status === 'running') {
            const priceStr = curPrice ? '$' + Number(curPrice).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '...';
            const ago = lastTime ? formatAgo(lastTime) : '';
            statusBlock = `<div class="bot-status-bar">
                <div class="bot-status-row">
                    <span class="bot-status-label">Live Price</span>
                    <span class="live-price">${priceStr}</span>
                </div>
                <div class="bot-status-row" style="margin-top:6px">
                    <span class="thinking">
                        <span class="thinking-dots"><span></span><span></span><span></span></span>
                        ${lastDetail || 'Watching...'}
                    </span>
                    <span class="activity-time">${ago}</span>
                </div>
            </div>`;
        } else if (b.status === 'paused') {
            statusBlock = `<div class="bot-status-bar"><span style="color:var(--yellow)">Paused — safety limit or manual pause</span></div>`;
        } else {
            statusBlock = `<div class="bot-status-bar"><span style="color:var(--text-dim)">Stopped — hit Start to begin</span></div>`;
        }

        return `<div class="bot-card ${b.status === 'running' ? 'is-running' : ''}">
            <div class="bot-card-header">
                <div>
                    <div class="bot-card-name">${b.id}</div>
                    <div class="bot-card-symbol">${STRATEGY_NAMES[b.bot_type] || b.bot_type} / ${b.symbol}</div>
                </div>
                <span class="badge badge-${b.status}">${b.status}</span>
            </div>
            <div class="bot-card-pnl ${pnlClass(pnl)}">${pnlSign(pnl)}${fmt(pnl)}</div>
            ${statusBlock}
            <div class="bot-card-meta">
                <span>${trades} trades</span>
                <span>Won: ${wr}%</span>
                <span>${openPos} open</span>
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
    // Reset the form completely
    $('newBotId').value = '';
    $('newBotSymbol').value = 'BTC/USDT';
    $('newBotAmount').value = 100;
    document.querySelectorAll('.strat-card').forEach((c, i) => {
        c.classList.toggle('active', i === 0);
    });
    document.querySelectorAll('.pill').forEach(p => {
        p.classList.toggle('active', p.textContent === '$100');
    });
    $('createBotModal').classList.add('active');
}

function closeModal(id) { $(id).classList.remove('active'); }

// Simple strategy names → internal types
const STRATEGY_NAMES = {
    scalper: 'Quick Scalper',
    grid: 'Bounce Trader',
    dca_momentum: 'Dip Buyer',
    funding_arb: 'Passive Earner',
    mean_reversion: 'Range Rider'
};

function selectStrategy(el) {
    document.querySelectorAll('.strat-card').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
}

function setAmount(val) {
    $('newBotAmount').value = val;
    document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
    event.target.classList.add('active');
}

function updateNewBotName() {
    const symbol = $('newBotSymbol').value;
    const coin = symbol.split('/')[0].toLowerCase();
    const strat = document.querySelector('.strat-card.active');
    const type = strat ? strat.dataset.type : 'grid';
    $('newBotId').value = coin + '-' + type.replace('_', '-');
}

// Smart defaults: user just picks amount, we figure out the rest
function buildSmartParams(type, amount, symbol) {
    if (type === 'scalper') {
        return {
            trade_amount: amount * 0.2,  // 20% per trade
            take_profit_pct: 0.5,
            stop_loss_pct: 0.3,
            max_open_trades: 3,
            cooldown_ticks: 2
        };
    }
    if (type === 'grid') {
        // Auto-calculate grid range as +/- 5% from a reference price
        // The bot will adjust on first tick anyway
        return {
            upper_price: 100000, // placeholder, bot adjusts
            lower_price: 80000,
            grid_count: 10,
            investment_amount: amount
        };
    }
    if (type === 'dca_momentum') {
        return {
            base_amount: amount * 0.1, // 10% per buy
            dca_multiplier: 1.5,
            rsi_buy_threshold: 30,
            rsi_sell_threshold: 70,
            take_profit_pct: 5,
            stop_loss_pct: 3
        };
    }
    if (type === 'funding_arb') {
        return {
            position_size: amount,
            min_funding_rate: 0.01,
            exit_funding_rate: 0.005
        };
    }
    if (type === 'mean_reversion') {
        return {
            bb_period: 20,
            bb_std: 2,
            position_size: amount * 0.2,
            take_profit_pct: 2,
            stop_loss_pct: 1.5,
            max_positions: 3
        };
    }
    return {};
}

// Backtest param config (kept technical for the backtester tab only)
const BOT_PARAMS = {
    grid: [
        { key: 'upper_price', label: 'Highest price to trade ($)', default: 70000, step: 100 },
        { key: 'lower_price', label: 'Lowest price to trade ($)', default: 60000, step: 100 },
        { key: 'grid_count', label: 'How many buy/sell points', default: 10 },
        { key: 'investment_amount', label: 'Total to invest ($)', default: 100, step: 10 }
    ],
    dca_momentum: [
        { key: 'base_amount', label: 'Buy amount each time ($)', default: 10, step: 1 },
        { key: 'dca_multiplier', label: 'Buy more on bigger dips (1 = same amount)', default: 1.5, step: 0.1 },
        { key: 'rsi_buy_threshold', label: 'How oversold to trigger buy (lower = pickier)', default: 30 },
        { key: 'rsi_sell_threshold', label: 'How overbought to trigger sell', default: 70 }
    ],
    funding_arb: [
        { key: 'position_size', label: 'Amount to put in ($)', default: 100, step: 10 },
        { key: 'min_funding_rate', label: 'Min payment to enter (%)', default: 0.01, step: 0.001 },
        { key: 'exit_funding_rate', label: 'Exit when payment drops to (%)', default: 0.005, step: 0.001 }
    ],
    mean_reversion: [
        { key: 'position_size', label: 'Amount per trade ($)', default: 50, step: 10 },
        { key: 'take_profit_pct', label: 'Sell when up by (%)', default: 2, step: 0.5 },
        { key: 'stop_loss_pct', label: 'Sell if down by (%)', default: 1.5, step: 0.5 }
    ]
};

async function createBot() {
    const strat = document.querySelector('.strat-card.active');
    const type = strat ? strat.dataset.type : 'grid';
    const symbol = $('newBotSymbol').value;
    const amount = parseFloat($('newBotAmount').value) || 100;
    const userInput = $('newBotId').value.trim();
    const coin = symbol.split('/')[0].toLowerCase();
    const uid = Date.now().toString(36).slice(-4);
    const botId = userInput ? userInput + '-' + uid : coin + '-' + type.replace('_', '-') + '-' + uid;

    const params = buildSmartParams(type, amount, symbol);

    const body = {
        bot_id: botId,
        bot_type: type,
        market: 'crypto',
        symbol: symbol,
        params
    };

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
            '<tr><td colspan="8" style="text-align:center;color:var(--text-dim)">No trades yet</td></tr>' :
            trades.map(t => `<tr>
                <td>${fmtTime(t.created_at)}</td>
                <td>${t.bot_id || '-'}</td>
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

function selectBtStrategy(el) {
    document.querySelectorAll('#btStrategyCards .strat-card').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
}

async function runBacktest() {
    const btn = $('btRunBtn');
    btn.textContent = 'Testing...';
    btn.disabled = true;

    const strat = document.querySelector('#btStrategyCards .strat-card.active');
    const type = strat ? strat.dataset.type : 'grid';

    // Smart defaults — user doesn't need to know these
    const params = buildSmartParams(type, 100, $('btSymbol').value);

    const result = await api('/api/backtest', {
        method: 'POST',
        body: {
            bot_type: type,
            symbol: $('btSymbol').value,
            market: 'crypto',
            params
        }
    });

    btn.textContent = 'Run Test';
    btn.disabled = false;

    if (!result || result.error) {
        $('btResults').innerHTML = `<p style="color:var(--red)">${result?.error || 'Test failed'}</p>`;
        return;
    }

    const verdict = result.total_return > 0 ?
        `<div style="padding:10px;background:rgba(63,185,80,0.1);border-radius:6px;margin-bottom:12px;font-size:14px">This strategy <strong class="positive">made money</strong> — ${result.total_return}% return</div>` :
        `<div style="padding:10px;background:rgba(248,81,73,0.1);border-radius:6px;margin-bottom:12px;font-size:14px">This strategy <strong class="negative">lost money</strong> — ${result.total_return}% return</div>`;

    $('btResults').innerHTML = `
        ${verdict}
        <div class="grid-2" style="gap:10px">
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.total_trades}</div><div class="stat-label">Trades Made</div></div>
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.win_rate}%</div><div class="stat-label">Trades Won</div></div>
            <div class="stat"><div class="stat-value negative" style="font-size:20px">${result.max_drawdown}%</div><div class="stat-label">Worst Dip</div></div>
            <div class="stat"><div class="stat-value" style="font-size:20px">${result.profit_factor}</div><div class="stat-label">Profit Score</div></div>
        </div>
        <div style="margin-top:12px;font-size:13px;color:var(--text-dim)">
            Ended with: <strong class="${pnlClass(result.total_return)}">${fmt(result.final_balance)}</strong> |
            Best trade: <span class="positive">${fmt(result.best_trade)}</span> |
            Worst trade: <span class="negative">${fmt(result.worst_trade)}</span>
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
        $('pastBacktests').innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-dim)">No tests run yet</td></tr>';
        return;
    }
    $('pastBacktests').innerHTML = results.map(r => `<tr>
        <td>${fmtTime(r.created_at)}</td>
        <td>${STRATEGY_NAMES[r.bot_type] || r.bot_type}</td>
        <td>${r.symbol}</td>
        <td>${r.total_trades}</td>
        <td>${r.win_rate}%</td>
        <td class="${pnlClass(r.total_return)}">${pnlSign(r.total_return)}${r.total_return}%</td>
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
            '<tr><td colspan="3" style="text-align:center;color:var(--text-dim)">No safety events yet — that\'s good!</td></tr>' :
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

// Auto-refresh every 10 seconds for live feel
setInterval(refreshAll, 10000);

// Initial load
loadOverview();
