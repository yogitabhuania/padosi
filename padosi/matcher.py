"""Step 2 of the PADOSI journey: read a messy shopper list and match each line to the catalogue.

Claude does the reading and matching. It never prices anything; pricing.py does the money.
Without an API key, a simple fuzzy matcher stands in so the app still works for typed lists.
"""
import difflib
import json
import os
import re

MODEL = os.environ.get("PADOSI_MODEL", "claude-opus-5-5")

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw": {"type": "string", "description": "The line exactly as the shopper wrote it"},
                    "catalogue_id": {"type": "string", "description": "Matching catalogue id, or empty string"},
                    "quantity": {"type": "number", "description": "Quantity in the catalogue item's unit"},
                    "confidence": {"type": "number", "description": "0 to 1"},
                    "note": {"type": "string", "description": "Short reason when unsure, else empty"},
                },
                "required": ["raw", "catalogue_id", "quantity", "confidence", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You read grocery lists that shoppers send to a neighbourhood kirana store in Bengaluru and match each requested item to the store's catalogue.

Lists mix Kannada, Hindi and English, often transliterated into Latin script or written in Kannada/Devanagari script, with misspellings, brand shorthand and handwriting. Use the catalogue's names and aliases, and your own knowledge of Indian groceries, to find the right item.

For every item the shopper asked for, return one entry:
- raw: the shopper's line (or the relevant fragment if one line names several items), copied as written.
- catalogue_id: the id of the best-matching catalogue item, or "" if nothing in the catalogue is a reasonable match.
- quantity: how many of the catalogue item's unit to supply. Convert the shopper's amount into that unit, e.g. "250g jeera" against a "100 g" unit is 2.5; "2 kg tomato" against a "1 kg" unit is 2; "rice 10kg" against a "5 kg bag" is 2. If no amount is given, use 1.
- confidence: how sure you are about both the item and the quantity, from 0 to 1. Use 0.9 or above only when the match is clear. Go below 0.75 when the item is ambiguous (for example "Amul 500" could be 500 ml milk or 500 g butter), the handwriting is unclear, the pack size differs a lot from what was asked, or there is no match.
- note: when confidence is below 0.75, a few words the store owner can act on, e.g. "could be milk or butter". Otherwise "".

Ignore greetings, names, addresses and anything that is not an item. Do not invent items the shopper did not ask for, and never calculate prices."""


def ai_enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _catalogue_block(catalogue):
    lines = ["id | name | aliases | unit"]
    lines += [f"{c['id']} | {c['name']} | {c['aliases']} | {c['unit']}" for c in catalogue]
    return "Store catalogue:\n" + "\n".join(lines)


class MatchError(Exception):
    pass


def match_with_claude(catalogue, text, image_b64=None, media_type=None):
    import anthropic

    content = []
    if image_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}})
        content.append({"type": "text", "text": "This photo is the shopper's list." + (
            f" The owner also typed:\n{text}" if text else "")})
    else:
        content.append({"type": "text", "text": f"Shopper's list:\n{text}"})

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            # The catalogue changes rarely, so cache it across quotes.
            system=[
                {"type": "text", "text": INSTRUCTIONS},
                {"type": "text", "text": _catalogue_block(catalogue), "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ITEM_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise MatchError("The Claude API key is invalid. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise MatchError("Claude is rate limited right now. Try again in a minute.")
    except anthropic.BadRequestError as e:
        raise MatchError(f"Claude could not read this list: {e.message}")
    except anthropic.APIStatusError as e:
        raise MatchError(f"Claude returned an error ({e.status_code}). Try again.")
    except anthropic.APIConnectionError:
        raise MatchError("Could not reach Claude. Check the internet connection.")

    if response.stop_reason == "refusal":
        raise MatchError("Claude declined to read this list.")
    if response.stop_reason == "max_tokens":
        raise MatchError("The list is too long to read in one go. Split it into two quotes.")
    text_out = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text_out)["items"]
    except (ValueError, KeyError):
        raise MatchError("Claude returned an unreadable answer. Try again.")


# ---------- offline fallback ----------

_QTY = re.compile(
    r"(?P<num>\d+(?:\.\d+)?|half|one|two|three|four|five|six|ten|dozen)\s*"
    r"(?P<unit>kgs?|kilo|kg\.|g|gm|gms|grams?|l|ltr|litres?|liters?|ml|pcs?|pieces?|packets?|pkts?|dozen|bunch(?:es)?|x)?\b",
    re.I,
)
# Greetings and chit-chat that the offline matcher should skip rather than force onto an item.
_CHATTER = re.compile(
    r"^(hi|hello|hey|namaskara|namaste|namaskar|thanks|thank you|thx|ok|bhaiya|anna|pls|please)\b", re.I
)
_WORD_NUM = {"half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "dozen": 12}


def _to_base(num, unit):
    """Return (amount, dimension) with weights in grams, volumes in ml, counts in pieces."""
    unit = (unit or "").lower().rstrip(".")
    if unit.startswith(("kg", "kilo")):
        return num * 1000, "g"
    if unit in ("g", "gm", "gms") or unit.startswith("gram"):
        return num, "g"
    if unit in ("l", "ltr") or unit.startswith(("litre", "liter")):
        return num * 1000, "ml"
    if unit == "ml":
        return num, "ml"
    if unit == "dozen":
        return num * 12, "pc"
    return num, "pc"


def _unit_size(unit_text):
    m = _QTY.search(unit_text or "")
    if not m:
        return 1, "pc"
    num = _WORD_NUM.get(m.group("num").lower()) or float(m.group("num"))
    return _to_base(num, m.group("unit"))


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def match_offline(catalogue, text):
    entries = []
    for c in catalogue:
        names = [c["name"]] + [a for a in c["aliases"].split(";") if a.strip()]
        entries.append((c, [" ".join(_norm(n)) for n in names]))

    results = []
    for raw in re.split(r"[\n,]+", text):
        raw = raw.strip(" -*•\t")
        if not raw or len(raw) < 2:
            continue
        m = _QTY.search(raw)
        words = " ".join(_norm(_QTY.sub(" ", raw)))
        if not words or _CHATTER.search(words):
            continue

        best, best_score = None, 0.0
        for c, names in entries:
            for n in names:
                score = difflib.SequenceMatcher(None, words, n).ratio()
                if n and (n in words or words in n):
                    score = max(score, 0.85 if len(n) > 3 else 0.7)
                if score > best_score:
                    best, best_score = c, score

        qty = 1.0
        if m and not m.group("unit") and m.group("num").replace(".", "").isdigit() and float(m.group("num")) >= 50:
            m = None  # a bare "500" is a pack size ("Amul 500"), not a count
        if m:
            num = _WORD_NUM.get(m.group("num").lower()) or float(m.group("num"))
            asked, dim = _to_base(num, m.group("unit"))
            if best:
                size, unit_dim = _unit_size(best["unit"])
                qty = asked / size if dim == unit_dim and dim != "pc" else num
        matched = best_score >= 0.55
        results.append({
            "raw": raw,
            "catalogue_id": best["id"] if matched else "",
            "quantity": round(qty, 2),
            "confidence": round(min(best_score, 0.95), 2),
            "note": "" if best_score >= 0.75 else ("guess, please check" if matched else "not found in catalogue"),
        })
    return results


def match_list(catalogue, text, image_b64=None, media_type=None):
    """Returns (items, engine)."""
    if ai_enabled():
        return match_with_claude(catalogue, text, image_b64, media_type), "claude"
    if image_b64:
        raise MatchError("Reading photos needs Claude. Add ANTHROPIC_API_KEY, or type the list instead.")
    return match_offline(catalogue, text), "offline"
