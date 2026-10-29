"""Refresh odds, fit models and write the priced board as JSON documents for the hosted
mobile dashboard (a Claude artifact whose database holds the snapshot).

    python -m app.export OUT_DIR [--groups football_eng nfl ...] [--no-refresh]

Writes OUT_DIR/chunk-<i>.json and OUT_DIR/meta.json (each a top-level JSON object under the
artifact store's 256 KiB document limit) plus OUT_DIR/writes.json, the list of
{collection, doc_id, file_path} entries to write. Write the chunks first and meta last:
the page reloads when meta changes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import budget, db, sports
from .service import Service

CHUNK_BYTES = 180_000
COLLECTION = "odds"
KEEP = ("sel_id", "event_id", "sport_key", "sport_title", "group", "commence_time", "event_name", "market",
        "market_label", "label", "source", "bet365_price", "best_price", "best_book", "median_price")
ROUND = ("fair_prob", "eff_prob", "promo_bonus", "model_prob", "market_prob", "model_weight")


def compact(sel: dict) -> dict:
    out = {k: sel[k] for k in KEEP if sel.get(k) is not None}
    out.update({k: round(sel[k], 5) for k in ROUND if sel.get(k) is not None})
    return out


def chunk(rows: list[dict], limit: int = CHUNK_BYTES) -> list[list[dict]]:
    chunks, cur, size = [], [], 0
    for r in rows:
        n = len(json.dumps(r, separators=(",", ":"))) + 1
        if cur and size + n > limit:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur or not chunks:
        chunks.append(cur)
    return chunks


def run(out: Path, groups: list[str], refresh: bool = True) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    svc = Service()
    result = svc.refresh(groups) if refresh else None
    svc.hub.warm(list(db.load_all_odds().keys()))
    svc._cache = None
    rows = [compact(s) for s in svc.selections()]
    parts = chunk(rows)
    writes = []
    for i, part in enumerate(parts):
        path = out / f"chunk-{i}.json"
        path.write_text(json.dumps({"rows": part}, separators=(",", ":")))
        writes.append({"collection": COLLECTION, "doc_id": f"chunk-{i}", "file_path": str(path.resolve())})
    s = db.get_settings()
    models = svc.hub.status()
    meta = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "demo": svc.demo,
        "chunks": len(parts),
        "count": len(rows),
        "groups": groups,
        "credits": budget.status(s) if not svc.demo else None,
        "refresh": {k: result.get(k) for k in ("ok", "cost", "reason", "errors", "fetched")} if result else None,
        "models": {"football": sorted(models["football"]), "ratings": sorted(models["ratings"]),
                   "errors": list(models["errors"].values())[:5]},
        "early_payout_keys": sorted(sports.EARLY_PAYOUT_KEYS),
        "acca_boost_table": s["acca_boost_table"],
        "acca_boost_min_leg_odds": s["acca_boost_min_leg_odds"],
    }
    path = out / "meta.json"
    path.write_text(json.dumps(meta, separators=(",", ":")))
    writes.append({"collection": COLLECTION, "doc_id": "meta", "file_path": str(path.resolve())})
    (out / "writes.json").write_text(json.dumps(writes, indent=1))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--groups", nargs="+", default=list(sports.GROUPS))
    ap.add_argument("--no-refresh", action="store_true", help="Price the cached odds without spending credits")
    args = ap.parse_args()
    meta = run(args.out, args.groups, refresh=not args.no_refresh)
    print(json.dumps({k: meta[k] for k in ("updated_at", "demo", "chunks", "count", "refresh", "credits")}, indent=1))


if __name__ == "__main__":
    main()
