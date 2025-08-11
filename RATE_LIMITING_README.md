# Rate Limiting System

This document describes the improved rate limiting system implemented for the Wynncraft Guild Checker API.

## Overview

The rate limiting system provides intelligent HTTP request management with the following features:

- **Header-based Rate Limiting**: Parses and respects HTTP headers like `RateLimit-Remaining`, `RateLimit-Reset`, `Cache-Control`, etc.
- **Proactive Throttling**: Prevents hitting rate limits by monitoring remaining quota
- **Request Queue System**: Queues requests when approaching limits
- **Automatic Retry Logic**: Handles rate limits and failures with exponential backoff
- **Cache Optimization**: Uses cache headers to avoid unnecessary requests
- **Comprehensive Logging**: Detailed monitoring and debugging information

## Supported Headers

The system handles the following HTTP headers:

- `RateLimit-Remaining`: Number of requests remaining before hitting the rate limit
- `RateLimit-Reset`: Number of seconds until the rate limit counter resets
- `RateLimit-Limit`: Maximum number of allowed requests per cycle (typically 120)
- `Cache-Control`: Contains the TTL (Time To Live) of the current route in seconds
- `Date`: Contains the timestamp of the current request
- `Expires`: If the route is cached, contains the expiration date when the cache will be purged
- `Version`: Current API version (currently v3.3)

## Usage

### Basic Usage

```python
from rate_limit_manager import rate_limit_manager

# Make a request with intelligent rate limiting
response = rate_limit_manager.make_request("https://api.wynncraft.com/v3/player/username")

# Check rate limit status
status = rate_limit_manager.get_status_summary()
print(status)
```

### Queue-based Requests

```python
from concurrent.futures import as_completed

# Queue multiple requests
futures = []
for username in usernames:
    url = f"https://api.wynncraft.com/v3/player/{username}"
    future = rate_limit_manager.queue_request(url, priority=0)
    futures.append(future)

# Process results as they complete
for future in as_completed(futures):
    response = future.result()
    # Process response...
```

### Configuration

The system can be configured via environment variables:

```bash
# Rate limiting settings
export RATE_LIMIT_DEFAULT_DELAY=0.2
export RATE_LIMIT_THROTTLE_THRESHOLD=10
export RATE_LIMIT_MAX_QUEUE_SIZE=1000
export RATE_LIMIT_QUEUE_WORKERS=5
export RATE_LIMIT_MAX_RETRIES=3

# API-specific settings
export WYNNCRAFT_DEFAULT_DELAY=0.2
export WYNNCRAFT_THROTTLE_THRESHOLD=10
export NORI_FISH_DEFAULT_DELAY=0.5
export NORI_FISH_THROTTLE_THRESHOLD=5

# Logging
export RATE_LIMIT_LOG_LEVEL=INFO
export RATE_LIMIT_DEBUG=false
```

## API Endpoints

### Rate Limit Status

Get current rate limit status:

```
GET /api/rate-limit-status
```

Response:
```json
{
  "status": "success",
  "timestamp": "2023-06-10T12:34:56.789012",
  "rate_limits": {
    "wynncraft_player_api": {
      "limit": 120,
      "remaining": 100,
      "reset_time": "2023-06-10T12:35:56.789012",
      "seconds_until_reset": 60,
      "is_rate_limited": false,
      "should_throttle": false,
      "cache_valid": true
    }
  },
  "queue_status": {
    "queue_size": 5,
    "max_queue_size": 1000,
    "queue_full": false,
    "queue_empty": false
  }
}
```

## Implementation Details

### Rate Limit Detection

The system proactively monitors rate limits:

1. **Remaining Quota**: When `RateLimit-Remaining` drops below the threshold (default: 10), throttling begins
2. **Progressive Delays**: Delays increase as remaining quota decreases:
   - < 10% remaining: 2 second delay
   - < 20% remaining: 1 second delay
   - < threshold: 0.5 second delay

### Retry Logic

- **Rate Limits (429)**: Waits for `RateLimit-Reset` seconds before retrying
- **Other Errors**: Exponential backoff (1s, 2.1s, 4.2s, etc.)
- **Maximum Retries**: Configurable (default: 3 attempts)

### Cache Optimization

The system respects cache headers to avoid unnecessary requests:

- **Cache-Control**: Uses `max-age` directive to determine cache validity
- **Expires**: Respects explicit expiration times
- **Conditional Requests**: Avoids requests when cached data is still valid

## Testing

Run the test suite:

```bash
python test_rate_limiting.py
```

This runs both unit tests and integration tests to verify the system works correctly.

## Files

- `rate_limit_manager.py`: Main rate limiting implementation
- `rate_limit_config.py`: Configuration management
- `test_rate_limiting.py`: Test suite
- `RATE_LIMITING_README.md`: This documentation

## Integration

The rate limiting system has been integrated into the existing API functions:

- `get_online_players()`: Uses rate limiting for player list requests
- `get_player_data_from_api()`: Uses rate limiting for individual player data
- `get_loot_data()`: Uses rate limiting for loot API requests

All existing functionality remains the same, but now benefits from intelligent rate limiting.
