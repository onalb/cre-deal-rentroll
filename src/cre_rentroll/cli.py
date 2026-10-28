from __future__ import annotations

import argparse
import json

from cre_rentroll import vault


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cre-rentroll")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Parse a rent-roll CSV into the deal vault")
    ing.add_argument("csv")

    sub.add_parser("deals", help="List ingested deals")

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


if __name__ == "__main__":
    main()
