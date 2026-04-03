"""Activity feed — real-time log of what every bot is doing."""

import time
import threading
from collections import deque

# In-memory activity log (last 200 events)
_activity_log = deque(maxlen=200)
_lock = threading.Lock()


def log_activity(bot_id, action, details="", price=None):
    """
    Log a bot activity event.

    action types:
        'watching'   - bot is monitoring price
        'buy'        - bot bought
        'sell'       - bot sold
        'profit'     - trade closed with profit
        'loss'       - trade closed with loss
        'paused'     - bot was paused (risk limit)
        'started'    - bot started
        'stopped'    - bot stopped
        'signal'     - bot detected a signal but didn't act
        'waiting'    - bot is waiting for conditions
        'error'      - something went wrong
    """
    entry = {
        'time': time.time(),
        'bot_id': bot_id,
        'action': action,
        'details': details,
        'price': price
    }
    with _lock:
        _activity_log.append(entry)


def get_activities(limit=50, bot_id=None):
    """Get recent activities, newest first."""
    with _lock:
        items = list(_activity_log)
    items.reverse()
    if bot_id:
        items = [a for a in items if a['bot_id'] == bot_id]
    return items[:limit]


def get_bot_state(bot_id):
    """Get the latest activity for a specific bot."""
    with _lock:
        for item in reversed(_activity_log):
            if item['bot_id'] == bot_id:
                return item
    return None
