"""Step 3 of the PADOSI journey: a plain script prices the quote and checks stock. No AI touches money."""
import uuid

LOW_CONFIDENCE = 0.75


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def price_lines(lines, catalogue):
    """Take matched or owner-edited lines and return (priced_lines, total, flagged_count).

    A line may carry `unit_price` when the owner overrode the catalogue price.
    """
    by_id = {c["id"]: c for c in catalogue}
    priced, total, flagged = [], 0.0, 0

    for line in lines:
        item = by_id.get(line.get("catalogue_id") or "")
        qty = max(_num(line.get("quantity"), 1), 0)
        confidence = _num(line.get("confidence"), 1)
        override = line.get("unit_price")
        unit_price = _num(override) if override not in (None, "") else (item["price"] if item else 0.0)

        flags = []
        if not item:
            flags.append("unmatched")
        else:
            if item["stock"] <= 0:
                flags.append("out_of_stock")
            elif item["stock"] < qty:
                flags.append("low_stock")
        if item and confidence < LOW_CONFIDENCE and not line.get("reviewed"):
            flags.append("low_confidence")

        line_total = round(qty * unit_price, 2) if item else 0.0
        total += line_total
        if flags:
            flagged += 1
        priced.append({
            "line_id": line.get("line_id") or uuid.uuid4().hex[:8],
            "raw": line.get("raw", ""),
            "catalogue_id": item["id"] if item else "",
            "name": item["name"] if item else "",
            "unit": item["unit"] if item else "",
            "quantity": round(qty, 3),
            "unit_price": round(unit_price, 2),
            "catalogue_price": item["price"] if item else None,
            "stock": item["stock"] if item else None,
            "line_total": line_total,
            "confidence": round(confidence, 2),
            "note": line.get("note", ""),
            "reviewed": bool(line.get("reviewed")),
            "flags": flags,
        })
    return priced, round(total, 2), flagged


def _fmt_qty(q):
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


def _fmt_money(x):
    return f"₹{int(x):,}" if float(x).is_integer() else f"₹{x:,.2f}"


def quote_message(order, store_name):
    """The WhatsApp text the owner copies and sends back to the shopper."""
    lines = [l for l in order["lines"] if l["catalogue_id"]]
    # Claude drafts the opening and closing in the shopper's language; the item lines and totals
    # below are always produced by this script.
    opening = order.get("reply_opening") or (
        f"Namaskara {order['customer_name']}!" if order.get("customer_name") else "Namaskara!")
    closing = order.get("reply_closing") or "Reply YES to confirm. We will pack it right away."
    out = [opening, f"*{store_name}*", ""]
    for i, l in enumerate(lines, 1):
        out.append(f"{i}. {l['name']} ({l['unit']}) x {_fmt_qty(l['quantity'])} = {_fmt_money(l['line_total'])}")
    out += ["", f"*Total: {_fmt_money(order['total'])}*"]
    missing = [l["raw"] for l in order["lines"] if not l["catalogue_id"] and l["raw"]]
    if missing:
        out += ["", "Not available today: " + ", ".join(missing)]
    out += ["", closing]
    return "\n".join(out)
