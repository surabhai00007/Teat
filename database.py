import motor.motor_asyncio
import os
from datetime import datetime
from config import DATABASE_URL

# MongoDB connection string (you can modify this as needed)
MONGO_URL = DATABASE_URL

# Create Motor client
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)

# Get database
db = client.appx_test_bot

# Helper functions for user management
async def add_user(user_data: dict):
    """Add new user to database"""
    user_id = user_data.get('user_id')
    if not user_id:
        return False
        
    # Check if user exists
    existing = await db.users.find_one({'user_id': user_id})
    if existing:
        # Update last_active
        await db.users.update_one(
            {'user_id': user_id},
            {'$set': {'last_active': datetime.now()}}
        )
        return False
        
    # Add join_date and last_active
    user_data['join_date'] = datetime.now()
    user_data['last_active'] = datetime.now()
    
    # Insert new user
    try:
        await db.users.insert_one(user_data)
        return True
    except Exception as e:
        print(f"Error adding user: {e}")
        return False

async def get_user(user_id: int):
    """Get user data from database"""
    return await db.users.find_one({'user_id': user_id})

async def update_user(user_id: int, update_data: dict):
    """Update user data in database"""
    try:
        await db.users.update_one(
            {'user_id': user_id},
            {'$set': update_data}
        )
        return True
    except Exception as e:
        print(f"Error updating user: {e}")
        return False

async def remove_user(user_id: int):
    """Remove user from database"""
    try:
        await db.users.delete_one({'user_id': user_id})
        return True
    except Exception as e:
        print(f"Error removing user: {e}")
        return False

# Create indexes
async def create_indexes():
    """Create necessary database indexes"""
    try:
        # User ID index
        await db.users.create_index('user_id', unique=True)
        # Join date index for sorting
        await db.users.create_index('join_date')
        # Last active index for analytics
        await db.users.create_index('last_active')
        # Referral code index
        await db.users.create_index('referral_code', sparse=True)
        print("✅ Database indexes created successfully")
    except Exception as e:
        print(f"❌ Error creating indexes: {e}")

# Initialize database
async def init_database():
    """Initialize database connection and setup"""
    try:
        # Ping database
        await client.admin.command('ping')
        print("✅ Connected to MongoDB successfully!")
        
        # Create indexes
        await create_indexes()
        
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False 