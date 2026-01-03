# Lease Recertification Bot

A Telegram bot that tracks Section 8 lease recertification dates and sends automated reminders.

## Features

- Track multiple leases with tenant name, property address, and lease start date
- Automatic calculation of recertification dates (9 months after lease start)
- Automated reminders 7 days before recertification is due
- Team notifications to a shared group/channel
- Simple SQLite database for persistent storage

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

You need to set the following environment variables:

**Required:**
- `TELEGRAM_BOT_TOKEN` - Your Telegram bot token (get it from @BotFather)

**Optional:**
- `TEAM_CHAT_ID` - Telegram chat ID of your team group/channel (reminders will be sent here too)

#### On macOS/Linux:
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TEAM_CHAT_ID="your_team_chat_id_here"  # Optional
```

#### On Windows (Command Prompt):
```cmd
set TELEGRAM_BOT_TOKEN=your_bot_token_here
set TEAM_CHAT_ID=your_team_chat_id_here
```

#### On Windows (PowerShell):
```powershell
$env:TELEGRAM_BOT_TOKEN="your_bot_token_here"
$env:TEAM_CHAT_ID="your_team_chat_id_here"
```

**How to get your Team Chat ID:**
1. Add your bot to the team group/channel
2. Send a message in the group
3. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Look for the `"chat":{"id":...}` field in the response

### 3. Run the Bot

```bash
python bot.py
```

The bot will:
- Initialize the SQLite database (`leases.db` will be created automatically)
- Start listening for commands
- Run a daily scheduler at 9:00 AM to check for reminders

## Available Commands

- `/start` - Get welcome message and instructions
- `/help` - Show help message
- `/add` - Add a new lease (interactive conversation)
- `/list` - View all your tracked leases
- `/remove` - Remove a lease (interactive selection)
- `/logout` - Delete all your tracked leases

## How It Works

1. **Adding a Lease**: When you use `/add`, the bot will ask for:
   - Tenant name
   - Property address
   - Lease start date (YYYY-MM-DD format)

2. **Automatic Calculations**:
   - Recertification due date = Lease start date + 9 months (270 days)
   - Reminder date = Recertification date - 7 days

3. **Reminders**: Every day at 9:00 AM, the bot checks for leases whose reminder date is today and sends notifications to:
   - The user who created the lease
   - The team chat (if `TEAM_CHAT_ID` is configured)

## Database

The bot uses SQLite with a `leases` table containing:
- `id` - Primary key
- `chat_id` - Telegram chat ID
- `tenant_name` - Name of the tenant
- `property_address` - Property address
- `lease_start_date` - Lease start date
- `recert_date` - Calculated recertification date
- `reminder_date` - Calculated reminder date
- `created_at` - Timestamp when lease was added

## Production Deployment Tips

1. **Environment Variables**: Use a `.env` file with a package like `python-dotenv` for easier management
2. **Process Management**: Use `systemd`, `supervisor`, or `pm2` to keep the bot running
3. **Logging**: Logs are written to stdout; redirect to a file or use a logging service
4. **Backup**: Regularly backup the `leases.db` file
5. **Time Zone**: The bot uses server local time; ensure your server is in the correct timezone

## Example Usage

```
User: /add
Bot: Enter tenant name:

User: John Smith
Bot: Enter property address:

User: 123 Main St, Detroit, MI 48201
Bot: Enter lease start date (YYYY-MM-DD):

User: 2025-01-15
Bot: ✅ Lease added.

Tenant: John Smith
Address: 123 Main St, Detroit, MI 48201
Start: 2025-01-15
Recert: 2025-10-12
Reminder: 2025-10-05
```

## License

MIT
