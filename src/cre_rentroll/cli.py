from __future__ import annotations

import argparse
import json

from cre_rentroll import vault
from cre_rentroll.harness import run_question
from cre_rentroll.questions import QUESTIONS


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cre-rentroll")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Parse a rent-roll CSV into the deal vault")
    ing.add_argument("csv")

    sub.add_parser("deals", help="List ingested deals")

    ask = sub.add_parser("ask", help="Run q1|q2|q3|weather against an ingested deal")
    ask.add_argument("deal_id")
    ask.add_argument("qid", choices=list(QUESTIONS))
    ask.add_argument("--offline", action="store_true")

    ev = sub.add_parser("eval", help="Run q1-q3 eval table for a deal")
    ev.add_argument("deal_id")
    ev.add_argument("--offline", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "ingest":
        roll = vault.ingest_and_save(args.csv)
        print(
            json.dumps(
                {
                    "deal_id": roll.meta.deal_id,
                    "name": roll.meta.name,
                    "units": len(roll.units),
                    "address": roll.meta.address,
                    "address_confidence": roll.meta.address_confidence,
                    "address_note": roll.meta.address_note,
                },
                indent=2,
            )
        )
        return
    if args.cmd == "deals":
        print("\n".join(vault.list_deals()) or "(none)")
        return
    if args.cmd == "ask":
        out = run_question(args.deal_id, args.qid, offline=args.offline)
        print(out["answer"])
        print("---")
        print(json.dumps({k: out[k] for k in ("status", "tools_used", "evidence_ids", "eval")}, indent=2))
        return
    if args.cmd == "eval":
        rows = []
        for qid in ("q1", "q2", "q3", "weather"):
            out = run_question(args.deal_id, qid, offline=args.offline)
            e = out["eval"]
            rows.append(
                {
                    "qid": qid,
                    "G": e["groundedness"],
                    "C": e["completeness"],
                    "R": e["relevance"],
                    "tools": out["tools_used"],
                }
            )
        print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
