"""Pydantic request models for the MediNexus API."""
from datetime import date
from typing import Optional, Literal
from pydantic import BaseModel, Field, EmailStr, ConfigDict


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class OrganizationCreate(InputModel):
    name: str = Field(min_length=2)
    organization_code: str = Field(min_length=2)
    organization_type: Literal["hospital", "pharmacy", "clinic", "warehouse"] = "hospital"
    address: str = ""
    phone: str = ""
    email: Optional[EmailStr] = None


class UserRegister(InputModel):
    name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=8)
    role: Literal["admin", "inventory_manager", "doctor", "staff", "procurement"] = "staff"
    organization_id: str


class UserLogin(InputModel):
    email: EmailStr
    password: str


class MedicineCreate(InputModel):
    medicine_code: str = Field(min_length=2)
    name: str = Field(min_length=2)
    composition: str = ""
    category: str = "General"
    unit: str = "units"
    description: str = ""


class InventoryCreate(InputModel):
    organization_id: str
    medicine_id: str
    minimum_stock: int = Field(default=0, ge=0)
    reorder_level: int = Field(default=0, ge=0)
    location: str = ""


class InventoryUpdate(InputModel):
    minimum_stock: Optional[int] = Field(default=None, ge=0)
    reorder_level: Optional[int] = Field(default=None, ge=0)
    location: Optional[str] = None


class StockIn(InputModel):
    organization_id: str
    medicine_id: str
    quantity: int = Field(gt=0)
    batch_number: str = Field(min_length=1)
    manufacturing_date: Optional[date] = None
    expiry_date: date
    supplier_id: Optional[str] = None
    reference: str = ""
    performed_by: str = "system"


class StockOut(InputModel):
    organization_id: str
    medicine_id: str
    quantity: int = Field(gt=0)
    reason: str = "issue"
    reference: str = ""
    performed_by: str = "system"


class MedicineRequestCreate(InputModel):
    organization_id: str
    medicine_id: str
    quantity: int = Field(gt=0)
    urgency: Literal["critical", "high", "medium", "low"] = "medium"
    patient_count: int = Field(default=1, ge=1)
    requested_by: str
    notes: str = ""


class RequestDecision(InputModel):
    decision: Literal["approve", "reject"]
    reviewed_by: str
    notes: str = ""


class SupplierCreate(InputModel):
    name: str = Field(min_length=2)
    contact_person: str = ""
    email: Optional[EmailStr] = None
    phone: str = ""
    address: str = ""


class PurchaseOrderCreate(InputModel):
    organization_id: str
    supplier_id: str
    items: list[dict]
    created_by: str
    notes: str = ""


class TransferCreate(InputModel):
    medicine_id: str
    from_organization_id: str
    to_organization_id: str
    quantity: int = Field(gt=0)
    requested_by: str
    notes: str = ""


class TransferAction(InputModel):
    action: Literal["approve", "reject", "dispatch", "receive"]
    performed_by: str
    notes: str = ""


class NotificationUpdate(InputModel):
    is_read: Optional[bool] = None
    is_resolved: Optional[bool] = None

