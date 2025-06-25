from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
THREAD_ID = os.getenv("THREAD_ID")

if not BOT_TOKEN or not THREAD_ID:
    raise ValueError("Missing BOT_TOKEN or THREAD_ID in .env file")
