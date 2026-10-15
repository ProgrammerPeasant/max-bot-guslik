import asyncio
import csv

import pytest

from festpass.desk import DrawError


def run(coro):
    return asyncio.run(coro)


def test_daily_draw_needs_confirmation(fest):
    for uid in range(1, 8):
        run(fest.visit(uid, 0))
    draw_id = fest.stage.desk.draw_day(fest.stage.clock.today())

    places = fest.stage.store.places(draw_id)
    assert [p["prize"] for p in places] == ["кружка", "стикеры", "стикеры"]
    assert len({p["user_id"] for p in places}) == 3

    with pytest.raises(DrawError):
        run(fest.herald.announce(draw_id))

    fest.stage.desk.confirm(draw_id, "Оля")
    assert run(fest.herald.announce(draw_id)) == 3
    winner = places[0]["user_id"]
    assert "хедлайнеров дня" in fest.api.last(winner).text
    assert "Ты выиграл кружка от Зерно" in fest.api.last(winner).text


def test_registration_closes_after_draw(fest):
    run(fest.visit(1, 0))
    fest.stage.desk.draw_day(fest.stage.clock.today())
    assert "уже подведен" in run(fest.visit(2, 0)).text


def test_cannot_draw_twice_or_in_future(fest):
    fest.stage.desk.draw_day(fest.stage.clock.today())
    with pytest.raises(DrawError):
        fest.stage.desk.draw_day(fest.stage.clock.today())
    with pytest.raises(DrawError):
        fest.stage.desk.draw_day(fest.stage.lineup.acts[1].day)


def test_verify_and_export(fest, tmp_path):
    for uid in range(1, 5):
        run(fest.visit(uid, 0))
    draw_id = fest.stage.desk.draw_day(fest.stage.clock.today())
    ok, _ = fest.stage.desk.verify(draw_id)
    assert ok

    path = fest.stage.desk.export(draw_id, tmp_path / "out")
    rows = list(csv.reader(path.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[0] == ["Розыгрыш", "2026-09-01 Зерно"]
    table = rows[rows.index([]) + 2:]
    assert len(table) == 4

    fest.stage.store.db.execute("UPDATE draw_places SET user_id = 999 WHERE draw_id = ? AND place = 1", (draw_id,))
    ok, note = fest.stage.desk.verify(draw_id)
    assert not ok and "другой список" in note


def _final_with_players(fest, players=6):
    days = ["2026-09-01", "2026-09-02", "2026-09-04"]
    for i, day in enumerate(days):
        fest.at(f"{day} 12:00")
        for uid in range(1, players + 1):
            if uid % (i + 1) == 0:
                run(fest.visit(uid, i))
    draw_id = fest.stage.desk.draw_final()
    fest.stage.desk.confirm(draw_id, "Оля")
    return draw_id


def test_final_weights_are_active_days(fest):
    draw_id = _final_with_players(fest)
    weights = fest.stage.store.entrant_weights(draw_id)
    assert weights[6] == 3 and weights[2] == 2 and weights[1] == 1
    roles = [p["role"] for p in fest.stage.store.places(draw_id)]
    assert roles == ["winner", "winner", "reserve", "reserve"]


def test_final_claim_happy_path(fest):
    draw_id = _final_with_players(fest)
    run(fest.herald.announce(draw_id))
    winner = fest.stage.store.places(draw_id, role="winner")[0]["user_id"]
    message = fest.api.last(winner)
    assert "главную сцену" in message.text
    assert message.rows[0][0].payload == f"claim:{draw_id}"

    assert "имя и фамилию" in run(fest.press(winner, f"claim:{draw_id}")).text
    assert "Не похоже" in run(fest.say(winner, "привет")).text
    consent = run(fest.say(winner, "Анна   Смирнова"))
    assert consent.text.startswith("Анна Смирнова, остался один шаг")
    assert "билетному оператору" in consent.text

    assert "Без согласия" in run(fest.press(winner, f"refuse:{draw_id}")).text
    run(fest.press(winner, f"claim:{draw_id}"))
    run(fest.say(winner, "Анна Смирнова"))
    assert "Данные приняты" in run(fest.press(winner, f"agree:{draw_id}")).text
    claim = fest.stage.store.claim(draw_id, winner)
    assert claim["full_name"] == "Анна Смирнова" and claim["consent"] == 1


def test_stranger_cannot_claim(fest):
    draw_id = _final_with_players(fest)
    run(fest.herald.announce(draw_id))
    assert "Не нашли приз" in run(fest.press(4242, f"claim:{draw_id}")).text
    assert "Не нашли приз" in run(fest.press(4242, "agree:nonsense")).text


def test_silent_winner_hands_prize_to_reserve(fest):
    draw_id = _final_with_players(fest)
    run(fest.herald.announce(draw_id))
    places = fest.stage.store.places(draw_id)
    silent, first_reserve = places[0]["user_id"], places[2]["user_id"]

    fest.at("2026-09-06 12:00")
    assert run(fest.herald.sweep()) == 2  # оба победителя промолчали
    assert "перешел резервному" in fest.api.last(silent).text
    assert "главную сцену" in fest.api.last(first_reserve).text
    assert "истек" in run(fest.press(silent, f"claim:{draw_id}")).text
    statuses = [p["status"] for p in fest.stage.store.places(draw_id)]
    assert statuses == ["expired", "expired", "notified", "notified"]


def test_unreachable_winner_is_replaced_without_waiting(fest):
    draw_id = _final_with_players(fest)
    first = fest.stage.store.places(draw_id)[0]["user_id"]
    fest.api.gone.add(first)
    run(fest.herald.announce(draw_id))
    assert run(fest.herald.sweep()) == 1


def test_push_segments(fest):
    run(fest.visit(1, 0))
    run(fest.say(2, "привет"))
    sent, failed = run(fest.herald.push("Завтра новый партнер", "idle"))
    assert (sent, failed) == (1, 0)
    assert fest.api.last(2).text == "Завтра новый партнер"
    assert fest.stage.store.audience("day:2026-09-01", "2026-09-01") == [1]
