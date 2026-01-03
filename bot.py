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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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

# Conversation states for vendor management
(VENDOR_NAME, VENDOR_PHONE, VENDOR_EMAIL, VENDOR_COMPANY,
 VENDOR_SPECIALTY, VENDOR_RATING, VENDOR_EDIT_CHOICE, VENDOR_EDIT_VALUE) = range(8)

# Additional states for PHA contacts
(PHA_AGENCY, PHA_CONTACT_PERSON, PHA_DEPARTMENT, PHA_EXTENSION,
 PHA_LINE_TYPE, PHA_BEST_TIME, PHA_FAX, PHA_ADDRESS, PHA_WEBSITE) = range(9, 18)

# Vendor categories
VENDOR_CATEGORIES = {
    'plumber': '🚰 Plumbers',
    'electrician': '⚡ Electricians',
    'contractor': '🏗️ General Contractors',
    'pha': '🏛️ PHA Contacts',
    'other': '🔨 Other Vendors'
}


# ============================================================================
# Database Functions
# ============================================================================

def init_database():
    """Initialize SQLite database and create tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Leases table
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

    # Vendors table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            company TEXT,
            specialty TEXT,
            rating INTEGER,
            times_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # PHA contacts table (additional fields for Public Housing Agencies)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pha_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            agency_name TEXT,
            contact_person TEXT,
            department TEXT,
            extension TEXT,
            line_type TEXT,
            best_time TEXT,
            fax TEXT,
            address TEXT,
            website TEXT,
            notes TEXT,
            FOREIGN KEY (vendor_id) REFERENCES vendors(id) ON DELETE CASCADE
        )
    ''')

    # Vendor notes table (for tracking interactions)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendor_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vendor_id) REFERENCES vendors(id) ON DELETE CASCADE
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
    """Retrieve all leases for a specific chat_id, sorted by recert date (soonest first)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, tenant_name, property_address, lease_start_date,
               recert_date, reminder_date
        FROM leases
        WHERE chat_id = ?
    ''', (chat_id,))

    leases = cursor.fetchall()
    conn.close()

    # Sort by recert_date (convert MM/DD/YYYY to date object for proper sorting)
    def parse_date(lease):
        try:
            recert_date_str = lease[4]  # recert_date is index 4
            return datetime.strptime(recert_date_str, '%m/%d/%Y')
        except:
            return datetime.max  # Put invalid dates at the end

    leases_sorted = sorted(leases, key=parse_date)

    return leases_sorted


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
# Vendor Database Functions
# ============================================================================

def add_vendor(chat_id: int, category: str, name: str, phone: str,
               email: str = None, company: str = None, specialty: str = None,
               rating: int = None) -> int:
    """Add a new vendor to the database. Returns vendor_id."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO vendors (chat_id, category, name, phone, email, company, specialty, rating)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (chat_id, category, name, phone, email, company, specialty, rating))

    vendor_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Added vendor {name} ({category}) in chat {chat_id}")

    return vendor_id


def add_pha_contact(vendor_id: int, agency_name: str = None, contact_person: str = None,
                   department: str = None, extension: str = None, line_type: str = None,
                   best_time: str = None, fax: str = None, address: str = None,
                   website: str = None, notes: str = None):
    """Add PHA-specific details for a vendor."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO pha_contacts (vendor_id, agency_name, contact_person, department,
                                 extension, line_type, best_time, fax, address, website, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (vendor_id, agency_name, contact_person, department, extension, line_type,
          best_time, fax, address, website, notes))

    conn.commit()
    conn.close()
    logger.info(f"Added PHA contact details for vendor {vendor_id}")


def get_vendors_by_category(chat_id: int, category: str) -> list:
    """Get all vendors for a specific category."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, name, phone, email, company, specialty, rating, times_used, created_at
        FROM vendors
        WHERE chat_id = ? AND category = ?
        ORDER BY name ASC
    ''', (chat_id, category))

    vendors = cursor.fetchall()
    conn.close()

    return vendors


def get_vendor_by_id(vendor_id: int, chat_id: int) -> tuple:
    """Get a specific vendor by ID."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, category, name, phone, email, company, specialty, rating, times_used, created_at
        FROM vendors
        WHERE id = ? AND chat_id = ?
    ''', (vendor_id, chat_id))

    vendor = cursor.fetchone()
    conn.close()

    return vendor


def get_pha_details(vendor_id: int) -> tuple:
    """Get PHA-specific details for a vendor."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT agency_name, contact_person, department, extension, line_type,
               best_time, fax, address, website, notes
        FROM pha_contacts
        WHERE vendor_id = ?
    ''', (vendor_id,))

    pha_details = cursor.fetchone()
    conn.close()

    return pha_details


def update_vendor(vendor_id: int, chat_id: int, **kwargs):
    """Update vendor fields."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Build dynamic UPDATE query
    fields = []
    values = []
    for key, value in kwargs.items():
        fields.append(f"{key} = ?")
        values.append(value)

    if fields:
        query = f"UPDATE vendors SET {', '.join(fields)} WHERE id = ? AND chat_id = ?"
        values.extend([vendor_id, chat_id])
        cursor.execute(query, values)
        conn.commit()

    conn.close()
    logger.info(f"Updated vendor {vendor_id}")


def delete_vendor(vendor_id: int, chat_id: int) -> bool:
    """Delete a vendor and all associated data."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('DELETE FROM vendors WHERE id = ? AND chat_id = ?', (vendor_id, chat_id))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def add_vendor_note(vendor_id: int, note: str):
    """Add a note to a vendor."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO vendor_notes (vendor_id, note)
        VALUES (?, ?)
    ''', (vendor_id, note))

    conn.commit()
    conn.close()


def get_vendor_notes(vendor_id: int) -> list:
    """Get all notes for a vendor."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT note, created_at
        FROM vendor_notes
        WHERE vendor_id = ?
        ORDER BY created_at DESC
    ''', (vendor_id,))

    notes = cursor.fetchall()
    conn.close()

    return notes


def search_vendors(chat_id: int, query: str) -> list:
    """Search vendors by name, company, or specialty."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    search_query = f"%{query}%"
    cursor.execute('''
        SELECT id, category, name, phone, email, company, specialty, rating
        FROM vendors
        WHERE chat_id = ? AND (
            name LIKE ? OR
            company LIKE ? OR
            specialty LIKE ?
        )
        ORDER BY name ASC
    ''', (chat_id, search_query, search_query, search_query))

    vendors = cursor.fetchall()
    conn.close()

    return vendors


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
            InlineKeyboardButton("🔧 Vendors", callback_data="menu_vendors"),
        ],
        [
            InlineKeyboardButton("ℹ️ Help", callback_data="menu_help"),
            InlineKeyboardButton("🔓 Logout", callback_data="menu_logout"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_vendor_categories_keyboard() -> InlineKeyboardMarkup:
    """Create keyboard for vendor category selection."""
    keyboard = [
        [InlineKeyboardButton("🚰 Plumbers", callback_data="vendor_cat_plumber")],
        [InlineKeyboardButton("⚡ Electricians", callback_data="vendor_cat_electrician")],
        [InlineKeyboardButton("🏗️ General Contractors", callback_data="vendor_cat_contractor")],
        [InlineKeyboardButton("🏛️ PHA Contacts", callback_data="vendor_cat_pha")],
        [InlineKeyboardButton("🔨 Other Vendors", callback_data="vendor_cat_other")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="vendor_back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_vendor_category_actions_keyboard(category: str) -> InlineKeyboardMarkup:
    """Create keyboard for vendor category actions."""
    cat_name = VENDOR_CATEGORIES.get(category, 'Vendors')
    keyboard = [
        [InlineKeyboardButton(f"➕ Add New {cat_name[2:]}", callback_data=f"vendor_add_{category}")],
        [InlineKeyboardButton("🔍 Search", callback_data=f"vendor_search_{category}")],
        [InlineKeyboardButton("🔙 Back to Categories", callback_data="menu_vendors")],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_vendor_list(vendors: list, category: str) -> str:
    """Format vendors as a list for display."""
    if not vendors:
        return f"No {VENDOR_CATEGORIES.get(category, 'vendors')} found."

    lines = []
    for idx, vendor in enumerate(vendors, 1):
        vendor_id, name, phone, email, company, specialty, rating, times_used, created = vendor

        rating_stars = "⭐" * rating if rating else "No rating"
        company_str = f" ({company})" if company else ""

        vendor_info = (
            f"{idx}) **{name}**{company_str}\n"
            f"   📞 {phone}\n"
        )
        if email:
            vendor_info += f"   📧 {email}\n"
        if specialty:
            vendor_info += f"   💡 {specialty}\n"
        vendor_info += f"   {rating_stars}\n"
        vendor_info += f"   Used: {times_used} times"

        lines.append(vendor_info)

    return "\n\n".join(lines)


def format_vendor_details(vendor: tuple, pha_details: tuple = None) -> str:
    """Format detailed vendor information."""
    vendor_id, category, name, phone, email, company, specialty, rating, times_used, created = vendor

    rating_stars = "⭐" * rating if rating else "No rating"

    details = (
        f"**{name}**\n\n"
    )

    if company:
        details += f"🏢 Company: {company}\n"

    details += f"📞 Phone: {phone}\n"

    if email:
        details += f"📧 Email: {email}\n"

    if specialty:
        details += f"💡 Specialty: {specialty}\n"

    details += f"⭐ Rating: {rating_stars}\n"
    details += f"📊 Times Used: {times_used}\n"
    details += f"📅 Added: {created[:10]}\n"

    # Add PHA-specific details if available
    if category == 'pha' and pha_details:
        agency, contact_person, dept, ext, line_type, best_time, fax, address, website, notes = pha_details
        details += "\n**PHA Details:**\n"
        if agency:
            details += f"🏛️ Agency: {agency}\n"
        if contact_person:
            details += f"👤 Contact: {contact_person}\n"
        if dept:
            details += f"🏢 Department: {dept}\n"
        if ext:
            details += f"📞 Extension: {ext}\n"
        if line_type:
            details += f"📱 Line Type: {line_type}\n"
        if best_time:
            details += f"🕐 Best Time: {best_time}\n"
        if fax:
            details += f"📠 Fax: {fax}\n"
        if address:
            details += f"📍 Address: {address}\n"
        if website:
            details += f"🌐 Website: {website}\n"
        if notes:
            details += f"📝 Notes: {notes}\n"

    return details


def get_vendor_detail_keyboard(vendor_id: int, category: str) -> InlineKeyboardMarkup:
    """Create keyboard for vendor detail actions."""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"vendor_edit_{vendor_id}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"vendor_delete_{vendor_id}"),
        ],
        [
            InlineKeyboardButton("📝 Add Note", callback_data=f"vendor_note_{vendor_id}"),
            InlineKeyboardButton("📋 View Notes", callback_data=f"vendor_viewnotes_{vendor_id}"),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data=f"vendor_cat_{category}"),
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
        "This bot helps you track lease recertifications and manage vendors.\n\n"
        "Commands:\n"
        "📝 /add - Add a new lease\n"
        "📋 /list - View all leases\n"
        "🗑️ /remove - Remove a lease\n"
        "🔧 Vendors - Manage your vendor contacts\n"
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


async def vendors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /vendors command - show vendor categories."""
    await update.message.reply_text(
        "🔧 **Vendor Management**\n\nSelect a category:",
        reply_markup=get_vendor_categories_keyboard(),
        parse_mode='Markdown'
    )


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
            "This bot helps you track lease recertifications and manage vendors.\n\n"
            "📝 Add Lease - Add a new lease with tenant info\n"
            "📋 View Leases - See all your tracked leases\n"
            "🗑️ Remove Lease - Delete a lease from tracking\n"
            "🔧 Vendors - Manage vendor contacts (plumbers, electricians, PHA contacts, etc.)\n"
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

    elif callback_data == "menu_vendors":
        # Show vendor categories
        await query.message.reply_text(
            "🔧 **Vendor Management**\n\nSelect a category:",
            reply_markup=get_vendor_categories_keyboard(),
            parse_mode='Markdown'
        )

    elif callback_data.startswith("vendor_cat_"):
        # Show vendors in a category
        category = callback_data.replace("vendor_cat_", "")
        vendors = get_vendors_by_category(chat_id, category)

        cat_name = VENDOR_CATEGORIES.get(category, 'Vendors')
        vendor_list = format_vendor_list(vendors, category)

        # Create inline buttons for each vendor
        keyboard_buttons = []
        for idx, vendor in enumerate(vendors, 1):
            vendor_id = vendor[0]
            vendor_name = vendor[1]
            keyboard_buttons.append([InlineKeyboardButton(
                f"{idx}. {vendor_name}",
                callback_data=f"vendor_view_{vendor_id}"
            )])

        # Add action buttons
        keyboard_buttons.extend(get_vendor_category_actions_keyboard(category).inline_keyboard)
        keyboard = InlineKeyboardMarkup(keyboard_buttons)

        await query.message.reply_text(
            f"{cat_name}\n\n{vendor_list}",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )

    elif callback_data.startswith("vendor_view_"):
        # View vendor details
        vendor_id = int(callback_data.replace("vendor_view_", ""))
        vendor = get_vendor_by_id(vendor_id, chat_id)

        if vendor:
            category = vendor[1]
            pha_details = get_pha_details(vendor_id) if category == 'pha' else None
            details = format_vendor_details(vendor, pha_details)

            await query.message.reply_text(
                details,
                reply_markup=get_vendor_detail_keyboard(vendor_id, category),
                parse_mode='Markdown'
            )

    elif callback_data.startswith("vendor_delete_"):
        # Confirm vendor deletion
        vendor_id = int(callback_data.replace("vendor_delete_", ""))
        vendor = get_vendor_by_id(vendor_id, chat_id)

        if vendor:
            vendor_name = vendor[2]
            category = vendor[1]
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⚠️ Yes, Delete", callback_data=f"vendor_confirm_delete_{vendor_id}_{category}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"vendor_view_{vendor_id}")
                ]
            ])

            await query.message.reply_text(
                f"⚠️ Are you sure you want to delete **{vendor_name}**?",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )

    elif callback_data.startswith("vendor_confirm_delete_"):
        # Actually delete the vendor
        parts = callback_data.replace("vendor_confirm_delete_", "").split("_")
        vendor_id = int(parts[0])
        category = parts[1]

        vendor = get_vendor_by_id(vendor_id, chat_id)
        if vendor:
            vendor_name = vendor[2]
            delete_vendor(vendor_id, chat_id)
            await query.message.reply_text(
                f"✅ {vendor_name} has been deleted.",
                reply_markup=get_vendor_category_actions_keyboard(category)
            )

    elif callback_data.startswith("vendor_edit_"):
        # Show edit options for vendor
        vendor_id = int(callback_data.replace("vendor_edit_", ""))
        vendor = get_vendor_by_id(vendor_id, chat_id)

        if vendor:
            vendor_name = vendor[2]
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Name", callback_data=f"vendor_editfield_{vendor_id}_name")],
                [InlineKeyboardButton("📞 Phone", callback_data=f"vendor_editfield_{vendor_id}_phone")],
                [InlineKeyboardButton("📧 Email", callback_data=f"vendor_editfield_{vendor_id}_email")],
                [InlineKeyboardButton("🏢 Company", callback_data=f"vendor_editfield_{vendor_id}_company")],
                [InlineKeyboardButton("💡 Specialty", callback_data=f"vendor_editfield_{vendor_id}_specialty")],
                [InlineKeyboardButton("⭐ Rating", callback_data=f"vendor_editfield_{vendor_id}_rating")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"vendor_view_{vendor_id}")],
            ])

            await query.message.reply_text(
                f"✏️ Edit **{vendor_name}**\n\nWhat would you like to edit?",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )

    elif callback_data.startswith("vendor_editfield_"):
        # Start editing a specific field
        parts = callback_data.replace("vendor_editfield_", "").split("_")
        vendor_id = int(parts[0])
        field = parts[1]

        context.user_data['edit_vendor_id'] = vendor_id
        context.user_data['edit_vendor_field'] = field

        field_names = {
            'name': 'Name',
            'phone': 'Phone Number',
            'email': 'Email',
            'company': 'Company Name',
            'specialty': 'Specialty/Notes',
            'rating': 'Rating (1-5)'
        }

        await query.message.reply_text(f"Enter new {field_names[field]}:")
        return VENDOR_EDIT_VALUE

    elif callback_data == "vendor_back_main":
        # Back to main menu from vendors
        await query.message.reply_text(
            "Main Menu",
            reply_markup=get_main_menu_keyboard()
        )


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
# Vendor Management - Conversation Flows
# ============================================================================

async def add_vendor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding a vendor from button."""
    query = update.callback_query
    category = query.data.replace("vendor_add_", "")
    context.user_data['vendor_category'] = category

    cat_name = VENDOR_CATEGORIES.get(category, 'Vendor')
    await query.message.reply_text(f"➕ Adding new {cat_name[2:]}\n\nEnter vendor name:")
    return VENDOR_NAME


async def vendor_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive vendor name."""
    context.user_data['vendor_name'] = update.message.text.strip()
    await update.message.reply_text("Enter phone number:")
    return VENDOR_PHONE


async def vendor_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive phone number."""
    context.user_data['vendor_phone'] = update.message.text.strip()
    await update.message.reply_text("Enter email (or type 'skip' to skip):")
    return VENDOR_EMAIL


async def vendor_email_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive email."""
    email = update.message.text.strip()
    context.user_data['vendor_email'] = None if email.lower() == 'skip' else email
    await update.message.reply_text("Enter company name (or type 'skip' to skip):")
    return VENDOR_COMPANY


async def vendor_company_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive company name."""
    company = update.message.text.strip()
    context.user_data['vendor_company'] = None if company.lower() == 'skip' else company
    await update.message.reply_text("Enter specialty/notes (or type 'skip' to skip):")
    return VENDOR_SPECIALTY


async def vendor_specialty_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive specialty."""
    specialty = update.message.text.strip()
    context.user_data['vendor_specialty'] = None if specialty.lower() == 'skip' else specialty
    await update.message.reply_text("Enter rating 1-5 stars (or type 'skip' to skip):")
    return VENDOR_RATING


async def vendor_rating_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive rating and save vendor."""
    rating_text = update.message.text.strip()

    rating = None
    if rating_text.lower() != 'skip':
        try:
            rating = int(rating_text)
            if rating < 1 or rating > 5:
                await update.message.reply_text("Invalid rating. Please enter 1-5:")
                return VENDOR_RATING
        except ValueError:
            await update.message.reply_text("Invalid rating. Please enter 1-5:")
            return VENDOR_RATING

    context.user_data['vendor_rating'] = rating

    # Save vendor
    chat_id = update.effective_chat.id
    category = context.user_data['vendor_category']
    name = context.user_data['vendor_name']
    phone = context.user_data['vendor_phone']
    email = context.user_data.get('vendor_email')
    company = context.user_data.get('vendor_company')
    specialty = context.user_data.get('vendor_specialty')

    vendor_id = add_vendor(chat_id, category, name, phone, email, company, specialty, rating)

    # If PHA category, ask for additional details
    if category == 'pha':
        context.user_data['vendor_id'] = vendor_id
        await update.message.reply_text("📋 PHA Contact - Enter agency name (or 'skip'):")
        return PHA_AGENCY

    # Show confirmation
    confirmation = (
        f"✅ Vendor added!\n\n"
        f"**{name}**\n"
        f"📞 {phone}\n"
    )
    if email:
        confirmation += f"📧 {email}\n"
    if company:
        confirmation += f"🏢 {company}\n"
    if specialty:
        confirmation += f"💡 {specialty}\n"
    if rating:
        confirmation += f"⭐ {'⭐' * rating}\n"

    await update.message.reply_text(
        confirmation,
        reply_markup=get_vendor_category_actions_keyboard(category),
        parse_mode='Markdown'
    )

    context.user_data.clear()
    return ConversationHandler.END


# PHA-specific conversation flow
async def pha_agency_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive PHA agency name."""
    agency = update.message.text.strip()
    context.user_data['pha_agency'] = None if agency.lower() == 'skip' else agency
    await update.message.reply_text("Enter contact person name (or 'skip'):")
    return PHA_CONTACT_PERSON


async def pha_contact_person_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive PHA contact person."""
    contact = update.message.text.strip()
    context.user_data['pha_contact_person'] = None if contact.lower() == 'skip' else contact
    await update.message.reply_text("Enter department (or 'skip'):")
    return PHA_DEPARTMENT


async def pha_department_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive PHA department."""
    dept = update.message.text.strip()
    context.user_data['pha_department'] = None if dept.lower() == 'skip' else dept
    await update.message.reply_text("That's all! Saving PHA contact...")

    # Save PHA details
    vendor_id = context.user_data['vendor_id']
    add_pha_contact(
        vendor_id=vendor_id,
        agency_name=context.user_data.get('pha_agency'),
        contact_person=context.user_data.get('pha_contact_person'),
        department=context.user_data.get('pha_department')
    )

    await update.message.reply_text(
        "✅ PHA Contact saved successfully!",
        reply_markup=get_vendor_category_actions_keyboard('pha')
    )

    context.user_data.clear()
    return ConversationHandler.END


# Edit vendor conversation flow
async def vendor_edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the new value for the vendor field being edited."""
    vendor_id = context.user_data.get('edit_vendor_id')
    field = context.user_data.get('edit_vendor_field')
    new_value = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Validate rating if that's what's being edited
    if field == 'rating':
        try:
            rating_value = int(new_value)
            if rating_value < 1 or rating_value > 5:
                await update.message.reply_text("❌ Invalid rating. Please enter 1-5:")
                return VENDOR_EDIT_VALUE
            new_value = rating_value
        except ValueError:
            await update.message.reply_text("❌ Invalid rating. Please enter 1-5:")
            return VENDOR_EDIT_VALUE

    # Update the vendor
    update_vendor(vendor_id, chat_id, **{field: new_value})

    # Get updated vendor info
    vendor = get_vendor_by_id(vendor_id, chat_id)
    if vendor:
        category = vendor[1]
        pha_details = get_pha_details(vendor_id) if category == 'pha' else None
        details = format_vendor_details(vendor, pha_details)

        await update.message.reply_text(
            f"✅ Updated successfully!\n\n{details}",
            reply_markup=get_vendor_detail_keyboard(vendor_id, category),
            parse_mode='Markdown'
        )

    context.user_data.clear()
    return ConversationHandler.END


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


async def set_bot_commands(application: Application):
    """Set up the bot commands menu that appears in Telegram UI."""
    commands = [
        BotCommand("start", "🏠 Main menu - Show bot interface"),
        BotCommand("add", "📝 Add lease - Add a new lease for tracking"),
        BotCommand("list", "📋 View leases - View all tracked leases"),
        BotCommand("remove", "🗑️ Remove lease - Remove a lease from tracking"),
        BotCommand("vendors", "🔧 Vendors - Manage vendor contacts"),
        BotCommand("help", "ℹ️ Help - Show help and instructions"),
        BotCommand("logout", "🔓 Logout - Logout from the bot"),
    ]

    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu set successfully")


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
    application.add_handler(CommandHandler("vendors", vendors_command))
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

    # Add conversation handler for vendor management (add new vendor)
    vendor_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_vendor_start, pattern="^vendor_add_")
        ],
        states={
            VENDOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_name_received)],
            VENDOR_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_phone_received)],
            VENDOR_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_email_received)],
            VENDOR_COMPANY: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_company_received)],
            VENDOR_SPECIALTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_specialty_received)],
            VENDOR_RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_rating_received)],
            PHA_AGENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, pha_agency_received)],
            PHA_CONTACT_PERSON: [MessageHandler(filters.TEXT & ~filters.COMMAND, pha_contact_person_received)],
            PHA_DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pha_department_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(vendor_conversation)

    # Add conversation handler for editing vendor
    vendor_edit_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_callback_handler, pattern="^vendor_editfield_")
        ],
        states={
            VENDOR_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_edit_value_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(vendor_edit_conversation)

    # Add callback query handler for inline buttons (non-conversation buttons)
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    # Set up background scheduler
    scheduler = setup_scheduler(application)

    # Set bot commands menu
    async def post_init(application: Application) -> None:
        """Post-initialization tasks."""
        await set_bot_commands(application)

    application.post_init = post_init

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
