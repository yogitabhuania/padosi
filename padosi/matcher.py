"""The AI steps of PADOSI.

1. Read a messy shopper list (text or photo), match each line to the catalogue, and draft the
   opening and closing lines of the reply in the shopper's own language.
2. Read a supplier bill or price list (text or photo) into catalogue rows, so a new store can
   set up its catalogue in minutes instead of typing it in.

Either Claude (ANTHROPIC_API_KEY) or Gemini (GEMINI_API_KEY) does the reading; Claude wins if
both keys are set. The AI never prices anything: pricing.py does the money, and the owner
approves every line. Without a key, a simple fuzzy matcher stands in so typed lists still work.
"""
import base64
import difflib
import json
import os
import re

CLAUDE_MODEL = os.environ.get("PADOSI_MODEL", "claude-opus-5-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = "gemini-flash-latest"
AI_NAMES = {"claude": "Claude", "gemini": "Gemini"}

LANGUAGES = ["english", "kannada", "hindi", "tamil", "telugu", "mixed"]

LIST_SCHEMA = {
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
        },
        "language": {"type": "string", "enum": LANGUAGES},
        "reply_opening": {"type": "string"},
        "reply_closing": {"type": "string"},
    },
    "required": ["items", "language", "reply_opening", "reply_closing"],
    "additionalProperties": False,
}

LIST_INSTRUCTIONS = """You read grocery lists that shoppers send to a neighbourhood kirana store in Bengaluru and match each requested item to the store's catalogue.

Lists mix Kannada, Hindi and English, often transliterated into Latin script or written in Kannada/Devanagari script, with misspellings, brand shorthand and handwriting. Use the catalogue's names and aliases, and your own knowledge of Indian groceries, to find the right item.

For every item the shopper asked for, return one entry in items:
- raw: the shopper's line (or the relevant fragment if one line names several items), copied as written.
- catalogue_id: the id of the best-matching catalogue item, or "" if nothing in the catalogue is a reasonable match.
- quantity: how many of the catalogue item's unit to supply. Convert the shopper's amount into that unit, e.g. "250g jeera" against a "100 g" unit is 2.5; "2 kg tomato" against a "1 kg" unit is 2; "rice 10kg" against a "5 kg bag" is 2. If no amount is given, use 1.
- confidence: how sure you are about both the item and the quantity, from 0 to 1. Use 0.9 or above only when the match is clear. Go below 0.75 when the item is ambiguous (for example "Amul 500" could be 500 ml milk or 500 g butter), the handwriting is unclear, the pack size differs a lot from what was asked, or there is no match.
- note: when confidence is below 0.75, a few words the store owner can act on, e.g. "could be milk or butter". Otherwise "".

Ignore greetings, names, addresses and anything that is not an item. Do not invent items the shopper did not ask for, and never calculate prices.

The owner will send the itemised quote back on WhatsApp. Write the two lines that go around it, in the language and script the shopper used (transliterated Kannada if they wrote Kannada in Latin letters, Devanagari Hindi if they wrote in Devanagari, and so on), in the warm, respectful tone of a neighbourhood shopkeeper:
- language: the shopper's main language.
- reply_opening: one short line greeting the customer (by name if one is given, with "avare" or "ji" as fits the language) and saying their order is below.
- reply_closing: one short line asking them to reply to confirm, and saying it will be packed right away.
Do not put prices, totals or item names in these two lines."""

CATALOGUE_CATEGORIES = [
    "Staples", "Pulses", "Oils", "Dairy", "Vegetables", "Fruits", "Masala", "Beverages",
    "Snacks", "Household", "Personal care", "Pooja", "Bakery", "Other",
]

CATALOGUE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_id": {"type": "string", "description": "id of the same product already in the catalogue, else empty"},
                    "name": {"type": "string"},
                    "aliases": {"type": "string", "description": "Shopper names separated by ;"},
                    "category": {"type": "string", "enum": CATALOGUE_CATEGORIES},
                    "unit": {"type": "string"},
                    "price": {"type": "number"},
                    "stock": {"type": "number"},
                    "confidence": {"type": "number"},
                },
                "required": ["existing_id", "name", "aliases", "category", "unit", "price", "stock", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

CATALOGUE_INSTRUCTIONS = """You help a kirana store owner in Bengaluru set up or refresh their store catalogue from a supplier bill, distributor price list, or handwritten stock note.

Return one entry per product (skip headers, totals, taxes, discounts and delivery charges):
- existing_id: if the product is already in the current catalogue below (same product and pack size), its id; otherwise "".
- name: the product as it is sold to shoppers, brand first where there is one, e.g. "Aashirvaad Atta", "Toor Dal".
- aliases: the names shoppers actually type for it, separated by ";": common Kannada and Hindi names in Latin script, everyday English words and frequent misspellings, e.g. "togari bele;tur dal;arhar dal;toor".
- category: the closest category.
- unit: the pack a shopper buys, e.g. "1 kg", "500 ml packet", "5 kg bag", "1 pc".
- price: the selling price for one unit, copied exactly as printed: the MRP or selling-price column if there is one, otherwise the per-unit rate. Never calculate, add tax, or divide totals. If no per-unit price is printed, use 0 so the owner fills it in.
- stock: the number of units received if the bill shows it, else 0.
- confidence: 0 to 1, how sure you are that you read the product, pack size and price correctly."""


def ai_provider():
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    return None


def ai_enabled():
    return ai_provider() is not None


def ai_name():
    return AI_NAMES.get(ai_provider(), "AI")


def ai_model():
    return {"claude": CLAUDE_MODEL, "gemini": GEMINI_MODEL}.get(ai_provider())


def _catalogue_block(catalogue):
    lines = ["id | name | aliases | unit"]
    lines += [f"{c['id']} | {c['name']} | {c['aliases']} | {c['unit']}" for c in catalogue]
    return "Store catalogue:\n" + "\n".join(lines)


class MatchError(Exception):
    pass


def _prompt(text, image_b64, photo_label, text_label):
    if image_b64:
        return photo_label + (f"\n\nThe owner also typed:\n{text}" if text else "")
    return f"{text_label}\n{text}"


def _ask_claude(instructions, catalogue, prompt, image_b64, media_type, schema):
    """One structured-output request. The catalogue goes in the system prompt so it is cached."""
    import anthropic

    content = []
    if image_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}})
    content.append({"type": "text", "text": prompt})
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=[
                {"type": "text", "text": instructions},
                {"type": "text", "text": _catalogue_block(catalogue), "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise MatchError("The Claude API key is invalid. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise MatchError("Claude is rate limited right now. Try again in a minute.")
    except anthropic.BadRequestError as e:
        raise MatchError(f"Claude could not read this: {e.message}")
    except anthropic.APIStatusError as e:
        raise MatchError(f"Claude returned an error ({e.status_code}). Try again.")
    except anthropic.APIConnectionError:
        raise MatchError("Could not reach Claude. Check the internet connection.")

    if response.stop_reason == "refusal":
        raise MatchError("Claude declined to read this.")
    if response.stop_reason == "max_tokens":
        raise MatchError("This is too long to read in one go. Split it into two.")
    text_out = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text_out)
    except ValueError:
        raise MatchError("Claude returned an unreadable answer. Try again.")


def _ask_gemini(instructions, catalogue, prompt, image_b64, media_type, schema):
    """Same request for Gemini, using JSON-schema structured output."""
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    parts = []
    if image_b64:
        parts.append(types.Part.from_bytes(data=base64.b64decode(image_b64), mime_type=media_type))
    parts.append(types.Part.from_text(text=prompt))
    config = types.GenerateContentConfig(
        system_instruction=instructions + "\n\n" + _catalogue_block(catalogue),
        response_mime_type="application/json",
        response_json_schema=schema,
    )
    response = None
    for model in dict.fromkeys([GEMINI_MODEL, GEMINI_FALLBACK_MODEL]):
        try:
            response = client.models.generate_content(
                model=model, contents=[types.Content(role="user", parts=parts)], config=config)
            break
        except errors.ClientError as e:
            message = str(getattr(e, "message", "") or e)
            if e.code == 404 and model != GEMINI_FALLBACK_MODEL:
                continue  # this key can't use the pinned model; try Google's latest-Flash alias
            if "API key" in message or e.code in (401, 403):
                raise MatchError("The Gemini API key is invalid. Check GEMINI_API_KEY.")
            if e.code == 429:
                raise MatchError("Gemini is rate limited or out of quota right now. Try again in a minute.")
            raise MatchError(f"Gemini could not read this: {message}")
        except errors.APIError as e:
            raise MatchError(f"Gemini returned an error ({getattr(e, 'code', '?')}). Try again.")
        except Exception:
            raise MatchError("Could not reach Gemini. Check the internet connection.")

    text_out = (response.text or "") if response else ""
    if not text_out:
        reason = ""
        if response and response.candidates:
            reason = str(response.candidates[0].finish_reason or "")
        if "MAX_TOKENS" in reason:
            raise MatchError("This is too long to read in one go. Split it into two.")
        raise MatchError("Gemini declined to read this.")
    try:
        return json.loads(text_out)
    except ValueError:
        raise MatchError("Gemini returned an unreadable answer. Try again.")


def _ask_ai(instructions, catalogue, prompt, image_b64, media_type, schema):
    ask = _ask_claude if ai_provider() == "claude" else _ask_gemini
    return ask(instructions, catalogue, prompt, image_b64, media_type, schema)


def match_with_ai(catalogue, text, image_b64=None, media_type=None, customer_name=""):
    prompt = _prompt(text, image_b64, "This photo is the shopper's list.", "Shopper's list:")
    if customer_name:
        prompt += f"\n\nCustomer's name: {customer_name}"
    return _ask_ai(LIST_INSTRUCTIONS, catalogue, prompt, image_b64, media_type, LIST_SCHEMA)


def extract_catalogue(catalogue, text, image_b64=None, media_type=None):
    if not ai_enabled():
        raise MatchError("Reading bills needs an AI key (GEMINI_API_KEY or ANTHROPIC_API_KEY), or upload a CSV instead.")
    prompt = _prompt(text, image_b64, "This photo is a supplier bill or price list.", "Supplier bill or price list:")
    return _ask_ai(CATALOGUE_INSTRUCTIONS, catalogue, prompt, image_b64, media_type, CATALOGUE_SCHEMA)["items"]


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


def match_list(catalogue, text, image_b64=None, media_type=None, customer_name=""):
    """Returns {items, language, reply_opening, reply_closing, engine}."""
    if ai_enabled():
        result = match_with_ai(catalogue, text, image_b64, media_type, customer_name)
        return {**result, "engine": ai_provider()}
    if image_b64:
        raise MatchError("Reading photos needs an AI key (GEMINI_API_KEY or ANTHROPIC_API_KEY). Type the list instead.")
    return {"items": match_offline(catalogue, text), "language": "", "reply_opening": "",
            "reply_closing": "", "engine": "offline"}
