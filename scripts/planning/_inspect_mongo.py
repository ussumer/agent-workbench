"""Temporary inspector: find runaway episode checkpoints in Mongo."""
import json
import sys
from collections import Counter

from pymongo import MongoClient

for port in (27028, 27017):
    client = MongoClient(f"mongodb://127.0.0.1:{port}", serverSelectionTimeoutMS=2000)
    try:
        names = client.list_database_names()
    except Exception as exc:
        print(port, "unreachable", type(exc).__name__)
        continue
    t64 = [n for n in names if "v4-compute-t64" in n]
    print(port, "dbs:", t64)
    for name in t64:
        db = client[name]
        for collection in db.list_collection_names():
            if "checkpoint" not in collection:
                continue
            pipeline = [
                {"$match": {"thread_id": {"$regex": "^v3-compute-"}}},
                {"$group": {"_id": "$thread_id", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": 8},
            ]
            for row in db[collection].aggregate(pipeline):
                print("  ", collection, row["_id"], "writes", row["n"])
