import requests
import time
import json
import os
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
            retry_after = int(response.headers.get('Retry-After', 60))
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
        
        return username, guild, highest_level
    except Exception as e:
        print(f"Error fetching data for {username}: {e}")
        return username, None, 0

def check_player_guilds(max_workers=5, delay=0.2, min_level=0):
    """Check guilds for all online players"""
    # Load cached player data
    cache = load_cache()
    
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
    
    # Then, process players that need to be fetched
    if need_fetch:
        processed = 0
        
        # Process in batches with multiple threads
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(get_player_data_from_api, username, cache) for username in need_fetch]
            
            for future in futures:
                username, guild, highest_level = future.result()
                processed += 1
                
                results[username] = {
                    "guild": guild,
                    "highest_level": highest_level
                }
                
                print(f"Progress: {processed}/{len(need_fetch)} - {username}: Guild: {guild if guild else 'None'}, Level: {highest_level}")
                
                # Avoid rate limiting
                time.sleep(delay)
    
    # Save updated cache
    save_cache(cache)
    
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
    
    # Use fewer workers and longer delay to avoid rate limiting
    results = check_player_guilds(max_workers=10, delay=0.2)
    
    # Get players without a guild filtered by minimum level
    no_guild_players = get_players_without_guild(results, min_level)
    
    # Format response
    response = {
        "timestamp": datetime.now().isoformat(),
        "min_level": min_level,
        "total_players": len(no_guild_players),
        "players": no_guild_players
    }
    
    return jsonify(response)

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
