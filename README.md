# Wynncraft Guild Checker API

This API provides information about Wynncraft players without a guild, allowing you to filter by minimum level.

## Features

- Get a list of online players without a guild
- Filter by minimum player level
- Data caching to reduce API calls to Wynncraft
- Easy deployment to Vercel
- Simple HTML frontend included

## API Endpoints

- `GET /api/no-guild-players` - Get players without a guild
  - Query parameter: `min_level` (optional) - Minimum player level (default: 0)
  - Example: `/api/no-guild-players?min_level=100`

- `POST /api/refresh-cache` - Manually refresh the player cache

## Using the Frontend

The project includes a simple HTML frontend that allows you to:

1. Enter a minimum level filter
2. Search for players without a guild that meet that level requirement
3. View the results in a nicely formatted table

To use the frontend, simply navigate to the root URL of the deployed application.

## Response Format

The API returns data in the following JSON format:

```json
{
  "timestamp": "2023-06-10T12:34:56.789012",
  "min_level": 100,
  "total_players": 5,
  "players": [
    {
      "username": "Player1",
      "level": 105
    },
    {
      "username": "Player2",
      "level": 103
    },
    ...
  ]
}
```

## Local Development

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Run the Flask application:
   ```
   python Wynncraftguildchecker.py
   ```

3. Access the API at `http://localhost:5000/`

## Notes

- The API caches player data for 48 hours to reduce load on the Wynncraft API
