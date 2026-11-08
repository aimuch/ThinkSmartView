"""[xiaozhi-hub] data/usage.db: one row per model request (tokens, money), summed per day / month / total.

Same pattern as chat_history.ChatDB: plain sqlite3, autocommit, WAL, one lock, private file.
Days and months are stored in the billing timezone at the time of the call, so a quota's "today"
is the user's today even when the server runs in UTC.
"""
import sqlite3
import threading
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  day TEXT NOT NULL,          -- YYYY-MM-DD in the billing timezone
  month TEXT NOT NULL,        -- YYYY-MM
  device TEXT,
  entry TEXT,                 -- config entry (DeepSeekLLM, ClaudeCLI ...)
  model TEXT,
  purpose TEXT,               -- chat, summary, intent, search, vision
  prompt_tokens INTEGER DEFAULT 0,
  cache_hit_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  cost REAL DEFAULT 0,
  currency TEXT,
  tier TEXT,                  -- 高峰 / 标准 ...
  priced INTEGER DEFAULT 1,   -- 0: no price rule matched this model (cost 0)
  estimated INTEGER DEFAULT 0 -- 1: the stream was cut before its usage arrived; tokens estimated
);
CREATE INDEX IF NOT EXISTS usage_day ON usage(day);
CREATE INDEX IF NOT EXISTS usage_month ON usage(month);
'''


class UsageDB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        self._lock = threading.Lock()
        self._c = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._c.execute('PRAGMA journal_mode=WAL')
        self._c.execute('PRAGMA synchronous=NORMAL')
        self._c.executescript(SCHEMA)

    def _q(self, sql, args=()):
        with self._lock:
            return self._c.execute(sql, args).fetchall()

    def add(self, row: dict):
        cols = ', '.join(row)
        with self._lock:
            self._c.execute(f'INSERT INTO usage ({cols}) VALUES ({", ".join("?" * len(row))})', tuple(row.values()))

    def spent(self, *, day=None, month=None, device=None) -> dict:
        """{currency: money} for a day, a month, or everything; optionally one device."""
        where, args = [], []
        for col, val in (('day', day), ('month', month), ('device', device)):
            if val is not None:
                where.append(f'{col} = ?')
                args.append(val)
        sql = 'SELECT currency, SUM(cost) FROM usage' + (' WHERE ' + ' AND '.join(where) if where else '') + \
              ' GROUP BY currency'
        return {cur: round(total or 0, 6) for cur, total in self._q(sql, args) if cur}

    def totals(self, where='', args=()):
        rows = self._q('SELECT COUNT(*), SUM(prompt_tokens), SUM(cache_hit_tokens), SUM(completion_tokens) '
                       'FROM usage' + (f' WHERE {where}' if where else ''), args)
        n, p, h, c = rows[0]
        return {'requests': n or 0, 'prompt': p or 0, 'cache_hit': h or 0, 'completion': c or 0}

    def by(self, column, where='', args=(), limit=50):
        assert column in ('model', 'device', 'entry', 'purpose', 'day', 'month', 'tier')
        sql = (f'SELECT {column}, currency, COUNT(*), SUM(prompt_tokens), SUM(cache_hit_tokens), '
               f'SUM(completion_tokens), SUM(cost), MIN(priced), MAX(estimated) FROM usage'
               + (f' WHERE {where}' if where else '') + f' GROUP BY {column}, currency ORDER BY {column} DESC LIMIT ?')
        return [{'key': k, 'currency': cur, 'requests': n, 'prompt': p or 0, 'cache_hit': h or 0,
                 'completion': c or 0, 'cost': round(m or 0, 6), 'unpriced': not mp, 'estimated': bool(me)}
                for k, cur, n, p, h, c, m, mp, me in self._q(sql, (*args, limit))]

    def unpriced_models(self):
        return [m for (m,) in self._q('SELECT DISTINCT model FROM usage WHERE priced = 0 AND model IS NOT NULL')]

    def clear(self):
        with self._lock:
            self._c.execute('DELETE FROM usage')
