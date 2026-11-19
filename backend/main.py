"""MediNexus Medical Supply Intelligence API.

Collections used: organizations, users, medicines, inventory, batches,
stockTransactions, medicineRequests, suppliers, purchaseOrders, transfers,
notifications, auditLogs.
"""
import httpx
from fastapi import Request
from datetime import datetime, date, timedelta, timezone
from typing import Any
import os

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query, status, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

import database as mongo
from models import (
    OrganizationCreate, UserRegister, UserLogin, MedicineCreate,
    InventoryCreate, InventoryUpdate, StockIn, StockOut,
    MedicineRequestCreate, RequestDecision, SupplierCreate,
    PurchaseOrderCreate, TransferCreate, TransferAction, NotificationUpdate,
)

app = FastAPI(
    title="MediNexus Medical Supply Intelligence API",
    description="Backend for hospital inventory, batch tracking, requests, procurement, transfers and notifications.",
    version="2.0.0",
)
origins = [x.strip() for x in os.getenv("FRONTEND_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-this-secret")
if os.getenv("APP_ENV", "development").lower() == "production" and (len(JWT_SECRET) < 32 or JWT_SECRET == "development-only-change-this-secret"):
    raise RuntimeError("Set a unique JWT_SECRET of at least 32 characters before production deployment.")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
AI_SERVICE_URL = os.getenv(
    "AI_SERVICE_URL",
    "http://127.0.0.1:8001"
)
bearer = HTTPBearer(auto_error=False)

PUBLIC_PATHS = {"/", "/api/health", "/api/auth/login", "/api/auth/register", "/docs", "/redoc", "/openapi.json"}
ORG_SCOPED_PATHS = (
    "/api/users", "/api/inventory", "/api/batches", "/api/stock-transactions",
    "/api/medicine-requests", "/api/transfers", "/api/notifications",
    "/api/audit-logs", "/api/dashboard/summary",
)
WRITE_ROLES = {"admin", "inventory_manager"}
ADMIN_ONLY_PATHS = {"/api/users", "/api/organizations", "/api/audit-logs", "/api/notifications/scan", "/api/medicine-requests/process-priority"}


@app.middleware("http")
async def authenticate_and_authorize(request: Request, call_next):
    """Validate protected API requests and enforce role/organization boundaries."""
    # Browser CORS preflight requests must reach CORSMiddleware without authentication.
    if request.method.upper() == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_PATHS:
        return await call_next(request)
    # One-time bootstrap: allow creating the very first organization before any user exists.
    if path == "/api/organizations" and request.method.upper() == "POST":
        try:
            if mongo.organizations.count_documents({}) == 0:
                return await call_next(request)
        except PyMongoError:
            return JSONResponse({"detail": "Database is unavailable during workspace setup."}, status_code=503)

    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return JSONResponse({"detail": "Authentication required. Please sign in."}, status_code=401)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id or not ObjectId.is_valid(user_id):
            raise JWTError("Invalid subject")
        user = mongo.users.find_one({"_id": ObjectId(user_id)})
        if not user or user.get("status", "active") != "active":
            return JSONResponse({"detail": "Account is inactive or no longer exists."}, status_code=401)
    except JWTError:
        return JSONResponse({"detail": "Invalid or expired access token. Please sign in again."}, status_code=401)
    except PyMongoError:
        return JSONResponse({"detail": "Authentication service is temporarily unavailable."}, status_code=503)

    role = user.get("role", "staff")
    org_id = str(user.get("organization_id", ""))
    request.state.user = user

    # Only administrators may manage users, organizations, audit logs, and system scans.
    if path in ADMIN_ONLY_PATHS or path.startswith("/api/organizations"):
        if role != "admin":
            return JSONResponse({"detail": "Administrator permission required."}, status_code=403)

    # Read-only clinical users cannot mutate inventory/procurement records.
    method = request.method.upper()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        if path.startswith(("/api/stock-in", "/api/stock-out", "/api/inventory", "/api/medicines", "/api/batches", "/api/purchase-orders", "/api/suppliers", "/api/transfers")) and role not in WRITE_ROLES and role != "procurement":
            return JSONResponse({"detail": "Your role does not have permission to modify this resource."}, status_code=403)
        if path.startswith("/api/medicine-requests") and role == "doctor" and method != "POST":
            return JSONResponse({"detail": "Doctors may submit requests but cannot approve or process them."}, status_code=403)
        if path.startswith("/api/medicine-requests") and (method != "POST" or path.endswith("/process-priority")) and role not in WRITE_ROLES:
            return JSONResponse({"detail": "Only administrators and inventory managers may review or process requests."}, status_code=403)

    # Bind organization-scoped endpoints to the signed-in user's organization.
    if role != "admin" and org_id:
        query = dict(request.query_params)
        requested_org = query.get("organization_id")
        if requested_org and requested_org != org_id:
            return JSONResponse({"detail": "You cannot access another organization's records."}, status_code=403)
        if path.startswith(ORG_SCOPED_PATHS):
            query["organization_id"] = org_id
            from urllib.parse import urlencode
            request.scope["query_string"] = urlencode(query).encode("utf-8")
        if method in {"POST", "PUT", "PATCH"} and request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                for key in ("organization_id", "from_organization_id"):
                    value = body.get(key)
                    if value and str(value) != org_id:
                        return JSONResponse({"detail": "You cannot modify another organization's records."}, status_code=403)
                if path.startswith("/api/transfers") and body.get("to_organization_id") and str(body["to_organization_id"]) != org_id:
                    return JSONResponse({"detail": "A transfer must originate from your organization."}, status_code=403)
    return await call_next(request)

URGENCY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def now():
    return datetime.now(timezone.utc)


def to_json(value: Any):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {("id" if k == "_id" else k): to_json(v) for k, v in value.items() if k != "password_hash"}
    if isinstance(value, list):
        return [to_json(v) for v in value]
    return value


def oid(value: str):
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=f"Invalid ID: {value}")
    return ObjectId(value)


def find_by_id(collection, value: str):
    if ObjectId.is_valid(value):
        doc = collection.find_one({"_id": ObjectId(value)})
        if doc:
            return doc
    return collection.find_one({"$or": [{"code": value}, {"organization_code": value}, {"medicine_code": value}]})


def require_exists(collection, value: str, label: str):
    doc = find_by_id(collection, value)
    if not doc:
        raise HTTPException(status_code=404, detail=f"{label} not found: {value}")
    return doc


def log_activity(action: str, entity: str, entity_id: str, performed_by: str, organization_id: str | None = None, details: dict | None = None):
    mongo.audit_logs.insert_one({
        "action": action, "entity": entity, "entity_id": str(entity_id),
        "performed_by": performed_by, "organization_id": organization_id,
        "details": details or {}, "created_at": now(),
    })


def notify(org_id: str | None, title: str, message: str, kind: str, severity: str = "medium", related_id: str | None = None):
    mongo.notifications.insert_one({
        "organization_id": org_id, "title": title, "message": message,
        "type": kind, "severity": severity, "related_id": related_id,
        "is_read": False, "is_resolved": False, "created_at": now(),
    })


def inventory_filter(org_id: str, med_id: str):
    return {"organization_id": org_id, "medicine_id": med_id}


def get_or_create_inventory(org_id: str, med_id: str):
    doc = mongo.inventory.find_one(inventory_filter(org_id, med_id))
    if doc:
        return doc
    result = mongo.inventory.insert_one({
        "organization_id": org_id, "medicine_id": med_id,
        "quantity": 0, "minimum_stock": 0, "reorder_level": 0,
        "location": "", "created_at": now(), "updated_at": now(),
    })
    return mongo.inventory.find_one({"_id": result.inserted_id})


def add_stock(org_id: str, med_id: str, qty: int, batch_number: str, expiry_date: date,
              manufacturing_date: date | None = None, supplier_id: str | None = None,
              reference: str = "", performed_by: str = "system", transaction_type: str = "stock_in"):
    inv = get_or_create_inventory(org_id, med_id)
    batch = mongo.batches.find_one({"organization_id": org_id, "medicine_id": med_id, "batch_number": batch_number})
    if batch:
        mongo.batches.update_one({"_id": batch["_id"]}, {"$inc": {"quantity": qty}, "$set": {"updated_at": now()}})
    else:
        mongo.batches.insert_one({
            "organization_id": org_id, "medicine_id": med_id,
            "batch_number": batch_number, "quantity": qty,
            "manufacturing_date": manufacturing_date,
            "expiry_date": datetime.combine(expiry_date, datetime.min.time(), tzinfo=timezone.utc),
            "supplier_id": supplier_id, "status": "active", "created_at": now(), "updated_at": now(),
        })
    mongo.inventory.update_one({"_id": inv["_id"]}, {"$inc": {"quantity": qty}, "$set": {"updated_at": now()}})
    mongo.stock_transactions.insert_one({
        "organization_id": org_id, "medicine_id": med_id,
        "transaction_type": transaction_type, "quantity": qty,
        "batch_number": batch_number, "reference": reference,
        "performed_by": performed_by, "created_at": now(),
    })
    return mongo.inventory.find_one({"_id": inv["_id"]})


def remove_stock(org_id: str, med_id: str, qty: int, reason: str, reference: str = "", performed_by: str = "system"):
    """Deduct aggregate stock, then consume active batches FEFO; restore aggregate if batches are short."""
    inv = mongo.inventory.find_one_and_update(
        {**inventory_filter(org_id, med_id), "quantity": {"$gte": qty}},
        {"$inc": {"quantity": -qty}, "$set": {"updated_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not inv:
        raise HTTPException(status_code=409, detail="Insufficient stock or inventory record not found")

    remaining = qty
    breakdown = []
    batch_docs = list(mongo.batches.find({
        **inventory_filter(org_id, med_id), "quantity": {"$gt": 0}, "status": "active",
        "expiry_date": {"$gte": datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)},
    }).sort("expiry_date", 1))

    # If batches exist, enforce batch-level stock. Legacy aggregate-only stock remains usable.
    total_batch_qty = sum(int(b.get("quantity", 0)) for b in batch_docs)
    has_batch_records = mongo.batches.find_one({**inventory_filter(org_id, med_id), "quantity": {"$gt": 0}}) is not None
    if has_batch_records and total_batch_qty < qty:
        mongo.inventory.update_one({"_id": inv["_id"]}, {"$inc": {"quantity": qty}, "$set": {"updated_at": now()}})
        raise HTTPException(status_code=409, detail="Not enough non-expired batch stock for this issue")

    try:
        for batch in batch_docs:
            if remaining <= 0:
                break
            take = min(remaining, int(batch.get("quantity", 0)))
            updated = mongo.batches.update_one(
                {"_id": batch["_id"], "quantity": {"$gte": take}},
                {"$inc": {"quantity": -take}, "$set": {"updated_at": now()}},
            )
            if updated.modified_count != 1:
                raise RuntimeError("Batch quantity changed during stock issue; retry the operation")
            breakdown.append({
                "batch_number": batch.get("batch_number"), "quantity": take,
                "expiry_date": batch.get("expiry_date"),
            })
            remaining -= take
        if has_batch_records and remaining > 0:
            raise RuntimeError("Batch stock was insufficient")
    except Exception as exc:
        # Best-effort compensation for a failed multi-document operation.
        for used in breakdown:
            mongo.batches.update_one(
                {**inventory_filter(org_id, med_id), "batch_number": used["batch_number"]},
                {"$inc": {"quantity": used["quantity"]}, "$set": {"updated_at": now()}},
            )
        mongo.inventory.update_one({"_id": inv["_id"]}, {"$inc": {"quantity": qty}, "$set": {"updated_at": now()}})
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(status_code=409, detail=str(exc))

    mongo.stock_transactions.insert_one({
        "organization_id": org_id, "medicine_id": med_id,
        "transaction_type": "stock_out", "reason": reason, "quantity": qty,
        "batch_breakdown": breakdown, "reference": reference,
        "performed_by": performed_by, "created_at": now(),
    })
    return mongo.inventory.find_one({"_id": inv["_id"]}), breakdown


@app.on_event("startup")
def startup():
    try:
        mongo.check_database()
        mongo.create_indexes()
    except Exception as exc:
        # Keep the API process available so /api/health can report the issue.
        print(f"MediNexus startup database warning: {exc}")


@app.get("/")
def home():
    return {"message": "MediNexus API is running", "version": "2.0.0", "docs": "/docs"}


@app.get("/api/health")
def health():
    try:
        mongo.check_database()
        return {"status": "ok", "database": "connected", "database_name": mongo.DATABASE_NAME}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"MongoDB connection failed: {str(exc)}")


# AUTHENTICATION AND ORGANIZATIONS
@app.post("/api/organizations", status_code=201)
def create_organization(data: OrganizationCreate, request: Request):
    # Initial organization setup is allowed only before the first organization exists.
    if mongo.organizations.count_documents({}) > 0:
        user = getattr(request.state, "user", None)
        if not user or user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Administrator permission required")
    doc = data.model_dump()
    doc.update({"status": "active", "created_at": now(), "updated_at": now()})
    try:
        result = mongo.organizations.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Organization code already exists")
    log_activity("organization_created", "organization", result.inserted_id, "system")
    return to_json(mongo.organizations.find_one({"_id": result.inserted_id}))


@app.get("/api/organizations")
def list_organizations(active_only: bool = True):
    query = {"status": "active"} if active_only else {}
    return [to_json(x) for x in mongo.organizations.find(query).sort("name", 1)]


@app.post("/api/auth/register", status_code=201)
def register(data: UserRegister):
    require_exists(mongo.organizations, data.organization_id, "Organization")
    doc = data.model_dump()
    password = doc.pop("password")
    # Never trust a role supplied by a public registration form. The first account
    # bootstraps the workspace administrator; all later public registrations are staff.
    doc["role"] = "admin" if mongo.users.count_documents({}) == 0 else "staff"
    doc["email"] = doc["email"].lower()
    doc["password_hash"] = pwd_context.hash(password)
    doc.update({"status": "active", "created_at": now(), "updated_at": now()})
    try:
        result = mongo.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email is already registered")
    log_activity("user_registered", "user", result.inserted_id, str(result.inserted_id), data.organization_id)
    return {"message": "User registered successfully", "user_id": str(result.inserted_id), "email": data.email}


@app.post("/api/auth/login")
def login(data: UserLogin):
    user = mongo.users.find_one({"email": data.email.lower()})
    if not user or not pwd_context.verify(data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("status", "active") != "active":
        raise HTTPException(status_code=403, detail="This account is inactive")
    expires = now() + timedelta(minutes=JWT_EXPIRE_MINUTES)
    token = jwt.encode({"sub": str(user["_id"]), "role": user.get("role", "staff"), "exp": expires}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat(),
            "user": {"id": str(user["_id"]), "name": user.get("name"), "email": user.get("email"),
                     "role": user.get("role"), "organization_id": user.get("organization_id")}}


@app.get("/api/auth/me")
def get_current_user(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"id": str(user["_id"]), "name": user.get("name"), "email": user.get("email"),
            "role": user.get("role", "staff"), "organization_id": user.get("organization_id")}


@app.get("/api/users")
def list_users(organization_id: str | None = None):
    query = {"organization_id": organization_id} if organization_id else {}
    return [to_json(x) for x in mongo.users.find(query, {"password_hash": 0}).sort("name", 1)]


# MEDICINES
@app.post("/api/medicines", status_code=201)
def create_medicine(data: MedicineCreate):
    doc = data.model_dump()
    doc.update({"status": "active", "created_at": now(), "updated_at": now()})
    try:
        result = mongo.medicines.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Medicine code already exists")
    log_activity("medicine_created", "medicine", result.inserted_id, "system")
    return to_json(mongo.medicines.find_one({"_id": result.inserted_id}))


@app.get("/api/medicines")
def list_medicines(search: str | None = None, category: str | None = None):
    query = {"status": {"$ne": "inactive"}}
    if category:
        query["category"] = category
    if search:
        query["$or"] = [{"name": {"$regex": search, "$options": "i"}}, {"medicine_code": {"$regex": search, "$options": "i"}}]
    return [to_json(x) for x in mongo.medicines.find(query).sort("name", 1)]


@app.put("/api/medicines/{medicine_id}")
def update_medicine(medicine_id: str, data: dict):
    allowed = {"name", "composition", "category", "unit", "description", "status"}
    changes = {k: v for k, v in data.items() if k in allowed}
    if not changes:
        raise HTTPException(status_code=400, detail="No valid medicine fields supplied")
    changes["updated_at"] = now()
    result = mongo.medicines.find_one_and_update({"_id": oid(medicine_id)}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    if not result:
        raise HTTPException(status_code=404, detail="Medicine not found")
    return to_json(result)


# INVENTORY AND BATCHES
@app.post("/api/inventory", status_code=201)
def create_inventory(data: InventoryCreate):
    require_exists(mongo.organizations, data.organization_id, "Organization")
    require_exists(mongo.medicines, data.medicine_id, "Medicine")
    doc = data.model_dump()
    doc.update({"quantity": 0, "created_at": now(), "updated_at": now()})
    try:
        result = mongo.inventory.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Inventory already exists for this organization and medicine")
    log_activity("inventory_record_created", "inventory", result.inserted_id, "system", data.organization_id)
    return to_json(mongo.inventory.find_one({"_id": result.inserted_id}))


@app.get("/api/inventory")
def list_inventory(organization_id: str | None = None, medicine_id: str | None = None, stock_status: str | None = None):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if medicine_id: query["medicine_id"] = medicine_id
    records = list(mongo.inventory.find(query))
    output = []
    for rec in records:
        qty, minimum = int(rec.get("quantity", 0)), int(rec.get("minimum_stock", 0))
        state = "out_of_stock" if qty == 0 else "low_stock" if qty <= minimum else "available"
        rec["stock_status"] = state
        if stock_status and state != stock_status: continue
        med = find_by_id(mongo.medicines, str(rec.get("medicine_id", "")))
        org = find_by_id(mongo.organizations, str(rec.get("organization_id", "")))
        rec["medicine_name"] = med.get("name") if med else rec.get("medicine_name", "Unknown medicine")
        rec["organization_name"] = org.get("name") if org else rec.get("organization_name", "Unknown organization")
        output.append(to_json(rec))
    return output


@app.put("/api/inventory/{inventory_id}")
def update_inventory(inventory_id: str, data: InventoryUpdate):
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    changes["updated_at"] = now()
    doc = mongo.inventory.find_one_and_update({"_id": oid(inventory_id)}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    if not doc: raise HTTPException(status_code=404, detail="Inventory record not found")
    log_activity("inventory_settings_updated", "inventory", inventory_id, "system", doc.get("organization_id"), changes)
    return to_json(doc)


@app.post("/api/stock-in", status_code=201)
def stock_in(data: StockIn):
    require_exists(mongo.organizations, data.organization_id, "Organization")
    require_exists(mongo.medicines, data.medicine_id, "Medicine")
    if data.expiry_date <= date.today():
        raise HTTPException(status_code=400, detail="Cannot receive an already expired batch")
    if data.manufacturing_date and data.manufacturing_date > data.expiry_date:
        raise HTTPException(status_code=400, detail="Manufacturing date cannot be after expiry date")
    if data.supplier_id: require_exists(mongo.suppliers, data.supplier_id, "Supplier")
    inv = add_stock(data.organization_id, data.medicine_id, data.quantity, data.batch_number,
                    data.expiry_date, data.manufacturing_date, data.supplier_id, data.reference, data.performed_by)
    log_activity("stock_received", "inventory", inv["_id"], data.performed_by, data.organization_id,
                 {"medicine_id": data.medicine_id, "quantity": data.quantity, "batch_number": data.batch_number})
    return {"message": "Stock received successfully", "inventory": to_json(inv)}


@app.post("/api/stock-out", status_code=201)
def stock_out(data: StockOut):
    inv, batch_breakdown = remove_stock(data.organization_id, data.medicine_id, data.quantity, data.reason, data.reference, data.performed_by)
    log_activity("stock_issued", "inventory", inv["_id"], data.performed_by, data.organization_id,
                 {"medicine_id": data.medicine_id, "quantity": data.quantity, "reason": data.reason})
    maybe_create_low_stock_notification(inv)
    return {"message": "Stock issued successfully", "inventory": to_json(inv), "batch_breakdown": to_json(batch_breakdown)}


@app.get("/api/batches")
def list_batches(organization_id: str | None = None, medicine_id: str | None = None, expiring_within_days: int | None = Query(default=None, ge=1, le=365)):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if medicine_id: query["medicine_id"] = medicine_id
    if expiring_within_days is not None:
        query["expiry_date"] = {"$lte": datetime.combine(date.today() + timedelta(days=expiring_within_days), datetime.max.time(), tzinfo=timezone.utc)}
    return [to_json(x) for x in mongo.batches.find(query).sort("expiry_date", 1)]


@app.get("/api/stock-transactions")
def list_stock_transactions(organization_id: str | None = None, medicine_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if medicine_id: query["medicine_id"] = medicine_id
    return [to_json(x) for x in mongo.stock_transactions.find(query).sort("created_at", -1).limit(limit)]


def maybe_create_low_stock_notification(inv):
    qty, minimum = int(inv.get("quantity", 0)), int(inv.get("minimum_stock", 0))
    if qty <= minimum:
        med = find_by_id(mongo.medicines, str(inv.get("medicine_id")))
        existing = mongo.notifications.find_one({
            "organization_id": inv.get("organization_id"), "type": "low_stock",
            "related_id": str(inv.get("_id")), "is_resolved": False,
        })
        if not existing:
            notify(inv.get("organization_id"), "Low stock alert",
                   f"{med.get('name') if med else 'Medicine'} has {qty} units remaining (minimum: {minimum}).",
                   "low_stock", "high" if qty == 0 else "medium", str(inv.get("_id")))


# MEDICINE REQUESTS AND PRIORITY ALLOCATION
@app.post("/api/medicine-requests", status_code=201)
def create_medicine_request(data: MedicineRequestCreate):
    require_exists(mongo.organizations, data.organization_id, "Organization")
    require_exists(mongo.medicines, data.medicine_id, "Medicine")
    doc = data.model_dump()
    doc.update({"status": "pending", "priority_rank": URGENCY_RANK[data.urgency], "created_at": now(), "updated_at": now()})
    result = mongo.medicine_requests.insert_one(doc)
    notify(data.organization_id, "New medicine request", f"A {data.urgency} priority request for {data.quantity} units was submitted.", "request_created", data.urgency, str(result.inserted_id))
    log_activity("medicine_request_created", "medicine_request", result.inserted_id, data.requested_by, data.organization_id)
    return to_json(mongo.medicine_requests.find_one({"_id": result.inserted_id}))


@app.get("/api/medicine-requests")
def list_medicine_requests(organization_id: str | None = None, request_status: str | None = None):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if request_status: query["status"] = request_status
    return [to_json(x) for x in mongo.medicine_requests.find(query).sort([("priority_rank", -1), ("created_at", 1)])]


def process_request(req, reviewer: str, notes: str = ""):
    if req.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Only pending requests can be approved")
    inv = mongo.inventory.find_one(inventory_filter(req["organization_id"], req["medicine_id"]))
    if not inv or int(inv.get("quantity", 0)) < int(req["quantity"]):
        raise HTTPException(status_code=409, detail="Insufficient inventory to fulfil this request")
    updated_inv, batches_used = remove_stock(req["organization_id"], req["medicine_id"], int(req["quantity"]), "medicine_request", str(req["_id"]), reviewer)
    updated_req = mongo.medicine_requests.find_one_and_update(
        {"_id": req["_id"], "status": "pending"},
        {"$set": {"status": "approved", "reviewed_by": reviewer, "review_notes": notes,
                  "approved_at": now(), "batch_breakdown": batches_used, "updated_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated_req:
        # Compensate if another reviewer changed request status concurrently.
        mongo.inventory.update_one({"_id": updated_inv["_id"]}, {"$inc": {"quantity": req["quantity"]}})
        for b in batches_used:
            mongo.batches.update_one({**inventory_filter(req["organization_id"], req["medicine_id"]), "batch_number": b["batch_number"]}, {"$inc": {"quantity": b["quantity"]}})
        raise HTTPException(status_code=409, detail="Request was already processed")
    notify(req["organization_id"], "Medicine request approved", f"Your request for {req['quantity']} units has been approved.", "request_approved", "low", str(req["_id"]))
    log_activity("medicine_request_approved", "medicine_request", req["_id"], reviewer, req["organization_id"])
    maybe_create_low_stock_notification(updated_inv)
    return updated_req


@app.put("/api/medicine-requests/{request_id}/decision")
def decide_medicine_request(request_id: str, data: RequestDecision):
    req = mongo.medicine_requests.find_one({"_id": oid(request_id)})
    if not req: raise HTTPException(status_code=404, detail="Medicine request not found")
    if data.decision == "approve":
        return to_json(process_request(req, data.reviewed_by, data.notes))
    updated = mongo.medicine_requests.find_one_and_update(
        {"_id": req["_id"], "status": "pending"},
        {"$set": {"status": "rejected", "reviewed_by": data.reviewed_by, "review_notes": data.notes, "updated_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated: raise HTTPException(status_code=409, detail="Request is no longer pending")
    notify(req["organization_id"], "Medicine request rejected", data.notes or "Your medicine request was rejected.", "request_rejected", "medium", request_id)
    log_activity("medicine_request_rejected", "medicine_request", request_id, data.reviewed_by, req.get("organization_id"))
    return to_json(updated)


@app.post("/api/medicine-requests/process-priority")
def process_priority_queue(organization_id: str, reviewed_by: str = "system", max_requests: int = Query(default=20, ge=1, le=100)):
    """Process pending requests from highest urgency, then oldest first."""
    pending = list(mongo.medicine_requests.find({"organization_id": organization_id, "status": "pending"}).sort([("priority_rank", -1), ("created_at", 1)]).limit(max_requests))
    processed, skipped = [], []
    for req in pending:
        try:
            result = process_request(req, reviewed_by, "Processed by priority queue")
            processed.append(str(result["_id"]))
        except HTTPException as exc:
            skipped.append({"request_id": str(req["_id"]), "reason": exc.detail})
    return {"processed_count": len(processed), "processed": processed, "skipped": skipped}


# SUPPLIERS AND PURCHASE ORDERS
@app.post("/api/suppliers", status_code=201)
def create_supplier(data: SupplierCreate):
    doc = data.model_dump()
    doc.update({"status": "active", "created_at": now(), "updated_at": now()})
    result = mongo.suppliers.insert_one(doc)
    log_activity("supplier_created", "supplier", result.inserted_id, "system")
    return to_json(mongo.suppliers.find_one({"_id": result.inserted_id}))


@app.get("/api/suppliers")
def list_suppliers():
    return [to_json(x) for x in mongo.suppliers.find({"status": {"$ne": "inactive"}}).sort("name", 1)]


@app.post("/api/purchase-orders", status_code=201)
def create_purchase_order(data: PurchaseOrderCreate):
    require_exists(mongo.organizations, data.organization_id, "Organization")
    require_exists(mongo.suppliers, data.supplier_id, "Supplier")
    if not data.items:
        raise HTTPException(status_code=400, detail="Purchase order must contain at least one item")
    clean_items = []
    total = 0.0
    for item in data.items:
        med_id = str(item.get("medicine_id", ""))
        require_exists(mongo.medicines, med_id, "Medicine")
        quantity = int(item.get("quantity", 0))
        unit_price = float(item.get("unit_price", 0))
        if quantity <= 0 or unit_price < 0:
            raise HTTPException(status_code=400, detail="Each item needs positive quantity and non-negative unit price")
        line_total = quantity * unit_price
        total += line_total
        clean_items.append({"medicine_id": med_id, "quantity": quantity, "unit_price": unit_price, "line_total": line_total})
    doc = {"organization_id": data.organization_id, "supplier_id": data.supplier_id,
           "items": clean_items, "total_amount": round(total, 2), "currency": "INR",
           "status": "draft", "created_by": data.created_by, "notes": data.notes,
           "created_at": now(), "updated_at": now()}
    result = mongo.purchase_orders.insert_one(doc)
    log_activity("purchase_order_created", "purchase_order", result.inserted_id, data.created_by, data.organization_id)
    return to_json(mongo.purchase_orders.find_one({"_id": result.inserted_id}))


@app.get("/api/purchase-orders")
def list_purchase_orders(organization_id: str | None = None, order_status: str | None = None):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if order_status: query["status"] = order_status
    return [to_json(x) for x in mongo.purchase_orders.find(query).sort("created_at", -1)]


@app.put("/api/purchase-orders/{order_id}/status")
def update_purchase_order_status(order_id: str, new_status: str, performed_by: str = "system"):
    allowed = {"submitted", "confirmed", "partially_received", "received", "cancelled"}
    if new_status not in allowed: raise HTTPException(status_code=400, detail=f"Status must be one of {sorted(allowed)}")
    doc = mongo.purchase_orders.find_one_and_update({"_id": oid(order_id), "status": {"$nin": ["received", "cancelled"]}},
        {"$set": {"status": new_status, "updated_at": now()}}, return_document=ReturnDocument.AFTER)
    if not doc: raise HTTPException(status_code=404, detail="Purchase order not found or already closed")
    log_activity("purchase_order_status_changed", "purchase_order", order_id, performed_by, doc.get("organization_id"), {"status": new_status})
    return to_json(doc)


# SMART STOCK REDISTRIBUTION / TRANSFERS
@app.post("/api/redistribution/recommend")
def recommend_redistribution(medicine_id: str, receiving_organization_id: str, requested_quantity: int = Query(gt=0)):
    require_exists(mongo.medicines, medicine_id, "Medicine")
    receiver = mongo.inventory.find_one(inventory_filter(receiving_organization_id, medicine_id))
    if not receiver: raise HTTPException(status_code=404, detail="Receiving organization has no inventory record")
    shortage = max(0, int(receiver.get("minimum_stock", 0)) - int(receiver.get("quantity", 0)))
    target_qty = min(requested_quantity, shortage if shortage else requested_quantity)
    donors = mongo.inventory.find({"medicine_id": medicine_id, "organization_id": {"$ne": receiving_organization_id}, "$expr": {"$gt": ["$quantity", "$minimum_stock"]}}).sort("quantity", -1)
    recommendations, remaining = [], target_qty
    for donor in donors:
        surplus = max(0, int(donor.get("quantity", 0)) - int(donor.get("minimum_stock", 0)))
        available_batches = sum(int(x.get("quantity", 0)) for x in mongo.batches.find({
            "organization_id": donor["organization_id"], "medicine_id": medicine_id,
            "quantity": {"$gt": 0}, "status": "active",
            "expiry_date": {"$gte": datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)},
        }))
        if available_batches:
            surplus = min(surplus, available_batches)
        take = min(surplus, remaining)
        if take:
            recommendations.append({"from_organization_id": donor["organization_id"], "to_organization_id": receiving_organization_id, "medicine_id": medicine_id, "available_surplus": surplus, "suggested_quantity": take})
            remaining -= take
        if remaining <= 0: break
    return {"requested_quantity": requested_quantity, "recommended_quantity": target_qty - remaining,
            "unmet_quantity": remaining, "recommendations": recommendations, "requires_approval": True}


@app.post("/api/transfers", status_code=201)
def create_transfer(data: TransferCreate):
    if data.from_organization_id == data.to_organization_id:
        raise HTTPException(status_code=400, detail="Source and destination organizations must be different")
    require_exists(mongo.organizations, data.from_organization_id, "Source organization")
    require_exists(mongo.organizations, data.to_organization_id, "Destination organization")
    require_exists(mongo.medicines, data.medicine_id, "Medicine")
    source = mongo.inventory.find_one(inventory_filter(data.from_organization_id, data.medicine_id))
    if not source or int(source.get("quantity", 0)) < data.quantity:
        raise HTTPException(status_code=409, detail="Source organization does not have enough stock")
    doc = data.model_dump()
    doc.update({"status": "pending_approval", "created_at": now(), "updated_at": now()})
    result = mongo.transfers.insert_one(doc)
    notify(data.from_organization_id, "Transfer approval required", f"A transfer of {data.quantity} units has been requested.", "transfer_requested", "medium", str(result.inserted_id))
    log_activity("transfer_created", "transfer", result.inserted_id, data.requested_by, data.from_organization_id)
    return to_json(mongo.transfers.find_one({"_id": result.inserted_id}))


@app.get("/api/transfers")
def list_transfers(organization_id: str | None = None, transfer_status: str | None = None):
    query = {}
    if organization_id: query["$or"] = [{"from_organization_id": organization_id}, {"to_organization_id": organization_id}]
    if transfer_status: query["status"] = transfer_status
    return [to_json(x) for x in mongo.transfers.find(query).sort("created_at", -1)]


@app.put("/api/transfers/{transfer_id}/action")
def transfer_action(transfer_id: str, data: TransferAction):
    transfer = mongo.transfers.find_one({"_id": oid(transfer_id)})
    if not transfer: raise HTTPException(status_code=404, detail="Transfer not found")
    current = transfer.get("status")
    if data.action == "approve":
        if current != "pending_approval": raise HTTPException(status_code=409, detail="Only pending transfers can be approved")
        changes = {"status": "approved", "approved_by": data.performed_by, "approved_at": now()}
    elif data.action == "reject":
        if current != "pending_approval": raise HTTPException(status_code=409, detail="Only pending transfers can be rejected")
        changes = {"status": "rejected", "reviewed_by": data.performed_by}
    elif data.action == "dispatch":
        if current != "approved": raise HTTPException(status_code=409, detail="Transfer must be approved before dispatch")
        _, breakdown = remove_stock(transfer["from_organization_id"], transfer["medicine_id"], int(transfer["quantity"]), "transfer_dispatch", transfer_id, data.performed_by)
        changes = {"status": "in_transit", "dispatched_by": data.performed_by, "dispatched_at": now(), "batch_breakdown": breakdown}
    else:  # receive
        if current != "in_transit": raise HTTPException(status_code=409, detail="Only in-transit transfers can be received")
        for batch in transfer.get("batch_breakdown", []):
            exp = batch.get("expiry_date")
            if isinstance(exp, datetime): exp_date = exp.date()
            elif isinstance(exp, str): exp_date = date.fromisoformat(exp[:10])
            else: exp_date = date.today() + timedelta(days=365)
            if exp_date <= date.today():
                raise HTTPException(status_code=409, detail="Transfer contains an expired batch; receiving is blocked")
            add_stock(transfer["to_organization_id"], transfer["medicine_id"], int(batch["quantity"]),
                      f"TR-{transfer_id[:8]}-{batch['batch_number']}", exp_date, reference=transfer_id,
                      performed_by=data.performed_by, transaction_type="transfer_in")
        changes = {"status": "completed", "received_by": data.performed_by, "received_at": now()}
    changes.update({"notes": data.notes or transfer.get("notes", ""), "updated_at": now()})
    updated = mongo.transfers.find_one_and_update({"_id": transfer["_id"], "status": current}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(status_code=409, detail="Transfer changed concurrently; refresh and retry")
    log_activity(f"transfer_{data.action}d", "transfer", transfer_id, data.performed_by, transfer.get("from_organization_id"))
    notify(transfer.get("to_organization_id"), f"Transfer {data.action}d", f"Transfer {transfer_id} is now {updated['status']}.", "transfer_update", "low", transfer_id)
    return to_json(updated)


# NOTIFICATIONS / ALERT SCAN
@app.post("/api/notifications/scan")
def scan_and_create_notifications(expiry_warning_days: int = Query(default=30, ge=1, le=180)):
    created = 0
    for inv in mongo.inventory.find({}):
        before = mongo.notifications.count_documents({"type": "low_stock", "related_id": str(inv["_id"]), "is_resolved": False})
        maybe_create_low_stock_notification(inv)
        after = mongo.notifications.count_documents({"type": "low_stock", "related_id": str(inv["_id"]), "is_resolved": False})
        created += max(0, after - before)
    cutoff = datetime.combine(date.today() + timedelta(days=expiry_warning_days), datetime.max.time(), tzinfo=timezone.utc)
    today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
    for batch in mongo.batches.find({"quantity": {"$gt": 0}, "expiry_date": {"$lte": cutoff}}):
        expiry = batch.get("expiry_date")
        if not isinstance(expiry, datetime): continue
        kind = "expired_batch" if expiry < today_start else "near_expiry"
        exists = mongo.notifications.find_one({"type": kind, "related_id": str(batch["_id"]), "is_resolved": False})
        if not exists:
            med = find_by_id(mongo.medicines, str(batch.get("medicine_id")))
            notify(batch.get("organization_id"), "Medicine batch expiry alert",
                   f"{med.get('name') if med else 'Medicine'} batch {batch.get('batch_number')} expires on {expiry.date().isoformat()}.",
                   kind, "high" if kind == "expired_batch" else "medium", str(batch["_id"]))
            created += 1
    return {"message": "Notification scan completed", "new_notifications": created}


@app.get("/api/notifications")
def list_notifications(organization_id: str | None = None, unread_only: bool = False, limit: int = Query(default=100, ge=1, le=500)):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if unread_only: query["is_read"] = False
    return [to_json(x) for x in mongo.notifications.find(query).sort("created_at", -1).limit(limit)]


@app.put("/api/notifications/{notification_id}")
def update_notification(notification_id: str, data: NotificationUpdate):
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if not changes: raise HTTPException(status_code=400, detail="No notification fields supplied")
    changes["updated_at"] = now()
    doc = mongo.notifications.find_one_and_update({"_id": oid(notification_id)}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    if not doc: raise HTTPException(status_code=404, detail="Notification not found")
    return to_json(doc)


# AUDIT LOGS AND DASHBOARD
@app.get("/api/audit-logs")
def list_audit_logs(organization_id: str | None = None, entity: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    query = {}
    if organization_id: query["organization_id"] = organization_id
    if entity: query["entity"] = entity
    return [to_json(x) for x in mongo.audit_logs.find(query).sort("created_at", -1).limit(limit)]


@app.get("/api/dashboard/summary")
def dashboard_summary(organization_id: str | None = None):
    query = {"organization_id": organization_id} if organization_id else {}
    inv = list(mongo.inventory.find(query))
    total_units = sum(int(x.get("quantity", 0)) for x in inv)
    low_stock = sum(1 for x in inv if int(x.get("quantity", 0)) <= int(x.get("minimum_stock", 0)))
    out_of_stock = sum(1 for x in inv if int(x.get("quantity", 0)) == 0)
    org_query = {"_id": oid(organization_id)} if organization_id and ObjectId.is_valid(organization_id) else ({"organization_code": organization_id} if organization_id else {})
    org_count = mongo.organizations.count_documents(org_query) if not organization_id else 1
    req_query = {"organization_id": organization_id} if organization_id else {}
    transfer_query = {"$or": [{"from_organization_id": organization_id}, {"to_organization_id": organization_id}]} if organization_id else {}
    return {
        "inventory_records": len(inv), "total_units": total_units,
        "low_stock_items": low_stock, "out_of_stock_items": out_of_stock,
        "organizations": org_count,
        "pending_requests": mongo.medicine_requests.count_documents({**req_query, "status": "pending"}),
        "pending_transfers": mongo.transfers.count_documents({**transfer_query, "status": {"$in": ["pending_approval", "approved", "in_transit"]}}),
        "unread_notifications": mongo.notifications.count_documents({**req_query, "is_read": False}),
        "active_medicines": mongo.medicines.count_documents({"status": {"$ne": "inactive"}}),
    }


@app.get("/api/dashboard/organization-stock")
def organization_stock(medicine_id: str | None = None):
    match = {"medicine_id": medicine_id} if medicine_id else {}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$organization_id", "total_units": {"$sum": "$quantity"}, "medicine_count": {"$sum": 1}, "low_stock_count": {"$sum": {"$cond": [{"$lte": ["$quantity", "$minimum_stock"]}, 1, 0]}}}},
        {"$sort": {"total_units": -1}},
    ]
    rows = list(mongo.inventory.aggregate(pipeline))
    for row in rows:
        org = find_by_id(mongo.organizations, str(row["_id"]))
        row["organization_name"] = org.get("name") if org else str(row["_id"])
        row["organization_id"] = str(row.pop("_id"))
    return to_json(rows)


@app.get("/api/dashboard/recent-activity")
def recent_activity(limit: int = Query(default=10, ge=1, le=100)):
    return [to_json(x) for x in mongo.audit_logs.find({}).sort("created_at", -1).limit(limit)]


# ==========================================================
# MEDINEXUS AI INTEGRATION
# FastAPI Backend -> Flask AI Service
# ==========================================================

def call_ai_service(endpoint: str, method: str = "GET", params=None):

    url = f"{AI_SERVICE_URL.rstrip('/')}/{endpoint.lstrip('/')}"

    try:
        with httpx.Client(timeout=60.0) as client:

            if method == "POST":
                response = client.post(url, params=params)
            else:
                response = client.get(url, params=params)

            response.raise_for_status()

            return response.json()

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"AI service error: {exc.response.text[:500]}"
        )

    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"AI service is unavailable: {str(exc)}"
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="AI service returned an invalid JSON response"
        )


# 1. Check AI connection
@app.get("/api/ai/health")
def ai_health():

    result = call_ai_service("/api/health")

    return {
        "backend": "connected",
        "ai_service": result
    }


# 2. AI dashboard summary
@app.get("/api/ai/summary")
def ai_summary():

    return call_ai_service("/api/summary")


# 3. AI recommendations
@app.get("/api/ai/recommendations")
def ai_recommendations(request: Request):

    return call_ai_service(
        "/api/recommendations",
        params=dict(request.query_params)
    )


# 4. Medicine shortage predictions
@app.get("/api/ai/shortage-risk")
def ai_shortage_risk(request: Request):

    return call_ai_service(
        "/api/shortage-risk",
        params=dict(request.query_params)
    )


# 5. Medicine expiry predictions
@app.get("/api/ai/expiry-risk")
def ai_expiry_risk(request: Request):

    return call_ai_service(
        "/api/expiry-risk",
        params=dict(request.query_params)
    )


# 6. Demand forecasting
@app.get("/api/ai/forecasts")
def ai_forecasts(request: Request):

    return call_ai_service(
        "/api/forecasts",
        params=dict(request.query_params)
    )


# 7. Forecast accuracy metrics
@app.get("/api/ai/forecast-metrics")
def ai_forecast_metrics():

    return call_ai_service("/api/forecast-metrics")


# 8. Outbreak / spike events
@app.get("/api/ai/spike-events")
def ai_spike_events():

    return call_ai_service("/api/spike-events")


# 9. Latest spike signals
@app.get("/api/ai/spike-signals")
def ai_spike_signals():

    return call_ai_service("/api/spike-signals")


# 10. Facility information from AI data
@app.get("/api/ai/facilities")
def ai_facilities(request: Request):

    return call_ai_service(
        "/api/facilities",
        params=dict(request.query_params)
    )


# 11. Run the AI pipeline
@app.post("/api/ai/run")
def run_ai_pipeline():

    return call_ai_service(
        "/api/run-ai",
        method="POST"
    )


# 12. Check AI pipeline execution status
@app.get("/api/ai/run/status")
def ai_pipeline_status():

    return call_ai_service("/api/run-ai/status")


# ==========================================================
# END OF AI INTEGRATION
# ==========================================================
