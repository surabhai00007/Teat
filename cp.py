import asyncio
import json
import re
import aiohttp
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from bs4 import BeautifulSoup
import os
import shutil
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional
import logging
from config import *  # Import configuration
from config import ADMIN_ID
from pyrogram.errors import FloodWait
import urllib.parse # Added for unquote
from motor.motor_asyncio import AsyncIOMotorClient
import secrets
from datetime import datetime, timedelta
import base64
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import random
import string
from urllib.parse import quote

from broadcast import *
from users import *


# Store file ID mappings
file_id_map = {}

def encrypt_id(file_id):
    """Create a short encrypted ID for display"""
    try:
        # Take first 6 chars of base64 encoded file_id
        enc = base64.urlsafe_b64encode(file_id.encode()).decode()[:6]
        # Store mapping
        file_id_map[enc] = file_id
        return enc
    except Exception as e:
        print(f"Error encrypting ID: {e}")
        return None

HTML_API_URL = os.getenv("HTML_API_URL", "https://ugxappxtest.netlify.app/")  # Set this to your hosted API URL
HTML_API_KEY = os.getenv("HTML_API_KEY", "ugxapi")  # Set this to your API key
SEND_HTML = os.getenv("SEND_HTML", "false").lower() == "true"  # Only send HTML if true

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
 
# MongoDB setup
DATABASE_URL = os.environ.get("DATABASE_URL", "mongodb+srv://@cluster0.krzxuop.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
mongo_client = AsyncIOMotorClient(DATABASE_URL)
db = mongo_client['wadir']
tests_collection = db['tests']
enc_ids_collection = db['enc_ids']
users_collection = db['users']

# Flask app URL
FLASK_APP_URL = "https://appxhtml-4a57963f254f.herokuapp.com"  # Replace with your actual domain

# Bot client setup
app = Client(
    "test",  # Session name
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir=".",  # Store session file in current directory
    in_memory=False  # Use persistent session file
)

# Store user sessions
user_sessions = {}


# Add flood wait handler
async def safe_send_message(client, chat_id, *args, **kwargs):
    """Safely send message with flood wait handling"""
    try:
        msg = await client.send_message(chat_id, *args, **kwargs)
        return msg
    except FloodWait as e:
        logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
        await asyncio.sleep(e.value)
        return await safe_send_message(client, chat_id, *args, **kwargs)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

async def safe_send_document(client, chat_id, *args, **kwargs):
    """Safely send document with flood wait handling"""
    try:
        msg = await client.send_document(chat_id, *args, **kwargs)
        return msg
    except FloodWait as e:
        logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
        await asyncio.sleep(e.value)
        return await safe_send_document(client, chat_id, *args, **kwargs)
    except Exception as e:
        logger.error(f"Error sending document: {e}")
        return None

async def send_to_both(client, user_id, *args, **kwargs):
    """Send message to both user and log channel"""
    user_msg = await safe_send_message(client, user_id, *args, **kwargs)
    log_msg = await safe_send_message(client, LOG_CHANNEL, *args, **kwargs)
    return user_msg, log_msg

async def send_document_to_both(client, user_id, *args, **kwargs):
    """Send document to both user and log channel"""
    user_msg = await safe_send_document(client, user_id, *args, **kwargs)
    log_msg = await safe_send_document(client, LOG_CHANNEL, *args, **kwargs)
    return user_msg, log_msg

class TestSeriesBot:
    def __init__(self):
        self.session = None
        self.apps_list = None
        
    async def load_apps_list(self):
        """Load apps list from appx.json"""
        if not self.apps_list:
            try:
                async with aiofiles.open('appx.json', 'r') as f:
                    content = await f.read()
                    self.apps_list = json.loads(content)
            except Exception as e:
                logger.error(f"Error loading apps list: {e}")
                self.apps_list = []
        return self.apps_list
    
    def get_website_url(self, app_name_or_url):
        """Convert app name to website URL or clean API URL"""
        if app_name_or_url.startswith(('http://', 'https://')):
            # If it's a URL, remove 'api.' if present
            return app_name_or_url.replace('api', '')
        
        # Search in apps list
        app_name = app_name_or_url.lower()
        for app in self.apps_list:
            if app['name'].lower() == app_name:
                # Convert API URL to website URL
                return app['api'].replace('api', '')
        return None
        
    async def create_session(self):
        """Create aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
            )
        return self.session
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def fetch_page(self, url: str) -> str:
        """Fetch webpage content"""
        session = await self.create_session()
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    logger.error(f"Failed to fetch {url}: Status {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    async def extract_next_data(self, html_content: str) -> Optional[Dict]:
        """Extract JSON data from __NEXT_DATA__ script tag"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
            
            if script_tag:
                json_data = json.loads(script_tag.string)
                return json_data
            else:
                logger.error("__NEXT_DATA__ script tag not found")
                return None
        except Exception as e:
            logger.error(f"Error extracting JSON data: {e}")
            return None
    
    async def get_test_series_list(self, website_url: str) -> List[Dict]:
        """Get list of test series from website"""
        if not website_url.endswith('/test-series/'):
            if website_url.endswith('/'):
                website_url += 'test-series/'
            else:
                website_url += '/test-series/'
        
        html_content = await self.fetch_page(website_url)
        if not html_content:
            return []
        
        json_data = await self.extract_next_data(html_content)
        if not json_data:
            return []
        
        try:
            # Navigate through the JSON structure to find test series data
            props = json_data.get('props', {})
            page_props = props.get('pageProps', {})
            
            # Look for test series data in various possible locations
            test_series_data = []
            
            # Check if it's directly in pageProps
            if 'testSeries' in page_props and isinstance(page_props['testSeries'], list):
                test_series_data = page_props['testSeries']
            elif 'data' in page_props and isinstance(page_props['data'], list):
                test_series_data = page_props['data']
            elif 'courses' in page_props and isinstance(page_props['courses'], list):
                test_series_data = page_props['courses']
            else:
                # Search recursively for test series data
                test_series_data = self._find_test_series_data(json_data)
            
            return test_series_data
        except Exception as e:
            logger.error(f"Error parsing test series data: {e}")
            return []
    
    def _find_test_series_data(self, data, depth=0, max_depth=5):
        """Recursively search for test series data in JSON"""
        if depth > max_depth:
            return []
        
        if isinstance(data, dict):
            for key, value in data.items():
                if key in ['testSeries', 'courses', 'data'] and isinstance(value, list):
                    # Check if this looks like test series data
                    if value and isinstance(value[0], dict) and 'title' in value[0]:
                        return value
                elif isinstance(value, (dict, list)):
                    result = self._find_test_series_data(value, depth + 1, max_depth)
                    if result:
                        return result
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and 'title' in item:
                    return data
                elif isinstance(item, (dict, list)):
                    result = self._find_test_series_data(item, depth + 1, max_depth)
                    if result:
                        return result
        
        return []
    
    async def get_test_details(self, website_url: str, test_series_slug: str) -> Optional[Dict]:
        """Get detailed information about a specific test series"""
        base_url = website_url.rstrip('/')
        if not base_url.endswith('/test-series'):
            base_url += '/test-series'
        
        test_url = f"{base_url}/{test_series_slug}"
        
        html_content = await self.fetch_page(test_url)
        if not html_content:
            return None
        
        json_data = await self.extract_next_data(html_content)
        if not json_data:
            return None
        
        try:
            props = json_data.get('props', {})
            page_props = props.get('pageProps', {})
            
            return {
                'testSeries': page_props.get('testSeries', {}),
                'subjects': page_props.get('subjects', []),
                'tests': page_props.get('tests', {}),
                'url': test_url
            }
        except Exception as e:
            logger.error(f"Error parsing test details: {e}")
            return None
    
    
    async def download_file(self, url: str, filename: str) -> bool:
        """Download file from URL"""
        try:
            session = await self.create_session()
            async with session.get(url) as response:
                if response.status == 200:
                    async with aiofiles.open(filename, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                    return True
                else:
                    logger.error(f"Failed to download {url}: Status {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            return False

async def generate_quiz_html(json_data, test_name="Quiz", test_series_name="Test Series", institute_name="Institute", time="180"):
    """Generate quiz HTML by replacing placeholders in template.html"""
    try:
        # Read the template file
        async with aiofiles.open('template.html', 'r', encoding='utf-8') as f:
            template_content = await f.read()
            
        # Convert JSON data to string if it's not already a string
        if isinstance(json_data, (dict, list)):
            json_data = json.dumps(json_data)
        elif not isinstance(json_data, str):
            json_data = '[]'
            
        # Replace placeholders
        html_content = template_content.replace('{test_name}', test_name)
        html_content = html_content.replace('{test_series_name}', test_series_name)
        html_content = html_content.replace('{website_url}', institute_name)
        html_content = html_content.replace('{json_data}', json_data)
        html_content = html_content.replace('{test_time}', str(time))
        
        return html_content
    except Exception as e:
        logger.error(f"Error generating quiz HTML: {e}")
        return None

async def cleanup_files():
    """Clean up all HTML files in root directory except template.html"""
    try:
        # Get list of HTML files excluding template.html
        html_files = [f for f in os.listdir() if f.endswith('.html') and f != 'template.html']
        
        # Remove the files
        for file in html_files:
            try:
                if os.path.exists(file):
                    os.remove(file)
                    logger.info(f"Cleaned up HTML file: {file}")
            except Exception as e:
                logger.error(f"Error removing file {file}: {e}")
    except Exception as e:
        logger.error(f"Error during HTML cleanup: {e}")








async def send_test_to_user(client, message, html_content: str, test_name: str, test_time: str, questions_count: int, file_id: str = None):
    """Send test to user with fallback mechanisms"""
    try:
        
        
        # Fallback to direct HTML send
        safe_test_name = "".join(x for x in test_name if x.isalnum() or x in (' ', '-', '_')).strip()
        safe_test_name = safe_test_name.replace(' ', '_')
        html_filename = f"{safe_test_name}.html"
        
        try:
            # Save HTML temporarily
            async with aiofiles.open(html_filename, 'w', encoding='utf-8') as f:
                await f.write(html_content)
            
            # Send to user
            await client.send_document(
                message.chat.id,
                html_filename,
                caption=f"📝 {test_name}\n\n"
                        f"⏱️ Duration: {test_time} minutes\n"
                        f"❓ Questions: {questions_count}\n\n"
                        "Open this HTML file in a browser to take the test."
            )
            
            # Cleanup
            if os.path.exists(html_filename):
                os.remove(html_filename)
            
            return True
            
        except Exception as e:
            logger.error(f"Error in direct HTML send: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending test: {e}")
        return False

# Initialize bot instance
bot_instance = TestSeriesBot()

async def check_subscription(client, user_id):
    """Check if user has joined the channel"""
    try:
        member = await client.get_chat_member(CHANNEL_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return False

def get_force_sub_buttons():
    """Get force subscribe buttons"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="check_sub")]
    ])

WELCOME_MSG = """🎯 **ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴛʜᴇ ᴠͥɪͣᴘͫ⃝𝚅𝚎𝚎𝚛 ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ ʙᴏᴛ!**

📚 ᴇxᴛʀᴀᴄᴛ ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ ꜰʀᴏᴍ ᴀɴʏ ᴀᴘᴘx ᴀᴘᴘʟɪᴄᴀᴛɪᴏɴ.

🌟 **ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ:**
• 🔓 ᴜɴʟɪᴍɪᴛᴇᴅ ᴛᴇꜱᴛ ᴇxᴛʀᴀᴄᴛɪᴏɴ
• 📄 ᴀʟʟ ʜᴛᴍʟ & ꜱᴏʟᴜᴛɪᴏɴ ᴘᴅꜰꜱ
• ⭐️ ᴘʀɪᴏʀɪᴛʏ ꜱᴜᴘᴘᴏʀᴛ
• 🎯 ɴᴏ ᴄʀᴇᴅɪᴛ ʟɪᴍɪᴛꜱ
• 💎 ʟɪꜰᴇᴛɪᴍᴇ ᴀᴄᴄᴇꜱꜱ (₹500 ᴏɴʟʏ!)

🆓 **ꜰʀᴇᴇ ᴜꜱᴇʀꜱ:**
• 10 ꜰʀᴇᴇ ᴄʀᴇᴅɪᴛꜱ ᴛᴏ ꜱᴛᴀʀᴛ
• ʙᴀꜱɪᴄ ꜰᴇᴀᴛᴜʀᴇꜱ
• 🎁 ᴇᴀʀɴ ᴍᴏʀᴇ ᴄʀᴇᴅɪᴛꜱ ʙʏ ʀᴇꜰᴇʀʀɪɴɢ ꜰʀɪᴇɴᴅꜱ!

📝 **ʜᴏᴡ ᴛᴏ ᴜꜱᴇ:**
1️⃣ ᴄʟɪᴄᴋ ᴏɴ ᴇxᴛʀᴀᴄᴛ ʙᴜᴛᴛᴏɴ
2️⃣ ꜱᴇɴᴅ ᴡᴇʙꜱɪᴛᴇ ᴜʀʟ (ᴇx: parmaracademy.com)
3️⃣ ꜱᴇʟᴇᴄᴛ ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ & ᴅᴏᴡɴʟᴏᴀᴅ!

🤖 ᴍᴀᴅᴇ ʙʏ @VeerJaatOffline"""

HELP_MSG = """📚 **ᴋᴇꜱᴇ ᴜꜱᴇ ᴋᴀʀᴇ?**

1️⃣ ꜱᴀʙꜱᴇ ᴘᴇʜʟᴇ ᴀᴘᴘx ᴡᴇʙꜱɪᴛᴇ ᴋᴀ ᴜʀʟ ʙʜᴇᴊᴇ
ᴊᴇꜱᴇ: parmaracademy.com ʏᴀ www.parmaracademy.com

2️⃣ ᴜꜱᴋᴇ ʙᴀᴀᴅ ᴀᴀᴘᴋᴏ ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ ᴋᴇ ʙᴜᴛᴛᴏɴꜱ ᴅɪᴋʜᴇɴɢᴇ
ᴊɪꜱ ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ ᴋᴏ ᴇxᴛʀᴀᴄᴛ ᴋᴀʀɴᴀ ʜᴀɪ ᴜꜱ ᴘᴀʀ ᴄʟɪᴄᴋ ᴋᴀʀᴇ

3️⃣ ꜰɪʀ ᴀᴀᴘᴋᴏ ꜱᴜʙᴊᴇᴄᴛꜱ ᴅɪᴋʜᴇɴɢᴇ
ꜱᴜʙᴊᴇᴄᴛ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴇ (ᴊᴇꜱᴇ: 1 ʏᴀ 2)

4️⃣ ᴜꜱᴋᴇ ʙᴀᴀᴅ ᴛᴇꜱᴛꜱ ᴋɪ ʟɪꜱᴛ ᴀᴀʏᴇɢɪ
ᴛᴇꜱᴛ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴇ ᴊᴏ ᴅᴏᴡɴʟᴏᴀᴅ ᴋᴀʀɴᴀ ʜᴀɪ

5️⃣ ʙᴏᴛ ᴀᴀᴘᴋᴏ ʜᴛᴍʟ ꜰɪʟᴇ ᴀᴜʀ ᴘᴅꜰ ʙʜᴇᴊ ᴅᴇɢᴀ
ʜᴛᴍʟ ꜰɪʟᴇ ᴋᴏ ʙʀᴏᴡꜱᴇʀ ᴍᴇ ᴏᴘᴇɴ ᴋᴀʀᴋᴇ ᴛᴇꜱᴛ ᴅᴇ ꜱᴀᴋᴛᴇ ʜᴀɪ

💡 **ᴛɪᴘꜱ:**
• ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀꜱ ᴋᴏ ᴜɴʟɪᴍɪᴛᴇᴅ ᴛᴇꜱᴛꜱ ᴍɪʟᴛᴇ ʜᴀɪ
• ꜰʀᴇᴇ ᴜꜱᴇʀꜱ ᴋᴏ 10 ᴄʀᴇᴅɪᴛꜱ ᴍɪʟᴛᴇ ʜᴀɪ
• ᴅᴏꜱᴛᴏ ᴋᴏ ʀᴇꜰᴇʀ ᴋᴀʀᴋᴇ ᴄʀᴇᴅɪᴛꜱ ᴋᴀᴍᴀ ꜱᴀᴋᴛᴇ ʜᴀɪ
• ᴘʀᴇᴍɪᴜᴍ ᴋᴇ ʟɪʏᴇ @i_veerJaat ᴘᴀʀ ᴍᴇꜱꜱᴀɢᴇ ᴋᴀʀᴇ"""

async def get_welcome_buttons(user_id: int = None):
    """Get welcome message buttons including pricing and referral"""
    buttons = []
    
    # Premium and Free Extract buttons
    buttons.append([
        InlineKeyboardButton("🌟 ᴘʀᴇᴍɪᴜᴍ ᴇxᴛʀᴀᴄᴛ", callback_data="extract_premium"),
        InlineKeyboardButton("🆓 ꜰʀᴇᴇ ᴇxᴛʀᴀᴄᴛ", callback_data="extract_demo")
    ])
    
    # Account and Pricing buttons
    buttons.append([
        InlineKeyboardButton("👤 ᴍʏ ᴀᴄᴄᴏᴜɴᴛ", callback_data="my_account"),
        InlineKeyboardButton("💎 ᴘʀɪᴄɪɴɢ", callback_data="show_pricing")
    ])
    
    # Help and Earn Credits buttons
    buttons.append([
        InlineKeyboardButton("❓ ʜᴇʟᴘ", callback_data="show_help"),
        InlineKeyboardButton("🎁 ᴇᴀʀɴ ᴄʀᴇᴅɪᴛꜱ", callback_data=f"earn_credits_{user_id}") if user_id else None
    ])
    
    # Updates channel button
    buttons.append([
        InlineKeyboardButton("📢 ᴜᴘᴅᴀᴛᴇs", url="https://t.me/Veerjaatoffline")
    ])
    
    # Remove None values from buttons (in case user_id wasn't provided)
    buttons = [[btn for btn in row if btn] for row in buttons]
    buttons = [row for row in buttons if row]
    
    return InlineKeyboardMarkup(buttons)

@app.on_callback_query(filters.regex("^show_help$"))
async def show_help(client, callback_query):
    """Show help message with instructions in Hinglish"""
    buttons = [[InlineKeyboardButton("◀️ ʙᴀᴄᴋ", callback_data="back_to_start")]]
    
    await callback_query.message.edit_text(
        HELP_MSG,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex("^show_pricing$"))
async def show_pricing(client, callback_query):
    """Show pricing information with purchase button"""
    pricing_text = """💎 **ʟɪꜰᴇᴛɪᴍᴇ ᴘʀᴇᴍɪᴜᴍ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ**

💫 **ᴡʜᴀᴛ ʏᴏᴜ ɢᴇᴛ:**
• 🔓 ᴜɴʟɪᴍɪᴛᴇᴅ ᴛᴇꜱᴛ ᴇxᴛʀᴀᴄᴛɪᴏɴ
• 📄 ᴀʟʟ ʜᴛᴍʟ & ꜱᴏʟᴜᴛɪᴏɴ ᴘᴅꜰꜱ
• ⭐️ ᴘʀɪᴏʀɪᴛʏ ꜱᴜᴘᴘᴏʀᴛ
• 🎯 ɴᴏ ᴄʀᴇᴅɪᴛ ʟɪᴍɪᴛꜱ
• 🌟 ʟɪꜰᴇᴛɪᴍᴇ ᴀᴄᴄᴇꜱꜱ

💰 **ᴘʱɪᴄᴇ:** ₹500 ᴏɴʟʏ!"""

    buttons = [
        [InlineKeyboardButton(
            "🛍️ ʙᴜʏ ɴᴏᴡ",
            url=f"https://t.me/i_veerjaat"
        )],
        [InlineKeyboardButton("◀️ ʙᴀᴄᴋ", callback_data="back_to_start")]
    ]
    
    await callback_query.message.edit_text(
        pricing_text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex("^earn_credits_(\d+)$"))
async def show_referral(client, callback_query):
    """Show referral link and information"""
    user_id = int(callback_query.data.split('_')[2])
    
    # Generate referral link
    ref_code = await generate_referral_link(user_id)
    if not ref_code:
        await callback_query.answer("❌ Error generating referral link", show_alert=True)
        return
    
    ref_link = f"https://t.me/{(await client.get_me()).username}?start={ref_code}"
    
    ref_text = """🎁 **ᴇᴀʀɴ ꜰʀᴇᴇ ᴄʀᴇᴅɪᴛꜱ!**

📱 ꜱʜᴀʀᴇ ʏᴏᴜʀ ʀᴇꜰᴇʀʀᴀʟ ʟɪɴᴋ ᴡɪᴛʜ ꜰʀɪᴇɴᴅꜱ
🎯 ɢᴇᴛ 5 ᴄʀᴇᴅɪᴛꜱ ᴡʜᴇɴ ᴛʜᴇʏ ᴊᴏɪɴ
⭐️ ᴏɴᴇ-ᴛɪᴍᴇ ʀᴇᴡᴀʀᴅ ᴘᴇʀ ʀᴇꜰᴇʀʀᴀʟ

📎 **ʏᴏᴜʀ ʀᴇꜰᴇʀʀᴀʟ ʟɪɴᴋ:**
`{ref_link}`"""

    buttons = [
        [InlineKeyboardButton(
            "📤 ꜱʜᴀʀᴇ ʟɪɴᴋ",
            url=f"https://t.me/share/url?url={quote(ref_link)}&text={quote('Join APPx Test Series Bot and get free tests!')}"
        )],
        [InlineKeyboardButton("◀️ ʙᴀᴄᴋ", callback_data="back_to_start")]
    ]
    
    await callback_query.message.edit_text(
        ref_text.format(ref_link=ref_link),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex("^back_to_start$"))
async def back_to_start(client, callback_query):
    """Return to start menu"""
    reply_markup = await get_welcome_buttons(callback_query.from_user.id)
    await callback_query.message.edit_text(
        WELCOME_MSG,
        reply_markup=reply_markup
    )

@app.on_callback_query(filters.regex("^extract_"))
async def handle_extract_callback(client, callback_query):
    """Handle extract button callbacks"""
    try:
        user_id = callback_query.from_user.id
        mode = callback_query.data.split('_')[1]
        
        # Get user data
        user = await users_collection.find_one({'user_id': user_id})
        if not user:
            user = await register_user(callback_query.from_user)
        
        if mode == 'premium':
            # Check if user is premium
            if not user.get('is_premium', False):
                await callback_query.answer(
                    "❌ This feature is only for premium users!\n"
                    "Contact @ItsUGxBot to get premium access.",
                    show_alert=True
                )
                return
        else:  # demo mode
            # Check credits
            credits = user.get('credits', 0)
            if credits <= 0:
                await callback_query.answer(
                    "❌ You've used all your demo credits!\n"
                    "Contact @i_VeerJaat to get premium access.",
                    show_alert=True
                )
                return
        
        # Set extracting mode in session
        if user_id not in user_sessions:
            user_sessions[user_id] = {}
        user_sessions[user_id]['extracting_mode'] = True
        
        # Show app input message
        await callback_query.message.reply(
            "🔍 sᴇɴᴅ ᴍᴇ ᴛʜᴇ ᴀᴘᴘ ɴᴀᴍᴇ ᴏʀ ᴡᴇʙsɪᴛᴇ ᴜʀʟ\n\n"
            "📝 ᴇxᴀᴍᴘʟᴇ: parmaracademy"
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error in extract callback: {e}")
        await callback_query.answer("❌ An error occurred", show_alert=True)

@app.on_callback_query(filters.regex('^my_account$'))
async def handle_account_callback(client, callback_query):
    """Handle my account button"""
    try:
        user_id = callback_query.from_user.id
        
        # Get or create user
        user = await users_collection.find_one({'user_id': user_id})
        if not user:
            user = await register_user(callback_query.from_user)
        
        status = "🌟 Premium" if user.get('is_premium') else "⭐ Demo"
        credits = "Unlimited" if user.get('is_premium') else user.get('credits', 0)
        joined_date = user.get('joined_date', datetime.now()).strftime('%Y-%m-%d')
        
        account_msg = (
            f"👤 ᴜsᴇʀ ᴘʀᴏғɪʟᴇ\n\n"
            f"🆔 ɪᴅ: `{user_id}`\n"
            f"👤 ɴᴀᴍᴇ: {user.get('first_name', 'Unknown')}\n"
            f"📊 sᴛᴀᴛᴜs: {status}\n"
            f"💳 ᴄʀᴇᴅɪᴛs: {credits}\n"
            f"📅 ᴊᴏɪɴᴇᴅ: {joined_date}\n\n"
            f"📞 ᴄᴏɴᴛᴀᴄᴛ @i_VeerJaat ғᴏʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss"
        )
        
        await callback_query.message.reply(account_msg)
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error in account callback: {e}")
        await callback_query.answer("❌ An error occurred", show_alert=True)

@app.on_message(filters.command(["addxug", "removexug"]) & filters.private)
async def handle_premium_command(client, message):
    """Handle premium add/remove commands"""
    try:
        # Debug logging
        print(f"\n=== Admin Command Debug ===")
        print(f"User ID: {message.from_user.id}")
        print(f"Command: {message.text}")
        
        # Check if sender is admin
        if str(message.from_user.id) != str(ADMIN_ID):
            print("Not admin - ignoring command")
            await message.reply("❌ Only admin can use this command!")
            return
            
        print("Admin verified - processing command")
            
        # Parse command arguments
        args = message.text.split()[1:]
        if len(args) != 2:
            await message.reply(
                "❌ Format: /addxveer user_id days\n\n"
                "Example:\n/addxveer 123456789 30"
            )
            return
            
        user_id = int(args[0])
        days = int(args[1])
        is_add = message.command[0].lower() == "addxveer"
        
        print(f"Processing: {'add' if is_add else 'remove'} user {user_id} for {days} days")
        
        try:
            # Try to get user info from Telegram
            user = await client.get_users(user_id)
            name = user.first_name
            if user.last_name:
                name += f" {user.last_name}"
        except:
            # If can't get user info, use ID as name
            name = f"User {user_id}"
        
        # Calculate expiry date
        expiry_date = datetime.now() + timedelta(days=days)
        expiry_str = expiry_date.strftime("%d-%m-%Y %H:%M:%S")
        
        # Update user premium status
        result = await users_collection.update_one(
            {'user_id': user_id},
            {
                '$set': {
                    'user_id': user_id,
                    'name': name,
                    'is_premium': is_add,
                    'credits': 999999 if is_add else 10,  # Set high credits for premium
                    'expiry_date': expiry_date,
                    'added_by': message.from_user.id,
                    'added_date': datetime.now()
                }
            },
            upsert=True  # Create user if not exists
        )
        
        # Send success message to admin
        action = "added to" if is_add else "removed from"
        await message.reply(
            f"✅ {name} ({user_id}) {action} premium!\n"
            f"📅 Expires: {expiry_str}"
        )
        
        # Notify user
        try:
            if is_add:
                await client.send_message(
                    user_id,
                    f"🌟 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴs! ʏᴏᴜ'ʀᴇ ɴᴏᴡ ᴀ ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀ!\n"
                    f"📅 Expires: {expiry_str}"
                )
            else:
                await client.send_message(
                    user_id,
                    "⚠️ ʏᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss ʜᴀs ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ."
                )
        except Exception as e:
            print(f"Error notifying user: {e}")
            
    except ValueError:
        await message.reply("❌ Invalid user ID or days. Please use numbers only.")
    except Exception as e:
        logger.error(f"Error in premium command: {e}")
        await message.reply(f"❌ An error occurred: {str(e)}")

async def is_premium(user_id):
    """Check if user is premium"""
    user = await users_collection.find_one({'user_id': user_id})
    return user and user.get('is_premium', False)

async def get_credits(user_id):
    """Get user's remaining credits"""
    user = await users_collection.find_one({'user_id': user_id})
    return user.get('credits', 0) if user else 0

async def use_credit(user_id):
    """Use one credit and return remaining credits"""
    result = await users_collection.find_one_and_update(
        {'user_id': user_id},
        {'$inc': {'credits': -1}},
        return_document=True
    )
    return result.get('credits', 0) if result else 0

async def register_user(user):
    """Register new user with 10 demo credits"""
    await users_collection.update_one(
        {'user_id': user.id},
        {
            '$setOnInsert': {
                'user_id': user.id,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_premium': False,
                'credits': 10,
                'joined_date': datetime.now()
            }
        },
        upsert=True
    )
    return await users_collection.find_one({'user_id': user.id})

async def encrypt_id(file_id):
    """Create a short encrypted ID and store in MongoDB"""
    try:
        # Take first 6 chars of base64 encoded file_id
        enc = base64.urlsafe_b64encode(file_id.encode()).decode()[:6]
        
        # Store mapping in MongoDB
        await enc_ids_collection.update_one(
            {'enc_id': enc},
            {
                '$set': {
                    'file_id': file_id,
                    'created_at': datetime.now()
                }
            },
            upsert=True
        )
        return enc
    except Exception as e:
        logger.error(f"Error encrypting ID: {e}")
        return None

@app.on_message(filters.command("decx") & filters.private)
async def decrypt_command(client, message):
    """Decrypt ID and send file"""
    try:
        if len(message.command) != 2:
            await message.reply("❌ ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴛʜᴇ ᴇɴᴄʀʏᴘᴛᴇᴅ ɪᴅ\n\nᴇxᴀᴍᴘʟᴇ: /dec abc123")
            return
            
        enc_id = message.command[1]
        
        # Get original file ID from MongoDB
        doc = await enc_ids_collection.find_one({'enc_id': enc_id})
        if not doc:
            await message.reply("❌ ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ᴇɴᴄʀʏᴘᴛᴇᴅ ɪᴅ")
            return
            
        file_id = doc['file_id']
            
        # Send the file
        await message.reply_document(
            file_id,
            caption=f"📝 ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʜᴛᴍʟ ғɪʟᴇ\n🔑 ᴇɴᴄ ɪᴅ: `{enc_id}`"
        )
        
    except Exception as e:
        logger.error(f"Error in decrypt command: {e}")
        await message.reply("❌ ᴀɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ")

# Cleanup old encrypted IDs (run periodically)
async def cleanup_old_enc_ids():
    """Remove encrypted IDs older than 30 days"""
    try:
        thirty_days_ago = datetime.now() - timedelta(days=30)
        await enc_ids_collection.delete_many({
            'created_at': {'$lt': thirty_days_ago}
        })
    except Exception as e:
        logger.error(f"Error cleaning up old enc_ids: {e}")

# Cleanup on shutdown
@app.on_disconnect()
async def cleanup(client):
    """Cleanup on bot disconnect"""
    try:
        await bot_instance.close_session()
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

async def process_test_series(client, message, test_series):
    """Process test series data and send to user"""
    try:
        user_id = message.from_user.id
        series_name = test_series.get('title', 'Unknown Series')
        tests = test_series.get('tests', [])
        subjects = test_series.get('subjects', [])
        series_id = test_series.get('id')
        series_slug = test_series.get('slug')
        website_url = test_series.get('website_url')
        
        # Store in user session
        user_sessions[user_id] = {
            'series_name': series_name,  # Store series name
            'tests': tests,
            'subjects': subjects,
            'test_series_id': series_id,
            'current_series_slug': series_slug,
            'website_url': website_url,
            'waiting_for_subject': bool(subjects)
        }
        
        # ... rest of the existing code ...
    except Exception as e:
        logger.error(f"Error processing test series: {e}")
        await message.reply("❌ Error processing test series. Please try again.")

# Add new functions for referral system
async def generate_referral_link(user_id: int) -> str:
    """Generate or retrieve a referral link for a user"""
    try:
        # Check if user already has a referral code
        user_data = await db.users.find_one({"user_id": user_id})
        if user_data and user_data.get("referral_code"):
            return user_data["referral_code"]
        
        # Generate new unique referral code
        while True:
            ref_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            existing = await db.users.find_one({"referral_code": ref_code})
            if not existing:
                break
        
        # Store referral code
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"referral_code": ref_code, "referral_used": False}},
            upsert=True
        )
        return ref_code
    except Exception as e:
        logger.error(f"Error generating referral link: {e}")
        return None

async def handle_referral(client: Client, message: Message, ref_code: str):
    """Handle referral link clicks"""
    try:
        # Get referrer info
        referrer_data = await db.users.find_one({"referral_code": ref_code})
        if not referrer_data:
            return
        
        new_user_id = message.from_user.id
        referrer_id = referrer_data["user_id"]
        
        # Check if new user already used a referral
        new_user_data = await db.users.find_one({"user_id": new_user_id})
        if new_user_data and new_user_data.get("used_referral"):
            return
        
        # Check if referral code was already used
        if referrer_data.get("referral_used"):
            return
        
        # Update referrer's credits
        await db.users.update_one(
            {"user_id": referrer_id},
            {
                "$inc": {"credits": 5},  # Add 5 credits
                "$set": {"referral_used": True}
            }
        )
        
        # Mark new user as having used a referral
        await db.users.update_one(
            {"user_id": new_user_id},
            {"$set": {"used_referral": True}},
            upsert=True
        )
        
        # Notify referrer
        try:
            referee_name = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.first_name}"
            await client.send_message(
                referrer_id,
                f"🎉 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ!\n\n"
                f"📱 {referee_name} ᴊᴜꜱᴛ ᴜꜱᴇᴅ ʏᴏᴜʀ ʀᴇꜰᴇʀʀᴀʟ ʟɪɴᴋ\n"
                f"🎁 5 ᴄʀᴇᴅɪᴛꜱ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ!"
            )
        except Exception as e:
            logger.error(f"Error notifying referrer: {e}")
            
    except Exception as e:
        logger.error(f"Error handling referral: {e}")

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    """Handle /start command and referral links"""
    try:
        args = message.text.split()
        if len(args) > 1:
            ref_code = args[1]
            if ref_code.startswith("buy_"):
                # Handle buy command
                pass
            else:
                # Handle referral
                await handle_referral(client, message, ref_code)
        
        # Show welcome message with buttons
        reply_markup = await get_welcome_buttons(message.from_user.id)
        await message.reply(
            WELCOME_MSG,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await message.reply("❌ An error occurred. Please try again.")

@app.on_callback_query(filters.regex("^back_to_tests$"))
async def back_to_tests_handler(client, callback_query):
    """Handle back to tests list"""
    try:
        user_id = callback_query.from_user.id
        session = user_sessions.get(user_id)
        
        if not session or 'current_test_series' not in session:
            await callback_query.answer("❌ Session expired. Please start over.", show_alert=True)
            return
            
        # Trigger view tests handler again
        series = session['current_test_series']
        await view_tests_handler(
            client,
            CallbackQuery(
                client,
                callback_query.message,
                "view_tests_" + str(series.get('id')) + "_" + str(series.get('testseries_slug', '')),
                callback_query.from_user,
                callback_query.chat_instance
            )
        )
    except Exception as e:
        logger.error(f"Error going back to tests: {e}")
        await callback_query.answer("❌ Error returning to tests list.", show_alert=True)

@app.on_message(filters.command("help") & filters.private)
async def help_handler(client, message):
    help_text = """
🔧 **Bot Commands & Features:**

**Basic Commands:**
• /start - Welcome message
• /help - Show this help
• /clear - Clear your session

**How it works:**
1. **Send Website URL** - Send any educational website URL
2. **Browse Test Series** - Bot will show available test series
3. **Select Test** - Choose a test series to see details
4. **Get Resources** - Access questions, PDFs, and solutions

**Supported Features:**
✅ Extract test series from websites
✅ Display test details with images
✅ Download question papers and solutions
✅ Show test statistics and features
✅ Handle multiple test formats

**Example Usage:**
Send: `https://www.parmaracademy.in/`
Bot will show all available test series with details.

Need help? Just send me a website URL! 🎓
"""
    await message.reply(help_text)

@app.on_message(filters.command("getdb") & filters.private)
async def getdb_handler(client, message):
    """Handle /getdb command to send all database files"""
    try:
        db_files = [f for f in os.listdir() if f.endswith('.db')]
        if not db_files:
            await message.reply("ɴᴏ ᴅᴀᴛᴀʙᴀꜱᴇ ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ ɪɴ ᴛʜᴇ ʀᴏᴏᴛ ᴅɪʀᴇᴄᴛᴏʀʏ.")
            return
            
        for db_file in db_files:
            try:
                await safe_send_document(
                    client,
                    message.chat.id,
                    db_file,
                    caption=f"ᴅᴀᴛᴀʙᴀꜱᴇ ꜰɪʟᴇ: {db_file}"
                )
            except Exception as e:
                await message.reply(f"ᴇʀʀᴏʀ ꜱᴇɴᴅɪɴɢ {db_file}: {str(e)}")
                
        await message.reply(f"ꜱᴇɴᴛ {len(db_files)} ᴅᴀᴛᴀʙᴀꜱᴇ ꜰɪʟᴇ(ꜱ).")
    except Exception as e:
        await message.reply(f"ᴇʀʀᴏʀ ᴡʜɪʟᴇ ɢᴇᴛᴛɪɴɢ ᴅᴀᴛᴀʙᴀꜱᴇ ꜰɪʟᴇꜱ: {str(e)}")

@app.on_message(filters.command("clear") & filters.private)
async def clear_handler(client, message):
    """Clear user session including extraction mode"""
    user_id = message.from_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    await message.reply("✅ ʏᴏᴜʀ ꜱᴇꜱꜱɪᴏɴ ʜᴀꜱ ʙᴇᴇɴ ᴄʟᴇᴀʀᴇᴅ! ꜱᴇɴᴅ /start ᴛᴏ ʙᴇɢɪɴ ᴀɢᴀɪɴ.")

@app.on_message(filters.text & filters.private & ~filters.command(["addxveer", "removexveer", "start", "help", "clear", "getdb", "decx"]))
async def handle_text_message(client, message):
    """Handle text messages (app names, URLs, or selections)"""
    user_id = message.from_user.id
    
    # Check channel subscription
    if not await check_subscription(client, user_id):
        await message.reply(
            FORCE_SUB_MSG,
            reply_markup=get_force_sub_buttons()
        )
        return
    
    session = user_sessions.get(user_id, {})
    text = message.text.strip()
    
    # First check if it's a test selection (any number)
    if text.isdigit():
        test_idx = int(text) - 1
        tests = session.get('tests', [])
        
        if tests and 0 <= test_idx < len(tests):
            await handle_test_selection(client, message, text)
            return
        elif tests:
            await message.reply("❌ Invalid test number. Please select a valid test number.")
            return
    
    # Check if waiting for API domain input
    if session.get('waiting_for_api_domain'):
        # Handle API domain input
        api_domain = text.strip().lower()
        api_domain = re.sub(r'^https?://', '', api_domain)  # Remove protocol
        api_domain = api_domain.split('/')[0]  # Remove path
        
        if not api_domain or '.' not in api_domain:
            await message.reply("❌ Please enter a valid API domain (e.g., parmaracademyapi.classx.co.in)")
            return
            
        print(f"\n=== Using API domain: {api_domain} ===")
        
        # Store API domain and retry subject test fetch
        session['custom_api_domain'] = api_domain
        session['waiting_for_api_domain'] = False
        
        # Get stored subject data
        subject_idx = session.get('pending_subject_idx')
        subjects = session.get('subjects', [])
        
        if subject_idx is None or not subjects:
            await message.reply("❌ Session expired. Please start over.")
            return
            
        # Retry fetching tests with custom API domain
        await handle_subject_selection(client, message, subject_idx, subjects, api_domain)
        return
    
    # Check if waiting for subject selection
    if session.get('waiting_for_subject'):
        try:
            subject_idx = int(text) - 1
            subjects = session.get('subjects', [])
            
            if subject_idx < 0 or subject_idx >= len(subjects):
                await message.reply("❌ Invalid subject number. Please select a valid subject.")
                return
            
            # Store subject index in case we need to retry with custom API domain
            session['pending_subject_idx'] = subject_idx
            user_sessions[user_id] = session
            
            await handle_subject_selection(client, message, subject_idx, subjects)
            return
            
        except ValueError:
            await message.reply("❌ Please send a valid subject number.")
            return
    
    # If we reach here, handle as URL/app name
    await bot_instance.load_apps_list()
    website_url = bot_instance.get_website_url(text)
    
    if not website_url:
        # If not a valid URL/app name and not in extracting mode, show welcome message
        if not session.get('extracting_mode', False):
            await message.reply(
                WELCOME_MSG,
                reply_markup=get_welcome_buttons()
            )
            return
        else:
            await message.reply(
                "❌ ɪɴᴠᴀʟɪᴅ ᴀᴘᴘ ɴᴀᴍᴇ ᴏʀ ᴜʀʟ!\n\n"
                "ᴘʟᴇᴀꜱᴇ ᴍᴀᴋᴇ ꜱᴜʀᴇ ʏᴏᴜ'ʀᴇ ꜱᴇɴᴅɪɴɢ ᴀ ᴠᴀʟɪᴅ ᴀᴘᴘx ᴀᴘᴘ ɴᴀᴍᴇ ᴏʀ ᴡᴇʙꜱɪᴛᴇ ᴜʀʟ."
            )
            return
    
    # Process website
    await handle_website_url(client, message, website_url)

async def handle_website_url(client, message, url):
    user_id = message.from_user.id
    
    # Send processing message
    processing_msg = await message.reply("🔄 ᴘʀᴏᴄᴇꜱꜱɪɴɢ ᴡᴇʙꜱɪᴛᴇ... ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ...")
    
    try:
        # Get test series list
        test_series_list = await bot_instance.get_test_series_list(url)
        
        if not test_series_list:
            await processing_msg.edit("❌ ɴᴏ ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ ꜰᴏᴜɴᴅ. ᴘʟᴇᴀꜱᴇ ᴄʜᴇᴄᴋ ᴛʜᴇ ᴜʀʟ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.")
            return
        
        # Store in user session
        user_sessions[user_id] = {
            'website_url': url,
            'test_series_list': test_series_list,
            'current_page': 0
        }
        
        await show_test_series_page(client, processing_msg, user_id)
        
    except Exception as e:
        logger.error(f"Error processing website URL: {e}")
        await processing_msg.edit("❌ ᴇʀʀᴏʀ ᴘʀᴏᴄᴇꜱꜱɪɴɢ ᴡᴇʙꜱɪᴛᴇ. ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.")

async def show_test_series_page(client, message, user_id, edit=True):
    """Show test series as buttons with pagination"""
    session = user_sessions.get(user_id)
    if not session:
        return
    
    test_series_list = session['test_series_list']
    current_page = session['current_page']
    items_per_page = 8
    total_pages = (len(test_series_list) + items_per_page - 1) // items_per_page
    
    start_idx = current_page * items_per_page
    end_idx = min(start_idx + items_per_page, len(test_series_list))
    current_items = test_series_list[start_idx:end_idx]
    
    # Create buttons for test series
    buttons = []
    for series in current_items:
            title = series.get('title', 'Unknown Title')
            exam_name = series.get('examname', series.get('exam', ''))
            price = series.get('price', 'Free')
            
        # Create button text with small caps
            button_text = f"📚 {title[:30]}..."
            if exam_name:
                button_text += f"\n👉 {exam_name[:20]}"
            if price != 'Free':
                button_text += f"\n💰 ₹{price}"
            
        # Add button with callback data
            buttons.append([InlineKeyboardButton(
            button_text,
            callback_data=f"series_{test_series_list.index(series)}"
        )])
    
    # Add pagination buttons if needed
    nav_buttons = []
    if total_pages > 1:
        if current_page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
        if current_page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Add refresh button
    buttons.append([InlineKeyboardButton("🔄 Refresh List", callback_data="refresh_list")])
    
    # Create message text
    msg_text = f"📋 **ꜰᴏᴜɴᴅ {len(test_series_list)} ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ**\n"
    msg_text += f"📍 ᴘᴀɢᴇ {current_page + 1}/{total_pages}\n\n"
    msg_text += "ᴄʟɪᴄᴋ ᴏɴ ᴀ ᴛᴇꜱᴛ ꜱᴇʀɪᴇꜱ ᴛᴏ ᴠɪᴇᴡ ᴛᴇꜱᴛꜱ:"
    
    # Edit or send message
    if edit:
        await message.edit(msg_text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply(msg_text, reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("^(prev_page|next_page|refresh_list)$"))
async def handle_pagination(client, callback_query):
    """Handle pagination callbacks"""
    user_id = callback_query.from_user.id
    session = user_sessions.get(user_id)
    
    if not session:
        await callback_query.answer("❌ Session expired. Please start over.", show_alert=True)
        return
    
    action = callback_query.data
    if action == "prev_page" and session['current_page'] > 0:
        session['current_page'] -= 1
    elif action == "next_page":
        session['current_page'] += 1
    elif action == "refresh_list":
        # Reload test series list
        url = session['website_url']
        test_series_list = await bot_instance.get_test_series_list(url)
        if test_series_list:
            session['test_series_list'] = test_series_list
            session['current_page'] = 0
    
    await show_test_series_page(client, callback_query.message, user_id)
    await callback_query.answer()

@app.on_callback_query(filters.regex("^series_(\d+)$"))
async def handle_series_selection(client, callback_query):
    """Handle test series selection"""
    user_id = callback_query.from_user.id
    session = user_sessions.get(user_id)
    
    if not session:
        await callback_query.answer("❌ Session expired. Please start over.", show_alert=True)
        return
    
    try:
        series_idx = int(callback_query.data.split('_')[1])
        selected_series = session['test_series_list'][series_idx]
        
        # Store current test series in session
        session['current_test_series'] = selected_series
        
        # Create simplified caption
        title = selected_series.get('title', 'Unknown Title')
        exam = selected_series.get('examname', '')
        price = selected_series.get('price', 'Free')
        
        caption = f"📚 **{title}**\n"
        if exam:
            caption += f"🎯 **Exam:** {exam}\n"
        caption += f"💰 **Price:** ₹{price}\n\n"
        
        # Add key features (limited to 2)
        features = []
        for i in range(1, 3):  # Only show 2 features
            feature = selected_series.get(f'feature_{i}')
            if feature:
                features.append(f"✨ {feature}")
        if features:
            caption += "\n".join(features)
        
        # Create buttons
        buttons = []
        
        # Get series ID and slug
        series_id = selected_series.get('id') 
        series_slug = selected_series.get('slug') or selected_series.get('testseries_slug')
        
        if series_slug:  # Use only slug for the callback data
            buttons.append([InlineKeyboardButton(
                "📝 View Tests",
                callback_data=f"view_tests_{series_id}_{series_slug}"
            )])
        
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_list")])
        
        # Send logo/banner image if available
        logo_url = selected_series.get('logo')
        banner_url = selected_series.get('banner')
        image_url = banner_url if banner_url else logo_url
        
        if image_url:
            try:
                await callback_query.message.reply_photo(
                    photo=image_url,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                await callback_query.message.delete()
            except Exception as e:
                logger.error(f"Error sending image: {e}")
                await callback_query.message.edit(
                    caption,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        else:
            await callback_query.message.edit(
                caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    
    except Exception as e:
        logger.error(f"Error handling series selection: {e}")
        await callback_query.answer("❌ Error processing selection.", show_alert=True)

@app.on_callback_query(filters.regex("^back_to_list$"))
async def back_to_list_handler(client, callback_query):
    """Handle back to list button"""
    user_id = callback_query.from_user.id
    if user_id in user_sessions:
        # Reset extracting mode when going back to list
        user_sessions[user_id]['extracting_mode'] = True
    await show_test_series_page(client, callback_query.message, user_id)
    await callback_query.answer()

@app.on_callback_query(filters.regex("^view_tests_(\d+)_(.+)$"))
async def view_tests_handler(client, callback_query):
    """Handle viewing tests for a series"""
    try:
        user_id = callback_query.from_user.id
        user_session = user_sessions.get(user_id)
        
        if not user_session:
            await callback_query.answer("❌ Session expired. Please start over.", show_alert=True)
            return
    
        # Extract series ID and slug
        match = re.match(r"view_tests_(\d+)_(.+)", callback_query.data)
        if not match:
            await callback_query.answer("❌ Invalid test series data.", show_alert=True)
            return
            
        series_id, series_slug = match.groups()
        print(f"\n=== Test Series ID from callback: {series_id} ===")
        
        # Show processing message
        await callback_query.answer("🔄 Fetching tests...")
        processing_msg = await callback_query.message.edit_text("🔄 Fetching tests...")
        
        # Get website URL from session
        website_url = user_session.get('website_url', '')
        if not website_url:
            await processing_msg.edit_text("❌ Website URL not found in session.")
            return
        
        # Construct API URL
        base_url = website_url.rstrip('/')
        api_url = f"{base_url}/test-series/{series_id}-{series_slug}"
        
        # Fetch HTML content
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url) as response:
                    if response.status != 200:
                        await processing_msg.edit_text("❌ Failed to fetch tests.")
                        return
    
                    html_content = await response.text()
                    
                    # Extract JSON from __NEXT_DATA__ script tag
                    soup = BeautifulSoup(html_content, 'html.parser')
                    script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
                    
                    if not script_tag:
                        # Try alternate script tag
                        script_tag = soup.find('script', {'type': 'application/json'})
                        if not script_tag:
                            await processing_msg.edit_text("❌ Failed to extract test data.")
                            return
                    
                    try:
                        json_data = json.loads(script_tag.string)
                        page_props = json_data.get('props', {}).get('pageProps', {})
                        
                        # Get test series info
                        test_series = page_props.get('testSeries', {})
                        
                        # First try to get direct tests
                        tests = []
                        tests_data = page_props.get('tests', {})
                        
                        if isinstance(tests_data, dict):
                            tests = tests_data.get('test_titles', []) or tests_data.get('tests', [])
                        elif isinstance(tests_data, list):
                            tests = tests_data
                        
                        if not tests:
                            tests = (page_props.get('test_titles', []) or 
                                   page_props.get('testTitles', []) or 
                                   page_props.get('testsList', []))
                        
                        # Check if tests have direct URLs
                        has_direct_urls = False
                        if tests:
                            for test in tests:
                                if isinstance(test, dict) and (test.get('json_url') or test.get('pdf_url') or 
                                   test.get('test_solutions_pdf') or test.get('solution_pdf') or 
                                   test.get('question_paper_pdf') or test.get('solutions_pdf')):
                                    has_direct_urls = True
                                    break
                        
                        # Only show subjects if no direct tests with URLs are found
                        subjects = page_props.get('subjects', [])
                        if not has_direct_urls and subjects and len(subjects) > 0:
                            # Print subjects data for debugging
                            print("\n=== Available Subjects ===")
                            for subject in subjects:
                                print(f"ID: {subject.get('subjectid')} - Name: {subject.get('subject_name')}")
                            
                            # Store subjects in session
                            user_sessions[user_id].update({
                                'subjects': subjects,
                                'test_series_id': series_id,  # Store the original series ID
                                'current_series_slug': series_slug,
                                'website_url': website_url,
                                'waiting_for_subject': True,
                                'current_test_series': test_series  # Store test series info
                            })
                            
                            # Split subjects into chunks of 20
                            CHUNK_SIZE = 20
                            subject_chunks = [subjects[i:i + CHUNK_SIZE] for i in range(0, len(subjects), CHUNK_SIZE)]
                            
                            # Send first chunk by editing original message
                            first_chunk = subject_chunks[0]
                            msg = f"📚 **{test_series.get('title', 'Test Series')}**\n\n"
                            msg += "**Select a subject:**\n\n"
                            
                            for i, subject in enumerate(first_chunk, 1):
                                subject_name = subject.get('subject_name', 'Unknown Subject')
                                is_paid = subject.get('is_paid', 0)
                                subject_id = subject.get('subjectid', 'N/A')
                                msg += f"{i:02d}. {'💎 ' if is_paid else '🆓 '}{subject_name} [ID: {subject_id}]\n\n"
                            
                            if len(subject_chunks) > 1:
                                msg += "\n(Continued in next message...)"
                            else:
                                msg += "\n💡 Send subject number to view tests"
                            
                            await processing_msg.edit_text(msg)
                            
                            # Send remaining chunks as new messages
                            for chunk_idx, chunk in enumerate(subject_chunks[1:], 2):
                                chunk_msg = f"📚 **Subjects (Part {chunk_idx})**\n\n"
                                
                                start_idx = (chunk_idx - 1) * CHUNK_SIZE + 1
                                for i, subject in enumerate(chunk, start_idx):
                                    subject_name = subject.get('subject_name', 'Unknown Subject')
                                    is_paid = subject.get('is_paid', 0)
                                    subject_id = subject.get('subjectid', 'N/A')
                                    chunk_msg += f"{i:02d}. {'💎 ' if is_paid else '🆓 '}{subject_name} [ID: {subject_id}]\n\n"
                                
                                if chunk_idx < len(subject_chunks):
                                    chunk_msg += "\n(Continued in next message...)"
                                else:
                                    chunk_msg += "\n💡 Send subject number to view tests"
                                
                                await callback_query.message.reply_text(chunk_msg)
                            
                            return
                        
                        # If we have direct tests or no subjects, continue with existing test list logic
                        if not tests:
                            await processing_msg.edit_text("❌ No tests found in this series.")
                            return
                        
                        # Store tests in session
                        user_sessions[user_id].update({
                            'tests': tests,
                            'test_series_json': json_data,
                            'current_series_id': series_id,
                            'current_series_slug': series_slug,
                            'current_test_series': test_series  # Store test series info
                        })
                        
                        # Split tests into chunks of 20
                        CHUNK_SIZE = 20
                        test_chunks = [tests[i:i + CHUNK_SIZE] for i in range(0, len(tests), CHUNK_SIZE)]
                        
                        # Send first chunk by editing original message
                        first_chunk = test_chunks[0]
                        msg = f"📚 **{test_series.get('title', 'Test Series')}**\n\n"
                        
                        for i, test in enumerate(first_chunk, 1):
                            if isinstance(test, dict):
                                title = test.get('title', 'Unknown Test')
                                duration = test.get('time', test.get('duration', 'N/A'))
                                questions = test.get('questions', test.get('total_questions', 'N/A'))
                                is_free = test.get('free_flag') == '1' or test.get('is_free') == True
                                
                                has_pdf = any([
                                    test.get('pdf_url'),
                                    test.get('test_solutions_pdf'),
                                    test.get('solution_pdf'),
                                    test.get('question_paper_pdf'),
                                    test.get('solutions_pdf')
                                ])
                                
                                test_line = f"{i:02d}. {'🆓 ' if is_free else '💎 '}{title}\n"
                                test_line += f"⏱️ {duration}m • ❓ {questions}q"
                                if has_pdf:
                                    test_line += " • 📄"
                                test_line += "\n\n"
                                
                                msg += test_line
                        
                        if len(test_chunks) > 1:
                            msg += "\n(Continued in next message...)"
                        else:
                            msg += "\n💡 Send test number to view details"
                        
                        await processing_msg.edit_text(msg)
                        
                        # Send remaining chunks as new messages
                        for chunk_idx, chunk in enumerate(test_chunks[1:], 2):
                            chunk_msg = f"📚 **Tests (Part {chunk_idx})**\n\n"
                            
                            start_idx = (chunk_idx - 1) * CHUNK_SIZE + 1
                            for i, test in enumerate(chunk, start_idx):
                                if isinstance(test, dict):
                                    title = test.get('title', 'Unknown Test')
                                    duration = test.get('time', test.get('duration', 'N/A'))
                                    questions = test.get('questions', test.get('total_questions', 'N/A'))
                                    is_free = test.get('free_flag') == '1' or test.get('is_free') == True
                                    
                                    has_pdf = any([
                                        test.get('pdf_url'),
                                        test.get('test_solutions_pdf'),
                                        test.get('solution_pdf'),
                                        test.get('question_paper_pdf'),
                                        test.get('solutions_pdf')
                                    ])
                                    
                                    test_line = f"{i:02d}. {'🆓 ' if is_free else '💎 '}{title}\n"
                                    test_line += f"⏱️ {duration}m • ❓ {questions}q"
                                    if has_pdf:
                                        test_line += " • 📄"
                                    test_line += "\n\n"
                                    
                                    chunk_msg += test_line
                            
                            if chunk_idx < len(test_chunks):
                                chunk_msg += "\n(Continued in next message...)"
                            else:
                                chunk_msg += "\n💡 Send test number to view details"
                            
                            await callback_query.message.reply_text(chunk_msg)
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
                        await processing_msg.edit_text("❌ Failed to parse test data.")
                        return
                    
            except aiohttp.ClientError as e:
                logger.error(f"HTTP request error: {e}")
                await processing_msg.edit_text("❌ Failed to connect to server.")
                return
                    
    except Exception as e:
        logger.error(f"Error viewing tests: {e}")
        await callback_query.message.edit_text("❌ Error fetching tests. Please try again.")

async def send_test_series_details(client, message, test_details, processing_msg):
    try:
        tests = test_details.get('tests', [])
        if not tests:
            await processing_msg.edit("❌ ɴᴏ ᴛᴇꜱᴛꜱ ꜰᴏᴜɴᴅ.")
            return

        # Store tests for selection
        user_id = message.from_user.id
        user_sessions[user_id] = {'tests': tests}

        # Format tests - super simple
        msg = ""
        count = 0
        
        for i, test in enumerate(tests, 1):
            title = test.get('title', 'Unknown Test')
            msg += f"> {i:02d} • {title}\n\n"
            count += 1
            
            # Send every 20 tests
            if count == 20:
                if i == 20:  # First message
                    await processing_msg.edit(msg)
                else:  # Later messages
                    await message.reply(f"ᴄᴏɴᴛɪɴᴜᴇᴅ...\n\n{msg}")
                msg = ""
                count = 0
        
        # Send remaining tests
        if msg:
            await message.reply(f"ᴄᴏɴᴛɪɴᴜᴇᴅ...\n\n{msg}")
        
        # Send instruction
        await message.reply("📝 ꜱᴇɴᴅ ᴛᴇꜱᴛ ɴᴜᴍʙᴇʀ ᴛᴏ ᴇxᴛʀᴀᴄᴛ")

    except Exception as e:
        logger.error(f"Error sending test series details: {e}")
        await processing_msg.edit("❌ ᴇʀʀᴏʀ")

@app.on_callback_query(filters.regex("^refresh_tests$"))
async def refresh_tests(client, callback_query):
    """Handle test list refresh"""
    try:
        message = callback_query.message
        user_id = callback_query.from_user.id
        session = user_sessions.get(user_id)
        
        if not session:
            await callback_query.answer("❌ Session expired. Please start over.", show_alert=True)
            return
            
        # Get fresh test details
        test_details = await bot_instance.get_test_details(
            session['website_url'],
            session.get('current_test_series', {}).get('slug')
        )
        
        if test_details:
            await send_test_series_details(client, message, test_details, message)
            await callback_query.answer("✅ Test list refreshed!")
        else:
            await callback_query.answer("❌ Failed to refresh tests.", show_alert=True)
            
    except Exception as e:
        logger.error(f"Error refreshing tests: {e}")
        await callback_query.answer("❌ Error refreshing tests.", show_alert=True)

async def handle_test_selection(client, message, text):
    """Handle test selection by index"""
    try:
        user_id = message.from_user.id
        
        # Check if premium, if not check and use credits
        if not await is_premium(user_id):
            credits = await get_credits(user_id)
            if credits <= 0:
                await message.reply(
                    "❌ ʏᴏᴜ'ᴠᴇ ᴜsᴇᴅ ᴀʟʟ ʏᴏᴜʀ ᴅᴇᴍᴏ ᴄʀᴇᴅɪᴛs!\n"
                    "📞 ᴄᴏɴᴛᴀᴄᴛ @i_VeerJaat ᴛᴏ ɢᴇᴛ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss."
                )
                return
            
            # Use one credit
            remaining = await use_credit(user_id)
            if remaining == 0:
                await message.reply(
                    "⚠️ ᴛʜɪs ɪs ʏᴏᴜʀ ʟᴀsᴛ ᴄʀᴇᴅɪᴛ!\n"
                    "📞 ᴄᴏɴᴛᴀᴄᴛ @i_VeerJaat ᴛᴏ ɢᴇᴛ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss."
                )
        
        if user_id not in user_sessions or 'tests' not in user_sessions[user_id]:
            await message.reply("❌ ᴘʟᴇᴀsᴇ sᴇʟᴇᴄᴛ ᴀ ᴛᴇsᴛ sᴇʀɪᴇs ғɪʀsᴛ.")
            return

        # Get session data
        session = user_sessions[user_id]
        tests = session['tests']
        
        # Validate test index
        try:
            test_idx = int(text.strip()) - 1
        except ValueError:
            await message.reply("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴛᴇsᴛ ɴᴜᴍʙᴇʀ.")
            return
            
        if test_idx < 0 or test_idx >= len(tests):
            await message.reply("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇsᴛ ɴᴜᴍʙᴇʀ. ᴘʟᴇᴀsᴇ sᴇʟᴇᴄᴛ ᴀ ᴠᴀʟɪᴅ ᴛᴇsᴛ.")
            return
        
        test = tests[test_idx]
        processing_msg = await message.reply("🔄 ᴘʀᴏᴄᴇssɪɴɢ ᴛᴇsᴛ ᴅᴇᴛᴀɪʟs...")
        
        try:
            # Send test details
            await send_test_details(client, message, test, processing_msg)
                
        except Exception as e:
            logger.error(f"Error processing test: {e}")
            await processing_msg.edit_text("❌ ғᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴄᴇss ᴛᴇsᴛ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")
            
    except Exception as e:
        logger.error(f"Error in test selection: {e}")
        await message.reply("❌ ᴀɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")

async def handle_subject_selection(client, message, subject_idx, subjects, custom_api_domain=None):
    """Handle subject selection and test fetching"""
    try:
        subject = subjects[subject_idx]
        processing_msg = await message.reply("🔄 Fetching tests for subject...")
        
        # Get test series ID from the stored series ID
        user_id = message.from_user.id
        session = user_sessions.get(user_id, {})
        test_series_id = session.get('test_series_id', '')
        if '-' in test_series_id:
            test_series_id = test_series_id.split('-')[0]  # Get the numeric ID part
        
        subject_id = subject.get('subjectid')
        website_url = session.get('website_url', '').rstrip('/')
        
        # Use custom API domain if provided, otherwise derive from website URL
        if custom_api_domain:
            api_domain = custom_api_domain
        else:
            base_domain = urlparse(website_url).netloc
            if '.' in base_domain:
                subdomain = base_domain.split('.')[0]
                rest_domain = '.'.join(base_domain.split('.')[1:])
                api_domain = f"{subdomain}api.{rest_domain}"
            else:
                api_domain = f"api.{base_domain}"
        
        print(f"\n=== Using API domain: {api_domain} ===")
        
        # Construct API URL using API domain
        api_url = f"https://{api_domain}/get/test_titlev2?testseriesid={test_series_id}&subject_id={subject_id}&userid=-1&search=&start=-1"
        print(f"\n=== Trying API URL ===\n{api_url}")
        
        # Simplified headers - only essential ones
        headers = {
            'Auth-Key': 'appxapi',
            'Client-Service': 'Appx',
            'source': 'website'
        }
        
        print("\n=== Request Headers ===")
        print(json.dumps(headers, indent=2))
        
        async with aiohttp.ClientSession() as http_session:
            try:
                # Make direct GET request without OPTIONS
                print("\nSending GET request...")
                async with http_session.get(api_url, headers=headers) as response:
                    print(f"GET Response Status: {response.status}")
                    print("GET Response Headers:")
                    print(json.dumps(dict(response.headers), indent=2))
                    response_text = await response.text()
                    
                    if response.status == 200:
                        try:
                            data = json.loads(response_text)
                            print("\n=== NEXT_DATA from API ===")
                            print(json.dumps({
                                'test_count': len(data.get('test_titles', [])),
                                'first_test': data.get('test_titles', [{}])[0] if data.get('test_titles') else None
                            }, indent=2))
                            
                            tests = data.get('test_titles', [])
                            
                            if not tests:
                                await processing_msg.edit("❌ No tests found in this subject.")
                                return
                            
                            # Store tests in session
                            session['tests'] = tests
                            session['waiting_for_subject'] = False
                            user_sessions[user_id] = session
                            
                            # Create test list message
                            msg = f"📚 **{subject.get('subject_name', 'Subject')} Tests**\n\n"
                            
                            # Split tests into chunks of 20
                            for i, test in enumerate(tests, 1):
                                title = test.get('title', 'Unknown Test')
                                duration = test.get('time', test.get('duration', 'N/A'))
                                questions = test.get('questions', test.get('total_questions', 'N/A'))
                                is_free = test.get('free_flag') == '1' or test.get('is_free') == True
                                
                                has_pdf = any([
                                    test.get('pdf_url'),
                                    test.get('test_solutions_pdf'),
                                    test.get('solution_pdf'),
                                    test.get('question_paper_pdf'),
                                    test.get('solutions_pdf')
                                ])
                                
                                test_line = f"{i:02d}. {'🆓 ' if is_free else '💎 '}{title}\n"
                                test_line += f"⏱️ {duration}m • ❓ {questions}q"
                                if has_pdf:
                                    test_line += " • 📄"
                                test_line += "\n\n"
                                
                                msg += test_line
                                
                                # Send every 20 tests
                                if i % 20 == 0:
                                    if i == 20:  # First chunk
                                        await processing_msg.edit(msg)
                                    else:  # Later chunks
                                        await message.reply(msg)
                                    msg = f"📚 **Tests (Continued...)**\n\n"  # Start new chunk
                            
                            # Send remaining tests if any
                            if msg and msg != f"📚 **Tests (Continued...)**\n\n":
                                await message.reply(msg)
                            
                            await message.reply("📝 Send test number to view details and download materials")
                            
                        except json.JSONDecodeError as je:
                            print(f"JSON Parse Error: {je}")
                            if not custom_api_domain:
                                # Ask for custom API domain
                                session['waiting_for_api_domain'] = True
                                user_sessions[user_id] = session
                                await processing_msg.edit(
                                    "❌ Failed to get tests data. Please enter the API domain to retry.\n\n"
                                    "Example format: parmaracademyapi.classx.co.in"
                                )
                            else:
                                await processing_msg.edit("❌ Error parsing subject tests data.")
                    else:
                        print(f"API Error - Status: {response.status}")
                        if not custom_api_domain:
                            # If this is the first attempt, ask for custom API domain
                            session['waiting_for_api_domain'] = True
                            user_sessions[user_id] = session
                            await processing_msg.edit(
                                "❌ Failed to fetch subject tests. Please enter the API domain to retry.\n\n"
                                "Example format: parmaracademyapi.classx.co.in"
                            )
                        else:
                            await processing_msg.edit(f"❌ Failed to fetch subject tests with custom API domain. Status: {response.status}")
                        
            except aiohttp.ClientError as e:
                print(f"HTTP Request Error: {e}")
                if "Cannot connect to host" in str(e) or "getaddrinfo failed" in str(e):
                    # Connection error - ask for custom API domain
                    session['waiting_for_api_domain'] = True
                    user_sessions[user_id] = session
                    await processing_msg.edit(
                        "❌ Could not connect to API server. Please enter the correct API domain.\n\n"
                        "Example format: parmaracademyapi.classx.co.in"
                    )
                else:
                    await processing_msg.edit("❌ Failed to connect to server.")
            except Exception as e:
                print(f"Unexpected Error: {e}")
                if not custom_api_domain:
                    # Ask for custom API domain for any error
                    session['waiting_for_api_domain'] = True
                    user_sessions[user_id] = session
                    await processing_msg.edit(
                        "❌ Error occurred. Please enter the API domain to retry.\n\n"
                        "Example format: parmaracademyapi.classx.co.in"
                    )
                else:
                    await processing_msg.edit("❌ An unexpected error occurred.")
            return
            
    except Exception as e:
        print(f"Error in handle_subject_selection: {e}")
        await message.reply("❌ An error occurred while processing your request.")

async def send_test_details(client, message, test_data, processing_msg):
    """Send test details to user"""
    try:
        user_id = message.from_user.id
        session = user_sessions.get(user_id, {})
        
        # Get test name and website URL
        test_name = test_data.get('title', 'Unknown Test')
        safe_test_name = "".join(x for x in test_name if x.isalnum() or x in (' ', '-', '_')).strip()
        safe_test_name = safe_test_name.replace(' ', '_')
        
        # Get website URL from session
        website_url = session.get('website_url', '')
        if website_url:
            if not website_url.startswith(('http://', 'https://')):
                website_url = 'https://' + website_url
        
        # Get test series name from session
        current_test_series = session.get('current_test_series', {})
        test_series_name = current_test_series.get('title', 'Test Series')
        
        # Get all question URLs
        question_urls = []
        if test_data.get('test_questions_url'):
            question_urls.append(test_data['test_questions_url'])
        if test_data.get('test_questions_url_2'):
            question_urls.append(test_data['test_questions_url_2'])
        
        # Process each question URL
        for i, url in enumerate(question_urls, 1):
            try:
                # Fetch questions data
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.get(url) as response:
                        if response.status == 200:
                            questions_data = await response.json()
                            logger.info(f"Successfully fetched questions data from {url}")
                            
                            # Generate HTML with test data
                            html_content = await generate_quiz_html(
                                json_data=questions_data,
                                test_name=f"{test_name} (Part {i})" if len(question_urls) > 1 else test_name,
                                test_series_name=test_series_name,
                                institute_name=website_url,
                                time=test_data.get('time') or test_data.get('duration') or "180"
                            )
                            
                            if html_content:
                                # Save HTML file
                                html_filename = f"{safe_test_name}_part{i}.html" if len(question_urls) > 1 else f"{safe_test_name}.html"
                                async with aiofiles.open(html_filename, 'w', encoding='utf-8') as f:
                                    await f.write(html_content)
                                
                                # Send HTML file
                                await client.send_document(
                                    message.chat.id,
                                    html_filename,
                                    caption=f"📝 {test_name} (Part {i})\n\nOpen this HTML file in a browser to take the test." if len(question_urls) > 1 else f"📝 {test_name}\n\nOpen this HTML file in a browser to take the test."
                                )
                                
                                # Clean up file
                                if os.path.exists(html_filename):
                                    os.remove(html_filename)
                        else:
                            logger.error(f"Failed to fetch questions from {url}: {response.status}")
            except Exception as e:
                logger.error(f"Error processing question URL {url}: {e}")
        
        # Handle PDFs
        pdf_sent = False
        base_url = session.get('website_url', '').rstrip('/')
        
        # Check and send solution PDF
        if test_data.get('test_solutions_pdf'):
            pdf_url = test_data['test_solutions_pdf']
            if pdf_url and pdf_url.strip():  # Check if URL is not empty
                if not pdf_url.startswith(('http://', 'https://')):
                    pdf_url = f"{base_url}{pdf_url if pdf_url.startswith('/') else '/' + pdf_url}"
                pdf_filename = f"solution-{safe_test_name}.pdf"
                if await bot_instance.download_file(pdf_url, pdf_filename):
                    try:
                        # Send to user
                        pdf_msg = await client.send_document(
                            message.chat.id,
                            pdf_filename,
                            caption=f"📚 Solutions PDF for {test_name}"
                        )
                        pdf_sent = True
                        
                        # Send to log channel
                        if 'LOG_CHANNEL' in globals():
                            await client.send_document(
                                LOG_CHANNEL,
                                pdf_filename,
                                caption=f"📚 Solutions PDF - {test_name}\nUser: {message.from_user.mention}"
                            )
                    except Exception as e:
                        logger.error(f"Error sending solution PDF: {e}")
                    finally:
                        # Clean up file
                        if os.path.exists(pdf_filename):
                            os.remove(pdf_filename)
        
        # Check and send question PDF
        if test_data.get('pdf_url'):
            pdf_url = test_data['pdf_url']
            if pdf_url and pdf_url.strip():  # Check if URL is not empty
                if not pdf_url.startswith(('http://', 'https://')):
                    pdf_url = f"{base_url}{pdf_url if pdf_url.startswith('/') else '/' + pdf_url}"
                pdf_filename = f"question-{safe_test_name}.pdf"
                if await bot_instance.download_file(pdf_url, pdf_filename):
                    try:
                        # Send to user
                        pdf_msg = await client.send_document(
                            message.chat.id,
                            pdf_filename,
                            caption=f"📝 Question Paper for {test_name}"
                        )
                        pdf_sent = True
                        
                        # Send to log channel
                        if 'LOG_CHANNEL' in globals():
                            await client.send_document(
                                LOG_CHANNEL,
                                pdf_filename,
                                caption=f"📝 Question Paper - {test_name}\nUser: {message.from_user.mention}"
                            )
                    except Exception as e:
                        logger.error(f"Error sending question PDF: {e}")
                    finally:
                        # Clean up file
                        if os.path.exists(pdf_filename):
                            os.remove(pdf_filename)
        
        if not pdf_sent:
            no_pdf_msg = await message.reply("📢 No PDFs available for this test.")
            if 'LOG_CHANNEL' in globals():
                await client.send_message(
                    LOG_CHANNEL,
                    f"ℹ️ No PDFs available for test: {test_name}\nUser: {message.from_user.mention}"
                )
        
        # Don't clear session yet to allow further interactions
        await processing_msg.edit("✅ ᴛᴇꜱᴛ ꜱᴇɴᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!")
        
    except Exception as e:
        logger.error(f"Error sending test details: {e}")
        await processing_msg.edit("❌ ᴇʀʀᴏʀ ꜱᴇɴᴅɪɴɢ ᴛᴇꜱᴛ. ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ.")

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    """Handle /start command and referral links"""
    try:
        args = message.text.split()
        if len(args) > 1:
            ref_code = args[1]
            if ref_code.startswith("buy_"):
                # Handle buy command
                pass
            else:
                # Handle referral
                await handle_referral(client, message, ref_code)
        
        # Show welcome message with buttons
        reply_markup = await get_welcome_buttons(message.from_user.id)
        await message.reply(
            WELCOME_MSG,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await message.reply("❌ An error occurred. Please try again.")

if __name__ == "__main__":
    print("🚀 Starting Advanced Test Series Bot...")
    print("Bot is ready to process test series from educational websites!")
    
    try:
        app.run()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot stopped due to error: {e}")
    finally:
        # Cleanup
        try:
            asyncio.run(bot_instance.close_session())
        except Exception as e:
            print(f"Error during final cleanup: {e}")
