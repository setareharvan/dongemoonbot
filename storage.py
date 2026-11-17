import os
import json
import time
import copy
import sqlite3
import threading
from contextlib import contextmanager

import config

DEFAULT = {"members": [], "expenses": [], "state": None}

_locks, _guard = {}, threading.Lock()


def _chat_lock(cid):
    with _guard:
        return _locks.setdefault(cid, threading.Lock())


class Store:
    """یک رکورد JSON به‌ازای هر چت؛ ساده، سریع و قابل مهاجرت به Postgres."""

    def __init__(self):
        if config.DATABASE_URL:
            import psycopg2
            self._pg = psycopg2
            self.kind = "pg"
            ddl = "CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, data TEXT, updated_at DOUBLE PRECISION)"
        else:
            self.kind = "sqlite"
            os.makedirs(config.DATA_DIR, exist_ok=True)
            ddl = "CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, data TEXT, updated_at REAL)"
        with self._conn() as c:
            cur = c.cursor()
            cur.execute(ddl)
            c.commit()

    @contextmanager
    def _conn(self):
        if self.kind == "pg":
            c = self._pg.connect(config.DATABASE_URL, connect_timeout=10)
        else:
            c = sqlite3.connect(config.DB_PATH, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
        finally:
            c.close()

    def _q(self, sql):
        return sql.replace("?", "%s") if self.kind == "pg" else sql

    def read(self, cid):
        with self._conn() as c:
            cur = c.cursor()
            cur.execute(self._q("SELECT data FROM chats WHERE chat_id=?"), (cid,))
            row = cur.fetchone()
        data = copy.deepcopy(DEFAULT)
        if row and row[0]:
            data.update(json.loads(row[0]))
        return data

    def write(self, cid, data):
        payload = json.dumps(data, ensure_ascii=False)
        sql = self._q(
            "INSERT INTO chats (chat_id, data, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at"
        )
        with self._conn() as c:
            cur = c.cursor()
            cur.execute(sql, (cid, payload, time.time()))
            c.commit()

    @contextmanager
    def session(self, cid):
        """خواندن → تغییر → ذخیره، با قفل مخصوص همان چت (ضد race condition)."""
        with _chat_lock(cid):
            data = self.read(cid)
            yield data
            self.write(cid, data)


store = Store()