from pyrogram import Client, filters
from config import ADMIN_ID
from database import db
import datetime

async def get_user_stats():
    """Get user statistics from database"""
    total_users = await db.users.count_documents({})
    premium_users = await db.users.count_documents({"is_premium": True})
    today = await db.users.count_documents({
        "join_date": {
            "$gte": datetime.datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        }
    })
    
    return total_users, premium_users, today

async def get_recent_users(limit: int = 10):
    """Get recent users from database"""
    users = await db.users.find().sort("join_date", -1).limit(limit).to_list(length=None)
    return users

async def show_users_command(client, message):
    """Handle /users command"""
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("❌ ᴏɴʟʏ ᴀᴅᴍɪɴ ᴄᴀɴ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ!")
        return

    # Get user stats
    total_users, premium_users, today_users = await get_user_stats()
    
    # Get recent users
    recent_users = await get_recent_users(10)
    
    # Create user list message
    user_list = ""
    for idx, user in enumerate(recent_users, 1):
        name = user.get('name', 'Unknown')
        user_id = user.get('user_id', 'N/A')
        join_date = user.get('join_date', datetime.datetime.now())
        is_premium = "🌟" if user.get('is_premium') else "⭐"
        
        user_list += f"{idx}. {is_premium} [{name}](tg://user?id={user_id})\n"
        user_list += f"   ├ ID: `{user_id}`\n"
        user_list += f"   └ Joined: `{join_date.strftime('%Y-%m-%d %H:%M:%S')}`\n\n"

    # Create and send message
    msg = f"""📊 **ᴜꜱᴇʀ ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ**

👥 **ᴛᴏᴛᴀʟ ᴜꜱᴇʀꜱ:** `{total_users}`
💎 **ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀꜱ:** `{premium_users}`
📅 **ɴᴇᴡ ᴛᴏᴅᴀʏ:** `{today_users}`

👤 **ʟᴀꜱᴛ 10 ᴜꜱᴇʀꜱ:**
{user_list}

🔍 ᴜꜱᴇ /getdb ᴛᴏ ɢᴇᴛ ꜰᴜʟʟ ᴅᴀᴛᴀʙᴀꜱᴇ"""

    await message.reply(msg)

def setup(app: Client):
    """Setup users command handler"""
    
    @app.on_message(filters.command("users") & filters.private)
    async def users_handler(client, message):
        await show_users_command(client, message) 