"""
Portfolio branching API for branch CRUD, manual trade logging, and equity curves.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db.schema import apply_schema

router = APIRouter(prefix="/branches", tags=["branches"])
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise HTTPException(status_code=422, detail=f"Invalid datetime value: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _direction_multiplier(side: str) -> int:
    normalized = side.strip().upper()
    if normalized in {"LONG", "BUY"}:
        return 1
    if normalized in {"SHORT", "SELL"}:
        return -1
    raise HTTPException(status_code=422, detail=f"Unsupported side: {side}")


def _normalized_side(side: str) -> str:
    return "LONG" if _direction_multiplier(side) == 1 else "SHORT"


def _normalized_status(status: Optional[str], *, close_px: Optional[float], exit_date: Optional[str]) -> str:
    if status:
        normalized = status.strip().upper()
        if normalized not in {"OPEN", "CLOSED"}:
            raise HTTPException(status_code=422, detail=f"Unsupported status: {status}")
        return normalized
    return "CLOSED" if close_px is not None or exit_date is not None else "OPEN"


def _resolve_initial_capital(data: "BranchCreate | BranchUpdate") -> Optional[float]:
    if getattr(data, "initial_capital", None) is not None:
        return float(data.initial_capital)
    if getattr(data, "balance", None) is not None:
        return float(data.balance)
    return None


def _resolve_entry_px(data: "TradeCreate | TradeUpdate") -> Optional[float]:
    if getattr(data, "entry_px", None) is not None:
        return float(data.entry_px)
    if getattr(data, "entry_price", None) is not None:
        return float(data.entry_price)
    return None


def _branches_conn() -> sqlite3.Connection:
    candidates = [
        Path(os.getenv("BRANCHES_DB_PATH", "")),
        Path(__file__).resolve().parent.parent / "data.db",
    ]
    db = next((p for p in candidates if str(p) and p.is_file()), None)
    if not db:
        db = Path(__file__).resolve().parent.parent / "data.db"
    conn = sqlite3.connect(str(db), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_branches_tables() -> None:
    conn = _branches_conn()
    apply_schema(conn)

    branch_columns = _column_names(conn, "portfolio_branches")
    if "initial_capital" not in branch_columns:
        conn.execute("ALTER TABLE portfolio_branches ADD COLUMN initial_capital REAL DEFAULT 10000")
    if "source_wallet_id" not in branch_columns:
        conn.execute("ALTER TABLE portfolio_branches ADD COLUMN source_wallet_id TEXT")
    if "balance" not in branch_columns:
        conn.execute("ALTER TABLE portfolio_branches ADD COLUMN balance REAL DEFAULT 10000")

    trade_columns = _column_names(conn, "branch_trades")
    if "leverage" not in trade_columns:
        conn.execute("ALTER TABLE branch_trades ADD COLUMN leverage REAL DEFAULT 1")
    if "margin" not in trade_columns:
        conn.execute("ALTER TABLE branch_trades ADD COLUMN margin REAL")
    if "mode" not in trade_columns:
        conn.execute("ALTER TABLE branch_trades ADD COLUMN mode TEXT DEFAULT 'Cross'")

    conn.execute(
        """
        UPDATE portfolio_branches
        SET initial_capital = COALESCE(initial_capital, balance, 10000),
            balance = COALESCE(balance, initial_capital, 10000)
        """
    )

    cur = conn.execute("SELECT COUNT(*) AS count FROM portfolio_branches")
    if (cur.fetchone()["count"]) == 0:
        now = _iso_now()
        conn.execute(
            """
            INSERT INTO portfolio_branches
                (id, name, color, is_main, parent_id, fork_date, initial_capital, balance, source_wallet_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("main", "Main Portfolio", "#2dd4bf", 1, None, now, 10000.0, 10000.0, None, now, now),
        )

    conn.commit()
    conn.close()


try:
    _ensure_branches_tables()
except Exception as exc:
    logger.warning("Could not init branches tables: %s", exc)


class BranchCreate(BaseModel):
    name: str
    color: str = "#2dd4bf"
    initial_capital: Optional[float] = None
    balance: Optional[float] = None
    parent_id: Optional[str] = None
    source_wallet_id: Optional[str] = None
    inherit_open_trades: bool = True


class BranchUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    initial_capital: Optional[float] = None
    balance: Optional[float] = None
    source_wallet_id: Optional[str] = None


class TradeCreate(BaseModel):
    coin: str
    side: str
    size: float
    leverage: float = 1.0
    margin: Optional[float] = None
    mode: str = "Cross"
    entry_px: Optional[float] = None
    entry_price: Optional[float] = None
    entry_date: str
    close_px: Optional[float] = None
    exit_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class TradeUpdate(BaseModel):
    coin: Optional[str] = None
    side: Optional[str] = None
    size: Optional[float] = None
    leverage: Optional[float] = None
    margin: Optional[float] = None
    mode: Optional[str] = None
    entry_px: Optional[float] = None
    entry_price: Optional[float] = None
    entry_date: Optional[str] = None
    close_px: Optional[float] = None
    exit_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class PositionCreate(BaseModel):
    asset: str
    direction: str
    mode: str = "Cross"
    leverage: float = 1.0
    margin: float
    entry_date: str
    entry_price: float
    exit_date: Optional[str] = None


class PositionUpdate(BaseModel):
    leverage: Optional[float] = None
    margin: Optional[float] = None
    exit_date: Optional[str] = None


def _get_branch_or_404(conn: sqlite3.Connection, branch_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM portfolio_branches WHERE id = ?", (branch_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Branch not found")
    return row


def _fetch_positions(conn: sqlite3.Connection, branch_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM branch_positions WHERE branch_id = ? ORDER BY entry_date ASC",
            (branch_id,),
        ).fetchall()
    ]


def _fetch_trades(conn: sqlite3.Connection, branch_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM branch_trades WHERE branch_id = ? ORDER BY entry_date ASC, created_at ASC",
            (branch_id,),
        ).fetchall()
    ]


def _get_latest_price(conn: sqlite3.Connection, coin: str) -> Optional[float]:
    row = conn.execute(
        "SELECT close FROM ohlcv WHERE asset = ? ORDER BY ts DESC LIMIT 1",
        (coin,),
    ).fetchone()
    if row and row["close"] is not None:
        return float(row["close"])

    row = conn.execute(
        "SELECT mark_px FROM asset_snapshots WHERE asset = ? AND mark_px IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (coin,),
    ).fetchone()
    if row and row["mark_px"] is not None:
        return float(row["mark_px"])
    return None


def _get_price_at_or_before(conn: sqlite3.Connection, coin: str, at: datetime) -> Optional[float]:
    ts = int(at.timestamp() * 1000)
    row = conn.execute(
        "SELECT close FROM ohlcv WHERE asset = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (coin, ts),
    ).fetchone()
    if row and row["close"] is not None:
        return float(row["close"])

    row = conn.execute(
        "SELECT mark_px FROM asset_snapshots WHERE asset = ? AND ts <= ? AND mark_px IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (coin, ts),
    ).fetchone()
    if row and row["mark_px"] is not None:
        return float(row["mark_px"])

    return _get_latest_price(conn, coin)


def _trade_pnl(conn: sqlite3.Connection, trade: dict, point_in_time: Optional[datetime] = None) -> float:
    entry_px = float(trade["entry_px"])
    size = float(trade["size"])
    multiplier = _direction_multiplier(trade["side"])
    exit_date = _parse_datetime(trade["exit_date"]) if trade.get("exit_date") else None
    is_closed = (trade.get("status") or "OPEN").upper() == "CLOSED" or exit_date is not None

    if is_closed and exit_date and point_in_time and exit_date > point_in_time:
        is_closed = False

    if is_closed:
        mark_px = trade.get("close_px")
        if mark_px is None and exit_date is not None:
            mark_px = _get_price_at_or_before(conn, trade["coin"], exit_date)
    else:
        lookup_time = point_in_time or _utcnow()
        mark_px = _get_price_at_or_before(conn, trade["coin"], lookup_time)

    if mark_px is None:
        mark_px = entry_px

    return (float(mark_px) - entry_px) * size * multiplier


def _compute_branch_metrics(conn: sqlite3.Connection, branch: dict, trades: list[dict]) -> dict:
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    winning_trades = 0
    closed_trades = 0

    for trade in trades:
        status = (trade.get("status") or "OPEN").upper()
        if status == "CLOSED" or trade.get("exit_date"):
            pnl = _trade_pnl(conn, trade)
            realized_pnl += pnl
            closed_trades += 1
            if pnl > 0:
                winning_trades += 1
        else:
            unrealized_pnl += _trade_pnl(conn, trade)

    initial_capital = float(branch.get("initial_capital") or branch.get("balance") or 0.0)
    all_time_pnl = realized_pnl + unrealized_pnl
    return {
        "trade_count": len(trades),
        "open_trade_count": sum(1 for trade in trades if (trade.get("status") or "OPEN").upper() == "OPEN" and not trade.get("exit_date")),
        "closed_trade_count": closed_trades,
        "winning_trades": winning_trades,
        "win_rate": (winning_trades / closed_trades * 100.0) if closed_trades else 0.0,
        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "all_time_pnl": round(all_time_pnl, 4),
        "account_value": round(initial_capital + all_time_pnl, 4),
    }


def _serialize_branch(conn: sqlite3.Connection, branch: sqlite3.Row | dict) -> dict:
    data = dict(branch)
    data["positions"] = _fetch_positions(conn, data["id"])
    data["trades"] = _fetch_trades(conn, data["id"])
    data["metrics"] = _compute_branch_metrics(conn, data, data["trades"])
    if data.get("initial_capital") is None:
        data["initial_capital"] = data.get("balance")
    return data


def _copy_open_positions(conn: sqlite3.Connection, parent_id: str, branch_id: str, now: str) -> None:
    parent_positions = conn.execute(
        "SELECT * FROM branch_positions WHERE branch_id = ? AND exit_date IS NULL ORDER BY entry_date ASC",
        (parent_id,),
    ).fetchall()
    for pos in parent_positions:
        conn.execute(
            """
            INSERT INTO branch_positions
                (id, branch_id, asset, direction, mode, leverage, margin, entry_date, entry_price, exit_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex[:12],
                branch_id,
                pos["asset"],
                pos["direction"],
                pos["mode"],
                pos["leverage"],
                pos["margin"],
                pos["entry_date"],
                pos["entry_price"],
                None,
                now,
            ),
        )


def _copy_open_trades(conn: sqlite3.Connection, parent_id: str, branch_id: str, now: str) -> None:
    parent_trades = conn.execute(
        """
        SELECT * FROM branch_trades
        WHERE branch_id = ?
          AND (status = 'OPEN' OR exit_date IS NULL)
        ORDER BY entry_date ASC
        """,
        (parent_id,),
    ).fetchall()
    for trade in parent_trades:
        conn.execute(
            """
            INSERT INTO branch_trades
                (id, branch_id, coin, side, size, leverage, margin, mode, entry_px, close_px, entry_date, exit_date, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex[:12],
                branch_id,
                trade["coin"],
                trade["side"],
                trade["size"],
                trade["leverage"],
                trade["margin"],
                trade["mode"],
                trade["entry_px"],
                None,
                trade["entry_date"],
                None,
                "OPEN",
                trade["notes"],
                now,
                now,
            ),
        )


def _create_branch(conn: sqlite3.Connection, data: BranchCreate) -> dict:
    now = _iso_now()
    branch_id = uuid.uuid4().hex[:8]
    initial_capital = _resolve_initial_capital(data) or 10000.0

    if data.parent_id:
        _get_branch_or_404(conn, data.parent_id)

    conn.execute(
        """
        INSERT INTO portfolio_branches
            (id, name, color, is_main, parent_id, fork_date, initial_capital, balance, source_wallet_id, created_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            branch_id,
            data.name,
            data.color,
            data.parent_id,
            now,
            initial_capital,
            initial_capital,
            data.source_wallet_id,
            now,
            now,
        ),
    )

    if data.parent_id and data.inherit_open_trades:
        _copy_open_positions(conn, data.parent_id, branch_id, now)
        _copy_open_trades(conn, data.parent_id, branch_id, now)

    conn.commit()
    return _serialize_branch(conn, _get_branch_or_404(conn, branch_id))


@router.get("")
def list_branches():
    conn = _branches_conn()
    branches = [
        _serialize_branch(conn, row)
        for row in conn.execute(
            "SELECT * FROM portfolio_branches ORDER BY is_main DESC, created_at ASC"
        ).fetchall()
    ]
    conn.close()
    return branches


@router.get("/{branch_id}")
def get_branch(branch_id: str):
    conn = _branches_conn()
    branch = _serialize_branch(conn, _get_branch_or_404(conn, branch_id))
    conn.close()
    return branch


@router.post("")
def create_branch(data: BranchCreate):
    conn = _branches_conn()
    branch = _create_branch(conn, data)
    conn.close()
    return branch


@router.post("/fork")
def fork_branch(data: BranchCreate):
    conn = _branches_conn()
    branch = _create_branch(conn, data)
    conn.close()
    return branch


@router.put("/{branch_id}")
def update_branch(branch_id: str, data: BranchUpdate):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)

    updates = {}
    if data.name is not None:
        updates["name"] = data.name
    if data.color is not None:
        updates["color"] = data.color
    initial_capital = _resolve_initial_capital(data)
    if initial_capital is not None:
        updates["initial_capital"] = initial_capital
        updates["balance"] = initial_capital
    if data.source_wallet_id is not None:
        updates["source_wallet_id"] = data.source_wallet_id

    if updates:
        updates["updated_at"] = _iso_now()
        set_clause = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE portfolio_branches SET {set_clause} WHERE id = ?",
            tuple(updates.values()) + (branch_id,),
        )
        conn.commit()

    branch = _serialize_branch(conn, _get_branch_or_404(conn, branch_id))
    conn.close()
    return branch


@router.put("/{branch_id}/adopt")
def adopt_branch(branch_id: str):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)
    conn.execute("UPDATE portfolio_branches SET is_main = 0")
    conn.execute("UPDATE portfolio_branches SET is_main = 1 WHERE id = ?", (branch_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "main": branch_id}


@router.delete("/{branch_id}")
def delete_branch(branch_id: str):
    conn = _branches_conn()
    branch = _get_branch_or_404(conn, branch_id)
    if branch["is_main"]:
        raise HTTPException(status_code=400, detail="Cannot delete the main branch")

    conn.execute("DELETE FROM branch_positions WHERE branch_id = ?", (branch_id,))
    conn.execute("DELETE FROM branch_trades WHERE branch_id = ?", (branch_id,))
    conn.execute("DELETE FROM portfolio_branches WHERE id = ?", (branch_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/{branch_id}/trades")
def add_trade(branch_id: str, trade: TradeCreate):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)

    trade_id = uuid.uuid4().hex[:12]
    now = _iso_now()
    entry_px = _resolve_entry_px(trade)
    if entry_px is None:
        raise HTTPException(status_code=422, detail="entry_px is required")

    side = _normalized_side(trade.side)
    status = _normalized_status(trade.status, close_px=trade.close_px, exit_date=trade.exit_date)
    conn.execute(
        """
        INSERT INTO branch_trades
            (id, branch_id, coin, side, size, leverage, margin, mode, entry_px, close_px, entry_date, exit_date, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            branch_id,
            trade.coin.upper(),
            side,
            trade.size,
            trade.leverage,
            trade.margin,
            trade.mode,
            entry_px,
            trade.close_px,
            _parse_datetime(trade.entry_date).isoformat(),
            _parse_datetime(trade.exit_date).isoformat() if trade.exit_date else None,
            status,
            trade.notes,
            now,
            now,
        ),
    )
    conn.commit()
    row = dict(conn.execute("SELECT * FROM branch_trades WHERE id = ?", (trade_id,)).fetchone())
    conn.close()
    return row


@router.put("/{branch_id}/trades/{trade_id}")
def update_trade(branch_id: str, trade_id: str, data: TradeUpdate):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)
    existing = conn.execute(
        "SELECT * FROM branch_trades WHERE id = ? AND branch_id = ?",
        (trade_id, branch_id),
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Trade not found")

    updates = {}
    if data.coin is not None:
        updates["coin"] = data.coin.upper()
    if data.side is not None:
        updates["side"] = _normalized_side(data.side)
    if data.size is not None:
        updates["size"] = data.size
    if data.leverage is not None:
        updates["leverage"] = data.leverage
    if data.margin is not None:
        updates["margin"] = data.margin
    if data.mode is not None:
        updates["mode"] = data.mode
    entry_px = _resolve_entry_px(data)
    if entry_px is not None:
        updates["entry_px"] = entry_px
    if data.entry_date is not None:
        updates["entry_date"] = _parse_datetime(data.entry_date).isoformat()
    if data.close_px is not None:
        updates["close_px"] = data.close_px
    if data.exit_date is not None:
        updates["exit_date"] = _parse_datetime(data.exit_date).isoformat()
    if data.notes is not None:
        updates["notes"] = data.notes

    status = data.status
    if status is not None or data.close_px is not None or data.exit_date is not None:
        updates["status"] = _normalized_status(
            status or existing["status"],
            close_px=updates.get("close_px", existing["close_px"]),
            exit_date=updates.get("exit_date", existing["exit_date"]),
        )

    if updates:
        updates["updated_at"] = _iso_now()
        set_clause = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE branch_trades SET {set_clause} WHERE id = ? AND branch_id = ?",
            tuple(updates.values()) + (trade_id, branch_id),
        )
        conn.commit()

    row = dict(
        conn.execute(
            "SELECT * FROM branch_trades WHERE id = ? AND branch_id = ?",
            (trade_id, branch_id),
        ).fetchone()
    )
    conn.close()
    return row


@router.delete("/{branch_id}/trades/{trade_id}")
def delete_trade(branch_id: str, trade_id: str):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)
    conn.execute("DELETE FROM branch_trades WHERE id = ? AND branch_id = ?", (trade_id, branch_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/{branch_id}/equity")
def get_branch_equity(
    branch_id: str,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
):
    conn = _branches_conn()
    branch = dict(_get_branch_or_404(conn, branch_id))
    trades = _fetch_trades(conn, branch_id)

    branch_created = _parse_datetime(branch["created_at"])
    if trades:
        first_trade = min(_parse_datetime(trade["entry_date"]) for trade in trades)
        start_dt = min(branch_created, first_trade)
    else:
        start_dt = branch_created

    if from_date:
        start_dt = _parse_datetime(from_date)

    end_dt = _parse_datetime(to_date) if to_date else _utcnow()
    if end_dt < start_dt:
        raise HTTPException(status_code=422, detail="`to` must be on or after `from`")

    start_day = start_dt.date()
    end_day = end_dt.date()
    curve = []
    initial_capital = float(branch.get("initial_capital") or branch.get("balance") or 0.0)
    day = start_day

    while day <= end_day:
        point_in_time = datetime.combine(day, time.max, tzinfo=timezone.utc)
        equity = initial_capital
        for trade in trades:
            entry_date = _parse_datetime(trade["entry_date"])
            if entry_date <= point_in_time:
                equity += _trade_pnl(conn, trade, point_in_time)

        curve.append(
            {
                "date": day.isoformat(),
                "equity": round(equity, 4),
                "pnl": round(equity - initial_capital, 4),
            }
        )
        day += timedelta(days=1)

    response = {
        "branch_id": branch_id,
        "initial_capital": initial_capital,
        "curve": curve,
        "summary": _compute_branch_metrics(conn, branch, trades),
    }
    conn.close()
    return response


@router.post("/{branch_id}/positions")
def add_position(branch_id: str, pos: PositionCreate):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)

    pos_id = uuid.uuid4().hex[:12]
    now = _iso_now()
    conn.execute(
        """
        INSERT INTO branch_positions
            (id, branch_id, asset, direction, mode, leverage, margin, entry_date, entry_price, exit_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pos_id,
            branch_id,
            pos.asset.upper(),
            pos.direction,
            pos.mode,
            pos.leverage,
            pos.margin,
            pos.entry_date,
            pos.entry_price,
            pos.exit_date,
            now,
        ),
    )
    conn.commit()
    row = dict(conn.execute("SELECT * FROM branch_positions WHERE id = ?", (pos_id,)).fetchone())
    conn.close()
    return row


@router.put("/{branch_id}/positions/{pos_id}")
def update_position(branch_id: str, pos_id: str, data: PositionUpdate):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)
    pos = conn.execute(
        "SELECT * FROM branch_positions WHERE id = ? AND branch_id = ?",
        (pos_id, branch_id),
    ).fetchone()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    updates = {}
    if data.leverage is not None:
        updates["leverage"] = data.leverage
    if data.margin is not None:
        updates["margin"] = data.margin
    if data.exit_date is not None:
        updates["exit_date"] = data.exit_date

    if updates:
        set_clause = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE branch_positions SET {set_clause} WHERE id = ? AND branch_id = ?",
            tuple(updates.values()) + (pos_id, branch_id),
        )
        conn.commit()

    row = dict(
        conn.execute(
            "SELECT * FROM branch_positions WHERE id = ? AND branch_id = ?",
            (pos_id, branch_id),
        ).fetchone()
    )
    conn.close()
    return row


@router.delete("/{branch_id}/positions/{pos_id}")
def delete_position(branch_id: str, pos_id: str):
    conn = _branches_conn()
    _get_branch_or_404(conn, branch_id)
    conn.execute("DELETE FROM branch_positions WHERE id = ? AND branch_id = ?", (pos_id, branch_id))
    conn.commit()
    conn.close()
    return {"ok": True}
