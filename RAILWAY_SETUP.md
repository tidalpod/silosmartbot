# Railway Deployment Setup

## Persistent Database Storage

To keep your lease and vendor data across deployments, you need to set up a **persistent volume** in Railway.

### Setup Instructions:

1. **Go to your Railway project**: https://railway.app/project/[your-project-id]

2. **Click on your service** (silosmartbot)

3. **Go to the "Volumes" tab** (in the left sidebar)

4. **Click "New Volume"**

5. **Configure the volume:**
   - **Mount Path**: `/data`
   - **Size**: 1 GB (more than enough for database)

6. **Click "Add"**

7. **Redeploy** your service (Railway will automatically redeploy)

### What This Does:

- Creates a persistent `/data` directory
- Database file will be stored at `/data/leases.db`
- Data persists across all deployments and restarts
- Environment variable `RAILWAY_VOLUME_MOUNT_PATH` is automatically set to `/data`

### Verification:

After setting up the volume and redeploying:
1. Add a lease or vendor in your bot
2. Push a code change to trigger a new deployment
3. Check if your data is still there - it should persist!

### Current Environment Variables Required:

Make sure these are set in Railway:
- `TELEGRAM_BOT_TOKEN` - Your bot token
- `TEAM_CHAT_ID` (optional) - Team notification chat ID
- `RAILWAY_VOLUME_MOUNT_PATH` - Automatically set to `/data` when volume is added

### Local Development:

When running locally, the bot will use `./leases.db` in the current directory (no volume needed).
