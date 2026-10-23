"""PADOSI API. Vercel runs this FastAPI app as one Python function; files in public/ are served by its CDN."""
import base64
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Local dev: pick up settings from .env (Vercel injects env vars itself). Must run before padosi imports.
_env = Path(__file__).resolve().parent / ".env"
if _env.exists() and not os.environ.get("VERCEL"):
    for _line in _env.read_text().splitlines():
        _key, _sep, _value = _line.partition("=")
        if _sep and not _line.lstrip().startswith("#") and _value.strip():
            os.environ.setdefault(_key.strip(), _value.strip().strip('"'))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from padosi import db, matcher, pricing

STORE_NAME = os.environ.get("STORE_NAME", "Padosi Kirana")
MAX_IMAGE_BYTES = 3_500_000  # Vercel caps request bodies at 4.5 MB; base64 adds a third

app = FastAPI(title="PADOSI")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


# ---------- models ----------

class QuoteRequest(BaseModel):
    text: str = ""
    image_base64: Optional[str] = None
    media_type: Optional[str] = None
    customer_name: str = ""
    customer_phone: str = ""
    started_at: Optional[str] = None


class Line(BaseModel):
    line_id: Optional[str] = None
    raw: str = ""
    catalogue_id: str = ""
    quantity: float = 1
    unit_price: Optional[float] = None
    confidence: float = 1
    note: str = ""
    reviewed: bool = False


class OrderUpdate(BaseModel):
    lines: List[Line]
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None


class CatalogueUpload(BaseModel):
    csv: str
    replace: bool = True


class BillRequest(BaseModel):
    text: str = ""
    image_base64: Optional[str] = None
    media_type: Optional[str] = None


class ManualTiming(BaseModel):
    seconds: Optional[float] = None


class CatalogueEdit(BaseModel):
    name: Optional[str] = None
    aliases: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[float] = None


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "store_name": STORE_NAME,
        "ai": matcher.ai_provider() or "offline",
        "ai_name": matcher.ai_name(),
        "model": matcher.ai_model(),
        "ai_key_from": matcher.ai_key_source(),
        "storage": db.storage_info(),
        "commit": os.environ.get("VERCEL_GIT_COMMIT_SHA", "")[:7] or None,
    }


def _check_image(image_base64, media_type):
    if not image_base64:
        return
    if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(400, "Photos must be JPEG, PNG, WebP or GIF.")
    try:
        size = len(base64.b64decode(image_base64, validate=True))
    except ValueError:
        raise HTTPException(400, "The photo could not be read.")
    if size > MAX_IMAGE_BYTES:
        raise HTTPException(413, "The photo is too large. Try a smaller one.")


@app.post("/api/quote")
def create_quote(req: QuoteRequest):
    text = req.text.strip()
    if not text and not req.image_base64:
        raise HTTPException(400, "Paste a list or add a photo first.")
    _check_image(req.image_base64, req.media_type)

    catalogue = db.list_catalogue()
    try:
        result = matcher.match_list(catalogue, text, req.image_base64, req.media_type, req.customer_name.strip())
    except matcher.MatchError as e:
        raise HTTPException(502, str(e))

    lines, total, flagged = pricing.price_lines(result["items"], catalogue)
    order = {
        "id": uuid.uuid4().hex[:10],
        "created_at": now_iso(),
        "customer_name": req.customer_name.strip(),
        "customer_phone": req.customer_phone.strip(),
        "source_type": "photo" if req.image_base64 else "text",
        "raw_text": text,
        "lines": lines,
        "total": total,
        "flagged_count": flagged,
        "status": "draft",
        "engine": result["engine"],
        "started_at": req.started_at or now_iso(),
        "reply_language": result["language"],
        "reply_opening": result["reply_opening"].strip(),
        "reply_closing": result["reply_closing"].strip(),
    }
    db.create_order(order)
    return db.get_order(order["id"])


def _get_order_or_404(order_id):
    order = db.get_order(order_id)
    if not order:
        raise HTTPException(404, "Order not found.")
    return order


def _reprice(order, update: OrderUpdate):
    lines, total, flagged = pricing.price_lines([l.dict() for l in update.lines], db.list_catalogue())
    fields = {"lines": lines, "total": total, "flagged_count": flagged}
    if update.customer_name is not None:
        fields["customer_name"] = update.customer_name.strip()
    if update.customer_phone is not None:
        fields["customer_phone"] = update.customer_phone.strip()
    return fields


@app.get("/api/orders")
def list_orders():
    return db.list_orders()


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    order = _get_order_or_404(order_id)
    order["message"] = pricing.quote_message(order, STORE_NAME)
    return order


@app.put("/api/orders/{order_id}")
def update_order(order_id: str, update: OrderUpdate):
    order = _get_order_or_404(order_id)
    if order["status"] == "approved":
        raise HTTPException(409, "This quote is already approved.")
    db.update_order(order_id, _reprice(order, update))
    return get_order(order_id)


@app.post("/api/orders/{order_id}/approve")
def approve_order(order_id: str, update: OrderUpdate):
    order = _get_order_or_404(order_id)
    if order["status"] == "approved":
        raise HTTPException(409, "This quote is already approved.")
    fields = _reprice(order, update)
    if not any(l["catalogue_id"] for l in fields["lines"]):
        raise HTTPException(400, "Add at least one item before approving.")

    approved_at = datetime.now(timezone.utc)
    started = _parse_iso(order["started_at"])
    fields.update({
        "status": "approved",
        "approved_at": approved_at.isoformat(),
        "seconds_to_approve": round((approved_at - started).total_seconds(), 1) if started else None,
    })
    stock_moves = {}
    for l in fields["lines"]:
        if l["catalogue_id"]:
            stock_moves[l["catalogue_id"]] = stock_moves.get(l["catalogue_id"], 0) + l["quantity"]
    db.approve_order(order_id, fields, stock_moves)
    return get_order(order_id)


@app.post("/api/orders/{order_id}/manual")
def record_manual_time(order_id: str, body: ManualTiming):
    """Field test: how long the owner took to quote the same list by hand."""
    _get_order_or_404(order_id)
    if body.seconds is not None and not 0 < body.seconds < 3600:
        raise HTTPException(400, "Enter a time between 1 second and 1 hour.")
    db.update_order(order_id, {"manual_seconds": body.seconds})
    return get_order(order_id)


@app.delete("/api/orders/{order_id}")
def delete_order(order_id: str):
    _get_order_or_404(order_id)
    db.delete_order(order_id)
    return {"ok": True}


@app.get("/api/stats")
def stats():
    orders = db.list_orders(limit=1000)
    approved = [o for o in orders if o["status"] == "approved"]
    timed = [o["seconds_to_approve"] for o in approved if o["seconds_to_approve"] is not None]
    # Orders timed both ways: the like-for-like comparison the field test needs.
    paired = [o for o in approved if o["seconds_to_approve"] is not None and o.get("manual_seconds")]
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "orders_total": len(orders),
        "orders_approved": len(approved),
        "approved_today": sum(1 for o in approved if (o["approved_at"] or "").startswith(today)),
        "revenue_approved": round(sum(o["total"] for o in approved), 2),
        "avg_seconds_to_approve": round(sum(timed) / len(timed), 1) if timed else None,
        "under_a_minute": sum(1 for t in timed if t <= 60),
        "lines_total": sum(len(o["lines"]) for o in orders),
        "paired_orders": len(paired),
        "avg_manual_seconds": round(sum(o["manual_seconds"] for o in paired) / len(paired), 1) if paired else None,
        "avg_padosi_seconds_paired": round(sum(o["seconds_to_approve"] for o in paired) / len(paired), 1) if paired else None,
        "lines_flagged_on_arrival": sum(o["flagged_count"] for o in orders if o["status"] == "draft"),
    }


@app.get("/api/catalogue")
def get_catalogue():
    return db.list_catalogue()


@app.get("/api/catalogue.csv")
def download_catalogue():
    return PlainTextResponse(
        db.catalogue_csv(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="padosi-catalogue.csv"'},
    )


@app.post("/api/catalogue/upload")
def upload_catalogue(body: CatalogueUpload):
    try:
        items = db.parse_catalogue_csv(body.csv)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.import_catalogue(items, replace=body.replace)
    return {"imported": len(items), "catalogue": db.list_catalogue()}


@app.post("/api/catalogue/extract")
def extract_catalogue(req: BillRequest):
    """Read a supplier bill or price list into draft catalogue rows for the owner to review."""
    if not req.text.strip() and not req.image_base64:
        raise HTTPException(400, "Paste a price list or add a photo of a bill first.")
    _check_image(req.image_base64, req.media_type)
    catalogue = db.list_catalogue()
    try:
        rows = matcher.extract_catalogue(catalogue, req.text.strip(), req.image_base64, req.media_type)
    except matcher.MatchError as e:
        raise HTTPException(502, str(e))
    by_id = {c["id"]: c for c in catalogue}
    for r in rows:
        current = by_id.get(r["existing_id"])
        r["existing_id"] = r["existing_id"] if current else ""
        r["current_price"] = current["price"] if current else None
        r["current_stock"] = current["stock"] if current else None
    return {"items": rows}


class CatalogueRows(BaseModel):
    items: List[dict]


@app.post("/api/catalogue/import")
def import_rows(body: CatalogueRows):
    """Save reviewed rows: update matched products (adding received stock), add new ones."""
    catalogue = {c["id"]: c for c in db.list_catalogue()}
    taken = set(catalogue)
    items = []
    for r in body.items:
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        try:
            price, stock = float(r.get("price") or 0), float(r.get("stock") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{name}: price and stock must be numbers.")
        current = catalogue.get(r.get("existing_id") or "")
        if current:
            item_id = current["id"]
            stock += current["stock"]
        else:
            base = item_id = db.slug(name) or "ITEM"
            k = 2
            while item_id in taken:
                item_id, k = f"{base}-{k}", k + 1
            taken.add(item_id)
        items.append({
            "id": item_id, "name": name, "aliases": str(r.get("aliases", "")).strip(),
            "category": str(r.get("category", "")).strip(), "unit": str(r.get("unit", "")).strip() or "1 pc",
            "price": price, "stock": stock,
        })
    if not items:
        raise HTTPException(400, "Nothing to import.")
    db.import_catalogue(items, replace=False)
    return {"imported": len(items), "catalogue": db.list_catalogue()}


@app.post("/api/catalogue/reset")
def reset_catalogue():
    db.reset_catalogue()
    return db.list_catalogue()


@app.patch("/api/catalogue/{item_id}")
def edit_catalogue_item(item_id: str, edit: CatalogueEdit):
    db.update_catalogue_item(item_id, {k: v for k, v in edit.dict().items() if v is not None})
    return {"ok": True}


PUBLIC = Path(__file__).resolve().parent / "public"


def _page(name):
    page = PUBLIC / name
    # On Vercel public/ lives on the CDN and may not be bundled with the function.
    return FileResponse(page) if page.exists() else RedirectResponse(f"/{name}")


@app.get("/")
def landing():
    return _page("index.html")


@app.get("/app")
def order_desk():
    return _page("app.html")


# On Vercel the CDN serves public/ before requests reach this app; locally the app serves it.
if not os.environ.get("VERCEL"):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=PUBLIC), name="public")
