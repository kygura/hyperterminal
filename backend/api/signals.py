"""
Read-only signal API — reads from the hl-signal-daemon's SQLite (data.db).
"""
import json
import sqlite3
import os
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/signals", tags=["signals"])

# The daemon writes to data.db in the project root.
# When running in Docker the databases are shared via a volume mount at /app/data.
_CANDIDATES = [
    Path(os.getenv("SIGNAL_DB_PATH", "")),
    Path(__file__).parents[1] / "data.db",
    Path("/app/data/data.db"),                    # Docker volume
]

def _get_db_path() -> Path:
    configured = os.getenv("SIGNAL_DB_PATH", "").strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        raise HTTPException(status_code=503, detail=f"Signal database not found: {path}")

    for p in _CANDIDATES:
        if p and p.exists():
            return p
    raise HTTPException(status_code=503, detail="Signal database not found")


def _conn() -> sqlite3.Connection:
    db = _get_db_path()
    conn = sqlite3.connect(db, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Parse signals_json if it's a string
    if isinstance(d.get("signals_json"), str):
        try:
            d["signals_json"] = json.loads(d["signals_json"])
        except (json.JSONDecodeError, TypeError):
            d["signals_json"] = {}
    d.setdefault("timeframe", _get_signal_timeframe())
    return d


def _get_signal_timeframe() -> str:
    config_root = _get_config_root()
    if not config_root:
        return "hourly"

    global_cfg = config_root / "global.yaml"
    if not global_cfg.exists():
        return "hourly"

    import yaml

    with open(global_cfg) as f:
        config = yaml.safe_load(f) or {}
    return str(config.get("strategy", {}).get("timeframe", "hourly")).lower()


@router.get("/active")
def get_active_signals(
    asset: Optional[str] = Query(None, description="Filter by asset, e.g. BTC"),
    limit: int = Query(50, ge=1, le=500),
):
    """
    Return the most-recent trade candidate per (asset, direction).
    'Active' = the latest signal for each unique (asset, direction) pair
    fired within the last 24 hours.
    """
    try:
        conn = _conn()
        cur = conn.cursor()

        # Latest signal per (asset, direction) in the last 24h
        cutoff_ms = int(__import__("time").time() * 1000) - 24 * 60 * 60 * 1000
        sql = """
            SELECT tc.*
            FROM trade_candidates tc
            INNER JOIN (
                SELECT asset, direction, MAX(ts) AS max_ts
                FROM trade_candidates
                WHERE (
                    (typeof(ts) = 'integer' AND ts >= ?)
                    OR
                    (typeof(ts) = 'text' AND ts >= datetime('now', '-24 hours'))
                )
                {asset_filter}
                GROUP BY asset, direction
            ) latest ON tc.asset = latest.asset
                    AND tc.direction = latest.direction
                    AND tc.ts = latest.max_ts
            ORDER BY tc.ts DESC
            LIMIT ?
        """
        asset_filter = "AND asset = ?" if asset else ""
        sql = sql.format(asset_filter=asset_filter)

        params = [cutoff_ms]
        if asset:
            params.append(asset)
        params.append(limit)
        cur.execute(sql, params)
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
def get_signal_history(
    asset: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    conviction: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated full history of fired trade candidates."""
    try:
        conn = _conn()
        cur = conn.cursor()

        conditions = []
        params: list = []

        if asset:
            conditions.append("asset = ?")
            params.append(asset)
        if direction:
            conditions.append("direction = ?")
            params.append(direction.upper())
        if conviction:
            conditions.append("conviction = ?")
            params.append(conviction.upper())

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur.execute(
            f"SELECT * FROM trade_candidates {where} ORDER BY ts DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_config_root() -> Optional[Path]:
    candidates = [
        Path(__file__).parents[1] / "config",
        Path("/app/config"),                    # Docker
    ]
    return next((p for p in candidates if p.exists()), None)


@router.get("/config")
def get_signal_config():
    """Return current signal thresholds from the daemon's YAML configs."""
    import yaml

    config_root = _get_config_root()
    if not config_root:
        return {"error": "Config directory not found"}

    result: dict = {}

    global_cfg = config_root / "global.yaml"
    if global_cfg.exists():
        with open(global_cfg) as f:
            result["global"] = yaml.safe_load(f)

    signals_dir = config_root / "signals"
    if signals_dir.exists():
        result["signals"] = {}
        for yaml_file in signals_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                result["signals"][yaml_file.stem] = yaml.safe_load(f)

    return result


class ConfigUpdateBody(BaseModel):
    data: dict


@router.put("/config/{section:path}")
def update_signal_config(section: str, body: ConfigUpdateBody):
    """
    Update a config section in-place.
    - section='global'  → writes config/global.yaml
    - section='signals/<name>' → writes config/signals/<name>.yaml
    Merges the supplied keys into the existing file (shallow merge).
    """
    import yaml

    config_root = _get_config_root()
    if not config_root:
        raise HTTPException(status_code=503, detail="Config directory not found")

    # Resolve target file safely (prevent path traversal)
    parts = section.strip("/").split("/", 1)
    if parts[0] == "global":
        target = config_root / "global.yaml"
    elif parts[0] == "signals" and len(parts) == 2 and parts[1]:
        target = config_root / "signals" / f"{parts[1]}.yaml"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown config section: {section!r}")

    # Reject any path that escapes the config root
    try:
        target.resolve().relative_to(config_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid section path")

    # Load existing, merge, write back
    existing: dict = {}
    if target.exists():
        with open(target) as f:
            existing = yaml.safe_load(f) or {}

    existing.update(body.data)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)

    return {"ok": True, "section": section, "data": existing}
