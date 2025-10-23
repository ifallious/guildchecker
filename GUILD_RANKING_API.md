# Guild Ranking API Endpoint

## Overview
The `/api/guild-ranking` endpoint returns an ordered list of guilds ranked by the number of online members, using the same system and data as the existing guild checker.

## Endpoint
```
GET /api/guild-ranking
```

## Query Parameters
- `min_level` (optional, integer, default: 0): Minimum level requirement for players to be counted in guild rankings

## Response Format

### Success Response (200)
```json
{
  "timestamp": "2024-01-01T12:00:00.000Z",
  "min_level": 0,
  "total_guilds": 25,
  "total_guild_members_online": 150,
  "guilds": [
    {
      "guild_name": "ExampleGuild",
      "online_members": 8,
      "members": [
        {
          "username": "Player1",
          "level": 105
        },
        {
          "username": "Player2", 
          "level": 98
        }
      ]
    }
  ],
  "status": "success",
  "cache_size": 1250,
  "checked_players": 200,
  "total_online_players": 250,
  "online_players_processed_percent": 80.0
}
```

### Error Response (500)
```json
{
  "timestamp": "2024-01-01T12:00:00.000Z",
  "min_level": 0,
  "total_guilds": 15,
  "total_guild_members_online": 100,
  "guilds": [...],
  "status": "error",
  "error_message": "API timeout",
  "cache_size": 1250,
  "is_partial_result": true,
  "total_online_players": 250
}
```

## Response Fields

### Main Response
- `timestamp`: ISO timestamp of the response
- `min_level`: Minimum level filter applied
- `total_guilds`: Number of guilds with online members
- `total_guild_members_online`: Total number of guild members online
- `guilds`: Array of guild objects, ordered by online member count (descending)
- `status`: "success" or "error"
- `cache_size`: Number of players in the cache
- `checked_players`: Number of players processed
- `total_online_players`: Total online players found
- `online_players_processed_percent`: Percentage of online players processed

### Guild Object
- `guild_name`: Name of the guild
- `online_members`: Number of online members in this guild
- `members`: Array of member objects, ordered by level (descending)

### Member Object
- `username`: Player's username
- `level`: Player's highest character level

## Features

1. **Same Data Source**: Uses the same player data and caching system as the existing guild checker
2. **Level Filtering**: Optional minimum level requirement for players
3. **Blacklist Support**: Automatically excludes blacklisted players
4. **Caching**: Leverages existing player cache for performance
5. **Error Handling**: Falls back to cached data if API calls fail
6. **Rate Limiting**: Uses the same rate limiting system as other endpoints

## Usage Examples

### Get all guilds ranked by online members
```
GET /api/guild-ranking
```

### Get guilds with only level 50+ players
```
GET /api/guild-ranking?min_level=50
```

### Get guilds with only level 100+ players
```
GET /api/guild-ranking?min_level=100
```

## Integration

This endpoint integrates seamlessly with the existing guild checker system:
- Uses the same `check_player_guilds()` function
- Respects the same blacklist and caching mechanisms
- Follows the same rate limiting and timeout handling
- Returns consistent error handling and fallback behavior
