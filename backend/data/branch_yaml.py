from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BRANCHES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "branches"
YAML_SOURCE_TYPE = "yaml"
_IMPORT_BRANCH_COLORS = [
    "#ed3602",
    "#38a67c",
    "#627eea",
    "#f7931a",
    "#9945ff",
    "#50d2c1",
]


def branches_dir() -> Path:
    override = os.getenv("BRANCHES_YAML_DIR", "").strip()
    return Path(override) if override else BRANCHES_DIR


def ensure_branches_dir() -> Path:
    directory = branches_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _parse_date(value: str) -> str:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Invalid date: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _normalize_mode(value: Any) -> str:
    mode = str(value or "Cross").strip()
    if mode not in {"Cross", "Isolated"}:
        raise ValueError(f"Unsupported mode: {value}")
    return mode


def _normalize_direction(value: Any) -> str:
    direction = str(value or "").strip()
    if direction not in {"Long", "Short"}:
        raise ValueError(f"Unsupported direction: {value}")
    return direction


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def _default_color(name: str) -> str:
    checksum = sum(ord(char) for char in name)
    return _IMPORT_BRANCH_COLORS[checksum % len(_IMPORT_BRANCH_COLORS)]


def _read_document(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("Import document is empty")
    try:
        if stripped.startswith("{") or stripped.startswith("["):
            value = json.loads(stripped)
        else:
            value = yaml.safe_load(stripped)
    except Exception as exc:
        raise ValueError(f"Invalid import syntax: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Import document root must be an object")
    return value


def _normalize_position_payload(position: dict[str, Any], index: int) -> dict[str, Any]:
    try:
        asset = str(position["asset"]).strip().upper()
        direction = _normalize_direction(position["direction"])
        mode = _normalize_mode(position.get("mode", "Cross"))
        leverage = float(position["leverage"])
        margin = float(position["margin"])
        entry_date = _parse_date(str(position["entry_date"]))
        entry_price = float(position["entry_price"])
        exit_date_raw = position.get("exit_date")
        exit_price_raw = position.get("exit_price")
        exit_date = _parse_date(str(exit_date_raw)) if exit_date_raw not in (None, "") else None
        exit_price = float(exit_price_raw) if exit_price_raw not in (None, "") else None
        if leverage <= 0 or margin <= 0 or entry_price <= 0:
            raise ValueError("Leverage, margin, and entry_price must be positive")
        if (exit_date is None) != (exit_price is None):
            raise ValueError("exit_date and exit_price must either both be set or both be omitted")
        if exit_price is not None and exit_price <= 0:
            raise ValueError("exit_price must be positive")
        if exit_date and exit_date < entry_date:
            raise ValueError("exit_date cannot be earlier than entry_date")
    except KeyError as exc:
        raise ValueError(f"Position {index + 1} missing required field: {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Position {index + 1} invalid: {exc}") from exc

    return {
        "id": str(position.get("id") or uuid.uuid4().hex[:12]),
        "asset": asset,
        "direction": direction,
        "mode": mode,
        "leverage": leverage,
        "margin": margin,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
    }


def normalize_saved_branch_document(
    value: dict[str, Any],
    *,
    file_name: str | None = None,
) -> dict[str, Any]:
    file_stem = Path(file_name).stem if file_name else None
    if "branch" in value:
        branch = value["branch"]
        if not isinstance(branch, dict):
            raise ValueError("branch must be an object")
        name = str(branch.get("name") or "").strip()
        if not name:
            raise ValueError("branch.name is required")
        positions_raw = branch.get("positions")
        if not isinstance(positions_raw, list) or not positions_raw:
            raise ValueError("branch.positions must be a non-empty array")
        normalized_positions = [
            _normalize_position_payload(dict(position), index)
            for index, position in enumerate(positions_raw)
        ]
        fork_date = branch.get("fork_date") or min(
            position["entry_date"] for position in normalized_positions
        )
        branch_id = str(branch.get("id") or _slugify(file_stem or name))
        return {
            "version": int(value.get("version") or 1),
            "branch": {
                "id": branch_id,
                "name": name,
                "color": str(branch.get("color") or _default_color(name)),
                "is_main": bool(branch.get("is_main", False)),
                "parent_id": branch.get("parent_id"),
                "fork_date": _parse_date(str(fork_date)),
                "balance": float(branch.get("balance") or 0),
                "positions": normalized_positions,
            },
        }

    portfolio = value.get("portfolio")
    if not isinstance(portfolio, dict):
        raise ValueError("Expected either a saved `branch` document or legacy `portfolio` import")

    name = str(portfolio.get("name") or "").strip()
    if not name:
        raise ValueError("portfolio.name is required")
    positions_raw = portfolio.get("positions")
    if not isinstance(positions_raw, list) or not positions_raw:
        raise ValueError("portfolio.positions must be a non-empty array")
    normalized_positions = [
        _normalize_position_payload(dict(position), index)
        for index, position in enumerate(positions_raw)
    ]
    return {
        "version": 1,
        "branch": {
            "id": _slugify(file_stem or name),
            "name": name,
            "color": _default_color(name),
            "is_main": False,
            "parent_id": None,
            "fork_date": min(position["entry_date"] for position in normalized_positions),
            "balance": float(portfolio.get("balance") or 0),
            "positions": normalized_positions,
        },
    }


def parse_saved_branch_text(raw_text: str, *, file_name: str | None = None) -> dict[str, Any]:
    return normalize_saved_branch_document(_read_document(raw_text), file_name=file_name)


def load_saved_branch_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw_text = handle.read()
    return parse_saved_branch_text(raw_text, file_name=path.stem)


def saved_branch_to_db_payload(document: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    branch = document["branch"]
    branch_payload = {
        "id": str(branch["id"]),
        "name": str(branch["name"]),
        "color": str(branch.get("color") or _default_color(str(branch["name"]))),
        "is_main": 1 if branch.get("is_main") else 0,
        "parent_id": branch.get("parent_id"),
        "fork_date": str(branch["fork_date"]),
        "initial_capital": float(branch["balance"]),
        "balance": float(branch["balance"]),
    }
    positions = [
        {
            "id": str(position["id"]),
            "asset": str(position["asset"]),
            "direction": str(position["direction"]),
            "mode": str(position.get("mode") or "Cross"),
            "leverage": float(position["leverage"]),
            "margin": float(position["margin"]),
            "entry_date": str(position["entry_date"]),
            "entry_price": float(position["entry_price"]),
            "exit_date": position.get("exit_date"),
            "exit_price": position.get("exit_price"),
        }
        for position in branch["positions"]
    ]
    return branch_payload, positions


def _upsert_branch_row(
    conn: sqlite3.Connection,
    branch: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO portfolio_branches
            (id, name, color, is_main, parent_id, fork_date, initial_capital, balance, source_wallet_id, source_type, source_path, source_mtime, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            color = excluded.color,
            is_main = excluded.is_main,
            parent_id = excluded.parent_id,
            fork_date = excluded.fork_date,
            initial_capital = excluded.initial_capital,
            balance = excluded.balance,
            source_type = excluded.source_type,
            source_path = excluded.source_path,
            source_mtime = excluded.source_mtime,
            updated_at = excluded.updated_at
        """,
        (
            branch["id"],
            branch["name"],
            branch["color"],
            branch["is_main"],
            branch["parent_id"],
            branch["fork_date"],
            branch["initial_capital"],
            branch["balance"],
            None,
            YAML_SOURCE_TYPE if source_path else None,
            str(source_path) if source_path else None,
            float(source_path.stat().st_mtime) if source_path and source_path.exists() else None,
            now,
            now,
        ),
    )


def _replace_positions(conn: sqlite3.Connection, branch_id: str, positions: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM branch_positions WHERE branch_id = ?", (branch_id,))
    for position in positions:
        conn.execute(
            """
            INSERT INTO branch_positions
                (id, branch_id, asset, direction, mode, leverage, margin, entry_date, entry_price, exit_date, exit_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position["id"],
                branch_id,
                position["asset"],
                position["direction"],
                position["mode"],
                position["leverage"],
                position["margin"],
                position["entry_date"],
                position["entry_price"],
                position["exit_date"],
                position["exit_price"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def upsert_saved_branch(
    conn: sqlite3.Connection,
    document: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    branch, positions = saved_branch_to_db_payload(document)
    _upsert_branch_row(conn, branch, source_path=source_path)
    _replace_positions(conn, branch["id"], positions)
    conn.commit()
    return branch


def sync_saved_branches(conn: sqlite3.Connection) -> None:
    directory = ensure_branches_dir()
    seen_paths: set[str] = set()

    for path in sorted(directory.glob("*.y*ml")):
        document = load_saved_branch_file(path)
        upsert_saved_branch(conn, document, source_path=path)
        seen_paths.add(str(path))

    stale_rows = conn.execute(
        "SELECT id, source_path FROM portfolio_branches WHERE source_type = ?",
        (YAML_SOURCE_TYPE,),
    ).fetchall()
    for row in stale_rows:
        source_path = str(row["source_path"] or "")
        if source_path and source_path not in seen_paths:
            conn.execute("DELETE FROM branch_positions WHERE branch_id = ?", (row["id"],))
            conn.execute("DELETE FROM branch_trades WHERE branch_id = ?", (row["id"],))
            conn.execute("DELETE FROM portfolio_branches WHERE id = ?", (row["id"],))
    conn.commit()


def build_saved_branch_document(branch: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "branch": {
            "id": branch["id"],
            "name": branch["name"],
            "color": branch.get("color") or _default_color(branch["name"]),
            "is_main": bool(branch.get("is_main")),
            "parent_id": branch.get("parent_id"),
            "fork_date": branch["fork_date"],
            "balance": float(branch.get("initial_capital") or branch.get("balance") or 0),
            "positions": [
                {
                    "id": position["id"],
                    "asset": position["asset"],
                    "direction": position["direction"],
                    "mode": position.get("mode") or "Cross",
                    "leverage": float(position["leverage"]),
                    "margin": float(position["margin"]),
                    "entry_date": position["entry_date"],
                    "entry_price": float(position["entry_price"]),
                    "exit_date": position.get("exit_date"),
                    "exit_price": float(position["exit_price"]) if position.get("exit_price") is not None else None,
                }
                for position in positions
            ],
        },
    }


def write_saved_branch_file(path: Path, document: dict[str, Any]) -> None:
    ensure_branches_dir()
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False, allow_unicode=True)


def source_path_for_branch(branch_id: str) -> Path:
    return ensure_branches_dir() / f"{_slugify(branch_id)}.yaml"


def branch_source_path(conn: sqlite3.Connection, branch_id: str) -> Path | None:
    row = conn.execute(
        "SELECT source_type, source_path FROM portfolio_branches WHERE id = ?",
        (branch_id,),
    ).fetchone()
    if not row or row["source_type"] != YAML_SOURCE_TYPE:
        return None
    source_path = str(row["source_path"] or "").strip()
    return Path(source_path) if source_path else source_path_for_branch(branch_id)


def persist_branch_to_yaml(conn: sqlite3.Connection, branch_id: str) -> Path | None:
    row = conn.execute("SELECT * FROM portfolio_branches WHERE id = ?", (branch_id,)).fetchone()
    if not row:
        return None
    source_path = branch_source_path(conn, branch_id)
    if source_path is None:
        return None
    positions = [
        dict(position)
        for position in conn.execute(
            "SELECT * FROM branch_positions WHERE branch_id = ? ORDER BY entry_date ASC",
            (branch_id,),
        ).fetchall()
    ]
    document = build_saved_branch_document(dict(row), positions)
    write_saved_branch_file(source_path, document)
    conn.execute(
        """
        UPDATE portfolio_branches
        SET source_type = ?, source_path = ?, source_mtime = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            YAML_SOURCE_TYPE,
            str(source_path),
            float(source_path.stat().st_mtime),
            datetime.now(timezone.utc).isoformat(),
            branch_id,
        ),
    )
    conn.commit()
    return source_path


def import_saved_branch_text(
    conn: sqlite3.Connection,
    raw_text: str,
    *,
    file_name: str | None = None,
) -> tuple[dict[str, Any], Path]:
    document = parse_saved_branch_text(raw_text, file_name=file_name)
    branch_id = document["branch"]["id"]
    path = source_path_for_branch(branch_id)
    write_saved_branch_file(path, document)
    branch = upsert_saved_branch(conn, document, source_path=path)
    return branch, path
