import datetime
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-cec5bf0618af"

def seed_firestore():
    db = firestore.Client(project=PROJECT_ID)
    pantry_ref = db.collection("pantry_items")

    sample_items = [
        {
            "id": "item-1",
            "name": "Organic Whole Milk",
            "category": "Dairy",
            "quantity": 1,
            "unit": "carton",
            "expiration_date": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)).strftime("%Y-%m-%d"),
        },
        {
            "id": "item-2",
            "name": "Arborio Rice",
            "category": "Grains",
            "quantity": 2,
            "unit": "kg",
            "expiration_date": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=180)).strftime("%Y-%m-%d"),
        },
        {
            "id": "item-3",
            "name": "Fresh Basil",
            "category": "Produce",
            "quantity": 1,
            "unit": "bunch",
            "expiration_date": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)).strftime("%Y-%m-%d"),
        },
        {
            "id": "item-4",
            "name": "Extra Virgin Olive Oil",
            "category": "Pantry Staples",
            "quantity": 1,
            "unit": "bottle",
            "expiration_date": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)).strftime("%Y-%m-%d"),
        },
    ]

    for item in sample_items:
        pantry_ref.document(item["id"]).set(item)
        print(f"Seeded document {item['id']}: {item['name']}")

    print("Firestore seeding complete!")

if __name__ == "__main__":
    seed_firestore()
