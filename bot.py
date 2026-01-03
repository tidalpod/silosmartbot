#!/usr/bin/env python3
"""
Lease Recertification Bot for Telegram
Tracks Section 8 lease recertification dates and sends automated reminders.
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database file
DB_FILE = 'leases.db'

# Conversation states for /add command
TENANT_NAME, PROPERTY_ADDRESS, LEASE_START_DATE = range(3)

# Conversation state for /remove command
REMOVE_CHOICE = range(1)


# ============================================================================
# Database Functions
# ============================================================================

def init_database():
    """Initialize SQLite database and create leases table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            tenant_name TEXT NOT NULL,
            property_address TEXT NOT NULL,
            lease_start_date TEXT NOT NULL,
            recert_date TEXT NOT NULL,
            reminder_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


def add_lease(chat_id: int, tenant_name: str, property_address: str,
              lease_start_date: str, recert_date: str, reminder_date: str):
    """Add a new lease to the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO leases (chat_id, tenant_name, property_address,
                          lease_start_date, recert_date, reminder_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, tenant_name, property_address, lease_start_date,
          recert_date, reminder_date))

    conn.commit()
    conn.close()
    logger.info(f"Added lease for {tenant_name} in chat {chat_id}")


def get_leases_by_chat(chat_id: int) -> list:
    """Retrieve all leases for a specific chat_id."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, tenant_name, property_address, lease_start_date,
               recert_date, reminder_date
        FROM leases
        WHERE chat_id = ?
        ORDER BY reminder_date ASC
    ''', (chat_id,))

    leases = cursor.fetchall()
    conn.close()

    return leases


def get_leases_for_reminder(today_str: str) -> list:
    """Get all leases whose reminder_date matches today."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT chat_id, tenant_name, property_address, lease_start_date,
               recert_date, reminder_date
        FROM leases
        WHERE reminder_date = ?
    ''', (today_str,))

    leases = cursor.fetchall()
    conn.close()

    return leases


def delete_lease(lease_id: int, chat_id: int) -> bool:
    """Delete a specific lease by ID and chat_id (for security)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        DELETE FROM leases
        WHERE id = ? AND chat_id = ?
    ''', (lease_id, chat_id))

    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return deleted


def delete_all_leases_for_chat(chat_id: int) -> int:
    """Delete all leases for a specific chat_id. Returns count deleted."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        DELETE FROM leases
        WHERE chat_id = ?
    ''', (chat_id,))

    count = cursor.rowcount
    conn.commit()
    conn.close()

    return count


# ============================================================================
# Helper Functions
# ============================================================================

def calculate_dates(lease_start_str: str) -> tuple:
    """
    Calculate recertification and reminder dates.
    Returns (recert_date_str, reminder_date_str) or (None, None) if invalid.
    """
    try:
        lease_start = datetime.strptime(lease_start_str, '%m/%d/%Y')

        # Recert date = lease start + 9 months
        # Approximate 9 months as 9 * 30 = 270 days
        recert_date = lease_start + timedelta(days=270)

        # Reminder date = recert date - 7 days
        reminder_date = recert_date - timedelta(days=7)

        return (recert_date.strftime('%m/%d/%Y'),
                reminder_date.strftime('%m/%d/%Y'))
    except Exception as e:
        logger.error(f"Error calculating dates: {e}")
        return (None, None)


def format_lease_list(leases: list) -> str:
    """Format leases as a numbered list with box styling."""
    lines = []
    for idx, lease in enumerate(leases, 1):
        lease_id, tenant, address, start, recert, reminder = lease
        lease_box = (
            f"{idx}) \n"
            f"┌───────────────────────────────\n"
            f"│ Tenant:   {tenant}\n"
            f"│ Address:  {address}\n"
            f"│ Start:    {start}\n"
            f"│ Recert:   {recert}\n"
            f"│ Reminder: {reminder}\n"
            f"└───────────────────────────────"
        )
        lines.append(lease_box)
    return "\n\n".join(lines)


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Create and return the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📝 Add Lease", callback_data="menu_add"),
            InlineKeyboardButton("📋 View Leases", callback_data="menu_list"),
        ],
        [
            InlineKeyboardButton("🗑️ Remove Lease", callback_data="menu_remove"),
            InlineKeyboardButton("ℹ️ Help", callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton("🔓 Logout", callback_data="menu_logout"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================================
# Command Handlers
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with interactive button menu."""
    welcome_text = (
        "Welcome to Lease Recertification Bot! 🏠\n\n"
        "This bot helps you track lease recertifications.\n\n"
        "The bot will automatically send reminders 7 days before recertification "
        "is due (9 months after lease start date).\n\n"
        "👇 Choose an option below:"
    )

    keyboard = get_main_menu_keyboard()
    await update.message.reply_text(welcome_text, reply_markup=keyboard)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command with button menu."""
    help_text = (
        "Welcome to Lease Recertification Bot! 🏠\n\n"
        "This bot helps you track lease recertifications.\n\n"
        "Commands:\n"
        "📝 /add - Add a new lease\n"
        "📋 /list - View all leases\n"
        "🗑️ /remove - Remove a lease\n"
        "🔓 /logout - Logout from the bot\n"
        "ℹ️ /help - Show this help message\n\n"
        "The bot will automatically send reminders 7 days before recertification "
        "is due (9 months after lease start date).\n\n"
        "👇 Use the menu below:"
    )

    keyboard = get_main_menu_keyboard()
    await update.message.reply_text(help_text, reply_markup=keyboard)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command - show all leases for this chat."""
    chat_id = update.effective_chat.id
    leases = get_leases_by_chat(chat_id)
    keyboard = get_main_menu_keyboard()

    if not leases:
        await update.message.reply_text(
            "No leases found. Use /add to create one.",
            reply_markup=keyboard
        )
        return

    lease_list = format_lease_list(leases)
    await update.message.reply_text(
        f"📋 Your leases:\n\n{lease_list}",
        reply_markup=keyboard
    )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logout command - delete all leases for this chat."""
    chat_id = update.effective_chat.id
    count = delete_all_leases_for_chat(chat_id)
    keyboard = get_main_menu_keyboard()

    await update.message.reply_text(
        "You have been logged out and all your tracked leases for this chat "
        "have been removed.",
        reply_markup=keyboard
    )
    logger.info(f"Logged out chat {chat_id}, deleted {count} leases")


# ============================================================================
# /add Command - Conversation Flow
# ============================================================================

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the /add conversation - ask for tenant name."""
    await update.message.reply_text("Enter tenant name:")
    return TENANT_NAME


async def add_tenant_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive tenant name, ask for property address."""
    context.user_data['tenant_name'] = update.message.text.strip()
    await update.message.reply_text("Enter property address:")
    return PROPERTY_ADDRESS


async def add_property_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive property address, ask for lease start date."""
    context.user_data['property_address'] = update.message.text.strip()
    await update.message.reply_text("Enter lease start date (MM/DD/YYYY):")
    return LEASE_START_DATE


async def add_lease_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and validate lease start date, save lease to database."""
    date_text = update.message.text.strip()

    # Validate date format
    try:
        datetime.strptime(date_text, '%m/%d/%Y')
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid date format. Please enter date as MM/DD/YYYY "
            "(e.g., 01/15/2025):"
        )
        return LEASE_START_DATE

    # Calculate recert and reminder dates
    recert_date, reminder_date = calculate_dates(date_text)

    if not recert_date or not reminder_date:
        await update.message.reply_text(
            "❌ Error calculating dates. Please try again with format MM/DD/YYYY:"
        )
        return LEASE_START_DATE

    # Save to database
    chat_id = update.effective_chat.id
    tenant_name = context.user_data['tenant_name']
    property_address = context.user_data['property_address']

    add_lease(
        chat_id=chat_id,
        tenant_name=tenant_name,
        property_address=property_address,
        lease_start_date=date_text,
        recert_date=recert_date,
        reminder_date=reminder_date
    )

    # Send confirmation with menu
    confirmation = (
        f"✅ Lease added.\n\n"
        f"Tenant: {tenant_name}\n"
        f"Address: {property_address}\n"
        f"Start: {date_text}\n"
        f"Recert: {recert_date}\n"
        f"Reminder: {reminder_date}"
    )
    keyboard = get_main_menu_keyboard()
    await update.message.reply_text(confirmation, reply_markup=keyboard)

    # Clear user data
    context.user_data.clear()

    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current conversation."""
    context.user_data.clear()
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


# ============================================================================
# /remove Command - Conversation Flow
# ============================================================================

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the /remove conversation - show leases and ask which to remove."""
    chat_id = update.effective_chat.id
    leases = get_leases_by_chat(chat_id)

    if not leases:
        await update.message.reply_text(
            "No leases found. There's nothing to remove."
        )
        return ConversationHandler.END

    # Store leases in context for later reference
    context.user_data['remove_leases'] = leases

    # Show numbered list
    lease_list = format_lease_list(leases)
    await update.message.reply_text(
        f"📋 Your leases:\n\n{lease_list}\n\n"
        f"Reply with the number of the lease you want to remove:"
    )

    return REMOVE_CHOICE


async def remove_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the user's choice and delete the selected lease."""
    choice_text = update.message.text.strip()

    # Validate choice is a number
    try:
        choice = int(choice_text)
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a valid number from the list:"
        )
        return REMOVE_CHOICE

    leases = context.user_data.get('remove_leases', [])

    # Validate choice is in range
    if choice < 1 or choice > len(leases):
        await update.message.reply_text(
            f"❌ Please enter a number between 1 and {len(leases)}:"
        )
        return REMOVE_CHOICE

    # Get the lease to delete
    lease_to_delete = leases[choice - 1]
    lease_id = lease_to_delete[0]
    tenant_name = lease_to_delete[1]

    # Delete from database
    chat_id = update.effective_chat.id
    success = delete_lease(lease_id, chat_id)

    keyboard = get_main_menu_keyboard()

    if success:
        await update.message.reply_text(
            f"✅ Lease for {tenant_name} has been removed.",
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            "❌ Error removing lease. Please try again.",
            reply_markup=keyboard
        )

    # Clear user data
    context.user_data.clear()

    return ConversationHandler.END


# ============================================================================
# Inline Button Callback Handlers
# ============================================================================

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button callbacks from the main menu."""
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    callback_data = query.data
    chat_id = update.effective_chat.id

    # Handle different button actions
    if callback_data == "menu_add":
        # Start the /add conversation
        await query.message.reply_text("📝 Adding a new lease...\n\nEnter tenant name:")
        return TENANT_NAME

    elif callback_data == "menu_list":
        # Show list of leases
        leases = get_leases_by_chat(chat_id)

        if not leases:
            await query.message.reply_text(
                "No leases found. Use 📝 Add Lease to create one.",
                reply_markup=get_main_menu_keyboard()
            )
        else:
            lease_list = format_lease_list(leases)
            await query.message.reply_text(
                f"📋 Your leases:\n\n{lease_list}",
                reply_markup=get_main_menu_keyboard()
            )

    elif callback_data == "menu_remove":
        # Start the /remove conversation
        leases = get_leases_by_chat(chat_id)

        if not leases:
            await query.message.reply_text(
                "No leases found. There's nothing to remove.",
                reply_markup=get_main_menu_keyboard()
            )
            return ConversationHandler.END

        # Store leases in context for later reference
        context.user_data['remove_leases'] = leases

        # Show numbered list
        lease_list = format_lease_list(leases)
        await query.message.reply_text(
            f"🗑️ Removing a lease...\n\n📋 Your leases:\n\n{lease_list}\n\n"
            f"Reply with the number of the lease you want to remove:"
        )
        return REMOVE_CHOICE

    elif callback_data == "menu_help":
        # Show help message
        help_text = (
            "ℹ️ Help - Lease Recertification Bot\n\n"
            "This bot helps you track lease recertifications.\n\n"
            "📝 Add Lease - Add a new lease with tenant info\n"
            "📋 View Leases - See all your tracked leases\n"
            "🗑️ Remove Lease - Delete a lease from tracking\n"
            "🔓 Logout - Remove all your data\n\n"
            "The bot automatically sends reminders 7 days before "
            "recertification is due (9 months after lease start date)."
        )
        await query.message.reply_text(help_text, reply_markup=get_main_menu_keyboard())

    elif callback_data == "menu_logout":
        # Logout and delete all leases
        count = delete_all_leases_for_chat(chat_id)
        await query.message.reply_text(
            "🔓 You have been logged out and all your tracked leases "
            "for this chat have been removed.",
            reply_markup=get_main_menu_keyboard()
        )
        logger.info(f"Logged out chat {chat_id} via button, deleted {count} leases")


async def add_command_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the /add conversation from button press."""
    await update.callback_query.message.reply_text("📝 Adding a new lease...\n\nEnter tenant name:")
    return TENANT_NAME


async def remove_command_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the /remove conversation from button press."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    leases = get_leases_by_chat(chat_id)

    if not leases:
        await query.message.reply_text(
            "No leases found. There's nothing to remove.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

    # Store leases in context for later reference
    context.user_data['remove_leases'] = leases

    # Show numbered list
    lease_list = format_lease_list(leases)
    await query.message.reply_text(
        f"🗑️ Removing a lease...\n\n📋 Your leases:\n\n{lease_list}\n\n"
        f"Reply with the number of the lease you want to remove:"
    )

    return REMOVE_CHOICE


# ============================================================================
# Background Reminder Scheduler
# ============================================================================

async def check_and_send_reminders(application: Application):
    """
    Background task that checks for leases due for reminder today
    and sends notifications to users and team chat.
    """
    today = datetime.now().strftime('%m/%d/%Y')
    logger.info(f"Checking for reminders on {today}")

    leases = get_leases_for_reminder(today)

    if not leases:
        logger.info("No reminders to send today")
        return

    team_chat_id = os.getenv('TEAM_CHAT_ID')

    for lease in leases:
        chat_id, tenant, address, start, recert, reminder = lease

        reminder_message = (
            f"🔔 Lease recertification reminder:\n\n"
            f"Tenant: {tenant}\n"
            f"Address: {address}\n"
            f"Start date: {start}\n"
            f"Recert due: {recert}\n\n"
            f"(7 days from today)"
        )

        # Send to original user
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=reminder_message
            )
            logger.info(f"Sent reminder to chat {chat_id} for {tenant}")
        except Exception as e:
            logger.error(f"Error sending reminder to chat {chat_id}: {e}")

        # Send to team chat if configured
        if team_chat_id:
            try:
                await application.bot.send_message(
                    chat_id=team_chat_id,
                    text=reminder_message
                )
                logger.info(f"Sent reminder to team chat for {tenant}")
            except Exception as e:
                logger.error(f"Error sending reminder to team chat: {e}")


def setup_scheduler(application: Application):
    """Set up the background scheduler for daily reminder checks."""
    scheduler = AsyncIOScheduler()

    # Run daily at 9:00 AM
    scheduler.add_job(
        check_and_send_reminders,
        trigger=CronTrigger(hour=9, minute=0),
        args=[application],
        id='daily_reminder_check',
        name='Daily Reminder Check',
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started - daily reminders will run at 9:00 AM")

    return scheduler


# ============================================================================
# Main Application
# ============================================================================

def main():
    """Initialize and run the bot."""
    # Get bot token from environment
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')

    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        raise ValueError("TELEGRAM_BOT_TOKEN is required")

    # Initialize database
    init_database()

    # Create application
    application = Application.builder().token(bot_token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("logout", logout_command))

    # Add conversation handler for /add (supports both command and button)
    add_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_command),
            CallbackQueryHandler(add_command_button, pattern="^menu_add$")
        ],
        states={
            TENANT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tenant_name)],
            PROPERTY_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_property_address)],
            LEASE_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_lease_start_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(add_conversation)

    # Add conversation handler for /remove (supports both command and button)
    remove_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("remove", remove_command),
            CallbackQueryHandler(remove_command_button, pattern="^menu_remove$")
        ],
        states={
            REMOVE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_choice)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(remove_conversation)

    # Add callback query handler for inline buttons (non-conversation buttons)
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    # Set up background scheduler
    scheduler = setup_scheduler(application)

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
