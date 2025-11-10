import os
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

from database import db, create_document, get_documents

app = FastAPI(title="POD Art Shop API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PRINTIFY_API_BASE = "https://api.printify.com/v1"
PRINTIFY_API_TOKEN = os.getenv("PRINTIFY_API_TOKEN")  # Set in backend .env
PRINTIFY_SHOP_ID = os.getenv("PRINTIFY_SHOP_ID")

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


class SyncResponse(BaseModel):
    synced: int
    products: List[Dict[str, Any]]


@app.get("/")
def read_root():
    return {"message": "POD Art Shop Backend running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


# --- Printify Helpers ---

def _printify_headers() -> Dict[str, str]:
    if not PRINTIFY_API_TOKEN:
        raise HTTPException(status_code=500, detail="PRINTIFY_API_TOKEN not set")
    return {"Authorization": f"Bearer {PRINTIFY_API_TOKEN}", "Content-Type": "application/json"}


def get_printify_products() -> List[Dict[str, Any]]:
    if not PRINTIFY_SHOP_ID:
        raise HTTPException(status_code=500, detail="PRINTIFY_SHOP_ID not set")
    url = f"{PRINTIFY_API_BASE}/shops/{PRINTIFY_SHOP_ID}/products.json"
    r = requests.get(url, headers=_printify_headers())
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    # API returns {data: [...], ...} or list depending on version; normalize
    return data.get("data", data)


# --- API: Sync & Catalog ---

@app.post("/api/printify/sync", response_model=SyncResponse)
def sync_printify_products():
    products = get_printify_products()
    synced = 0
    saved_docs: List[Dict[str, Any]] = []
    for p in products:
        product_id = p.get("id") or p.get("_id")
        if not product_id:
            continue
        title = p.get("title") or p.get("name") or "Untitled"
        description = p.get("description")
        images: List[str] = []
        # collect preview images
        previews = p.get("images") or p.get("files") or []
        for im in previews:
            url = im.get("src") or im.get("preview_url") or im.get("url")
            if url:
                images.append(url)
        # price and variants
        variants = p.get("variants") or []
        default_variant_id = None
        price = 0
        currency = "USD"
        for v in variants:
            if v.get("is_default"):
                default_variant_id = v.get("id") or v.get("variant_id")
            if isinstance(v.get("price"), (int, float)):
                price = max(price, float(v.get("price")) / 100.0 if v.get("price") and v.get("price") > 10 else float(v.get("price")))
        doc = {
            "id": product_id,
            "title": title,
            "description": description,
            "images": images[:8],
            "tags": p.get("tags") or [],
            "categories": p.get("categories") or [],
            "variants": variants,
            "default_variant_id": default_variant_id,
            "price": round(price or 0, 2),
            "currency": currency,
            "available": p.get("visible", True),
        }
        # upsert by product id
        existing = db["storeproduct"].find_one({"id": product_id}) if db else None
        if existing:
            db["storeproduct"].update_one({"id": product_id}, {"$set": doc})
        else:
            create_document("storeproduct", doc)
        synced += 1
        saved_docs.append(doc)
    return {"synced": synced, "products": saved_docs}


@app.get("/api/catalog")
def get_catalog(category: Optional[str] = None, q: Optional[str] = None):
    filt: Dict[str, Any] = {"available": True}
    if category:
        filt["categories"] = {"$in": [category]}
    if q:
        filt["title"] = {"$regex": q, "$options": "i"}
    items = get_documents("storeproduct", filt, limit=100)
    return items


# --- Wishlist ---
class WishlistIn(BaseModel):
    user_id: str
    product_id: str


@app.post("/api/wishlist")
def add_wishlist(item: WishlistIn):
    create_document("wishlist", item.dict())
    return {"status": "ok"}


@app.get("/api/wishlist/{user_id}")
def get_wishlist(user_id: str):
    items = get_documents("wishlist", {"user_id": user_id})
    return items


# --- Checkout with Stripe (simplified) ---

class CheckoutItem(BaseModel):
    product_id: str
    variant_id: Optional[int] = None
    quantity: int = 1
    unit_amount: Optional[float] = None


class CheckoutSessionIn(BaseModel):
    user_id: Optional[str] = None
    items: List[CheckoutItem]
    currency: str = "usd"


@app.post("/api/checkout/create-session")
def create_checkout_session(payload: CheckoutSessionIn):
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    import stripe
    stripe.api_key = STRIPE_API_KEY

    line_items = []
    amount_total = 0.0
    for it in payload.items:
        sp = db["storeproduct"].find_one({"id": it.product_id})
        if not sp:
            raise HTTPException(status_code=404, detail=f"Product {it.product_id} not found")
        price = float(it.unit_amount or sp.get("price", 0))
        amount_total += price * it.quantity
        line_items.append({
            "price_data": {
                "currency": payload.currency,
                "product_data": {
                    "name": sp.get("title", "Item"),
                    "images": sp.get("images", [])[:1],
                },
                "unit_amount": int(price * 100),
            },
            "quantity": it.quantity,
        })

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        success_url=f"{FRONTEND_URL}?success=true",
        cancel_url=f"{FRONTEND_URL}?canceled=true",
    )

    order_doc = {
        "user_id": payload.user_id,
        "items": [i.dict() for i in payload.items],
        "amount_total": round(amount_total, 2),
        "currency": payload.currency.upper(),
        "status": "created",
        "stripe_session_id": session.id,
    }
    create_document("order", order_doc)

    return {"id": session.id, "url": session.url}


# --- Webhook for Stripe to create Printify order (simplified placeholder) ---

class StripeWebhook(BaseModel):
    id: str
    type: str
    data: Dict[str, Any]


@app.post("/api/stripe/webhook")
def stripe_webhook(event: StripeWebhook):
    # In production, verify signature. Here we act on a paid session for demo.
    evt_type = event.type
    if evt_type == "checkout.session.completed":
        session_id = event.data.get("object", {}).get("id")
        order = db["order"].find_one({"stripe_session_id": session_id})
        if order:
            # Create Printify order
            try:
                _create_printify_order_from_order(order)
                db["order"].update_one({"_id": order["_id"]}, {"$set": {"status": "paid"}})
            except Exception:
                db["order"].update_one({"_id": order["_id"]}, {"$set": {"status": "failed"}})
    return {"received": True}


def _create_printify_order_from_order(order: Dict[str, Any]):
    if not PRINTIFY_SHOP_ID:
        raise HTTPException(status_code=500, detail="PRINTIFY_SHOP_ID not set")
    url = f"{PRINTIFY_API_BASE}/shops/{PRINTIFY_SHOP_ID}/orders.json"
    line_items = []
    for it in order.get("items", []):
        line_items.append({
            "product_id": it.get("product_id"),
            "variant_id": it.get("variant_id") or db["storeproduct"].find_one({"id": it.get("product_id")}).get("default_variant_id"),
            "quantity": it.get("quantity", 1),
        })
    payload = {
        "line_items": line_items,
        "external_id": str(order.get("_id")),
        "label": "POD Art Shop Order",
        "shipping_method": 1,
        "send_shipping_notification": False,
        "address_to": {"first_name": "Customer", "last_name": "", "country": "US"}
    }
    r = requests.post(url, headers=_printify_headers(), json=payload)
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=r.status_code, detail=r.text)
    resp = r.json()
    db["order"].update_one({"_id": order["_id"]}, {"$set": {"printify_order_id": resp.get("id")}})
    return resp


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
