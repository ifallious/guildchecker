import requests
import time
import json
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request, send_from_directory
import db

# Cache expiration time (in hours)
CACHE_EXPIRATION_HOURS = 48

# Cache refresh interval (in minutes)
CACHE_REFRESH_INTERVAL_MINUTES = 5

app = Flask(__name__, static_folder='public')

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
        
        # Save the player data directly to the database
        db.save_player_to_cache(username, guild, highest_level)
        
        # Also update the in-memory cache for this request
        if username in cache:
            cache[username] = {
                "guild": guild,
                "highest_level": highest_level,
                "timestamp": datetime.now().isoformat()
            }
        
        return username, guild, highest_level
    except Exception as e:
        print(f"Error fetching data for {username}: {e}")
        return username, None, 0

def check_player_guilds(max_workers=10, delay=0.2, min_level=0):
    """Check guilds for all online players"""
    # Load cached player data from database
    cache = db.get_all_players_from_cache()
    
    # Periodically clear expired cache entries
    db.clear_expired_cache()
    
    players = get_online_players()
    print(f"Found {len(players)} online players")
    
    # Identify players that need to be fetched (not in cache or cache expired)
    need_fetch = [p for p in players if p not in cache or not db.is_cache_valid(cache[p].get("timestamp"))]
    cached_players = [p for p in players if p in cache and db.is_cache_valid(cache[p].get("timestamp"))]
    
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
        max_players_to_process = min(125, len(need_fetch))
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
        
        # Get cache stats from database
        cache_size = db.get_cache_size()
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
        # Try to get players without a guild from the database
        try:
            # Get all players from database
            all_cached_players = db.get_all_players_from_cache()
            
            # Filter for players without a guild and meeting min level
            cached_no_guild_players = []
            for username, data in all_cached_players.items():
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
            
            # Get cache stats
            cache_size = db.get_cache_size()
            
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
        except Exception as inner_e:
            # If all else fails, return a basic error response
            return jsonify({
                "status": "error",
                "error_message": f"Error: {str(e)}. Additional error: {str(inner_e)}",
                "timestamp": datetime.now().isoformat()
            }), 500

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
