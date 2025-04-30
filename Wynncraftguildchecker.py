import requests
import time
import json
import os
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request, send_from_directory

# Cache file path - use /tmp directory for Vercel serverless functions
# Vercel has a writable /tmp directory that can be used for temporary storage
is_vercel = os.environ.get('VERCEL', False)
if is_vercel:
    CACHE_FILE = '/tmp/wynncraft_player_cache.json'
else:
    CACHE_FILE = 'wynncraft_player_cache.json'

# Cache expiration time (in hours)
CACHE_EXPIRATION_HOURS = 48

# Cache refresh interval (in minutes)
CACHE_REFRESH_INTERVAL_MINUTES = 5

# Global flag to track if a background refresh is in progress
is_refreshing = False

app = Flask(__name__, static_folder='public')

def load_cache():
    """Load cached player data from file"""
    if not os.path.exists(CACHE_FILE):
        return {}
        
    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            print(f"Loaded cache with {len(cache)} players")
            return cache
    except Exception as e:
        print(f"Error loading cache: {e}")
        return {}

def save_cache(cache):
    """Save player data to cache file"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
        print(f"Saved {len(cache)} players to cache")
    except Exception as e:
        print(f"Error saving cache: {e}")

def is_cache_valid(timestamp_str):
    """Check if cached data is still valid"""
    if not timestamp_str:
        return False
        
    try:
        # Parse the cached timestamp
        cached_time = datetime.fromisoformat(timestamp_str)
        # Calculate expiration time
        expiration_time = datetime.now() - timedelta(hours=CACHE_EXPIRATION_HOURS)
        # Return True if cached data is newer than expiration time
        return cached_time > expiration_time
    except Exception:
        return False

def should_refresh_cache():
    """Check if the cache needs to be refreshed based on last update time"""
    try:
        if not os.path.exists(CACHE_FILE):
            return True
            
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            
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
        refresh_time = datetime.now() - timedelta(minutes=CACHE_REFRESH_INTERVAL_MINUTES)
        return latest_timestamp < refresh_time
    except Exception as e:
        print(f"Error checking if cache needs refresh: {e}")
        return True

def get_online_players():
    """Get all online players from the Wynncraft API"""
    url = "https://api.wynncraft.com/v3/player?identifier=username&server="
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Failed to get online players: {response.status_code}")
        return []
    
    data = response.json()
    return list(data["players"].keys())

def get_player_data_from_api(username, cache):
    """Get player data from the API and update cache"""
    url = f"https://api.wynncraft.com/v3/player/{username}?fullResult"
    
    try:
        response = requests.get(url)
        
        # Handle rate limiting (typically 429 status code)
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 5))
            print(f"Rate limited! Waiting for {retry_after} seconds before retrying...")
            time.sleep(retry_after)
            # Try again after waiting
            return get_player_data_from_api(username, cache)
        
        if response.status_code != 200:
            print(f"Failed to get data for {username}: {response.status_code}")
            return username, None, 0
        
        data = response.json()
        # Check if data is valid and has the expected structure
        if not data:
            print(f"Warning: Empty data for {username}")
            return username, None, 0
        
        # Get guild information
        guild = None
        if 'guild' in data and data['guild'] and isinstance(data['guild'], dict):
            guild = data['guild'].get('name')
            print(f"Found guild for {username}: {guild}")
        
        # Get highest character level
        highest_level = 0
        if 'characters' in data and data['characters']:
            for char_id, char_data in data['characters'].items():
                if 'level' in char_data:
                    highest_level = max(highest_level, char_data.get('level', 0))
        
        # Update cache with new data
        cache[username] = {
            "guild": guild,
            "highest_level": highest_level,
            "timestamp": datetime.now().isoformat()
        }
        
        # Save cache immediately after updating
        save_cache(cache)
        
        return username, guild, highest_level
    except Exception as e:
        print(f"Error fetching data for {username}: {e}")
        return username, None, 0

def background_refresh_cache():
    """Refresh the cache in the background"""
    global is_refreshing
    
    if is_refreshing:
        print("A refresh is already in progress, skipping this one")
        return
    
    try:
        is_refreshing = True
        print("Starting background cache refresh")
        
        # Load cached player data
        cache = load_cache()
        
        # Get list of online players
        all_online_players = get_online_players()
        
        # Identify players not in cache or with expired cache
        need_refresh = [p for p in all_online_players if p not in cache or not is_cache_valid(cache[p].get("timestamp"))]
        
        # Process up to 20 players in the background
        players_to_process = need_refresh[:150]
        total_to_process = len(players_to_process)
        
        print(f"Background refresh: Found {len(all_online_players)} online players, processing {total_to_process}")
        
        # Use thread pool to process players concurrently with a smaller max_workers
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all tasks at once
            future_to_username = {executor.submit(get_player_data_from_api, username, cache): username for username in players_to_process}
            
            # Process results as they complete
            processed = 0
            for future in future_to_username:
                try:
                    future.result()  # We don't need the result, just wait for completion
                    processed += 1
                    print(f"Background refresh progress: {processed}/{total_to_process} completed")
                except Exception as e:
                    print(f"Error in background refresh for a player: {e}")
    except Exception as e:
        print(f"Error in background refresh: {e}")
    finally:
        is_refreshing = False
        print("Background cache refresh completed")

def check_player_guilds(max_workers=10, delay=0.2, min_level=0):
    """Check guilds for all online players"""
    # Load cached player data
    cache = load_cache()
    
    # Check if we should start a background refresh
    if should_refresh_cache() and not is_refreshing:
        # Start a background thread to refresh the cache
        refresh_thread = threading.Thread(target=background_refresh_cache)
        refresh_thread.daemon = True
        refresh_thread.start()
        print("Started background refresh thread")
    
    players = get_online_players()
    print(f"Found {len(players)} online players")
    
    # Identify players that need to be fetched (not in cache or cache expired)
    need_fetch = [p for p in players if p not in cache or not is_cache_valid(cache[p].get("timestamp"))]
    cached_players = [p for p in players if p in cache and is_cache_valid(cache[p].get("timestamp"))]
    
    print(f"Need to fetch {len(need_fetch)} players, using {len(cached_players)} from cache")
    
    results = {}
    
    # First, add all cached players to results
    for username in cached_players:
        player_data = cache[username]
        results[username] = {
            "guild": player_data["guild"],
            "highest_level": player_data["highest_level"]
        }
        print(f"Using cached data for {username}: Guild: {player_data['guild'] if player_data['guild'] else 'None'}, Level: {player_data['highest_level']}")
    
    # Then, process players that need to be fetched (up to max_players_to_process)
    if need_fetch:
        max_players_to_process = min(175, len(need_fetch))
        need_fetch = need_fetch[:max_players_to_process]
        
        total_to_process = len(need_fetch)
        print(f"Processing {total_to_process} players for this request using {max_workers} workers")
        
        # Use thread pool to process players concurrently
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks at once
            future_to_username = {executor.submit(get_player_data_from_api, username, cache): username for username in need_fetch}
            
            # Process results as they complete
            processed = 0
            for future in future_to_username:
                try:
                    username, guild, highest_level = future.result()
                    processed += 1
                    
                    results[username] = {
                        "guild": guild,
                        "highest_level": highest_level
                    }
                    
                    print(f"Progress: {processed}/{total_to_process} - {username}: Guild: {guild if guild else 'None'}, Level: {highest_level}")
                except Exception as e:
                    print(f"Error processing player: {e}")
                time.sleep(delay)
    
    return results

def get_players_without_guild(results, min_level=0):
    """Get players without a guild, filtered by minimum level"""
    no_guild_high_level_players = []
    
    for player, data in results.items():
        guild = data["guild"]
        level = data["highest_level"]
        
        if not guild and level >= min_level:
            no_guild_high_level_players.append({
                "username": player,
                "level": level
            })
    
    # Sort by level in descending order
    no_guild_high_level_players.sort(key=lambda x: x["level"], reverse=True)
    return no_guild_high_level_players

def save_results(results, filename='guild_results.json'):
    """Save results to a JSON file"""
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {filename}")

@app.route('/api/no-guild-players', methods=['GET'])
def no_guild_players_api():
    """API endpoint to get players without a guild, filtered by minimum level"""
    # Get minimum level from query parameter, default to 0
    min_level = request.args.get('min_level', default=0, type=int)
    
    try:
        # Get total number of online players first
        all_online_players = get_online_players()
        total_online_players = len(all_online_players)
        
        # Use fewer workers and longer delay to avoid rate limiting
        results = check_player_guilds(max_workers=10, delay=0.2)
        
        # Get players without a guild filtered by minimum level
        no_guild_players = get_players_without_guild(results, min_level)
        
        # Get cache stats
        cache = load_cache()
        cache_size = len(cache) if cache else 0
        checked_players_count = len(results)
        
        # Format response
        response = {
            "timestamp": datetime.now().isoformat(),
            "min_level": min_level,
            "total_players": len(no_guild_players),
            "players": no_guild_players,
            "status": "success",
            "cache_size": cache_size,
            "checked_players": checked_players_count,
            "total_online_players": total_online_players,
            "online_players_processed_percent": round(checked_players_count / total_online_players * 100 if total_online_players > 0 else 0, 1)
        }
        
        return jsonify(response)
    except Exception as e:
        # Get cache stats even in case of an error
        cache = load_cache()
        cache_size = len(cache) if cache else 0
        
        # Try to get any players we might have in cache
        cached_no_guild_players = []
        for username, data in cache.items():
            if data.get("guild") is None and data.get("highest_level", 0) >= min_level:
                cached_no_guild_players.append({
                    "username": username,
                    "level": data.get("highest_level", 0)
                })
        
        # Sort by level
        cached_no_guild_players.sort(key=lambda x: x["level"], reverse=True)
        
        # Try to get the total number of online players
        try:
            all_online_players = get_online_players()
            total_online_players = len(all_online_players)
        except:
            total_online_players = 0
        
        # Return a partial response with error information
        response = {
            "timestamp": datetime.now().isoformat(),
            "min_level": min_level,
            "total_players": len(cached_no_guild_players),
            "players": cached_no_guild_players,
            "status": "error",
            "error_message": str(e),
            "cache_size": cache_size,
            "is_partial_result": True,
            "total_online_players": total_online_players
        }
        
        return jsonify(response), 500

@app.route('/api/refresh-cache', methods=['POST'])
def refresh_cache_api():
    """API endpoint to manually refresh the cache"""
    # Use fewer workers and longer delay to avoid rate limiting
    results = check_player_guilds(max_workers=10, delay=0.2)
    
    return jsonify({
        "status": "success",
        "message": f"Cache refreshed with {len(results)} players",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Serve the static HTML file"""
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == "__main__":
    # For local development
    app.run(debug=True)
