"""Статистика фестиваля: одна сводная таблица по дням плюс итоги."""

from __future__ import annotations

import csv
from pathlib import Path

from festpass.config import Lineup
from festpass.store import Store

COLUMNS = (
    ("day", "День"),
    ("partner", "Партнер"),
    ("new_users", "Новых"),
    ("promo_requests", "Запросов кода"),
    ("promo_issued", "Выдано"),
    ("promo_left", "Осталось"),
    ("entrants", "В розыгрыше"),
    ("active", "Активных"),
)


def _by_day(store: Store, sql: str) -> dict[str, int]:
    return {row[0]: row[1] for row in store.db.execute(sql)}


def collect(store: Store, lineup: Lineup) -> dict:
    new_users = _by_day(store, "SELECT started_on, COUNT(*) FROM users GROUP BY started_on")
    requests = _by_day(store, "SELECT day, COUNT(*) FROM promo_requests GROUP BY day")
    issued = _by_day(store, "SELECT day, COUNT(user_id) FROM promo_codes GROUP BY day")
    left = _by_day(store, "SELECT day, COUNT(*) - COUNT(user_id) FROM promo_codes GROUP BY day")
    entrants = _by_day(store, "SELECT day, COUNT(*) FROM entries GROUP BY day")
    active = _by_day(store, "SELECT day, COUNT(*) FROM active_days GROUP BY day")

    rows = []
    for act in lineup.acts:
        rows.append({
            "day": act.key,
            "partner": act.partner,
            "new_users": new_users.get(act.key, 0),
            "promo_requests": requests.get(act.key, 0),
            "promo_issued": issued.get(act.key, 0),
            "promo_left": left.get(act.key, 0),
            "entrants": entrants.get(act.key, 0),
            "active": active.get(act.key, 0),
        })

    festival_days = {act.key for act in lineup.acts}
    count = lambda sql: store.db.execute(sql).fetchone()[0]
    totals = {
        "Всего запустили бота": count("SELECT COUNT(*) FROM users"),
        "Пришли вне дней фестиваля": sum(v for k, v in new_users.items() if k not in festival_days),
        "Заблокировали бота": count("SELECT COUNT(*) FROM users WHERE blocked = 1"),
        "Запросов промокода": count("SELECT COUNT(*) FROM promo_requests"),
        "Выдано промокодов": count("SELECT COUNT(user_id) FROM promo_codes"),
        "Участников с шансами": count("SELECT COUNT(DISTINCT user_id) FROM active_days"),
    }
    return {"days": rows, "totals": totals}


def render(report: dict) -> str:
    header = [title for _, title in COLUMNS]
    body = [[str(row[key]) for key, _ in COLUMNS] for row in report["days"]]
    widths = [max(len(cell) for cell in col) for col in zip(header, *body)]
    line = lambda cells: "  ".join(cell.ljust(w) for cell, w in zip(cells, widths)).rstrip()
    out = [line(header), line(["-" * w for w in widths])]
    out += [line(cells) for cells in body]
    out.append("")
    pad = max(len(k) for k in report["totals"])
    out += [f"{k.ljust(pad)}  {v}" for k, v in report["totals"].items()]
    return "\n".join(out)


def to_csv(report: dict, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        out = csv.writer(fh, delimiter=";")
        out.writerow([title for _, title in COLUMNS])
        for row in report["days"]:
            out.writerow([row[key] for key, _ in COLUMNS])
