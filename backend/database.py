
"""MongoDB Atlas connection and collection definitions for MediNexus."""

import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING

# Load backend .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

MONGODB_URI = os.getenv("MONGODB_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "MediNexus")

if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI is missing. Configure your backend .env file."
    )

# MongoDB connection
client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=20000,
    connectTimeoutMS=20000,
    socketTimeoutMS=20000,
    tls=True
)

db = client[DATABASE_NAME]

# Collections
organizations = db["organizations"]
users = db["users"]
medicines = db["medicines"]
inventory = db["inventory"]
batches = db["batches"]
stock_transactions = db["stockTransactions"]
medicine_requests = db["medicineRequests"]
suppliers = db["suppliers"]
purchase_orders = db["purchaseOrders"]
transfers = db["transfers"]
notifications = db["notifications"]
audit_logs = db["auditLogs"]


def check_database():
    """Check MongoDB Atlas connectivity."""
    client.admin.command("ping")
    return True


def create_indexes():
    """Create useful database indexes."""

    organizations.create_index(
        [("organization_code", ASCENDING)],
        unique=True,
        sparse=True
    )

    users.create_index(
        [("email", ASCENDING)],
        unique=True,
        sparse=True
    )

    medicines.create_index(
        [("medicine_code", ASCENDING)],
        unique=True,
        sparse=True
    )

    inventory.create_index(
        [
            ("organization_id", ASCENDING),
            ("medicine_id", ASCENDING)
        ],
        unique=True
    )

    batches.create_index([
        ("organization_id", ASCENDING),
        ("medicine_id", ASCENDING),
        ("expiry_date", ASCENDING)
    ])

    stock_transactions.create_index([
        ("organization_id", ASCENDING),
        ("created_at", DESCENDING)
    ])

    medicine_requests.create_index([
        ("status", ASCENDING),
        ("priority", DESCENDING),
        ("created_at", ASCENDING)
    ])

    transfers.create_index([
        ("status", ASCENDING),
        ("created_at", DESCENDING)
    ])

    notifications.create_index([
        ("organization_id", ASCENDING),
        ("is_read", ASCENDING),
        ("created_at", DESCENDING)
    ])


print("MediNexus database configuration loaded.")
