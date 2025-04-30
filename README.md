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

## Deployment to Vercel

### Prerequisites

- [GitHub](https://github.com/) account
- [Vercel](https://vercel.com/) account

### Step 1: Push your code to GitHub

1. Create a new GitHub repository
2. Initialize git in your local project folder:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/yourusername/wynncraft-guild-checker.git
   git push -u origin main
   ```

### Step 2: Deploy to Vercel

1. Go to [Vercel Dashboard](https://vercel.com/dashboard)
2. Click "New Project"
3. Import your GitHub repository
4. Configure the project:
   - Framework Preset: Other
   - Build Command: Leave empty
   - Output Directory: Leave empty
5. Click "Deploy"

Vercel will automatically detect the Python application and deploy it based on the `vercel.json` configuration.

### Step 3: Test Your API

Once deployed, you can access your API at the URL provided by Vercel:

- Home page: `https://your-project-name.vercel.app/`
- API endpoint: `https://your-project-name.vercel.app/api/no-guild-players?min_level=100`

## Using the Frontend

The project includes a simple HTML frontend that allows you to:

1. Enter a minimum level filter
2. Search for players without a guild that meet that level requirement
3. View the results in a nicely formatted table

To use the frontend, simply navigate to the root URL of your deployed application.

## API Usage Examples

### Using cURL

```bash
# Get players without a guild with level 100+
curl https://your-project-name.vercel.app/api/no-guild-players?min_level=100

# Manually refresh the cache
curl -X POST https://your-project-name.vercel.app/api/refresh-cache
```

### Using JavaScript

```javascript
// Get players without a guild
async function getNoGuildPlayers(minLevel = 0) {
  const response = await fetch(`https://your-project-name.vercel.app/api/no-guild-players?min_level=${minLevel}`);
  const data = await response.json();
  return data;
}

// Usage
getNoGuildPlayers(100).then(data => {
  console.log(`Found ${data.total_players} players without a guild at level ${data.min_level}+`);
  console.log(data.players);
});
```

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
- Be mindful of Wynncraft API rate limits
- The `/tmp` directory is used for caching on Vercel 