"""
Shared data models used across Middleware, Store Nodes, and Delivery service.
Kept in one file so every service imports the exact same contract.
"""
from pydantic import BaseModel, Field
from typing import Dict, Optional


class OrderRequest(BaseModel):
    """What the Customer UI sends to the Middleware."""
    customer_name: str
    item: str
    qty: int = Field(gt=0)
    address: str


class OrderAssignment(BaseModel):
    """What the Middleware sends down to the chosen Store Node."""
    order_id: str
    customer_name: str
    item: str
    qty: int
    address: str


class OrderResponse(BaseModel):
    """What the Middleware sends back up to the Customer UI."""
    order_id: str
    status: str
    assigned_store: Optional[str] = None
    message: str
    store_workload_snapshot: Optional[Dict[str, int]] = None


class StoreStatus(BaseModel):
    """What each Store Node reports about itself."""
    store_id: str
    store_name: str
    inventory: Dict[str, int]
    workload: int
    max_capacity: int
    online: bool = True


class DeliveryHandoff(BaseModel):
    """The payload a Store Node pushes to Delivery over the WebRTC DataChannel."""
    order_id: str
    store_id: str
    customer_name: str
    item: str
    qty: int
    address: str
