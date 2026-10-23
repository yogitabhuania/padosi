"""Storage layer. Postgres when DATABASE_URL (or POSTGRES_URL) is set, SQLite otherwise.

SQL in this module is written with `?` placeholders; they are rewritten to `%s` for Postgres.
"""
import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

def _find_database_url():
    """Return (name, url) of the Postgres connection string to use, or (None, None).

    Vercel's Neon integration may add a prefix (e.g. STORAGE_DATABASE_URL), so after the usual
    names, take any variable holding a postgres:// URL, preferring pooled ones.
    """
    for name in ("DATABASE_URL", "POSTGRES_URL"):
        if os.environ.get(name, "").startswith(("postgres://", "postgresql://")):
            return name, os.environ[name]
    found = [k for k, v in os.environ.items() if v.startswith(("postgres://", "postgresql://"))]
    rank = lambda k: (
        "UNPOOLED" in k or "NON_POOLING" in k,
        not k.endswith("DATABASE_URL"),
        not k.endswith("POSTGRES_URL"),
        k,
    )
    found.sort(key=rank)
    return (found[0], os.environ[found[0]]) if found else (None, None)


DATABASE_URL_NAME, DATABASE_URL = _find_database_url()
ON_VERCEL = bool(os.environ.get("VERCEL"))
# Vercel's filesystem is read-only except /tmp, and /tmp is wiped between cold starts.
SQLITE_PATH = "/tmp/padosi.db" if ON_VERCEL else str(Path(__file__).resolve().parent.parent / "padosi.db")
SAMPLE_CSV = Path(__file__).resolve().parent / "sample_catalogue.csv"

IS_POSTGRES = bool(DATABASE_URL)
CATALOGUE_FIELDS = ["id", "name", "aliases", "category", "unit", "price", "stock"]

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS catalogue (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        unit TEXT NOT NULL DEFAULT '',
        price DOUBLE PRECISION NOT NULL DEFAULT 0,
        stock DOUBLE PRECISION NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        customer_name TEXT NOT NULL DEFAULT '',
        customer_phone TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL DEFAULT 'text',
        raw_text TEXT NOT NULL DEFAULT '',
        lines_json TEXT NOT NULL DEFAULT '[]',
        total DOUBLE PRECISION NOT NULL DEFAULT 0,
        flagged_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        engine TEXT NOT NULL DEFAULT '',
        started_at TEXT,
        approved_at TEXT,
        seconds_to_approve DOUBLE PRECISION
    )""",
]

# Columns added after the first release; created on start-up if an older database lacks them.
ORDER_COLUMNS_ADDED = [
    ("reply_language", "TEXT NOT NULL DEFAULT ''"),
    ("reply_opening", "TEXT NOT NULL DEFAULT ''"),
    ("reply_closing", "TEXT NOT NULL DEFAULT ''"),
    ("manual_seconds", "DOUBLE PRECISION"),
]

_initialised = False


def _connect():
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        # Neon's pooled URL goes through PgBouncer, which doesn't keep prepared statements
        # between transactions, so never let psycopg prepare them.
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False, prepare_threshold=None)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sql(query):
    return query.replace("?", "%s") if IS_POSTGRES else query


@contextmanager
def connection():
    global _initialised
    conn = _connect()
    try:
        if not _initialised:
            _init(conn)
            _initialised = True
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def _init(conn):
    cur = conn.cursor()
    for stmt in SCHEMA:
        cur.execute(stmt)
    if IS_POSTGRES:
        for name, decl in ORDER_COLUMNS_ADDED:
            cur.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {name} {decl}")
    else:
        existing = {r[1] for r in cur.execute("PRAGMA table_info(orders)").fetchall()}
        for name, decl in ORDER_COLUMNS_ADDED:
            if name not in existing:
                cur.execute(f"ALTER TABLE orders ADD COLUMN {name} {decl}")
    cur.execute("SELECT COUNT(*) AS n FROM catalogue")
    if dict(cur.fetchone())["n"] == 0:
        _write_catalogue(cur, parse_catalogue_csv(SAMPLE_CSV.read_text(encoding="utf-8")), replace=True)


def storage_info():
    if IS_POSTGRES:
        return {"engine": "postgres", "persistent": True, "source": DATABASE_URL_NAME}
    return {"engine": "sqlite", "persistent": not ON_VERCEL}


# ---------- catalogue ----------

def parse_catalogue_csv(text):
    """Parse an owner's CSV. Needs at least name and price columns; everything else is optional."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise ValueError("The CSV is empty.")
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    if "name" not in headers or "price" not in headers:
        raise ValueError("The CSV needs at least 'name' and 'price' columns.")

    items, seen = [], set()
    for n, row in enumerate(reader, start=2):
        get = lambda key: (row.get(headers[key]) or "").strip() if key in headers else ""
        name = get("name")
        if not name:
            continue
        try:
            price = float(get("price").replace("₹", "").replace(",", "") or 0)
            stock = float(get("stock") or 0)
        except ValueError:
            raise ValueError(f"Row {n}: price and stock must be numbers.")
        item_id = get("id") or slug(name)
        base, k = item_id, 2
        while item_id in seen:
            item_id, k = f"{base}-{k}", k + 1
        seen.add(item_id)
        items.append({
            "id": item_id, "name": name, "aliases": get("aliases"), "category": get("category"),
            "unit": get("unit") or "1 pc", "price": price, "stock": stock,
        })
    if not items:
        raise ValueError("No items found in the CSV.")
    return items


def slug(name):
    out = "".join(c if c.isalnum() else "-" for c in name.upper())
    return "-".join(p for p in out.split("-") if p)[:40]


def _write_catalogue(cur, items, replace):
    if replace:
        cur.execute("DELETE FROM catalogue")
    upsert = _sql(
        "INSERT INTO catalogue (id, name, aliases, category, unit, price, stock) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET name = excluded.name, aliases = excluded.aliases, "
        "category = excluded.category, unit = excluded.unit, price = excluded.price, stock = excluded.stock"
    )
    for it in items:
        cur.execute(upsert, [it[f] for f in CATALOGUE_FIELDS])


def list_catalogue():
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM catalogue ORDER BY category, name")
        return _rows(cur)


def import_catalogue(items, replace):
    with connection() as conn:
        _write_catalogue(conn.cursor(), items, replace)


def reset_catalogue():
    import_catalogue(parse_catalogue_csv(SAMPLE_CSV.read_text(encoding="utf-8")), replace=True)


def update_catalogue_item(item_id, fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "aliases", "category", "unit", "price", "stock")}
    if not allowed:
        return
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with connection() as conn:
        conn.cursor().execute(_sql(f"UPDATE catalogue SET {sets} WHERE id = ?"), [*allowed.values(), item_id])


def catalogue_csv():
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CATALOGUE_FIELDS)
    writer.writeheader()
    for it in list_catalogue():
        writer.writerow({f: it[f] for f in CATALOGUE_FIELDS})
    return out.getvalue()


# ---------- orders ----------

def _order_from_row(row):
    row = dict(row)
    row["lines"] = json.loads(row.pop("lines_json") or "[]")
    return row


def create_order(order):
    with connection() as conn:
        conn.cursor().execute(
            _sql(
                "INSERT INTO orders (id, created_at, customer_name, customer_phone, source_type, raw_text, "
                "lines_json, total, flagged_count, status, engine, started_at, reply_language, reply_opening, "
                "reply_closing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                order["id"], order["created_at"], order["customer_name"], order["customer_phone"],
                order["source_type"], order["raw_text"], json.dumps(order["lines"]), order["total"],
                order["flagged_count"], order["status"], order["engine"], order["started_at"],
                order["reply_language"], order["reply_opening"], order["reply_closing"],
            ],
        )


def get_order(order_id):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(_sql("SELECT * FROM orders WHERE id = ?"), [order_id])
        row = cur.fetchone()
        return _order_from_row(row) if row else None


def list_orders(limit=100):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(_sql("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?"), [limit])
        return [_order_from_row(r) for r in cur.fetchall()]


def update_order(order_id, fields):
    fields = dict(fields)
    if "lines" in fields:
        fields["lines_json"] = json.dumps(fields.pop("lines"))
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connection() as conn:
        conn.cursor().execute(_sql(f"UPDATE orders SET {sets} WHERE id = ?"), [*fields.values(), order_id])


def approve_order(order_id, fields, stock_moves):
    """Mark an order approved and take its items out of stock, in one transaction."""
    fields = dict(fields)
    fields["lines_json"] = json.dumps(fields.pop("lines"))
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(_sql(f"UPDATE orders SET {sets} WHERE id = ?"), [*fields.values(), order_id])
        greatest = "GREATEST" if IS_POSTGRES else "MAX"
        for item_id, qty in stock_moves.items():
            cur.execute(_sql(f"UPDATE catalogue SET stock = {greatest}(stock - ?, 0) WHERE id = ?"), [qty, item_id])


def delete_order(order_id):
    with connection() as conn:
        conn.cursor().execute(_sql("DELETE FROM orders WHERE id = ?"), [order_id])
