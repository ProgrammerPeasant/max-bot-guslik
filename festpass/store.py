"""Хранилище на SQLite.

Один файл базы, никаких внешних сервисов: на фестивальную нагрузку
(десятки тысяч человек за день) этого хватает с запасом. Все
изменения, где важна гонка двух быстрых нажатий, идут через
BEGIN IMMEDIATE и уникальные индексы.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    username    TEXT,
    started_at  TEXT NOT NULL,
    started_on  TEXT NOT NULL,
    state       TEXT,
    blocked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS promo_codes (
    id         INTEGER PRIMARY KEY,
    day        TEXT NOT NULL,
    code       TEXT NOT NULL UNIQUE,
    user_id    INTEGER,
    issued_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS promo_one_per_day
    ON promo_codes (day, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS promo_free ON promo_codes (day, user_id, id);

CREATE TABLE IF NOT EXISTS promo_requests (
    user_id  INTEGER NOT NULL,
    day      TEXT NOT NULL,
    at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    day            TEXT NOT NULL,
    user_id        INTEGER NOT NULL,
    registered_at  TEXT NOT NULL,
    PRIMARY KEY (day, user_id)
);

-- один активный день = одна строка = один шанс в финале
CREATE TABLE IF NOT EXISTS active_days (
    user_id  INTEGER NOT NULL,
    day      TEXT NOT NULL,
    reason   TEXT NOT NULL,
    at       TEXT NOT NULL,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS draws (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL,
    seed          TEXT NOT NULL,
    seed_hash     TEXT NOT NULL,
    sealed_at     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'sealed',
    drawn_at      TEXT,
    confirmed_at  TEXT,
    confirmed_by  TEXT,
    announced_at  TEXT,
    UNIQUE (kind, key)
);

CREATE TABLE IF NOT EXISTS draw_entrants (
    draw_id  INTEGER NOT NULL REFERENCES draws(id),
    user_id  INTEGER NOT NULL,
    weight   INTEGER NOT NULL,
    PRIMARY KEY (draw_id, user_id)
);

CREATE TABLE IF NOT EXISTS draw_places (
    draw_id      INTEGER NOT NULL REFERENCES draws(id),
    place        INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    role         TEXT NOT NULL,
    prize        TEXT NOT NULL,
    status       TEXT NOT NULL,
    notified_at  TEXT,
    deadline     TEXT,
    PRIMARY KEY (draw_id, place),
    UNIQUE (draw_id, user_id)
);

CREATE TABLE IF NOT EXISTS claims (
    draw_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    full_name    TEXT,
    consent      INTEGER,
    answered_at  TEXT,
    PRIMARY KEY (draw_id, user_id)
);

CREATE TABLE IF NOT EXISTS pushes (
    id       INTEGER PRIMARY KEY,
    text     TEXT NOT NULL,
    segment  TEXT NOT NULL,
    sent     INTEGER NOT NULL,
    failed   INTEGER NOT NULL,
    at       TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def _one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def _scalar(self, sql: str, *args):
        row = self._one(sql, *args)
        return row[0] if row else None

    # пользователи

    def touch_user(self, user_id: int, name: str | None, username: str | None, now: str) -> bool:
        """Запомнить пользователя. True, если видим его впервые."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO users (id, name, username, started_at, started_on) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, name, username, now, now[:10]),
        )
        if cur.rowcount:
            return True
        self.db.execute(
            "UPDATE users SET name = COALESCE(?, name), username = COALESCE(?, username), "
            "blocked = 0 WHERE id = ?",
            (name, username, user_id),
        )
        return False

    def user(self, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM users WHERE id = ?", user_id)

    def state_of(self, user_id: int) -> str | None:
        return self._scalar("SELECT state FROM users WHERE id = ?", user_id)

    def set_state(self, user_id: int, state: str | None) -> None:
        self.db.execute("UPDATE users SET state = ? WHERE id = ?", (state, user_id))

    def mark_blocked(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET blocked = 1 WHERE id = ?", (user_id,))

    def audience(self, segment: str, today: str) -> list[int]:
        """Получатели рассылки.

        all      все, кто не заблокировал бота
        active   накопили хотя бы один шанс
        idle     еще ничего не сделали сегодня (напоминалка вечером)
        day:ДАТА участники розыгрыша этого дня
        """
        base = "SELECT id FROM users WHERE blocked = 0"
        if segment == "all":
            rows = self._all(base)
        elif segment == "active":
            rows = self._all(base + " AND id IN (SELECT user_id FROM active_days)")
        elif segment == "idle":
            rows = self._all(
                base + " AND id NOT IN (SELECT user_id FROM active_days WHERE day = ?)", today
            )
        elif segment.startswith("day:"):
            rows = self._all(
                base + " AND id IN (SELECT user_id FROM entries WHERE day = ?)", segment[4:]
            )
        else:
            raise ValueError(f"неизвестный сегмент: {segment}")
        return [r[0] for r in rows]

    # промокоды

    def load_codes(self, day: str, codes: list[str]) -> int:
        added = 0
        with self.tx():
            for code in codes:
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO promo_codes (day, code) VALUES (?, ?)", (day, code)
                )
                added += cur.rowcount
        return added

    def promo_of(self, user_id: int, day: str) -> str | None:
        return self._scalar(
            "SELECT code FROM promo_codes WHERE day = ? AND user_id = ?", day, user_id
        )

    def log_promo_request(self, user_id: int, day: str, now: str) -> None:
        self.db.execute(
            "INSERT INTO promo_requests (user_id, day, at) VALUES (?, ?, ?)", (user_id, day, now)
        )

    def take_code(self, user_id: int, day: str, now: str) -> str | None:
        """Закрепить за человеком свободный код дня. None, если коды кончились."""
        with self.tx():
            mine = self.promo_of(user_id, day)
            if mine:
                return mine
            rows = self._all(
                "UPDATE promo_codes SET user_id = ?, issued_at = ? WHERE id = ("
                "  SELECT id FROM promo_codes WHERE day = ? AND user_id IS NULL"
                "  ORDER BY id LIMIT 1"
                ") RETURNING code",
                user_id, now, day,
            )
        return rows[0]["code"] if rows else None

    # участие и шансы

    def entry_of(self, day: str, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM entries WHERE day = ? AND user_id = ?", day, user_id)

    def register(self, day: str, user_id: int, now: str) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO entries (day, user_id, registered_at) VALUES (?, ?, ?)",
            (day, user_id, now),
        )
        return cur.rowcount == 1

    def entrants(self, day: str) -> list[int]:
        rows = self._all("SELECT user_id FROM entries WHERE day = ? ORDER BY registered_at", day)
        return [r[0] for r in rows]

    def mark_active(self, user_id: int, day: str, reason: str, now: str) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO active_days (user_id, day, reason, at) VALUES (?, ?, ?, ?)",
            (user_id, day, reason, now),
        )
        return cur.rowcount == 1

    def active_days(self, user_id: int) -> set[str]:
        rows = self._all("SELECT day FROM active_days WHERE user_id = ?", user_id)
        return {r[0] for r in rows}

    def chances(self, user_id: int, cap: int) -> int:
        return min(len(self.active_days(user_id)), cap)

    def chance_weights(self, cap: int) -> dict[int, int]:
        rows = self._all("SELECT user_id, COUNT(*) FROM active_days GROUP BY user_id")
        return {r[0]: min(r[1], cap) for r in rows}

    # розыгрыши

    def create_draw(self, kind: str, key: str, seed: str, seed_hash: str, now: str) -> sqlite3.Row:
        self.db.execute(
            "INSERT OR IGNORE INTO draws (kind, key, seed, seed_hash, sealed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, key, seed, seed_hash, now),
        )
        return self.find_draw(kind, key)

    def find_draw(self, kind: str, key: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM draws WHERE kind = ? AND key = ?", kind, key)

    def draw(self, draw_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM draws WHERE id = ?", draw_id)

    def draws(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self._all("SELECT * FROM draws WHERE status = ? ORDER BY id", status)
        return self._all("SELECT * FROM draws ORDER BY kind = 'final', key")

    def update_draw(self, draw_id: int, **fields) -> None:
        cols = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE draws SET {cols} WHERE id = ?", (*fields.values(), draw_id))

    def save_result(self, draw_id: int, weights: dict[int, int], places: list[tuple], now: str) -> None:
        """places: (место, user_id, роль, приз, статус)."""
        with self.tx():
            self.db.executemany(
                "INSERT INTO draw_entrants (draw_id, user_id, weight) VALUES (?, ?, ?)",
                [(draw_id, uid, w) for uid, w in weights.items()],
            )
            self.db.executemany(
                "INSERT INTO draw_places (draw_id, place, user_id, role, prize, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(draw_id, *p) for p in places],
            )
            self.db.execute(
                "UPDATE draws SET status = 'drawn', drawn_at = ? WHERE id = ?", (now, draw_id)
            )

    def entrant_weights(self, draw_id: int) -> dict[int, int]:
        rows = self._all("SELECT user_id, weight FROM draw_entrants WHERE draw_id = ?", draw_id)
        return {r[0]: r[1] for r in rows}

    def places(self, draw_id: int, role: str | None = None, status: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM draw_places WHERE draw_id = ?", [draw_id]
        if role:
            sql += " AND role = ?"
            args.append(role)
        if status:
            sql += " AND status = ?"
            args.append(status)
        return self._all(sql + " ORDER BY place", *args)

    def place_of(self, draw_id: int, user_id: int) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM draw_places WHERE draw_id = ? AND user_id = ?", draw_id, user_id
        )

    def update_place(self, draw_id: int, place: int, **fields) -> None:
        cols = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(
            f"UPDATE draw_places SET {cols} WHERE draw_id = ? AND place = ?",
            (*fields.values(), draw_id, place),
        )

    def overdue(self, now: str) -> list[sqlite3.Row]:
        """Победители, которые не ответили вовремя или до которых не достучались."""
        return self._all(
            "SELECT * FROM draw_places "
            "WHERE role = 'winner' AND deadline IS NOT NULL "
            "  AND (status = 'unreachable' OR (status = 'notified' AND deadline < ?))",
            now,
        )

    def next_reserve(self, draw_id: int) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM draw_places WHERE draw_id = ? AND role = 'reserve' "
            "AND status = 'waiting' ORDER BY place LIMIT 1",
            draw_id,
        )

    def export_rows(self, draw_id: int) -> list[sqlite3.Row]:
        return self._all(
            "SELECT e.user_id, u.name, u.username, e.weight, p.place, p.role, p.prize, "
            "       p.status, c.full_name, c.consent "
            "FROM draw_entrants e "
            "LEFT JOIN users u ON u.id = e.user_id "
            "LEFT JOIN draw_places p ON p.draw_id = e.draw_id AND p.user_id = e.user_id "
            "LEFT JOIN claims c ON c.draw_id = e.draw_id AND c.user_id = e.user_id "
            "WHERE e.draw_id = ? "
            "ORDER BY p.place IS NULL, p.place, e.user_id",
            draw_id,
        )

    # подтверждение приза

    def claim(self, draw_id: int, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM claims WHERE draw_id = ? AND user_id = ?", draw_id, user_id)

    def save_claim_name(self, draw_id: int, user_id: int, full_name: str, now: str) -> None:
        self.db.execute(
            "INSERT INTO claims (draw_id, user_id, full_name, answered_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (draw_id, user_id) DO UPDATE SET full_name = excluded.full_name, "
            "consent = NULL, answered_at = excluded.answered_at",
            (draw_id, user_id, full_name, now),
        )

    def save_consent(self, draw_id: int, user_id: int, consent: bool, now: str) -> None:
        self.db.execute(
            "UPDATE claims SET consent = ?, answered_at = ? WHERE draw_id = ? AND user_id = ?",
            (int(consent), now, draw_id, user_id),
        )

    # рассылки

    def log_push(self, text: str, segment: str, sent: int, failed: int, now: str) -> None:
        self.db.execute(
            "INSERT INTO pushes (text, segment, sent, failed, at) VALUES (?, ?, ?, ?, ?)",
            (text, segment, sent, failed, now),
        )
