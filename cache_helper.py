import os
import json
import time
import requests
from datetime import datetime, timedelta

# Cache file path - use /tmp directory for Vercel serverless functions
CACHE_FILE = '/tmp/wynncraft_player_cache.json'

# Get app URL and token from environment
APP_URL = os.environ.get('VERCEL_URL', '')
if APP_URL and not APP_URL.startswith('http'):
    APP_URL = f"https://{APP_URL}"
CACHE_TOKEN = os.environ.get('CACHE_TOKEN', '')

def load_cache():
    """Load cached player data from file and restore from backup if needed"""
    cache = {}
    
    # Try to load from local tmp directory first
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
                print(f"Loaded local cache with {len(cache)} players")
                return cache
        except Exception as e:
            print(f"Error loading local cache: {e}")
    
    # If local cache failed or doesn't exist, try to restore from our application's persistent storage
    if APP_URL:
        try:
            restore_url = f"{APP_URL}/api/cache/restore"
            if CACHE_TOKEN:
                restore_url += f"?token={CACHE_TOKEN}"
                
            response = requests.get(restore_url)
            if response.status_code == 200 and response.json():
                cache = response.json()
                # Save the restored cache locally
                with open(CACHE_FILE, 'w') as f:
                    json.dump(cache, f, indent=2)
                print(f"Restored cache from app storage with {len(cache)} players")
                return cache
        except Exception as e:
            print(f"Error restoring cache from app storage: {e}")
    
    return {}

def save_cache(cache):
    """Save player data to cache file and backup if configured"""
    # Always save to local tmp directory
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
        print(f"Saved {len(cache)} players to local cache")
    except Exception as e:
        print(f"Error saving local cache: {e}")
    
    # If we have the app URL, also save to our persistent storage
    if APP_URL and len(cache) > 0:
        try:
            headers = {'Content-Type': 'application/json'}
            if CACHE_TOKEN:
                headers['X-Cache-Token'] = CACHE_TOKEN
                
            backup_url = f"{APP_URL}/api/cache/backup"
            response = requests.post(backup_url, json=cache, headers=headers)
            if response.status_code == 200:
                print(f"Backed up cache to app persistent storage")
            else:
                print(f"Error backing up cache: {response.status_code}")
        except Exception as e:
            print(f"Error backing up cache: {e}")

def is_cache_valid(timestamp_str, hours=48):
    """Check if cached data is still valid"""
    if not timestamp_str:
        return False
        
    try:
        cached_time = datetime.fromisoformat(timestamp_str)
        expiration_time = datetime.now() - timedelta(hours=hours)
        return cached_time > expiration_time
    except Exception:
        return False

def should_refresh_cache(minutes=15):
    """Check if the cache needs to be refreshed based on last update time"""
    try:
        cache = load_cache()
            
        # If cache is empty, it needs to be refreshed
        if not cache:
            return True
            
        # Find the most recent timestamp in the cache
        latest_timestamp = None
        for player_data in cache.values():
            if "timestamp" in player_data:
                timestamp = datetime.fromisoformat(player_data["timestamp"])
                if latest_timestamp is None or timestamp > latest_timestamp:
                    latest_timestamp = timestamp
        
        # If no valid timestamp is found, cache needs refresh
        if latest_timestamp is None:
            return True
            
        # Check if the cache is older than the refresh interval
        refresh_time = datetime.now() - timedelta(minutes=minutes)
        return latest_timestamp < refresh_time
    except Exception as e:
        print(f"Error checking if cache needs refresh: {e}")
        return True 