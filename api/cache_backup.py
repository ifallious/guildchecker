import json
import os
import sys

# Add parent directory to path so we can import from the main file
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify

# Create a separate app for the cache backup
app = Flask(__name__)

# This is the cache backup that persists between deployments
PERSISTENT_CACHE = {}

@app.route('/api/cache/backup', methods=['POST'])
def backup_cache():
    """Endpoint to backup the cache data"""
    global PERSISTENT_CACHE
    try:
        # Get the cache data from the request
        cache_data = request.json
        
        # Store it in our global variable
        PERSISTENT_CACHE = cache_data
        
        return jsonify({"status": "success", "message": f"Cached {len(cache_data)} players"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cache/restore', methods=['GET'])
def restore_cache():
    """Endpoint to restore the cache data"""
    global PERSISTENT_CACHE
    try:
        # Simple security - require an access token
        access_token = request.args.get('token')
        expected_token = os.environ.get('CACHE_ACCESS_TOKEN')
        
        if not expected_token or access_token != expected_token:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
        # Return the cached data
        return jsonify(PERSISTENT_CACHE), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# This is required for Vercel serverless functions
if __name__ == "__main__":
    app.run(debug=True) 