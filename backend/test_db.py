
from database import client, db

try:
    print("Checking MongoDB Atlas connection...")

    client.admin.command("ping")
    print("MongoDB Atlas connected successfully!")

    print("Database:", db.name)

    result = db.connection_test.insert_one({
        "message": "MediNexus connection test",
        "status": "success"
    })

    print("Test record inserted:", result.inserted_id)

    client.admin.command("ping")
    print("MongoDB connection verified after insertion.")

except Exception as e:
    print("MongoDB connection failed:")
    print(repr(e))

finally:
    client.close()
