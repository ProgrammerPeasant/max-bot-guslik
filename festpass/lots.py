"""Честный жребий.

Схема commit-reveal. Перед розыгрышем генерируем случайный ключ и сразу
публикуем его sha256-отпечаток. Сам ключ раскрываем только после
розыгрыша. Победители однозначно выводятся из ключа и списка
участников, поэтому результат может пересчитать кто угодно, а подменить
ключ задним числом не получится: не сойдется отпечаток.

Как тянем жребий. Каждый билет участника превращается в число
sha256("<ключ>:<user_id>:<номер билета>"). Участник идет в зачет со
своим лучшим (наименьшим) билетом, места раздаются по возрастанию.
Это ровно выборка без возвращения с весом, равным числу билетов: у
кого семь активных дней, у того семь попыток вытянуть маленькое число.
Один человек при этом не может занять два места.
"""

from __future__ import annotations

import hashlib
import secrets


def new_seed() -> str:
    return secrets.token_hex(32)


def fingerprint(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def ticket(seed: str, user_id: int, number: int) -> int:
    digest = hashlib.sha256(f"{seed}:{user_id}:{number}".encode()).digest()
    return int.from_bytes(digest, "big")


def draw(seed: str, weights: dict[int, int], places: int) -> list[int]:
    """Вернуть user_id победителей в порядке мест."""
    best = {
        user_id: min(ticket(seed, user_id, n) for n in range(count))
        for user_id, count in weights.items()
        if count > 0
    }
    return sorted(best, key=best.__getitem__)[:places]
