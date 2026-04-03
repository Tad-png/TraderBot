"""TraderBot API Server — thin Flask routes only."""

import os
import json
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from modules.config_manager import load_config, save_config
from modules import state, db
from modules.portfolio import get_portfolio_value, get_portfolio_breakdown, start_snapshot_thread
from modules.risk_manager import get_risk_status
from modules.data_feed import get_current_price, get_candles
from modules.order_manager import place_order
from modules.activity import get_activities

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'logs', 'trading.log'),
                            mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger('traderbot')

app = Flask(__name__, static_folder='static')
CORS(app)

# ── Initialization ──

def init_app():
    """Initialize database, state, and background threads."""
    os.makedirs(os.path.join(os.path.dirname(__file__), 'logs'), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data', 'price_cache'), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data', 'backtest_results'), exist_ok=True)

    db.init_db()
    config = load_config()
    state.trading_mode = config.get('trading_mode', 'paper')
    state.init_paper_balances(config.get('paper_starting_balance', 10000))

    interval = config.get('portfolio_snapshot_interval_minutes', 5)
    start_snapshot_thread(interval)

    # Auto-restart bots that were running before shutdown
    _restart_saved_bots(config)

    logger.info(f"TraderBot initialized — mode: {state.trading_mode}")


def _restart_saved_bots(config):
    """Restart any bots that were running when the server last shut down."""
    saved_bots = db.get_all_bot_configs()
    tick_config = config.get('bot_tick_interval_seconds', {})
    restarted = 0

    for bot_cfg in saved_bots:
        if bot_cfg['status'] == 'running':
            try:
                bot_class = _get_bot_class(bot_cfg['bot_type'])
                if not bot_class:
                    continue
                tick_interval = tick_config.get(bot_cfg['market'], 30)
                bot = bot_class(
                    bot_id=bot_cfg['id'],
                    market=bot_cfg['market'],
                    symbol=bot_cfg['symbol'],
                    params=bot_cfg['params'],
                    tick_interval=tick_interval
                )
                bot.start()
                restarted += 1
                logger.info(f"Auto-restarted bot: {bot_cfg['id']}")
            except Exception as e:
                logger.error(f"Failed to restart bot {bot_cfg['id']}: {e}")

    if restarted:
        logger.info(f"Restarted {restarted} bot(s) from previous session")


# ── Dashboard ──

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'mode': state.trading_mode,
        'active_bots': len(state.active_bots)
    })


# ── Mode ──

@app.route('/api/mode', methods=['GET'])
def get_mode():
    return jsonify({'mode': state.trading_mode})


@app.route('/api/mode', methods=['POST'])
def set_mode():
    data = request.json or {}
    new_mode = data.get('mode', 'paper')
    if new_mode not in ('paper', 'live'):
        return jsonify({'error': 'Mode must be paper or live'}), 400
    state.trading_mode = new_mode
    config = load_config()
    config['trading_mode'] = new_mode
    save_config(config)
    logger.info(f"Trading mode changed to: {new_mode}")
    return jsonify({'mode': new_mode})


# ── Portfolio ──

@app.route('/api/portfolio')
def portfolio():
    pv = get_portfolio_value()
    pnl = db.get_pnl_summary()
    return jsonify({**pv, **pnl, 'mode': state.trading_mode})


@app.route('/api/portfolio/history')
def portfolio_history():
    period = request.args.get('period', '1w')
    is_paper = 1 if state.trading_mode == 'paper' else 0
    snapshots = db.get_snapshots(period, is_paper)
    return jsonify(snapshots)


@app.route('/api/portfolio/breakdown')
def portfolio_breakdown():
    breakdown = get_portfolio_breakdown()
    return jsonify(breakdown)


# ── Positions & Trades ──

@app.route('/api/positions')
def positions():
    pos = db.get_open_positions()
    return jsonify(pos)


@app.route('/api/trades')
def trades():
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    bot_id = request.args.get('bot')
    market = request.args.get('market')
    result = db.get_trades(limit=limit, offset=offset, bot_id=bot_id, market=market)
    return jsonify(result)


@app.route('/api/trades/summary')
def trades_summary():
    return jsonify(db.get_pnl_summary())


# ── Bots ──

@app.route('/api/bots', methods=['GET'])
def list_bots():
    configs = db.get_all_bot_configs()
    # Add live status from active_bots
    for cfg in configs:
        if cfg['id'] in state.active_bots:
            bot = state.active_bots[cfg['id']]
            cfg['live_status'] = bot.get_status()
    return jsonify(configs)


@app.route('/api/bots', methods=['POST'])
def create_bot():
    data = request.json or {}
    required = ['bot_id', 'bot_type', 'market', 'symbol']
    for field in required:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400

    params = data.get('params', {})
    is_paper = 1 if state.trading_mode == 'paper' else 0
    db.save_bot_config(data['bot_id'], data['bot_type'], data['market'],
                       data['symbol'], params, is_paper)
    return jsonify({'success': True, 'bot_id': data['bot_id']})


@app.route('/api/bots/<bot_id>')
def get_bot(bot_id):
    config = db.get_bot_config(bot_id)
    if not config:
        return jsonify({'error': 'Bot not found'}), 404
    if bot_id in state.active_bots:
        config['live_status'] = state.active_bots[bot_id].get_status()
    return jsonify(config)


@app.route('/api/bots/<bot_id>/start', methods=['POST'])
def start_bot(bot_id):
    config = db.get_bot_config(bot_id)
    if not config:
        return jsonify({'error': 'Bot not found'}), 404

    if bot_id in state.active_bots:
        return jsonify({'error': 'Bot already running'}), 400

    # Import the right bot class
    bot_class = _get_bot_class(config['bot_type'])
    if not bot_class:
        return jsonify({'error': f"Unknown bot type: {config['bot_type']}"}), 400

    tick_config = load_config().get('bot_tick_interval_seconds', {})
    tick_interval = tick_config.get(config['market'], 30)
    # Scalper runs faster — every 15 seconds
    if config['bot_type'] == 'scalper':
        tick_interval = 15

    bot = bot_class(
        bot_id=bot_id,
        market=config['market'],
        symbol=config['symbol'],
        params=config['params'],
        tick_interval=tick_interval
    )
    bot.start()
    return jsonify({'success': True, 'status': 'running'})


@app.route('/api/bots/<bot_id>/pause', methods=['POST'])
def pause_bot(bot_id):
    if bot_id not in state.active_bots:
        return jsonify({'error': 'Bot not running'}), 400
    state.active_bots[bot_id].pause()
    return jsonify({'success': True, 'status': 'paused'})


@app.route('/api/bots/<bot_id>/stop', methods=['POST'])
def stop_bot(bot_id):
    if bot_id not in state.active_bots:
        db.update_bot_status(bot_id, 'stopped')
        return jsonify({'success': True, 'status': 'stopped'})
    state.active_bots[bot_id].stop()
    return jsonify({'success': True, 'status': 'stopped'})


@app.route('/api/bots/<bot_id>', methods=['PUT'])
def update_bot(bot_id):
    config = db.get_bot_config(bot_id)
    if not config:
        return jsonify({'error': 'Bot not found'}), 404
    data = request.json or {}
    params = data.get('params', config['params'])
    db.save_bot_config(bot_id, config['bot_type'], config['market'],
                       config['symbol'], params)
    return jsonify({'success': True})


@app.route('/api/bots/<bot_id>', methods=['DELETE'])
def delete_bot(bot_id):
    if bot_id in state.active_bots:
        state.active_bots[bot_id].stop()
    db.delete_bot_config(bot_id)
    return jsonify({'success': True})


@app.route('/api/bots/<bot_id>/trades')
def bot_trades(bot_id):
    result = db.get_trades(limit=100, bot_id=bot_id)
    return jsonify(result)


# ── Activity Feed ──

@app.route('/api/activity')
def activity():
    limit = int(request.args.get('limit', 50))
    bot_id = request.args.get('bot')
    return jsonify(get_activities(limit=limit, bot_id=bot_id))


# ── Risk ──

@app.route('/api/risk')
def risk():
    return jsonify(get_risk_status())


@app.route('/api/risk/events')
def risk_events():
    return jsonify(db.get_risk_events())


@app.route('/api/risk/settings', methods=['POST'])
def update_risk():
    data = request.json or {}
    config = load_config()
    config['risk'].update(data)
    save_config(config)
    return jsonify({'success': True, 'risk': config['risk']})


# ── Market Data ──

@app.route('/api/prices/<market>/<path:symbol>')
def price(market, symbol):
    p = get_current_price(market, symbol)
    if p is None:
        return jsonify({'error': f'Could not fetch price for {symbol}'}), 404
    return jsonify({'market': market, 'symbol': symbol, 'price': p})


@app.route('/api/candles/<market>/<path:symbol>')
def candles(market, symbol):
    tf = request.args.get('tf', '1h')
    limit = int(request.args.get('limit', 100))
    data = get_candles(market, symbol, tf, limit)
    return jsonify(data)


# ── Backtester ──

@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    from modules.backtester import run_backtest as _run_backtest
    data = request.json or {}
    bot_type = data.get('bot_type')
    symbol = data.get('symbol', 'BTC/USDT')
    params = data.get('params', {})
    start_date = data.get('start_date')
    end_date = data.get('end_date')

    # Fetch historical candles
    market = data.get('market', 'crypto')
    candle_data = get_candles(market, symbol, '1h', limit=500)
    if not candle_data:
        return jsonify({'error': 'Could not fetch historical data'}), 400

    results = _run_backtest(bot_type, symbol, params, candle_data)
    if 'error' in results:
        return jsonify(results), 400

    # Save to DB
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO backtest_runs (bot_type, symbol, params, start_date, end_date,
           win_rate, profit_factor, max_drawdown, sharpe_ratio, total_return,
           total_trades, results_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bot_type, symbol, json.dumps(params), results.get('start_date'), results.get('end_date'),
         results.get('win_rate'), results.get('profit_factor'), results.get('max_drawdown'),
         results.get('sharpe_ratio'), results.get('total_return'), results.get('total_trades'),
         results.get('results_file'))
    )
    conn.commit()
    conn.close()

    return jsonify(results)


@app.route('/api/backtest/results')
def backtest_results():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Config ──

@app.route('/api/config', methods=['GET'])
def get_config():
    config = load_config()
    # Mask API keys
    safe = dict(config)
    for key in ['crypto_api_key', 'crypto_api_secret', 'alpaca_api_key',
                'alpaca_api_secret', 'oanda_api_token']:
        if safe.get(key):
            safe[key] = safe[key][:4] + '****'
    return jsonify(safe)


@app.route('/api/config', methods=['POST'])
def update_config():
    data = request.json or {}
    config = load_config()
    for key, val in data.items():
        if isinstance(val, dict) and isinstance(config.get(key), dict):
            config[key].update(val)
        else:
            config[key] = val
    save_config(config)
    return jsonify({'success': True})


# ── Helpers ──

def _get_bot_class(bot_type):
    """Import and return the correct bot class."""
    if bot_type == 'grid':
        from modules.bots.grid_bot import GridBot
        return GridBot
    elif bot_type == 'dca_momentum':
        from modules.bots.dca_momentum_bot import DCAMomentumBot
        return DCAMomentumBot
    elif bot_type == 'funding_arb':
        from modules.bots.funding_arb_bot import FundingArbBot
        return FundingArbBot
    elif bot_type == 'mean_reversion':
        from modules.bots.mean_reversion_bot import MeanReversionBot
        return MeanReversionBot
    elif bot_type == 'scalper':
        from modules.bots.scalper_bot import ScalperBot
        return ScalperBot
    return None


# ── Main ──

if __name__ == '__main__':
    init_app()
    logger.info("TraderBot starting on http://localhost:5000")
    app.run(host='0.0.0.0', port=5001, debug=False)
