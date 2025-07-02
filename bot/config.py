# config.py
from dotenv import load_dotenv
import os
import sys

# Load environment variables from a .env file
load_dotenv()

# Get the variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_SERVICE_ACCOUNT_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

# Get IDs and convert them to integers, allowing them to be optional
try:
    THREAD_ID = int(os.getenv("THREAD_ID")) if os.getenv("THREAD_ID") else None
    WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID")) if os.getenv("WELCOME_CHANNEL_ID") else None
except (ValueError, TypeError):
    print("ERROR: THREAD_ID or WELCOME_CHANNEL_ID in your .env file is not a valid number.")
    sys.exit()

# Check that the most critical variables are not missing
if not BOT_TOKEN or not FIREBASE_SERVICE_ACCOUNT_PATH:
    # Use raise to stop the bot if secrets aren't found.
    raise ValueError("ERROR: Make sure BOT_TOKEN and FIREBASE_SERVICE_ACCOUNT_PATH are defined in your .env file.")