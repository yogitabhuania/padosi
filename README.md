# PADOSI

A neighbourhood kirana's WhatsApp order desk. The owner pastes a shopper's list (or a photo of a handwritten one), Claude matches each item to the store's catalogue, a script prices it and checks stock, and the owner approves the quote in one tap and sends it back on WhatsApp.

This is the hackathon MVP from *PADOSI – MVP Scope*: **one journey, from an owner pasting a list to an approved, priced quote.**

## What's in the MVP

| Scope item | Where |
|---|---|
| List reading: paste text or upload a photo; Claude matches items to the catalogue with a confidence score | `padosi/matcher.py` |
| Priced quote with flags: script totals prices, checks stock, flags ambiguous and missing items | `padosi/pricing.py` |
| One-tap approval: edit any line, approve, copy the quote for WhatsApp | `public/app.js` |
| Catalogue CSV upload, inline price and stock edits | Catalogue tab |
| Timing: every quote records seconds from list arriving to approval, to test the "under 1 minute" goal | Orders tab |

AI only reads and matches. Money, stock and approval stay with the script and the owner, as the scope doc requires.

**Out of scope (mention, don't build):** live WhatsApp Business integration, voice notes, UPI payment links, "cheaper than Blinkit" claims, delivery tracking, khata, multiple stores.

## Stack

- **Backend:** Python + FastAPI (`app.py`), deployed as a Vercel Python function
- **Database:** Postgres when `DATABASE_URL` is set (e.g. Neon via the Vercel Marketplace), SQLite file otherwise
- **AI:** Claude (`claude-opus-5-5`) through the Anthropic Python SDK, with structured JSON output
- **Frontend:** plain HTML/CSS/JS in `public/`, no build step

n8n from the scope doc is replaced by the API route in `app.py`, which does the same job (receive list → call Claude → run the pricing script → flag low-confidence lines) with one fewer service to host.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt uvicorn
cp .env.example .env   # then add your ANTHROPIC_API_KEY
.venv/bin/python -m uvicorn app:app --reload --port 8000
```

Open http://localhost:8000. Without an API key the app runs in demo mode: typed lists go through a basic fuzzy matcher and photo upload is disabled.

## Deploy to Vercel

1. Push this folder to a GitHub repo.
2. In Vercel: **Add New → Project**, import the repo. Vercel detects FastAPI from `app.py`; no settings needed.
3. **Storage → Create Database → Neon (Postgres)** and connect it to the project. This sets `DATABASE_URL`.
4. **Settings → Environment Variables:** add `ANTHROPIC_API_KEY` and, optionally, `STORE_NAME`.
5. Redeploy. Tables and the sample catalogue are created on first request.

Without a database, the deployed app still works but stores data in `/tmp`, which resets whenever the function restarts.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | AI mode, storage mode, store name |
| POST | `/api/quote` | `{text, image_base64, media_type, customer_name, customer_phone, started_at}` → draft order |
| GET/PUT/DELETE | `/api/orders/{id}` | Read, edit lines (repriced server-side), delete |
| POST | `/api/orders/{id}/approve` | Approve, take stock, return WhatsApp message |
| GET | `/api/orders`, `/api/stats` | Order list, timing and value stats |
| GET/POST/PATCH | `/api/catalogue…` | List, upload CSV, reset to sample, edit price/stock |

## Catalogue CSV

```
id,name,aliases,category,unit,price,stock
TOOR-1,Toor Dal,togari bele;tur dal;arhar dal,Pulses,1 kg,168,22
```

Only `name` and `price` are required. Aliases (separated by `;`) help Claude match Kannada and Hindi names. `padosi/sample_catalogue.csv` holds 56 typical Bengaluru kirana items.
