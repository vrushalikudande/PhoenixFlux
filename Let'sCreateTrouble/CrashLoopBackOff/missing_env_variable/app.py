from fastapi import FastAPI
import os
import psycopg2
import time

app = FastAPI()

# Fetch the database URL from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("[ERROR] Missing DATABASE_URL environment variable. Exiting...")
    time.sleep(5)  # Simulate retry before failing
    exit(1)

try:
    conn = psycopg2.connect(DATABASE_URL)
    print("[INFO] Successfully connected to the database.")
except Exception as e:
    print(f"[ERROR] Failed to connect to the database: {e}")
    exit(1)

@app.get("/")
def read_root():
    return {"message": "Service is running!"}
