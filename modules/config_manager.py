"""Config manager — load/save config.json with defaults."""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')

DEFAULTS = {
    "trading_mode": "paper",
    "paper_starting_balance": 10000,
    "crypto_exchange": "binance",
    "crypto_api_key": "",
    "crypto_api_secret": "",
    "alpaca_api_key": "",
    "alpaca_api_secret": "",
    "alpaca_paper": True,
    "oanda_account_id": "",
    "oanda_api_token": "",
    "oanda_practice": True,
    "risk": {
        "per_trade_pct": 1.5,
        "daily_loss_pct": 4.0,
        "weekly_loss_pct": 8.0,
        "use_kelly": True,
        "kelly_fraction": 0.25
    },
    "portfolio_snapshot_interval_minutes": 5,
    "bot_tick_interval_seconds": {
        "crypto": 30,
        "stock": 60,
        "forex": 60
    },
    "notifications": {
        "log_to_console": True,
        "discord_webhook": ""
    }
}


def load_config():
    """Load config from disk, filling in any missing defaults."""
    config = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            saved = json.load(f)
        for key, val in saved.items():
            if isinstance(val, dict) and isinstance(config.get(key), dict):
                config[key].update(val)
            else:
                config[key] = val
    else:
        save_config(config)
    return config


def save_config(config):
    """Write config to disk."""
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=4)
