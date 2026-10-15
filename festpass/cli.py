"""festbot: запуск бота и пульт оператора фестиваля."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

from festpass import stats
from festpass.app import assemble, connect, serve
from festpass.config import ConfigError
from festpass.desk import DrawError


def _parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="festbot", description="Бот розыгрышей для MAX")
    p.add_argument("--lineup", default=env("FESTPASS_LINEUP", "lineup.toml"))
    p.add_argument("--texts", default=env("FESTPASS_TEXTS", "texts.toml"))
    p.add_argument("--db", default=env("FESTPASS_DB", "festpass.db"))
    sub = p.add_subparsers(dest="cmd", required=True, metavar="команда")

    sub.add_parser("run", help="запустить бота")

    promo = sub.add_parser("promo", help="загрузить промокоды партнера на день")
    promo.add_argument("day", type=date.fromisoformat, help="день из афиши, ГГГГ-ММ-ДД")
    promo.add_argument("file", type=Path, help="файл с кодами, по одному на строку")

    sub.add_parser("seal", help="опечатать розыгрыши и показать отпечатки ключей")
    sub.add_parser("draws", help="список розыгрышей и их статусы")

    draw = sub.add_parser("draw", help="провести розыгрыш дня или финал")
    draw.add_argument("target", help="ГГГГ-ММ-ДД или final")

    confirm = sub.add_parser("confirm", help="подтвердить результаты (без этого уведомления не уйдут)")
    confirm.add_argument("draw_id", type=int)
    confirm.add_argument("--by", required=True, help="кто подтверждает, попадет в выгрузку")

    announce = sub.add_parser("announce", help="уведомить победителей")
    announce.add_argument("draw_id", type=int)

    verify = sub.add_parser("verify", help="пересчитать розыгрыш по раскрытому ключу")
    verify.add_argument("draw_id", type=int)

    export = sub.add_parser("export", help="выгрузить участников и победителей в CSV")
    export.add_argument("draw_id", type=int)
    export.add_argument("--out", type=Path, default=Path("exports"))

    sub.add_parser("sweep", help="передать неподтвержденные призы резервным победителям")

    push = sub.add_parser("push", help="разослать сообщение")
    text = push.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--file", type=Path)
    push.add_argument("--segment", default="all", help="all, active, idle или day:ГГГГ-ММ-ДД")
    push.add_argument("--rate", type=float, default=20.0, help="сообщений в секунду")
    push.add_argument("--dry-run", action="store_true", help="только посчитать получателей")

    report = sub.add_parser("stats", help="статистика по дням")
    report.add_argument("--csv", type=Path)
    return p


def _read_codes(path: Path) -> list[str]:
    codes = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        code = line.strip().split(";")[0].strip()
        if code and not code.startswith("#"):
            codes.append(code)
    return codes


async def _with_api(job):
    api = connect()
    try:
        return await job(api)
    finally:
        await api.close()


def run(args: argparse.Namespace) -> int:
    stage = assemble(args.lineup, args.texts, args.db)
    desk = stage.desk

    if args.cmd == "run":
        asyncio.run(serve(stage))

    elif args.cmd == "promo":
        if stage.lineup.act_on(args.day) is None:
            raise DrawError(f"{args.day} нет в афише")
        codes = _read_codes(args.file)
        added = stage.store.load_codes(args.day.isoformat(), codes)
        print(f"в файле {len(codes)} кодов, новых загружено {added}")

    elif args.cmd in ("seal", "draws"):
        rows = desk.seal_all() if args.cmd == "seal" else stage.store.draws()
        for d in rows:
            print(f"#{d['id']:<3} {desk.title(d):<32} {d['status']:<10} {d['seed_hash']}")

    elif args.cmd == "draw":
        if args.target == "final":
            draw_id = desk.draw_final()
        else:
            draw_id = desk.draw_day(date.fromisoformat(args.target))
        places = stage.store.places(draw_id)
        print(f"розыгрыш #{draw_id} проведен, мест: {len(places)}")
        for p in places:
            print(f"  {p['place']:>2}. {p['user_id']:<14} {p['role']:<8} {p['prize']}")
        print(f"проверь выгрузку и подтверди: festbot confirm {draw_id} --by <имя>")

    elif args.cmd == "confirm":
        desk.confirm(args.draw_id, args.by)
        print(f"#{args.draw_id} подтвержден, можно уведомлять: festbot announce {args.draw_id}")

    elif args.cmd == "announce":
        sent = asyncio.run(_with_api(lambda api: stage.herald(api).announce(args.draw_id)))
        print(f"уведомлений доставлено: {sent}")

    elif args.cmd == "verify":
        ok, note = desk.verify(args.draw_id)
        print(("ОК: " if ok else "РАСХОЖДЕНИЕ: ") + note)
        return 0 if ok else 1

    elif args.cmd == "export":
        print(desk.export(args.draw_id, args.out))

    elif args.cmd == "sweep":
        moved = asyncio.run(_with_api(lambda api: stage.herald(api).sweep()))
        print(f"передано резервным победителям: {moved}")

    elif args.cmd == "push":
        text = args.text if args.text else args.file.read_text(encoding="utf-8").strip()
        audience = stage.store.audience(args.segment, stage.clock.today().isoformat())
        if args.dry_run:
            print(f"получателей: {len(audience)}")
            return 0
        job = lambda api: stage.herald(api, args.rate).push(text, args.segment)
        sent, failed = asyncio.run(_with_api(job))
        print(f"доставлено {sent}, не доставлено {failed}")

    elif args.cmd == "stats":
        report = stats.collect(stage.store, stage.lineup)
        print(stats.render(report))
        if args.csv:
            stats.to_csv(report, args.csv)

    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except (ConfigError, DrawError, ValueError) as exc:
        print(f"festbot: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
