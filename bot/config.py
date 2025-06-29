from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
THREAD_ID = int(os.getenv("THREAD_ID")) if os.getenv("THREAD_ID") else None
FIREBASE_SERVICE_ACCOUNT_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

if not BOT_TOKEN or not THREAD_ID or not FIREBASE_SERVICE_ACCOUNT_PATH:
    raise ValueError("Missing BOT_TOKEN, THREAD_ID, or FIREBASE_SERVICE_ACCOUNT_PATH in .env")
