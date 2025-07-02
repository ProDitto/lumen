import discord
from discord.ext import commands, tasks
import logging
import sys
import datetime  # Import for timestamps
import re  # Import for regular expressions to help with parsing mentions

# Firebase imports
import firebase_admin
from firebase_admin import credentials, firestore

# --- MODIFIED: Import the config module ---
# This single line imports your config.py file, which handles loading the .env variables.
import config

# --- Firebase Initialization ---
# This block initializes the Firebase Admin SDK using the service account key.
# It now uses config.FIREBASE_SERVICE_ACCOUNT_PATH.
try:
    cred = credentials.Certificate(config.FIREBASE_SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()  # Get a Firestore client instance
    logging.info("Firebase initialized successfully.")
except Exception as e:
    logging.critical(f"Failed to initialize Firebase: {e}")
    sys.exit("Firebase initialization failed. Please verify FIREBASE_SERVICE_ACCOUNT_PATH in your .env file and ensure the JSON key is valid.")

# --- Discord Bot Setup ---
# Configure basic logging for the bot's operations.
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

# Define Discord intents. These specify what events your bot wants to receive.
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True  # Required for guild-related events like channel fetching
intents.members = True # Required for the on_member_join event

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
async def on_member_join(member):
    """
    Called when a new member joins the server.
    Sends a welcome message with instructions on how to ask a doubt.
    """
    # Uses config.WELCOME_CHANNEL_ID
    if config.WELCOME_CHANNEL_ID:
        channel = bot.get_channel(config.WELCOME_CHANNEL_ID)
        if channel:
            welcome_message = (
                f"Welcome to the server, {member.mention}! 👋\n\n"
                "If you have a question or a doubt, you can get help from our mentors.\n"
                "To ask a doubt, please use the following format in the appropriate channel:\n"
                "`doubt @mentor1 [optional @mentor2] Your doubt description here.`\n\n"
                "We're happy to have you here!"
            )
            try:
                await channel.send(welcome_message)
                logging.info(f"Sent welcome message to {member.name} in channel {channel.name}.")
            except discord.Forbidden:
                logging.warning(f"Could not send welcome message to {member.name} in channel {channel.name} due to insufficient permissions.")
            except Exception as e:
                logging.error(f"An unexpected error occurred while sending a welcome message: {e}")
    else:
        logging.warning("WELCOME_CHANNEL_ID is not set in the .env file. Cannot send welcome message.")


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
    if hasattr(message.channel, 'name') and message.channel.name.startswith("bot"):
        logging.info(f"New message in '{message.channel.name}': {message.author}: {message.content}")

    # Feature: Process messages containing the "doubt" keyword.
    if "doubt" in message.content.lower():
        # Expected format: "doubt @mention <description>"
        doubt_keyword_position = message.content.lower().find("doubt")
        content_after_doubt_prefix = message.content[doubt_keyword_position + len("doubt"):].strip()

        if not message.mentions:
            await message.channel.send(
                f"{message.author.mention}, to create a doubt, you must mention at least one mentor. "
                "The correct format is: `doubt @mentor1 [optional @mentor2] Your doubt description here.`"
            )
            logging.info(f"Rejected doubt query from {message.author.name} (no mention): '{message.content}'")
            return

        doubt_description = content_after_doubt_prefix
        for mention in message.mentions:
            doubt_description = doubt_description.replace(mention.mention, "").strip()

        min_doubt_description_length = 5
        if len(doubt_description) < min_doubt_description_length:
            await message.channel.send(
                f"{message.author.mention}, your doubt description seems too short or incomplete after mentioning. "
                "Please provide more details, for example: "
                "`doubt @mentor How do I fix this error in Python?`"
            )
            logging.info(f"Rejected short doubt description from {message.author.name}: '{message.content}'")
            return

        try:
            thread_name = f"Doubt from {message.author.name} - {doubt_description[:25].strip()}"
            result_thread = await message.create_thread(name=thread_name)
            logging.info(f"Created new thread: '{thread_name}' [ID: {result_thread.id}]")

            try:
                doc_ref = db.collection('queries').document(str(result_thread.id))
                await bot.loop.run_in_executor(
                    None,
                    lambda: doc_ref.set({
                        'thread_id': result_thread.id,
                        'message_id': message.id,
                        'author_id': message.author.id,
                        'author_name': message.author.name,
                        'query_content': message.content,
                        'doubt_description': doubt_description,
                        'mentioned_mentors_ids': [m.id for m in message.mentions],
                        'created_at': firestore.SERVER_TIMESTAMP,
                        'last_activity_at': firestore.SERVER_TIMESTAMP,
                        'status': 'open',
                        'mentor_pinged': False,
                        'channel_id': message.channel.id
                    })
                )
                logging.info(f"Stored query {result_thread.id} in Firestore.")
            except Exception as db_e:
                logging.error(f"Failed to store query in Firestore for thread {result_thread.id}: {db_e}")

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
@commands.has_permissions(manage_guild=True) # This line restricts the command
async def resolve_doubt(ctx: commands.Context):
    """
    Command to mark a doubt as 'resolved'.
    This command should be used inside a doubt thread and requires Manage Server permission.
    Usage: !resolve
    """
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("This command can only be used inside a doubt thread.")
        return

    thread_id = ctx.channel.id
    query_doc_ref = db.collection('queries').document(str(thread_id))

    try:
        query_doc = await bot.loop.run_in_executor(None, lambda: query_doc_ref.get())
        if not query_doc.exists:
            await ctx.send("This thread does not correspond to an active doubt in the database.")
            logging.warning(f"Resolve command used in thread {thread_id}, but no corresponding query found.")
            return

        current_status = query_doc.get('status')
        if current_status == 'resolved':
            await ctx.send("This doubt is already marked as resolved.")
            return

        await bot.loop.run_in_executor(
            None,
            lambda: query_doc_ref.update({
                'status': 'resolved',
                'resolved_by_id': ctx.author.id,
                'resolved_by_name': ctx.author.name,
                'resolved_at': firestore.SERVER_TIMESTAMP,
                'last_activity_at': firestore.SERVER_TIMESTAMP
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

@resolve_doubt.error
async def resolve_error(ctx, error):
    """Handles errors for the !resolve command."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have the necessary permissions (Manage Server) to use this command.")
        logging.warning(f"User {ctx.author.name} tried to use !resolve without permissions.")
    else:
        logging.error(f"Unhandled error in !resolve command: {error}")
        await ctx.send("An unexpected error occurred with the !resolve command.")


@bot.command(name="list")
@commands.has_permissions(manage_guild=True)
async def list_open_doubts(ctx: commands.Context):
    """
    Command to list all currently open doubts from the Firestore database.
    This version sends the list directly to the user's DMs silently.
    """
    try:
        # Immediately delete the command message to keep the channel clean
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException) as e:
            logging.warning(f"Could not delete command message for !list: {e}")

        # Fetch all documents from Firestore where the status is 'open'
        queries_ref = db.collection('queries')
        open_queries = await bot.loop.run_in_executor(None, lambda: queries_ref.where('status', '==', 'open').get())

        if not open_queries:
            # Silently notify user in DM if there are no doubts, then log it.
            try:
                await ctx.author.send("You requested a list of open doubts, but there are none at the moment. Great job!")
            except discord.Forbidden:
                pass  # Can't notify user if DMs are closed, just log it.
            logging.info(f"No open queries found for {ctx.author.name}.")
            return

        # Prepare the content of the report
        response_message = "**Currently Open Doubts:**\n\n"
        for query_doc in open_queries:
            query_data = query_doc.to_dict()
            thread_id = query_data.get('thread_id')
            author_name = query_data.get('author_name', 'Unknown User')
            doubt_description = query_data.get('doubt_description', 'No description provided.')
            created_at = query_data.get('created_at')
            guild_id = ctx.guild.id  # Get guild ID for the link

            thread_mention = f"https://discord.com/channels/{guild_id}/{thread_id}"
            created_at_str = created_at.strftime('%Y-%m-%d %H:%M UTC') if created_at else 'N/A'

            response_message += (
                f"- **Author:** {author_name}\n"
                f"  **Doubt:** {doubt_description}\n"
                f"  **Created:** {created_at_str}\n"
                f"  **Thread:** <#{thread_id}> (Link: {thread_mention})\n\n"
            )

        # --- New Delivery Logic: DM Only & Silent ---
        try:
            # Try to send the list directly to the user's DMs
            for chunk in [response_message[i:i + 1900] for i in range(0, len(response_message), 1900)]:
                await ctx.author.send(chunk)
            logging.info(f"Successfully sent open doubts list to {ctx.author.name} via DM.")

        except discord.Forbidden:
            # This happens if the user has DMs blocked from the server.
            logging.error(f"Failed to send DM to {ctx.author.name}. They may have DMs disabled.")
            # Send a public error as a last resort since the DM failed.
            await ctx.send(f"{ctx.author.mention}, I tried to send you the list, but your DMs are closed. Please check your privacy settings.", delete_after=20)

    except Exception as e:
        logging.error(f"An unexpected error occurred in the !list command for {ctx.author.name}: {e}")
        await ctx.send("An error occurred while fetching the list of open doubts.")


@list_open_doubts.error
async def list_error(ctx, error):
    """Handles errors for the !list command."""
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.HTTPException):
        logging.warning(f"Could not delete error-triggering command message in channel {ctx.channel.id}.")

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have the necessary permissions (Manage Server) to use this command.")
        logging.warning(f"User {ctx.author.name} tried to use !list without permissions.")
    else:
        logging.error(f"Unhandled error in !list command: {error}")
        await ctx.send("An unexpected error occurred with the !list command.")


@tasks.loop(minutes=5)
async def check_thread_messages():
    """
    A background task to periodically check a specific thread for messages.
    """
    # Uses config.THREAD_ID
    if not config.THREAD_ID:
        return
        
    try:
        thread = await bot.fetch_channel(config.THREAD_ID)
        if isinstance(thread, discord.Thread):
            logging.info(f"Checking for messages in general thread: {thread.name}")
            async for msg in thread.history(limit=10):
                logging.info(f"  [General Thread Check] {msg.author}: {msg.content}")
    except discord.NotFound:
        logging.error(f"Periodic check failed: General Thread with ID {config.THREAD_ID} not found. Please verify the THREAD_ID in your .env file.")
    except discord.Forbidden:
        logging.error(f"Periodic check failed: Bot lacks permissions for general thread {config.THREAD_ID}.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in check_thread_messages (general thread check): {e}")

@check_thread_messages.before_loop
async def before_check_thread():
    """
    Ensures the bot is ready before the check_thread_messages task loop starts.
    """
    await bot.wait_until_ready()


def main():
    """
    Main function to configure and run the Discord bot.
    """
    # Critical check: ensure BOT_TOKEN is loaded.
    # Uses config.BOT_TOKEN
    if not config.BOT_TOKEN:
        logging.critical("BOT_TOKEN is not configured. The bot cannot start. Please set it in your .env file.")
        sys.exit("Bot token is not configured.")
        
    try:
        bot.run(config.BOT_TOKEN)
    except discord.errors.LoginFailure:
        logging.critical("Login failed. The BOT_TOKEN in your .env file is invalid. Please check your token.")
        sys.exit("Invalid bot token.")
    except Exception as e:
        logging.critical(f"An unexpected error occurred during bot startup: {e}")
        sys.exit("Bot startup failed.")

if __name__ == "__main__":
    main()