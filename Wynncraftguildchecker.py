import requests
import time
import json
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context
import db
import concurrent.futures
from rate_limit_manager import rate_limit_manager
import signal
from functools import wraps

# Cache expiration time (in hours)
CACHE_EXPIRATION_HOURS = 48

# Cache refresh interval (in minutes)
CACHE_REFRESH_INTERVAL_MINUTES = 5

# Base URL for the loot API
LOOT_API_BASE_URL = "https://nori.fish"

app = Flask(__name__, static_folder='public')


class TimeoutError(Exception):
    """Custom timeout exception"""
    pass


def timeout_handler(func):
    """
    Decorator to add timeout handling to API functions.
    Prevents Vercel serverless function timeouts by failing gracefully.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.Timeout as e:
            print(f"Request timeout in {func.__name__}: {e}")
            # Return appropriate default values based on function
            if func.__name__ == 'get_online_players':
                return []
            elif func.__name__ == 'get_player_data_from_api':
                # Return username, None guild, 0 level
                return args[0] if args else 'unknown', None, 0
            elif func.__name__ == 'get_loot_data':
                return None
            else:
                return None
        except Exception as e:
            print(f"Error in {func.__name__}: {e}")
            # Return appropriate default values based on function
            if func.__name__ == 'get_online_players':
                return []
            elif func.__name__ == 'get_player_data_from_api':
                return args[0] if args else 'unknown', None, 0
            elif func.__name__ == 'get_loot_data':
                return None
            else:
                return None
    return wrapper

@timeout_handler
def get_online_players():
    """Get all online players from the Wynncraft API with timeout handling"""
    url = "https://api.wynncraft.com/v3/player?identifier=username&server="

    print(f"Fetching online players from {url}")
    response = rate_limit_manager.make_request(url)

    if response.status_code != 200:
        print(f"Failed to get online players: {response.status_code}")
        return []

    data = response.json()
    player_list = list(data["players"].keys())
    print(f"Successfully fetched {len(player_list)} online players")
    return player_list

@timeout_handler
def get_player_data_from_api(username, cache):
    """Get player data from the API and update cache with timeout handling"""
    url = f"https://api.wynncraft.com/v3/player/{username}?fullResult"

    print(f"Fetching data for player {username}")
    # Use the rate limit manager for intelligent request handling with timeout
    response = rate_limit_manager.make_request(url)

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

    print(f"Successfully processed {username}: Guild={guild}, Level={highest_level}")
    return username, guild, highest_level

def check_player_guilds(max_workers=10, delay=0.2, min_level=0):
    """Check guilds for all online players"""
    # Load cached player data from database
    cache = db.get_all_players_from_cache()

    # Periodically clear expired cache entries
    db.clear_expired_cache()

    players = get_online_players()
    # Exclude blacklisted players from consideration
    players = [p for p in players if not db.is_blacklisted(p)]

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
        max_players_to_process = min(2000, len(need_fetch))
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
    return results

def get_players_without_guild(results, min_level=0):
    """Get players without a guild, filtered by minimum level"""
    no_guild_high_level_players = []

    for player, data in results.items():
        # Skip blacklisted players entirely
        if db.is_blacklisted(player):
            continue
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

def get_guild_ranking(results, min_level=0):
    """Get guilds ranked by number of online members, filtered by minimum level"""
    guild_members = {}
    
    for player, data in results.items():
        # Skip blacklisted players entirely
        if db.is_blacklisted(player):
            continue
        guild = data["guild"]
        level = data["highest_level"]
        
        # Only count players that meet the minimum level requirement
        if guild and level >= min_level:
            if guild not in guild_members:
                guild_members[guild] = {
                    "guild_name": guild,
                    "online_members": 0,
                    "members": []
                }
            
            guild_members[guild]["online_members"] += 1
            guild_members[guild]["members"].append({
                "username": player,
                "level": level
            })
    
    # Convert to list and sort by number of online members (descending)
    guild_ranking = list(guild_members.values())
    guild_ranking.sort(key=lambda x: x["online_members"], reverse=True)
    
    # Sort members within each guild by level (descending)
    for guild in guild_ranking:
        guild["members"].sort(key=lambda x: x["level"], reverse=True)
    
    return guild_ranking

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

            # Filter for players without a guild and meeting min level, excluding blacklisted
            cached_no_guild_players = []
            for username, data in all_cached_players.items():
                if db.is_blacklisted(username):
                    continue
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

@app.route('/api/no-guild-players-stream', methods=['GET'])
def no_guild_players_stream_api():
    """Streaming API endpoint to get players without a guild, filtered by minimum level"""
    min_level = request.args.get('min_level', default=0, type=int)

    @stream_with_context
    def generate():
        try:
            # Send initial response header
            yield json.dumps({
                "type": "init",
                "timestamp": datetime.now().isoformat(),
                "min_level": min_level,
                "status": "processing"
            }) + '\n'

            # Get total number of online players first
            all_online_players = get_online_players()
            # Exclude blacklisted players from the stream as well
            all_online_players = [p for p in all_online_players if not db.is_blacklisted(p)]
            total_online_players = len(all_online_players)

            yield json.dumps({
                "type": "status",
                "message": f"Found {total_online_players} online players"
            }) + '\n'

            # Load cached player data from database
            cache = db.get_all_players_from_cache()

            # Periodically clear expired cache entries
            db.clear_expired_cache()

            # Parallelize the filtering of players using a thread pool
            with ThreadPoolExecutor(max_workers=20) as filter_executor:
                # Function to check if a player needs to be fetched or can use cache
                def classify_player(username):
                    if username not in cache or not db.is_cache_valid(cache[username].get("timestamp")):
                        return ("fetch", username)
                    else:
                        return ("cache", username)

                # Submit all player classifications to thread pool
                classification_futures = [filter_executor.submit(classify_player, player) for player in all_online_players]

                # Collect results
                need_fetch = []
                cached_players = []

                for future in concurrent.futures.as_completed(classification_futures):
                    try:
                        player_type, username = future.result()
                        if player_type == "fetch":
                            need_fetch.append(username)
                        else:
                            cached_players.append(username)
                    except Exception as e:
                        print(f"Error classifying player: {e}")

            yield json.dumps({
                "type": "status",
                "message": f"Need to fetch {len(need_fetch)} players, using {len(cached_players)} from cache",
                "cached_count": len(cached_players),
                "fetch_count": len(need_fetch)
            }) + '\n'

            processed_count = 0
            no_guild_players = []

            # Set up executor for API fetch operations
            fetch_executor = ThreadPoolExecutor(max_workers=20)
            # Set up a separate executor for cache processing
            cache_executor = ThreadPoolExecutor(max_workers=30)

            try:
                # Function to process a cached player
                def process_cached_player(username):
                    player_data = cache[username]
                    guild = player_data["guild"]
                    highest_level = player_data["highest_level"]

                    # Respect blacklist, skip entirely
                    if db.is_blacklisted(username):
                        return None
                    # Only return no-guild players that meet the level requirement
                    if guild is None and highest_level >= min_level:
                        return {
                            "username": username,
                            "level": highest_level,
                            "from_cache": True
                        }
                    return None

                # Start processing all cached players at once
                cache_futures = []
                for username in cached_players:
                    future = cache_executor.submit(process_cached_player, username)
                    cache_futures.append(future)

                # Start fetch operations (limited to max_players_to_process)
                fetch_futures = []
                if need_fetch:
                    max_players_to_process = min(2000, len(need_fetch))
                    need_fetch = need_fetch[:max_players_to_process]

                    # Submit all fetch requests to fetch_executor
                    for username in need_fetch:
                        future = fetch_executor.submit(get_player_data_from_api, username, cache)
                        fetch_futures.append(future)

                # For progress tracking
                total_tasks = len(cache_futures) + len(fetch_futures)
                completed_tasks = 0

                # Process cache results - these should complete very quickly
                for future in concurrent.futures.as_completed(cache_futures):
                    try:
                        result = future.result()
                        completed_tasks += 1
                        processed_count += 1

                        if result is not None:
                            no_guild_players.append(result)
                            yield json.dumps({
                                "type": "player",
                                "player": result,
                                "progress": {
                                    "processed": processed_count,
                                    "total": total_online_players,
                                    "percent": round((processed_count / total_online_players) * 100, 1) if total_online_players > 0 else 0
                                }
                            }) + '\n'

                        # Update progress occasionally
                        if completed_tasks % 10 == 0 or completed_tasks == total_tasks:
                            yield json.dumps({
                                "type": "progress",
                                "progress": {
                                    "processed": processed_count,
                                    "total": total_online_players,
                                    "percent": round((processed_count / total_online_players) * 100, 1) if total_online_players > 0 else 0
                                }
                            }) + '\n'
                    except Exception as e:
                        print(f"Error processing cached player: {e}")

                # Process fetch results - these will take longer due to API rate limits
                for future in concurrent.futures.as_completed(fetch_futures):
                    try:
                        username, guild, highest_level = future.result()
                        completed_tasks += 1
                        processed_count += 1

                        # Respect blacklist
                        if db.is_blacklisted(username):
                            continue
                        # Only send no-guild players that meet the level requirement
                        if guild is None and highest_level >= min_level:
                            player_info = {
                                "username": username,
                                "level": highest_level,
                                "from_cache": False
                            }
                            no_guild_players.append(player_info)

                            yield json.dumps({
                                "type": "player",
                                "player": player_info,
                                "progress": {
                                    "processed": processed_count,
                                    "total": total_online_players,
                                    "percent": round((processed_count / total_online_players) * 100, 1) if total_online_players > 0 else 0
                                }
                            }) + '\n'

                        # Update progress occasionally
                        if completed_tasks % 5 == 0 or completed_tasks == total_tasks:
                            yield json.dumps({
                                "type": "progress",
                                "progress": {
                                    "processed": processed_count,
                                    "total": total_online_players,
                                    "percent": round((processed_count / total_online_players) * 100, 1) if total_online_players > 0 else 0
                                }
                            }) + '\n'

                    except Exception as e:
                        print(f"Error processing fetch player: {e}")

                # Sort players by level
                no_guild_players.sort(key=lambda x: x["level"], reverse=True)

                # Send final summary
                cache_size = db.get_cache_size()
                yield json.dumps({
                    "type": "complete",
                    "total_players": len(no_guild_players),
                    "cache_size": cache_size,
                    "checked_players": processed_count,
                    "total_online_players": total_online_players,
                    "online_players_processed_percent": round(processed_count / total_online_players * 100 if total_online_players > 0 else 0, 1),
                    "timestamp": datetime.now().isoformat(),
                    "min_level": min_level
                }) + '\n'

            finally:
                # Ensure both executors are shut down properly
                cache_executor.shutdown(wait=False)
                fetch_executor.shutdown(wait=False)

        except Exception as e:
            # Send error message
            yield json.dumps({
                "type": "error",
                "error_message": str(e),
                "timestamp": datetime.now().isoformat()
            }) + '\n'

    return Response(generate(), mimetype='application/x-ndjson')

@app.route('/')
def home():
    """Serve the static HTML file"""
    return send_from_directory(app.static_folder, 'index.html')

@timeout_handler
def get_loot_data():
    """Get loot data from the API with timeout handling"""
    print("Fetching loot data from API")
    # First get the CSRF token using rate limit manager
    r = rate_limit_manager.make_request(f"{LOOT_API_BASE_URL}/api/tokens")
    cookies = r.cookies
    csrf_token = cookies.get('csrf_token')

    # Then get the loot data using rate limit manager
    r = rate_limit_manager.make_request(
        f"{LOOT_API_BASE_URL}/api/lootpool",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf_token}
    )

    if r.status_code != 200:
        print(f"Failed to get loot data: {r.status_code}")
        return None

    print("Successfully fetched loot data")
    return r.json()

@app.route('/api/rate-limit-status', methods=['GET'])
def rate_limit_status_api():
    """API endpoint to get current rate limit status"""
    try:
        status_summary = rate_limit_manager.get_status_summary()
        queue_status = rate_limit_manager.get_queue_status()

        # Add timeout configuration info
        timeout_config = {
            "wynncraft_player_api": rate_limit_manager._get_timeout_settings("https://api.wynncraft.com/v3/player/test"),
            "wynncraft_api_v3": rate_limit_manager._get_timeout_settings("https://api.wynncraft.com/v3/guild/test"),
            "nori_fish_api": rate_limit_manager._get_timeout_settings("https://nori.fish/api/test"),
            "default": (rate_limit_manager.connect_timeout, rate_limit_manager.request_timeout)
        }

        return jsonify({
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "rate_limits": status_summary,
            "queue_status": queue_status,
            "timeout_config": {
                api: {"connect_timeout": connect, "request_timeout": request}
                for api, (connect, request) in timeout_config.items()
            }
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error_message": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/region-mythic-prices', methods=['GET'])
def region_mythic_prices_api():
    """API endpoint to get average mythic prices per region"""
    try:
        # Get the loot data
        loot_data = get_loot_data()
        if not loot_data or 'Loot' not in loot_data:
            return jsonify({
                "status": "error",
                "error": "Failed to fetch loot data",
                "timestamp": datetime.now().isoformat()
            }), 500

        # Get stored mythic prices
        mythic_prices = db.get_mythic_items()

        # Calculate average prices per region
        region_prices = {}
        errors = []  # Track any errors during processing

        for region_name, region_data in loot_data['Loot'].items():
            if 'Mythic' in region_data and isinstance(region_data['Mythic'], list):
                total_price = 0
                counted_items = 0
                mythics_with_prices = []
                mythics_without_prices = []

                for mythic_name in region_data['Mythic']:
                    if mythic_name in mythic_prices:
                        total_price += mythic_prices[mythic_name]['price']
                        counted_items += 1
                        mythics_with_prices.append({
                            "name": mythic_name,
                            "price": mythic_prices[mythic_name]['price'],
                            "last_updated": mythic_prices[mythic_name]['timestamp']
                        })
                    else:
                        mythics_without_prices.append(mythic_name)
                        errors.append({
                            "type": "missing_price",
                            "region": region_name,
                            "mythic_name": mythic_name,
                            "message": f"No price data found for mythic item: {mythic_name}"
                        })

                region_prices[region_name] = {
                    "average_price": total_price // counted_items if counted_items > 0 else 0,
                    "mythics_counted": counted_items,
                    "total_mythics": len(region_data['Mythic']),
                    "mythics_with_prices": mythics_with_prices,
                    "mythics_without_prices": mythics_without_prices,
                    "coverage_percentage": round((counted_items / len(region_data['Mythic']) * 100), 2) if region_data['Mythic'] else 0
                }

        response = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "region_prices": region_prices,
            "total_regions": len(region_prices),
            "total_mythics_in_db": len(mythic_prices),
            "errors": errors,
            "error_count": len(errors)
        }

        # Add warning if there are regions without any price data
        regions_without_prices = [region for region, data in region_prices.items() if data["mythics_counted"] == 0]
        if regions_without_prices:
            response["warnings"] = [{
                "type": "no_price_data",
                "regions": regions_without_prices,
                "message": f"No price data available for any mythics in {len(regions_without_prices)} regions"
            }]

        return jsonify(response)

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/mythic-items', methods=['POST'])
def save_mythic_item_api():
    """API endpoint to save a mythic item's price"""
    try:
        data = request.get_json()
        if not data or 'mythic_name' not in data or 'price' not in data:
            return jsonify({
                "status": "error",
                "error": "Missing required fields: mythic_name and price",
                "timestamp": datetime.now().isoformat()
            }), 400

        mythic_name = data['mythic_name']
        price = int(data['price'])

        if price < 0:
            return jsonify({
                "status": "error",
                "error": "Price cannot be negative",
                "timestamp": datetime.now().isoformat()
            }), 400

        success = db.save_mythic_item(mythic_name, price)
        if not success:
            return jsonify({
                "status": "error",
                "error": "Failed to save mythic item",
                "timestamp": datetime.now().isoformat()
            }), 500

        return jsonify({
            "status": "success",
            "message": f"Successfully saved price for {mythic_name}",
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/guild-ranking', methods=['GET'])
def guild_ranking_api():
    """API endpoint to get guilds ranked by number of online members, filtered by minimum level"""
    # Get minimum level from query parameter, default to 0
    min_level = request.args.get('min_level', default=0, type=int)

    try:
        # Get total number of online players first
        all_online_players = get_online_players()
        total_online_players = len(all_online_players)

        # Use fewer workers and longer delay to avoid rate limiting
        results = check_player_guilds(max_workers=10, delay=0.2)

        # Get guild ranking filtered by minimum level
        guild_ranking = get_guild_ranking(results, min_level)

        # Get cache stats from database
        cache_size = db.get_cache_size()
        checked_players_count = len(results)

        # Calculate total guild members online
        total_guild_members = sum(guild["online_members"] for guild in guild_ranking)

        # Format response
        response = {
            "timestamp": datetime.now().isoformat(),
            "min_level": min_level,
            "total_guilds": len(guild_ranking),
            "total_guild_members_online": total_guild_members,
            "guilds": guild_ranking,
            "status": "success",
            "cache_size": cache_size,
            "checked_players": checked_players_count,
            "total_online_players": total_online_players,
            "online_players_processed_percent": round(checked_players_count / total_online_players * 100 if total_online_players > 0 else 0, 1)
        }

        return jsonify(response)
    except Exception as e:
        # Try to get guild ranking from the database
        try:
            # Get all players from database
            all_cached_players = db.get_all_players_from_cache()

            # Filter for players with guilds and meeting min level, excluding blacklisted
            cached_guild_members = {}
            for username, data in all_cached_players.items():
                if db.is_blacklisted(username):
                    continue
                guild = data.get("guild")
                level = data.get("highest_level", 0)
                
                if guild and level >= min_level:
                    if guild not in cached_guild_members:
                        cached_guild_members[guild] = {
                            "guild_name": guild,
                            "online_members": 0,
                            "members": []
                        }
                    
                    cached_guild_members[guild]["online_members"] += 1
                    cached_guild_members[guild]["members"].append({
                        "username": username,
                        "level": level
                    })

            # Convert to list and sort by number of online members (descending)
            cached_guild_ranking = list(cached_guild_members.values())
            cached_guild_ranking.sort(key=lambda x: x["online_members"], reverse=True)
            
            # Sort members within each guild by level (descending)
            for guild in cached_guild_ranking:
                guild["members"].sort(key=lambda x: x["level"], reverse=True)

            # Try to get the total number of online players
            try:
                all_online_players = get_online_players()
                total_online_players = len(all_online_players)
            except:
                total_online_players = 0

            # Get cache stats
            cache_size = db.get_cache_size()
            total_guild_members = sum(guild["online_members"] for guild in cached_guild_ranking)

            # Return a partial response with error information
            response = {
                "timestamp": datetime.now().isoformat(),
                "min_level": min_level,
                "total_guilds": len(cached_guild_ranking),
                "total_guild_members_online": total_guild_members,
                "guilds": cached_guild_ranking,
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

@app.route('/api/blacklist/add', methods=['GET'])
def add_to_blacklist_api():
    """Add a player to the blacklist via query params: player (required), reason (optional)."""
    try:
        player = request.args.get('player', type=str)
        reason = request.args.get('reason', default=None, type=str)

        if not player or not player.strip():
            return jsonify({
                "status": "error",
                "error": "Missing required query parameter: player",
                "timestamp": datetime.now().isoformat()
            }), 400

        success = db.add_to_blacklist(player, reason)
        if not success:
            return jsonify({
                "status": "error",
                "error": "Failed to add player to blacklist",
                "timestamp": datetime.now().isoformat()
            }), 500

        return jsonify({
            "status": "success",
            "message": f"Player '{player}' has been added to the blacklist",
            "player": player,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

if __name__ == "__main__":
    # For local development
    app.run(debug=True)
