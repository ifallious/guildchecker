import sys
import os

# Add parent directory to path so we can import from the main file
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the Flask app from the main file
from Wynncraftguildchecker import app
from flask import request, jsonify

# Global persistent cache storage
persistent_cache = {}

@app.route('/api/cache/backup', methods=['POST'])
def cache_backup():
    """Store the cache for persistence between deployments"""
    global persistent_cache
    try:
        token = request.headers.get('X-Cache-Token')
        expected_token = os.environ.get('CACHE_TOKEN')
        
        if expected_token and token != expected_token:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
            
        persistent_cache = request.json
        return jsonify({"status": "success", "players": len(persistent_cache)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cache/restore', methods=['GET'])
def cache_restore():
    """Retrieve the cache for persistence between deployments"""
    global persistent_cache
    try:
        token = request.args.get('token')
        expected_token = os.environ.get('CACHE_TOKEN')
        
        if expected_token and token != expected_token:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
            
        return jsonify(persistent_cache), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# This is required for Vercel serverless functions
if __name__ == "__main__":
    app.run(debug=True) 