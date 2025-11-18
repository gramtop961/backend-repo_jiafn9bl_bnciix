import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId
import hashlib
import base64
import json
import requests

from database import db, create_document, get_documents
from schemas import Tenant, Product, Order, Customer, Coupon, AdminUser, Webhook, ThemeSettings

app = FastAPI(title="DailyBudgetMart API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helpers
class ObjectIdStr(BaseModel):
    id: str


def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id format")


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def make_token(tenant_id: str, email: str, role: str) -> str:
    payload = {"tenant_id": tenant_id, "email": email, "role": role}
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).decode()


@app.get("/")
def root():
    return {"name": "DailyBudgetMart", "status": "ok"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected & Working"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, "name") else "❌ Unknown"
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:10]
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# ============ Tenant Endpoints ============
@app.post("/api/tenants", response_model=dict)
def create_tenant(tenant: Tenant):
    tenant_id = create_document("tenant", tenant)
    return {"id": tenant_id}


@app.get("/api/tenants", response_model=List[dict])
def list_tenants(limit: int = 50):
    items = get_documents("tenant", {}, limit)
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


# ============ Admin Auth (simple) ============
class RegisterAdmin(BaseModel):
    tenant_id: str
    email: str
    password: str
    role: str = "owner"


@app.post("/api/admin/register")
def register_admin(payload: RegisterAdmin):
    # ensure tenant exists
    if db["tenant"].count_documents({"_id": oid(payload.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")
    existing = db["adminuser"].find_one({"tenant_id": payload.tenant_id, "email": payload.email})
    if existing:
        raise HTTPException(status_code=400, detail="Admin already exists")
    doc = AdminUser(tenant_id=payload.tenant_id, email=payload.email, password_hash=hash_password(payload.password), role=payload.role)
    _id = create_document("adminuser", doc)
    return {"id": _id}


class LoginAdmin(BaseModel):
    tenant_id: str
    email: str
    password: str


@app.post("/api/admin/login")
def login_admin(payload: LoginAdmin):
    user = db["adminuser"].find_one({"tenant_id": payload.tenant_id, "email": payload.email})
    if not user or user.get("password_hash") != hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = make_token(payload.tenant_id, payload.email, user.get("role", "staff"))
    return {"token": token, "role": user.get("role", "staff")}


# ============ Theme Settings ============
@app.get("/api/theme", response_model=dict)
def get_theme(tenant_id: str):
    doc = db["themesettings"].find_one({"tenant_id": tenant_id})
    if not doc:
        return ThemeSettings(tenant_id=tenant_id).model_dump()
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.post("/api/theme", response_model=dict)
def set_theme(theme: ThemeSettings):
    # upsert
    db["themesettings"].update_one({"tenant_id": theme.tenant_id}, {"$set": theme.model_dump()}, upsert=True)
    return theme.model_dump()


# ============ Product Endpoints ============
@app.post("/api/products", response_model=dict)
def add_product(product: Product):
    # Ensure tenant exists
    if db["tenant"].count_documents({"_id": oid(product.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")
    pid = create_document("product", product)
    return {"id": pid}


@app.get("/api/products", response_model=List[dict])
def list_products(tenant_id: str, q: Optional[str] = None, limit: int = 100):
    flt = {"tenant_id": tenant_id}
    if q:
        flt["title"] = {"$regex": q, "$options": "i"}
    items = get_documents("product", flt, limit)
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


@app.get("/api/products/{product_id}", response_model=dict)
def get_product(product_id: str, tenant_id: str):
    doc = db["product"].find_one({"_id": oid(product_id), "tenant_id": tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    doc["id"] = str(doc.pop("_id"))
    return doc


class UpdateStock(BaseModel):
    delta: int


@app.patch("/api/products/{product_id}/stock")
def update_stock(product_id: str, payload: UpdateStock, tenant_id: str):
    res = db["product"].update_one({"_id": oid(product_id), "tenant_id": tenant_id}, {"$inc": {"stock": int(payload.delta)}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    doc = db["product"].find_one({"_id": oid(product_id)})
    return {"id": str(doc["_id"]), "stock": doc.get("stock", 0)}


# ============ Customers ============
@app.post("/api/customers", response_model=dict)
def create_customer(customer: Customer):
    # ensure tenant exists
    if db["tenant"].count_documents({"_id": oid(customer.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")
    _id = create_document("customer", customer)
    return {"id": _id}


@app.get("/api/customers", response_model=List[dict])
def list_customers(tenant_id: str, q: Optional[str] = None, limit: int = 100):
    flt = {"tenant_id": tenant_id}
    if q:
        flt["name"] = {"$regex": q, "$options": "i"}
    items = get_documents("customer", flt, limit)
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


# ============ Coupons ============
@app.post("/api/coupons", response_model=dict)
def create_coupon(coupon: Coupon):
    # ensure tenant exists
    if db["tenant"].count_documents({"_id": oid(coupon.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")
    exists = db["coupon"].find_one({"tenant_id": coupon.tenant_id, "code": coupon.code})
    if exists:
        raise HTTPException(status_code=400, detail="Coupon already exists")
    _id = create_document("coupon", coupon)
    return {"id": _id}


@app.get("/api/coupons", response_model=List[dict])
def list_coupons(tenant_id: str, active: Optional[bool] = None, limit: int = 100):
    flt = {"tenant_id": tenant_id}
    if active is not None:
        flt["active"] = active
    items = get_documents("coupon", flt, limit)
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


# ============ Webhooks ============
@app.post("/api/webhooks", response_model=dict)
def create_webhook(webhook: Webhook):
    # ensure tenant exists
    if db["tenant"].count_documents({"_id": oid(webhook.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")
    _id = create_document("webhook", webhook)
    return {"id": _id}


@app.get("/api/webhooks", response_model=List[dict])
def list_webhooks(tenant_id: str, active: Optional[bool] = None, limit: int = 100):
    flt = {"tenant_id": tenant_id}
    if active is not None:
        flt["active"] = active
    items = get_documents("webhook", flt, limit)
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


# ============ Orders Endpoints ============
class CreateOrder(BaseModel):
    tenant_id: str
    items: List[dict]
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    coupon_code: Optional[str] = None


def fire_webhooks(tenant_id: str, event: str, payload: dict):
    try:
        hooks = list(db["webhook"].find({"tenant_id": tenant_id, "active": True}))
        for h in hooks:
            try:
                requests.post(h.get("url"), json={"event": event, "data": payload}, timeout=2)
            except Exception:
                pass
    except Exception:
        pass


@app.post("/api/orders", response_model=dict)
def create_order(payload: CreateOrder, background_tasks: BackgroundTasks):
    # Validate tenant
    if db["tenant"].count_documents({"_id": oid(payload.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Fetch product prices and compute total
    subtotal = 0.0
    normalized_items = []
    for item in payload.items:
        pid = item.get("product_id")
        qty = int(item.get("quantity", 1))
        if not pid or qty <= 0:
            raise HTTPException(status_code=400, detail="Invalid item")
        product = db["product"].find_one({"_id": oid(pid), "tenant_id": payload.tenant_id})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        price = float(product.get("price", 0))
        if int(product.get("stock", 0)) < qty:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {product.get('title')}")
        subtotal += price * qty
        normalized_items.append({
            "product_id": pid,
            "quantity": qty,
            "price": price,
            "title": product.get("title"),
        })

    discount = 0.0
    applied_coupon = None
    if payload.coupon_code:
        c = db["coupon"].find_one({"tenant_id": payload.tenant_id, "code": payload.coupon_code, "active": True})
        if c:
            applied_coupon = {"code": c.get("code")}
            if c.get("percent_off"):
                discount += subtotal * (float(c.get("percent_off")) / 100.0)
            if c.get("amount_off"):
                discount += float(c.get("amount_off"))
            # track redemption
            db["coupon"].update_one({"_id": c["_id"]}, {"$inc": {"times_redeemed": 1}})
        else:
            raise HTTPException(status_code=400, detail="Invalid coupon code")

    total = max(0.0, subtotal - discount)

    order_data = Order(
        tenant_id=payload.tenant_id,
        items=normalized_items,  # type: ignore
        total=round(total, 2),
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
    )
    oid_str = create_document("order", order_data)

    # decrement stock
    for it in normalized_items:
        db["product"].update_one({"_id": oid(it["product_id"])}, {"$inc": {"stock": -int(it["quantity"])}})

    # fire webhooks async
    background_tasks.add_task(fire_webhooks, payload.tenant_id, "order.created", {"order_id": oid_str, "total": order_data.total, "coupon": applied_coupon})

    return {"id": oid_str, "total": order_data.total, "subtotal": round(subtotal, 2), "discount": round(discount, 2)}


@app.get("/api/orders", response_model=List[dict])
def list_orders(tenant_id: str, limit: int = 100):
    items = get_documents("order", {"tenant_id": tenant_id}, limit)
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


# ============ Schemas Discovery (for migrations/tools) ============
@app.get("/schema")
def get_schema():
    # Minimal representation for migration tooling / admin
    return {
        "tenant": {
            "fields": ["name", "domain", "plan", "contact_email"],
            "indexes": ["domain"],
        },
        "product": {
            "fields": [
                "tenant_id",
                "title",
                "description",
                "price",
                "image",
                "stock",
                "category",
                "is_active",
            ],
            "indexes": ["tenant_id", "title"],
        },
        "customer": {"fields": ["tenant_id", "name", "email"], "indexes": ["tenant_id", "email"]},
        "order": {
            "fields": [
                "tenant_id",
                "customer_id",
                "customer_name",
                "customer_email",
                "items",
                "total",
                "status",
            ],
            "indexes": ["tenant_id", "status"],
        },
        "coupon": {
            "fields": ["tenant_id", "code", "percent_off", "amount_off", "active", "max_redemptions", "times_redeemed"],
            "indexes": ["tenant_id", "code"],
        },
        "adminuser": {
            "fields": ["tenant_id", "email", "password_hash", "role"],
            "indexes": ["tenant_id", "email"],
        },
        "webhook": {
            "fields": ["tenant_id", "url", "events", "active"],
            "indexes": ["tenant_id"],
        },
        "themesettings": {
            "fields": ["tenant_id", "primary_color", "hero_heading", "hero_subtext", "logo_url", "featured_categories"],
            "indexes": ["tenant_id"],
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
