from flask import Flask, render_template_string, abort
from pymongo import MongoClient
import os
from datetime import datetime, timedelta
import json
import secrets
from config import *

app = Flask(__name__)

# MongoDB setup
client = MongoClient(DATABASE_URL)
db = client[DB_NAME]
tests_collection = db[COLLECTION_NAME]

# Store active test sessions
active_sessions = {}

@app.route('/')
def home():
    return "APPx Test Server is running!"

@app.route('/test/<session_token>')
def serve_test(session_token):
    """Serve the test HTML based on session token"""
    try:
        # Check if session exists and is valid
        session_data = active_sessions.get(session_token)
        if not session_data:
            return abort(404, description="Test session not found")
        
        # Check if session has expired
        if datetime.now() > session_data['expires_at']:
            del active_sessions[session_token]
            return abort(404, description="Test session has expired")
        
        # Get test data from MongoDB
        test_data = tests_collection.find_one({'file_id': session_data['file_id']})
        if not test_data:
            return abort(404, description="Test not found")
        
        # Serve the HTML content
        return render_template_string(test_data['html_content'])
        
    except Exception as e:
        print(f"Error serving test: {e}")
        return abort(500, description="Internal server error")

@app.route('/create_session/<file_id>')
def create_test_session(file_id):
    """Create a new test session"""
    try:
        # Generate unique session token
        session_token = secrets.token_urlsafe(32)
        
        # Set session expiry (e.g., 3 hours)
        expires_at = datetime.now() + timedelta(hours=3)
        
        # Store session data
        active_sessions[session_token] = {
            'file_id': file_id,
            'expires_at': expires_at
        }
        
        # Get Heroku app URL from environment or use localhost
        app_url = os.environ.get('APP_URL', 'http://localhost:5000')
        
        # Return session URL
        test_url = f"{app_url}/test/{session_token}"
        return json.dumps({
            'success': True,
            'test_url': test_url,
            'session_token': session_token,
            'expires_at': expires_at.isoformat()
        })
        
    except Exception as e:
        print(f"Error creating session: {e}")
        return json.dumps({
            'success': False,
            'error': str(e)
        })

@app.route('/cleanup_expired')
def cleanup_expired_sessions():
    """Cleanup expired test sessions"""
    try:
        current_time = datetime.now()
        expired_tokens = [
            token for token, data in active_sessions.items()
            if current_time > data['expires_at']
        ]
        
        for token in expired_tokens:
            del active_sessions[token]
            
        return json.dumps({
            'success': True,
            'cleaned_sessions': len(expired_tokens)
        })
        
    except Exception as e:
        print(f"Error cleaning sessions: {e}")
        return json.dumps({
            'success': False,
            'error': str(e)
        })

if __name__ == '__main__':
    # Get port from environment (Heroku sets this)
    port = int(os.environ.get('PORT', 5000))
    
    # Run app
    app.run(host='0.0.0.0', port=port) 
    