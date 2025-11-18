"""
Database Schemas for DailyBudgetMart (Multi-tenant E‑commerce SaaS)

Each Pydantic model represents a MongoDB collection. The collection name is the
lowercase of the class name (e.g., Tenant -> "tenant").

Tenancy model: Every business/storefront is a tenant. All tenant-owned data
(products, customers, orders) carries a tenant_id field so the data can migrate
or be isolated per tenant easily.
"""

from pydantic import BaseModel, Field
from typing import Optional, List


class Tenant(BaseModel):
    """
    Tenants collection schema
    Collection: "tenant"
    """
    name: str = Field(..., description="Business name")
    domain: Optional[str] = Field(None, description="Custom domain or subdomain")
    plan: str = Field("free", description="Subscription plan tier")
    contact_email: Optional[str] = Field(None, description="Support or owner email")


class Product(BaseModel):
    """
    Products collection schema
    Collection: "product"
    """
    tenant_id: str = Field(..., description="Owning tenant id")
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Unit price")
    image: Optional[str] = Field(None, description="Image URL")
    stock: int = Field(0, ge=0, description="Units in stock")
    category: Optional[str] = Field(None, description="Category name")
    is_active: bool = Field(True, description="Whether product is visible for sale")


class Customer(BaseModel):
    """
    Customers collection schema
    Collection: "customer"
    """
    tenant_id: str = Field(..., description="Owning tenant id")
    name: str
    email: str


class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)
    price: float = Field(..., ge=0)
    title: Optional[str] = None


class Order(BaseModel):
    """
    Orders collection schema
    Collection: "order"
    """
    tenant_id: str = Field(..., description="Owning tenant id")
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    items: List[OrderItem]
    total: float = Field(..., ge=0)
    status: str = Field("pending", description="pending|paid|shipped|cancelled|refunded")


# Note for the platform:
# 1) The database viewer can read these schemas from GET /schema
# 2) Each model aligns to a MongoDB collection with the lowercased class name
# 3) This design is tenant-aware and ready to migrate to any Mongo instance
