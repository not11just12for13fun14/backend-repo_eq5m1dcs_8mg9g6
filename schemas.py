"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class StoreProduct(BaseModel):
    """
    Printify-synced product stored for storefront rendering
    Collection name: "storeproduct"
    """
    id: str = Field(..., description="Printify product id")
    title: str
    description: Optional[str] = None
    images: List[str] = []
    tags: List[str] = []
    categories: List[str] = []
    variants: List[Dict[str, Any]] = []
    default_variant_id: Optional[int] = None
    price: float = Field(..., ge=0)
    currency: str = "USD"
    available: bool = True

class Wishlist(BaseModel):
    """
    Wishlist entries per user
    Collection name: "wishlist"
    """
    user_id: str = Field(..., description="Anonymous or authenticated user id")
    product_id: str = Field(..., description="Printify product id")

class Order(BaseModel):
    """
    Lightweight order record for storefront
    Collection name: "order"
    """
    user_id: Optional[str] = None
    items: List[Dict[str, Any]] = Field(..., description="List of items with product_id, variant_id, quantity, unit_amount")
    amount_total: float = Field(..., ge=0)
    currency: str = "USD"
    status: str = Field("created", description="created, paid, failed, fulfilled")
    stripe_session_id: Optional[str] = None
    printify_order_id: Optional[str] = None
