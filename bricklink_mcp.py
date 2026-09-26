# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp>=1.2,<2",
#   "requests>=2.31",
#   "requests-oauthlib>=1.3",
# ]
# ///
"""BrickLink Store API MCP server (local, stdio).

Runs on your own computer so your BrickLink API keys never leave it.
Credentials come from environment variables:

    BRICKLINK_CONSUMER_KEY, BRICKLINK_CONSUMER_SECRET,
    BRICKLINK_TOKEN_VALUE,  BRICKLINK_TOKEN_SECRET

Writes (changing prices, quantities, stockroom, descriptions, creating lots)
are OFF unless BRICKLINK_ALLOW_WRITES=true. There is no delete tool and no
order-status or feedback tool on purpose.

Test your keys from a terminal:   uv run bricklink_mcp.py --check
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests
from requests_oauthlib import OAuth1

BASE = "https://api.bricklink.com/api/store/v1"
ITEM_TYPES = {
    "PART", "SET", "MINIFIG", "BOOK", "GEAR", "CATALOG",
    "INSTRUCTION", "UNSORTED_LOT", "ORIGINAL_BOX",
}
_TYPE_ALIASES = {"P": "PART", "S": "SET", "M": "MINIFIG", "B": "BOOK", "G": "GEAR",
                 "C": "CATALOG", "I": "INSTRUCTION", "O": "ORIGINAL_BOX"}
_ENV = ("BRICKLINK_CONSUMER_KEY", "BRICKLINK_CONSUMER_SECRET",
        "BRICKLINK_TOKEN_VALUE", "BRICKLINK_TOKEN_SECRET")


class BrickLinkError(RuntimeError):
    pass


def _auth() -> OAuth1:
    missing = [k for k in _ENV if not os.environ.get(k)]
    if missing:
        raise BrickLinkError("Missing environment variables: " + ", ".join(missing))
    return OAuth1(
        os.environ["BRICKLINK_CONSUMER_KEY"],
        client_secret=os.environ["BRICKLINK_CONSUMER_SECRET"],
        resource_owner_key=os.environ["BRICKLINK_TOKEN_VALUE"],
        resource_owner_secret=os.environ["BRICKLINK_TOKEN_SECRET"],
        signature_method="HMAC-SHA1",
    )


def _writes_allowed() -> bool:
    return os.environ.get("BRICKLINK_ALLOW_WRITES", "false").strip().lower() in {"1", "true", "yes"}


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        r = requests.request(method, BASE + path, params=params, json=body, auth=_auth(), timeout=30)
    except requests.RequestException as e:
        raise BrickLinkError(f"Network error calling BrickLink: {e}") from e
    try:
        payload = r.json()
    except ValueError:
        raise BrickLinkError(f"BrickLink returned HTTP {r.status_code} with a non-JSON body")
    meta = payload.get("meta", {})
    code = int(meta.get("code", r.status_code))
    if not 200 <= code < 300:
        msg = f"BrickLink error {code}: {meta.get('message', '')} {meta.get('description', '')}".strip()
        if "IP" in msg.upper() and "MISMATCH" in msg.upper():
            msg += " (the access token is bound to a different IP address; update it on BrickLink's API page)"
        raise BrickLinkError(msg)
    return payload.get("data")


def _type(t: str) -> str:
    t = t.strip().upper()
    t = _TYPE_ALIASES.get(t, t)
    if t not in ITEM_TYPES:
        raise BrickLinkError(f"Unknown item type '{t}'. Use one of: {', '.join(sorted(ITEM_TYPES))}")
    return t


_colors: dict[int, str] | None = None


def _color_name(color_id: Any) -> str | None:
    global _colors
    if color_id in (None, 0):
        return None
    if _colors is None:
        try:
            _colors = {c["color_id"]: c["color_name"] for c in _call("GET", "/colors")}
        except BrickLinkError:
            _colors = {}
    return _colors.get(int(color_id))


def _lot(inv: dict) -> dict:
    item = inv.get("item", {})
    out = {
        "inventory_id": inv.get("inventory_id"),
        "type": item.get("type"),
        "no": item.get("no"),
        "name": item.get("name"),
        "color": inv.get("color_name") or _color_name(inv.get("color_id")),
        "color_id": inv.get("color_id"),
        "qty": inv.get("quantity"),
        "price": inv.get("unit_price"),
        "cond": inv.get("new_or_used"),
        "stockroom": inv.get("is_stock_room", False),
    }
    if inv.get("completeness"):
        out["completeness"] = inv["completeness"]
    if inv.get("stock_room_id"):
        out["stockroom_id"] = inv["stock_room_id"]
    return out


# ---------------------------------------------------------------- tools

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("bricklink")


@mcp.tool()
def get_catalog_item(item_type: str, item_no: str) -> dict:
    """Look up a BrickLink catalog item. item_type: PART, SET, MINIFIG, GEAR, BOOK, INSTRUCTION, etc.
    Set numbers need the -1 suffix (e.g. 21322-1)."""
    d = _call("GET", f"/items/{_type(item_type)}/{item_no}")
    return {k: d.get(k) for k in ("no", "name", "type", "category_id", "year_released",
                                  "weight", "dim_x", "dim_y", "dim_z", "is_obsolete")}


@mcp.tool()
def get_part_out(item_type: str, item_no: str, break_minifigs: bool = False,
                 include_extras: bool = True) -> dict:
    """Full parts inventory (part-out) of a set or minifig, as a compact list.
    Each row: no, name, color, qty, extra (spare) qty; alternates/counterparts are flagged."""
    data = _call("GET", f"/items/{_type(item_type)}/{item_no}/subsets",
                 {"break_minifigs": str(break_minifigs).lower()})
    rows = []
    for group in data or []:
        for e in group.get("entries", []):
            it = e.get("item", {})
            row = {"type": it.get("type"), "no": it.get("no"), "name": it.get("name"),
                   "color": _color_name(e.get("color_id")), "color_id": e.get("color_id"),
                   "qty": e.get("quantity")}
            if include_extras and e.get("extra_quantity"):
                row["extra"] = e["extra_quantity"]
            if e.get("is_alternate"):
                row["alternate"] = True
            if e.get("is_counterpart"):
                row["counterpart"] = True
            rows.append(row)
    return {"item": f"{_type(item_type)} {item_no}", "lots": len(rows),
            "pieces": sum((r.get("qty") or 0) for r in rows), "parts": rows}


@mcp.tool()
def get_price_guide(item_type: str, item_no: str, color_id: int | None = None,
                    guide_type: str = "sold", new_or_used: str = "U",
                    country_code: str | None = None, currency_code: str = "USD") -> dict:
    """Price guide summary. guide_type: 'sold' (last 6 months sales) or 'stock' (currently for sale).
    new_or_used: 'N' or 'U'. For sets the price guide is not split by completeness."""
    d = _call("GET", f"/items/{_type(item_type)}/{item_no}/price", {
        "color_id": color_id, "guide_type": guide_type, "new_or_used": new_or_used.upper(),
        "country_code": country_code, "currency_code": currency_code})
    return {k: d.get(k) for k in ("new_or_used", "currency_code", "min_price", "avg_price",
                                  "qty_avg_price", "max_price", "unit_quantity", "total_quantity")} | {
        "item": f"{_type(item_type)} {item_no}", "guide_type": guide_type}


@mcp.tool()
def list_inventory(search: str | None = None, item_type: str | None = None,
                   stockroom: str = "any", limit: int = 100) -> dict:
    """List lots in your BrickLink store. search matches item number or name (case-insensitive).
    stockroom: 'any', 'only' (just stockroom lots) or 'exclude' (just lots for sale)."""
    data = _call("GET", "/inventories", {"item_type": _type(item_type) if item_type else None})
    lots = [_lot(i) for i in data or []]
    if search:
        s = search.lower()
        lots = [l for l in lots if s in str(l["no"]).lower() or s in str(l["name"]).lower()]
    if stockroom == "only":
        lots = [l for l in lots if l["stockroom"]]
    elif stockroom == "exclude":
        lots = [l for l in lots if not l["stockroom"]]
    return {"matching_lots": len(lots), "shown": min(len(lots), limit), "lots": lots[:limit]}


@mcp.tool()
def get_inventory(inventory_id: int) -> dict:
    """One store lot in full, including description and remarks."""
    inv = _call("GET", f"/inventories/{inventory_id}")
    out = _lot(inv)
    out.update({k: inv.get(k) for k in ("description", "remarks", "bulk", "is_retain", "date_created",
                                        "my_cost", "sale_rate")})
    return out


@mcp.tool()
def list_orders(direction: str = "in", status: str | None = None, limit: int = 50) -> dict:
    """Orders received (direction='in') or placed (direction='out'). status is BrickLink's
    comma-separated status filter, e.g. 'PAID,PACKED' or '-PURGED,-COMPLETED'."""
    data = _call("GET", "/orders", {"direction": direction, "status": status})
    orders = [{
        "order_id": o.get("order_id"), "date": o.get("date_ordered"), "status": o.get("status"),
        "buyer": o.get("buyer_name"), "seller": o.get("seller_name"),
        "items": o.get("total_count"), "lots": o.get("unique_count"),
        "total": (o.get("cost") or {}).get("grand_total"),
        "currency": (o.get("cost") or {}).get("currency_code"),
        "payment": (o.get("payment") or {}).get("status"),
    } for o in data or []]
    return {"count": len(orders), "orders": orders[:limit]}


@mcp.tool()
def get_order(order_id: int, include_items: bool = True) -> dict:
    """One order with shipping/cost summary and (optionally) its items."""
    o = _call("GET", f"/orders/{order_id}")
    out = {k: o.get(k) for k in ("order_id", "date_ordered", "status", "buyer_name", "seller_name",
                                 "total_count", "unique_count", "remarks")}
    out["cost"] = o.get("cost")
    out["payment_status"] = (o.get("payment") or {}).get("status")
    ship = o.get("shipping") or {}
    out["shipping"] = {"method": ship.get("method"), "tracking_no": ship.get("tracking_no"),
                       "date_shipped": ship.get("date_shipped")}
    if include_items:
        batches = _call("GET", f"/orders/{order_id}/items") or []
        out["items"] = [{"no": (i.get("item") or {}).get("no"), "name": (i.get("item") or {}).get("name"),
                         "color": i.get("color_name") or _color_name(i.get("color_id")),
                         "qty": i.get("quantity"), "cond": i.get("new_or_used"),
                         "price": i.get("unit_price_final") or i.get("unit_price")}
                        for batch in batches for i in batch]
    return out


@mcp.tool()
def find_color(name: str) -> list[dict]:
    """Find BrickLink color IDs by name, e.g. 'reddish brown' -> 88."""
    _color_name(1)
    n = name.lower()
    return [{"color_id": cid, "color": cname} for cid, cname in (_colors or {}).items() if n in cname.lower()]


@mcp.tool()
def review_pricing(search: str | None = None, item_type: str | None = None, include_stockroom: bool = True,
                   threshold_pct: float = 20.0, currency_code: str = "USD", max_lots: int = 50) -> dict:
    """Compare your store lots' prices with BrickLink's last-6-months sold average for the same item,
    color and condition. Flags lots more than threshold_pct above or below the market.
    One price-guide call per lot, so large stores should narrow with search/item_type or max_lots.
    Note: set price guides don't separate complete from incomplete sets."""
    data = _call("GET", "/inventories", {"item_type": _type(item_type) if item_type else None})
    lots = [_lot(i) for i in data or []]
    if search:
        s = search.lower()
        lots = [l for l in lots if s in str(l["no"]).lower() or s in str(l["name"]).lower()]
    if not include_stockroom:
        lots = [l for l in lots if not l["stockroom"]]
    skipped = max(0, len(lots) - max_lots)
    rows, cache = [], {}
    for l in lots[:max_lots]:
        key = (l["type"], l["no"], l["color_id"], l["cond"])
        if key not in cache:
            try:
                pg = _call("GET", f"/items/{l['type']}/{l['no']}/price", {
                    "color_id": l["color_id"] or None, "guide_type": "sold",
                    "new_or_used": l["cond"], "currency_code": currency_code})
                cache[key] = pg
            except BrickLinkError as e:
                cache[key] = {"error": str(e)}
        pg = cache[key]
        price = float(l["price"] or 0)
        avg = float(pg.get("qty_avg_price") or pg.get("avg_price") or 0) if "error" not in pg else 0
        row = {"inventory_id": l["inventory_id"], "item": f"{l['no']} {l['name']}", "color": l["color"],
               "cond": l["cond"], "stockroom": l["stockroom"], "your_price": round(price, 2),
               "sold_avg": round(avg, 2) if avg else None,
               "sold_min": pg.get("min_price"), "sold_max": pg.get("max_price"),
               "sales_count": pg.get("unit_quantity")}
        if "error" in pg:
            row["verdict"] = "price guide unavailable"
        elif not avg or not pg.get("unit_quantity"):
            row["verdict"] = "no recent sales"
        else:
            diff = (price - avg) / avg * 100
            row["diff_pct"] = round(diff, 1)
            row["verdict"] = ("above market" if diff > threshold_pct else
                              "below market" if diff < -threshold_pct else "in line")
        rows.append(row)
    rows.sort(key=lambda r: -abs(r.get("diff_pct", 0)))
    out = {"lots_reviewed": len(rows), "flagged": sum(r["verdict"] in ("above market", "below market") for r in rows),
           "threshold_pct": threshold_pct, "lots": rows}
    if skipped:
        out["not_reviewed"] = skipped
    return out


@mcp.tool()
def draft_set_listing(set_no: str, new_or_used: str = "U", complete: bool = True, has_box: bool = True,
                      box_notes: str | None = None, has_instructions: bool = True,
                      minifigs_included: bool = True, missing: str | None = None,
                      notes: str | None = None, currency_code: str = "USD") -> dict:
    """Draft a listing for a set you're about to sell: pulls the catalog name, recent sold and current
    for-sale prices, suggests a price, and writes a description. Nothing is created on BrickLink;
    review the draft, then create it with create_inventory (it goes to the stockroom by default).
    set_no needs the -1 suffix (e.g. 21322-1). Only states what you pass in - no assumed claims."""
    item = _call("GET", f"/items/SET/{set_no}")
    cond = new_or_used.upper()
    sold = _call("GET", f"/items/SET/{set_no}/price", {"guide_type": "sold", "new_or_used": cond,
                                                       "currency_code": currency_code})
    stock = _call("GET", f"/items/SET/{set_no}/price", {"guide_type": "stock", "new_or_used": cond,
                                                        "currency_code": currency_code})
    sold_avg = float(sold.get("qty_avg_price") or sold.get("avg_price") or 0)
    stock_min = float(stock.get("min_price") or 0)
    suggested = sold_avg if sold_avg else stock_min
    if not complete and suggested:
        suggested *= 0.85
    parts = [f"{set_no} {item.get('name')}."]
    if has_box:
        parts.append(f"Includes Box{' - ' + box_notes if box_notes else ''}.")
    else:
        parts.append("No box.")
    parts.append("Includes Instructions." if has_instructions else "No instructions.")
    if complete:
        parts.append("Complete" + (" with Minifigs." if minifigs_included else "."))
    else:
        parts.append(f"Incomplete - missing: {missing or '(list what is missing)'}.")
    if notes:
        parts.append(notes.strip().rstrip(".") + ".")
    return {
        "item": f"SET {set_no} {item.get('name')}", "year": item.get("year_released"),
        "condition": cond, "completeness": "C" if complete else "B",
        "sold_6mo": {k: sold.get(k) for k in ("min_price", "qty_avg_price", "max_price", "unit_quantity")},
        "for_sale_now": {k: stock.get(k) for k in ("min_price", "qty_avg_price", "max_price", "unit_quantity")},
        "suggested_price": round(suggested, 2) if suggested else None,
        "price_basis": ("6-month sold average" if sold_avg else "lowest current listing" if stock_min else "no data")
                       + (", reduced 15% for incomplete" if (not complete and suggested) else ""),
        "description": " ".join(parts),
        "next_step": "Review, then create_inventory(item_type='SET', item_no=..., quantity=1, unit_price=..., "
                     "new_or_used=..., completeness=..., description=...). Needs BRICKLINK_ALLOW_WRITES=true.",
    }


_XML_TYPE = {"PART": "P", "SET": "S", "MINIFIG": "M", "BOOK": "B", "GEAR": "G",
             "CATALOG": "C", "INSTRUCTION": "I", "ORIGINAL_BOX": "O"}


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


@mcp.tool()
def build_wanted_list_xml(parts: list[dict] | None = None, from_item: str | None = None,
                          from_item_type: str = "SET", condition: str | None = None,
                          include_extras: bool = False, remarks: str | None = None,
                          save_as: str | None = None) -> dict:
    """Build a BrickLink wanted-list upload (XML) to paste into Wanted > Upload > 'Upload BrickLink XML format'.
    Give either `parts` - a list like [{"no": "3023", "color_id": 88, "qty": 2, "type": "PART",
    "condition": "U", "max_price": 0.10, "remarks": "..."}] (type defaults to PART) - or `from_item`
    (e.g. "21322-1") to list a whole set/minifig part-out. Duplicate part+color rows are merged.
    condition 'N' or 'U' applies to every row that doesn't set its own (omit for any condition).
    save_as writes the XML to ~/bricklink-mcp/wanted/<save_as>.xml on this computer.
    This only builds the file; it does not change anything on BrickLink."""
    rows: list[dict] = []
    if from_item:
        data = _call("GET", f"/items/{_type(from_item_type)}/{from_item}/subsets", {"break_minifigs": "false"})
        for group in data or []:
            for e in group.get("entries", []):
                if e.get("is_alternate") or e.get("is_counterpart"):
                    continue
                it = e.get("item", {})
                qty = (e.get("quantity") or 0) + ((e.get("extra_quantity") or 0) if include_extras else 0)
                rows.append({"type": it.get("type", "PART"), "no": it.get("no"),
                             "color_id": e.get("color_id", 0), "qty": qty})
    for p in parts or []:
        if not p.get("no"):
            raise BrickLinkError(f"Part row is missing 'no': {p}")
        rows.append(p)
    if not rows:
        raise BrickLinkError("Nothing to build: pass `parts` or `from_item`.")

    merged: dict[tuple, dict] = {}
    for r in rows:
        t = _type(r.get("type", "PART"))
        if t not in _XML_TYPE:
            raise BrickLinkError(f"Item type {t} can't go on a wanted list.")
        key = (t, str(r["no"]), int(r.get("color_id") or 0), (r.get("condition") or condition or "").upper())
        if key in merged:
            merged[key]["qty"] += int(r.get("qty") or 1)
        else:
            merged[key] = {"qty": int(r.get("qty") or 1), "max_price": r.get("max_price"),
                           "remarks": r.get("remarks") or remarks}

    lines = ["<INVENTORY>"]
    for (t, no, color, cond), v in merged.items():
        item = [f"<ITEMTYPE>{_XML_TYPE[t]}</ITEMTYPE>", f"<ITEMID>{_xml_escape(no)}</ITEMID>"]
        if color:
            item.append(f"<COLOR>{color}</COLOR>")
        item.append(f"<MINQTY>{v['qty']}</MINQTY>")
        if cond in ("N", "U"):
            item.append(f"<CONDITION>{cond}</CONDITION>")
        if v["max_price"] is not None:
            item.append(f"<MAXPRICE>{float(v['max_price']):.4f}</MAXPRICE>")
        if v["remarks"]:
            item.append(f"<REMARKS>{_xml_escape(v['remarks'])}</REMARKS>")
        item.append("<NOTIFY>N</NOTIFY>")
        lines.append("<ITEM>" + "".join(item) + "</ITEM>")
    lines.append("</INVENTORY>")
    xml = "\n".join(lines)

    out = {"lots": len(merged), "pieces": sum(v["qty"] for v in merged.values()), "xml": xml}
    if save_as:
        import re
        from pathlib import Path
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", save_as).strip("._") or "wanted"
        folder = Path.home() / "bricklink-mcp" / "wanted"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.xml"
        path.write_text(xml, encoding="utf-8")
        out["saved_to"] = str(path)
    return out


def _require_writes() -> None:
    if not _writes_allowed():
        raise BrickLinkError("Writes are disabled. Set BRICKLINK_ALLOW_WRITES=true in the server's "
                             "environment to allow inventory changes.")


@mcp.tool()
def update_inventory(inventory_id: int, unit_price: float | None = None, quantity_change: int | None = None,
                     description: str | None = None, remarks: str | None = None,
                     stockroom: bool | None = None, stockroom_id: str | None = None,
                     completeness: str | None = None, new_or_used: str | None = None) -> dict:
    """Change one store lot. Only the fields you pass are changed.
    quantity_change is relative (+2 adds two, -1 removes one). stockroom=True hides the lot from buyers,
    False puts it back up for sale. completeness for sets: 'C' complete, 'B' incomplete, 'S' sealed.
    Requires BRICKLINK_ALLOW_WRITES=true."""
    _require_writes()
    body: dict[str, Any] = {}
    if unit_price is not None:
        body["unit_price"] = f"{unit_price:.4f}"
    if quantity_change:
        body["quantity"] = f"{quantity_change:+d}"
    if description is not None:
        body["description"] = description
    if remarks is not None:
        body["remarks"] = remarks
    if stockroom is not None:
        body["is_stock_room"] = stockroom
    if stockroom_id is not None:
        body["stock_room_id"] = stockroom_id
    if completeness is not None:
        body["completeness"] = completeness.upper()
    if new_or_used is not None:
        body["new_or_used"] = new_or_used.upper()
    if not body:
        raise BrickLinkError("Nothing to change.")
    return _lot(_call("PUT", f"/inventories/{inventory_id}", body=body))


@mcp.tool()
def create_inventory(item_type: str, item_no: str, quantity: int, unit_price: float, new_or_used: str,
                     color_id: int = 0, completeness: str | None = None, description: str | None = None,
                     remarks: str | None = None, stockroom: bool = True) -> dict:
    """Create a new store lot. New lots go to the stockroom by default so nothing is listed
    for sale until you review it. Requires BRICKLINK_ALLOW_WRITES=true."""
    _require_writes()
    body: dict[str, Any] = {
        "item": {"no": item_no, "type": _type(item_type)}, "color_id": color_id,
        "quantity": quantity, "unit_price": f"{unit_price:.4f}", "new_or_used": new_or_used.upper(),
        "is_stock_room": stockroom, "is_retain": False,
    }
    if completeness:
        body["completeness"] = completeness.upper()
    if description:
        body["description"] = description
    if remarks:
        body["remarks"] = remarks
    return _lot(_call("POST", "/inventories", body=body))


def _check() -> int:
    try:
        colors = _call("GET", "/colors")
        lots = _call("GET", "/inventories") or []
    except BrickLinkError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    stock = sum(1 for l in lots if l.get("is_stock_room"))
    print(f"OK: connected to BrickLink. {len(colors)} colors in catalog; your store has {len(lots)} lots "
          f"({stock} in stockroom). Writes {'ENABLED' if _writes_allowed() else 'disabled'}.")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())
    mcp.run()
