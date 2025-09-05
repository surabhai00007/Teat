from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserIsBlocked, ChatWriteForbidden
import asyncio
import time
from config import ADMIN_ID
from database import db

async def broadcast_message(client, message):
    """Broadcast a message to all users"""
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("❌ ᴏɴʟʏ ᴀᴅᴍɪɴ ᴄᴀɴ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ!")
        return

    if not message.reply_to_message:
        await message.reply("❌ ᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀꜱᴛ!")
        return

    # Get all users from database
    users = await db.users.find({}).to_list(length=None)
    total_users = len(users)
    
    if total_users == 0:
        await message.reply("❌ ɴᴏ ᴜꜱᴇʀꜱ ꜰᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ!")
        return

    # Send status message
    status_msg = await message.reply(
        f"🔄 ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ {total_users} ᴜꜱᴇʀꜱ..."
    )

    start_time = time.time()
    success = 0
    failed = 0
    blocked = 0

    # Broadcast message to all users
    for user in users:
        try:
            await message.reply_to_message.copy(user['user_id'])
            success += 1
            # Add small delay to avoid flood
            await asyncio.sleep(0.1)
        except UserIsBlocked:
            blocked += 1
            failed += 1
        except ChatWriteForbidden:
            failed += 1
        except FloodWait as e:
            # Sleep the required time if flood wait occurs
            await asyncio.sleep(e.value)
            # Retry sending to this user
            try:
                await message.reply_to_message.copy(user['user_id'])
                success += 1
            except:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"Error broadcasting to {user['user_id']}: {str(e)}")

    end_time = time.time()
    time_taken = round(end_time - start_time, 2)

    # Send completion message
    await status_msg.edit(
        f"""✅ **ʙʀᴏᴀᴅᴄᴀꜱᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!**

📊 **ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ:**
• ᴛᴏᴛᴀʟ ᴜꜱᴇʀꜱ: `{total_users}`
• ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ: `{success}`
• ꜰᴀɪʟᴇᴅ: `{failed}`
• ʙʟᴏᴄᴋᴇᴅ: `{blocked}`
• ᴛɪᴍᴇ ᴛᴀᴋᴇɴ: `{time_taken}s`"""
    )

def setup(app: Client):
    """Setup broadcast command handler"""
    
    @app.on_message(filters.command("bc") & filters.private)
    async def broadcast_handler(client, message):
        await broadcast_message(client, message) 