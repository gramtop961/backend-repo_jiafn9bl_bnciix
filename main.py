import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Tenant, Product, Order, Customer

app = FastAPI(title="DailyBudgetMart API", version="0.1.0")

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


# ============ Orders Endpoints ============
class CreateOrder(BaseModel):
    tenant_id: str
    items: List[dict]
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None


@app.post("/api/orders", response_model=dict)
def create_order(payload: CreateOrder):
    # Validate tenant
    if db["tenant"].count_documents({"_id": oid(payload.tenant_id)}, limit=1) == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Fetch product prices and compute total
    total = 0.0
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
        total += price * qty
        normalized_items.append({
            "product_id": pid,
            "quantity": qty,
            "price": price,
            "title": product.get("title"),
        })

    order_data = Order(
        tenant_id=payload.tenant_id,
        items=normalized_items,  # type: ignore
        total=round(total, 2),
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
    )
    oid_str = create_document("order", order_data)
    return {"id": oid_str, "total": order_data.total}


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
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
