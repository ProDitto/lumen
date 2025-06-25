# main.py
import discord
from discord.ext import commands, tasks
import logging
import sys

try:
    from config import BOT_TOKEN, THREAD_ID
except ImportError:
    print("ERROR: config.py not found or is empty. Please create it and add BOT_TOKEN and THREAD_ID.")
    sys.exit()


logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    """Called when the bot successfully connects to Discord."""
    logging.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    # Start the background task to check the thread.
    check_thread_messages.start()


@bot.event
async def on_message(message: discord.Message):
    """Called for every message in any channel the bot can see."""
    if message.author == bot.user:
        return

    # Feature: Print messages from channels starting with "bot".
    if hasattr(message.channel, 'name') and message.channel.name.startswith("bot"):
        logging.info(f"New message in '{message.channel.name}': {message.author}: {message.content}")

    # Feature: Create a thread for messages starting with "Doubt".
    if message.content.lower().startswith("doubt"):
        try:
            thread_name = f"Doubt from {message.author.name} - {message.content[5:30]}"
            result = await message.create_thread(name=thread_name)
            logging.info(f"Created new thread: '{thread_name} [ID : {result.id}]'")
        except discord.HTTPException as e:
            logging.error(f"Failed to create thread: {e}")

    await bot.process_commands(message)


@bot.command(name="hello")
async def hello_world(ctx: commands.Context):
    """A command to make the bot say 'Hello world!' (Trigger: !hello)"""
    await ctx.send("Hello world!")


@tasks.loop(minutes=5)
async def check_thread_messages():
    """A background task to periodically check a specific thread for messages."""
    # This check prevents errors if the user hasn't configured the thread ID.
    if not isinstance(THREAD_ID, int) or THREAD_ID == 123456789012345678:
        return
        
    try:
        thread = await bot.fetch_channel(THREAD_ID)
        if isinstance(thread, discord.Thread):
            logging.info(f"Checking for messages in thread: {thread.name}")
            async for msg in thread.history(limit=10):
                logging.info(f"  [Thread Check] {msg.author}: {msg.content}")
    except discord.NotFound:
        logging.error(f"Periodic check failed: Thread with ID {THREAD_ID} not found.")
    except discord.Forbidden:
        logging.error(f"Periodic check failed: Bot lacks permissions for thread {THREAD_ID}.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in check_thread_messages: {e}")

@check_thread_messages.before_loop
async def before_check_thread():
    """Ensures the bot is ready before the task loop starts."""
    await bot.wait_until_ready()


def main():
    """Main function to configure and run the bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        logging.critical("BOT_TOKEN is not configured in config.py. The bot cannot start.")
        sys.exit("Bot token is not configured.")
    
    try:
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure:
        logging.critical("Login failed. The BOT_TOKEN in config.py is invalid.")
        sys.exit("Invalid bot token.")

if __name__ == "__main__":
    main()