import asyncio


def run(coro):
    return asyncio.run(coro)


def test_start_shows_menu(fest):
    run(fest.bot.handle({"update_type": "bot_started", "user": {"user_id": 7, "name": "Аня"}}))
    reply = fest.api.last(7)
    assert reply.text.startswith("Добро пожаловать в Бот розыгрышей")
    labels = [row[0].text for row in reply.rows]
    assert labels == ["Получить промокод", "Участвовать в розыгрыше", "Как участвовать", "Мои шансы", "Правила акции"]


def test_promo_is_pinned_for_the_day(fest):
    fest.stage.store.load_codes("2026-09-01", ["AAA111", "BBB222"])
    first = run(fest.press(1, "promo"))
    again = run(fest.press(1, "promo"))
    assert "Промокод: AAA111" in first.text
    assert "Промокод: AAA111" in again.text
    assert "до 30 сентября" in first.text
    assert fest.stage.store.chances(1, 10) == 1


def test_sold_out_still_lets_you_join(fest):
    fest.stage.store.load_codes("2026-09-01", ["ONLY1"])
    run(fest.press(1, "promo"))
    reply = run(fest.press(2, "promo"))
    assert "уже закончились" in reply.text
    assert reply.rows[0][0].payload == "join"
    assert "Ты в лайнапе" in run(fest.visit(2, 0)).text
    # запрос без выданного кода шанса не дает, а регистрация дает
    assert fest.stage.store.chances(2, 10) == 1


def test_subscription_flow(fest):
    reply = run(fest.press(5, "join"))
    assert reply.rows[0][0].url == "https://max.ru/zerno"
    assert run(fest.press(5, "check")).text.startswith("Пока не вижу подписку")
    assert run(fest.press(5, "check")).text.startswith("Пока не вижу подписку")
    ok = run(fest.visit(5, 0))
    assert "Шансов на главный приз: 1 из 3" in ok.text
    assert "Возвращайся завтра" in ok.text
    assert "уже участвуешь" in run(fest.press(5, "check")).text
    assert "уже участвуешь" in run(fest.press(5, "join")).text
    assert fest.stage.store.entrants("2026-09-01") == [5]


def test_promo_and_draw_same_day_is_one_chance(fest):
    fest.stage.store.load_codes("2026-09-01", ["X1"])
    run(fest.press(3, "promo"))
    run(fest.visit(3, 0))
    assert fest.stage.store.chances(3, 10) == 1


def test_gap_before_next_partner_is_announced(fest):
    fest.at("2026-09-02 10:00")
    reply = run(fest.visit(4, 1))
    assert "Следующий партнер выйдет на сцену 4 сентября" in reply.text
    fest.at("2026-09-03 10:00")
    assert "антракт" in run(fest.press(4, "promo")).text


def test_check_failure_is_not_a_rejection(fest):
    fest.api.broken = True
    assert "технический антракт" in run(fest.press(9, "check")).text
    assert fest.stage.store.entrants("2026-09-01") == []


def test_chances_are_capped(fest):
    for i, day in enumerate(["2026-09-01", "2026-09-02", "2026-09-04"]):
        fest.at(f"{day} 12:00")
        run(fest.visit(8, i))
    # четвертый день вручную, как будто афишу расширили задним числом
    fest.stage.store.mark_active(8, "2026-09-05", "draw", "x")
    reply = run(fest.press(8, "pass"))
    assert "Накоплено шансов: 3 из 3" in reply.text
    assert "Осталось дней фестиваля: 1" in reply.text


def test_pass_shows_lineup(fest):
    run(fest.visit(6, 0))
    fest.at("2026-09-02 09:00")
    text = run(fest.press(6, "pass")).text
    mark = fest.stage.texts.mark
    assert "Накоплено шансов: 1 из 3" in text
    assert mark("slot_on") + mark("slot_off") * 2 in text
    assert f"01.09  Зерно  {mark('visited')}" in text
    assert "02.09  Вжух  сегодня" in text


def test_rules_show_todays_fingerprint(fest):
    assert "Отпечаток появится" in run(fest.press(1, "rules")).text
    fest.stage.desk.seal_all()
    seal = fest.stage.store.find_draw("daily", "2026-09-01")["seed_hash"]
    assert seal in run(fest.press(1, "rules")).text


def test_navigation_edits_but_promo_is_sent(fest):
    fest.stage.store.load_codes("2026-09-01", ["KEEP"])
    run(fest.press(1, "how"))
    assert fest.api.last(1).keep is False
    run(fest.press(1, "promo"))
    assert fest.api.last(1).keep is True


def test_blocked_user_is_marked(fest):
    fest.api.gone.add(11)
    run(fest.say(11, "привет"))
    assert fest.stage.store.user(11)["blocked"] == 1
