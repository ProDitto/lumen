# main.py
import discord
from discord.ext import commands, tasks
import logging
import sys
import datetime # Import for timestamps
import re # Import for regular expressions to help with parsing mentions

# Firebase imports
import firebase_admin
from firebase_admin import credentials, firestore

try:
    # Ensure config.py correctly defines and exports these variables
    from config import BOT_TOKEN, THREAD_ID, FIREBASE_SERVICE_ACCOUNT_PATH
except ImportError:
    print("ERROR: config.py not found or is empty. Please ensure it exists and defines BOT_TOKEN, THREAD_ID, and FIREBASE_SERVICE_ACCOUNT_PATH.")
    sys.exit()

# --- Firebase Initialization ---
# This block initializes the Firebase Admin SDK using the service account key.
# It's crucial for interacting with Firestore.
try:
    cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client() # Get a Firestore client instance
    logging.info("Firebase initialized successfully.")
except Exception as e:
    logging.critical(f"Failed to initialize Firebase: {e}")
    sys.exit("Firebase initialization failed. Please verify FIREBASE_SERVICE_ACCOUNT_PATH in your .env file and ensure the JSON key is valid.")

# --- Discord Bot Setup ---
# Configure basic logging for the bot's operations.
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

# Define Discord intents. These specify what events your bot wants to receive.
# message_content is required to read message content.
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True # Required for guild-related events like channel fetching

# Initialize the bot with a command prefix and defined intents.
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    """
    Called when the bot successfully connects to Discord.
    Logs the bot's name and ID, and starts the background task.
    """
    logging.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    # Start the periodic background task to check threads.
    check_thread_messages.start()


@bot.event
async def on_message(message: discord.Message):
    """
    Called for every message in any channel the bot can see.
    Handles bot's own messages, logs messages in "bot" channels,
    and processes "doubt" queries.
    """
    # Ignore messages sent by the bot itself to prevent infinite loops.
    if message.author == bot.user:
        return

    # Feature: Log messages from channels starting with "bot".
    # Checks if the channel has a 'name' attribute and starts with "bot".
    if hasattr(message.channel, 'name') and message.channel.name.startswith("bot"):
        logging.info(f"New message in '{message.channel.name}': {message.author}: {message.content}")

    # Feature: Process messages starting with "doubt".
    # This includes creating a thread, storing query in DB, and format validation.
    if message.content.lower().startswith("doubt"):
        # Expected format: "doubt @mention <description>"
        
        # Remove the "doubt" prefix for easier parsing of mentions and description
        content_after_doubt_prefix = message.content[len("doubt"):].strip()

        # Check if there are any mentions in the message
        if not message.mentions:
            await message.channel.send(
                f"{message.author.mention}, to create a doubt, you must mention at least one mentor. "
                "The correct format is: `doubt @mentor1 [optional @mentor2] Your doubt description here.`"
            )
            logging.info(f"Rejected doubt query from {message.author.name} (no mention): '{message.content}'")
            return # Stop processing, format is incorrect

        # Extract the doubt description by removing mentions from the string.
        # This regex will match user, role, and channel mentions.
        # discord.py automatically parses message.mentions for actual User/Role/Channel objects.
        # We need to remove the raw mention strings from the content to get the clean description.
        doubt_description = content_after_doubt_prefix
        for mention in message.mentions:
            # Replace user mentions (<@ID> or <@!ID>)
            doubt_description = doubt_description.replace(mention.mention, "").strip()
        
        # Define a minimum length for the meaningful part of a doubt description.
        min_doubt_description_length = 5
        if len(doubt_description) < min_doubt_description_length:
            await message.channel.send(
                f"{message.author.mention}, your doubt description seems too short or incomplete after mentioning. "
                "Please provide more details, for example: "
                "`doubt @mentor How do I fix this error in Python?`"
            )
            logging.info(f"Rejected short doubt description from {message.author.name}: '{message.content}'")
            return # Stop processing, format is incorrect

        # If format is correct, proceed to create thread and store in DB
        try:
            # Create a thread name using the author's name and a snippet of the description.
            # Truncate the description snippet to ensure the thread name isn't excessively long.
            thread_name = f"Doubt from {message.author.name} - {doubt_description[:25].strip()}"
            # Create the actual thread on Discord in the same channel as the original message.
            result_thread = await message.create_thread(name=thread_name)
            logging.info(f"Created new thread: '{thread_name}' [ID: {result_thread.id}]")

            # Store query information in Firestore database.
            try:
                # Use the Discord thread ID as the Firestore document ID for easy lookup.
                doc_ref = db.collection('queries').document(str(result_thread.id))
                
                # Corrected: Use bot.loop.run_in_executor to run synchronous Firestore set() in a thread.
                await bot.loop.run_in_executor(
                    None, # Use the default executor
                    lambda: doc_ref.set({
                        'thread_id': result_thread.id,
                        'message_id': message.id, # ID of the original message that triggered the doubt
                        'author_id': message.author.id,
                        'author_name': message.author.name,
                        'query_content': message.content, # Full original message content
                        'doubt_description': doubt_description, # Cleaned doubt description
                        'mentioned_mentors_ids': [m.id for m in message.mentions], # Store IDs of mentioned users
                        'created_at': firestore.SERVER_TIMESTAMP, # Timestamp from Firestore server
                        'last_activity_at': firestore.SERVER_TIMESTAMP, # Initially same as created_at
                        'status': 'open', # Initial status of the query (e.g., 'open', 'resolved', 'pending')
                        'mentor_pinged': False, # Flag to track if mentor has been pinged for this query
                        'channel_id': message.channel.id # ID of the original channel where the doubt was raised
                    })
                )
                logging.info(f"Stored query {result_thread.id} in Firestore.")
            except Exception as db_e:
                logging.error(f"Failed to store query in Firestore for thread {result_thread.id}: {db_e}")

            # Bot's reply in the original channel
            await message.channel.send(
                f"{message.author.mention}, your doubt has been submitted! "
                f"Please discuss further in the new thread: {result_thread.mention}. "
                "Waiting for mentors to reply."
            )
            logging.info(f"Bot replied to {message.author.name} about thread creation for doubt.")

        except discord.HTTPException as e:
            logging.error(f"Failed to create thread for '{message.content}': {e}")
            await message.channel.send(f"Sorry, {message.author.mention}, I couldn't create a thread for your doubt. Please check my permissions.")
        except Exception as e:
            logging.error(f"An unexpected error occurred during doubt processing for '{message.content}': {e}")
            await message.channel.send(f"An unexpected error occurred while processing your doubt, {message.author.mention}.")

    # Process other commands (e.g., !hello).
    await bot.process_commands(message)


@bot.command(name="hello")
async def hello_world(ctx: commands.Context):
    """
    A simple command to make the bot say 'Hello world!'.
    Triggered by: `!hello`
    """
    await ctx.send("Hello world!")


@bot.command(name="resolve")
async def resolve_doubt(ctx: commands.Context):
    """
    Command to mark a doubt as 'resolved'.
    This command should be used inside a doubt thread.
    Usage: !resolve
    """
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("This command can only be used inside a doubt thread.")
        return

    thread_id = ctx.channel.id
    query_doc_ref = db.collection('queries').document(str(thread_id))

    try:
        # Use bot.loop.run_in_executor for synchronous Firestore get()
        query_doc = await bot.loop.run_in_executor(None, lambda: query_doc_ref.get())
        
        if not query_doc.exists:
            await ctx.send("This thread does not correspond to an active doubt in the database.")
            logging.warning(f"Resolve command used in thread {thread_id}, but no corresponding query found.")
            return

        current_status = query_doc.get('status')
        if current_status == 'resolved':
            await ctx.send("This doubt is already marked as resolved.")
            return

        # Update the document with resolved status and relevant timestamps/IDs
        await bot.loop.run_in_executor(
            None,
            lambda: query_doc_ref.update({
                'status': 'resolved',
                'resolved_by_id': ctx.author.id,
                'resolved_by_name': ctx.author.name,
                'resolved_at': firestore.SERVER_TIMESTAMP,
                'last_activity_at': firestore.SERVER_TIMESTAMP # Update last activity
            })
        )
        await ctx.send(
            f"Doubt in this thread has been marked as **RESOLVED** by {ctx.author.mention}. "
            "Thank you for using lumen!"
        )
        logging.info(f"Doubt {thread_id} resolved by {ctx.author.name}.")

    except Exception as e:
        logging.error(f"Error resolving doubt {thread_id}: {e}")
        await ctx.send(f"An error occurred while trying to resolve this doubt. Please try again later.")


@bot.command(name="list")
@commands.has_permissions(manage_guild=True) # Only allow users with 'Manage Server' permission
async def list_open_doubts(ctx: commands.Context):
    """
    Command to list all currently open doubts from the Firestore database.
    Only accessible to users with 'Manage Server' permission.
    The list is sent privately to the invoking admin.
    Usage: !list
    """
    try:
        # Attempt to delete the command message from the public channel.
        # This requires 'manage_messages' permission for the bot in the channel.
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            logging.warning(f"Bot lacks 'manage_messages' permission to delete command message in channel {ctx.channel.id}.")
            # Optionally, send a message saying the bot couldn't delete the command.
            # await ctx.send("I couldn't delete your command message due to missing permissions.", delete_after=5)
        except discord.HTTPException as e:
            logging.error(f"Failed to delete command message: {e}")
            # await ctx.send("An error occurred while deleting your command message.", delete_after=5)


        # Fetch all documents where the status is 'open'
        queries_ref = db.collection('queries')
        # Use bot.loop.run_in_executor for synchronous Firestore get()
        open_queries = await bot.loop.run_in_executor(None, lambda: queries_ref.where('status', '==', 'open').get())
        
        if not open_queries:
            await ctx.author.send("There are no open doubts at the moment. Great job!")
            logging.info(f"No open queries found for {ctx.author.name}.")
            return

        response_message = "**Currently Open Doubts:**\n\n"
        for query_doc in open_queries:
            query_data = query_doc.to_dict()
            thread_id = query_data.get('thread_id')
            author_name = query_data.get('author_name', 'Unknown User')
            doubt_description = query_data.get('doubt_description', 'No description provided.')
            created_at = query_data.get('created_at')

            # Fetch the thread object to get its mentionable link
            thread_mention = f"https://discord.com/channels/{ctx.guild.id}/{thread_id}"
            try:
                # Attempt to fetch the actual Discord thread for a proper mention
                discord_thread = await bot.fetch_channel(thread_id)
                if isinstance(discord_thread, discord.Thread):
                    thread_mention = discord_thread.mention
            except (discord.NotFound, discord.Forbidden):
                logging.warning(f"Could not fetch Discord thread {thread_id} for listing. Using URL fallback.")
                # Fallback to URL if bot cannot access the thread
                pass
            
            created_at_str = created_at.strftime('%Y-%m-%d %H:%M UTC') if created_at else 'N/A'

            response_message += (
                f"- **Author:** {author_name}\n"
                f"  **Doubt:** {doubt_description}\n"
                f"  **Created:** {created_at_str}\n"
                f"  **Thread:** {thread_mention}\n\n"
            )
        
        # Split the message into chunks if it's too long for a single Discord message (2000 characters limit)
        for chunk in [response_message[i:i + 1900] for i in range(0, len(response_message), 1900)]:
            await ctx.author.send(chunk) # Send to the author's DMs
        
        # Removed the public confirmation message as requested.
        logging.info(f"Listed open queries privately for {ctx.author.name}.")

    except discord.Forbidden:
        # If the bot cannot send DMs to the user (e.g., user blocked DMs), inform them in the channel.
        # This will be the *only* public message if DM fails.
        await ctx.send("I tried to send you the list of doubts in your direct messages (DM), but I couldn't. Please check your privacy settings to allow DMs from server members.")
        logging.error(f"Bot lacks permissions to DM list of doubts to {ctx.author.name}.")
    except Exception as e:
        logging.error(f"Error listing open doubts for {ctx.author.name}: {e}")
        await ctx.send("An error occurred while trying to fetch the list of open doubts.")

@list_open_doubts.error
async def list_error(ctx, error):
    """Handles errors for the !list command."""
    # Attempt to delete the command message even if there's an error in the command itself
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.HTTPException):
        logging.warning(f"Could not delete error-triggering command message in channel {ctx.channel.id}.")

    if isinstance(error, commands.MissingPermissions):
        # Send permission error message publicly, as the command itself was public.
        await ctx.send("You don't have the necessary permissions (Manage Server) to use this command.")
        logging.warning(f"User {ctx.author.name} tried to use !list without permissions.")
    else:
        logging.error(f"Unhandled error in !list command: {error}")
        await ctx.send("An unexpected error occurred with the !list command.")


@tasks.loop(minutes=5)
async def check_thread_messages():
    """
    A background task to periodically check a specific thread for messages.
    This function currently checks only the general THREAD_ID.
    It will be expanded in later steps to iterate through all active query threads from Firestore.
    """
    # Skip if THREAD_ID is not configured or is the default placeholder.
    if not THREAD_ID or THREAD_ID == 123456789012345678:
        # logging.warning("THREAD_ID is not configured or is a placeholder. Skipping periodic thread check for general ID.")
        return
        
    try:
        # Fetch the thread object from Discord using its ID.
        thread = await bot.fetch_channel(THREAD_ID)
        if isinstance(thread, discord.Thread):
            logging.info(f"Checking for messages in general thread: {thread.name}")
            # Iterate through the last 10 messages in the thread and log them.
            async for msg in thread.history(limit=10):
                logging.info(f"  [General Thread Check] {msg.author}: {msg.content}")
    except discord.NotFound:
        logging.error(f"Periodic check failed: General Thread with ID {THREAD_ID} not found. Please verify the THREAD_ID in your .env file.")
    except discord.Forbidden:
        logging.error(f"Periodic check failed: Bot lacks permissions for general thread {THREAD_ID}. Ensure the bot has 'Read Message History' and 'View Channel' permissions.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in check_thread_messages (general thread check): {e}")

@check_thread_messages.before_loop
async def before_check_thread():
    """
    Ensures the bot is ready before the check_thread_messages task loop starts.
    This prevents errors by ensuring Discord connection is established.
    """
    await bot.wait_until_ready()


def main():
    """
    Main function to configure and run the Discord bot.
    Handles initial token validation and bot startup.
    """
    # Critical check: ensure BOT_TOKEN is set and not the placeholder.
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        logging.critical("BOT_TOKEN is not configured. The bot cannot start. Please set it in your .env file.")
        sys.exit("Bot token is not configured.")
        
    try:
        # Attempt to run the bot with the provided token.
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure:
        logging.critical("Login failed. The BOT_TOKEN in config.py is invalid. Please check your token.")
        sys.exit("Invalid bot token.")
    except Exception as e:
        # Catch any other unexpected errors during bot startup.
        logging.critical(f"An unexpected error occurred during bot startup: {e}")
        sys.exit("Bot startup failed.")

if __name__ == "__main__":
    main()
