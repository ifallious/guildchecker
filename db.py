import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re

# Load environment variables
load_dotenv()

# Get database connection info from environment variables
DB_URL = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_POSTGRES_URL')
DB_PASSWORD = os.environ.get('POSTGRES_PASSWORD') or os.environ.get('SUPABASE_POSTGRES_PASSWORD')

# Fallback for local development without env vars
if not DB_URL:
    print("Warning: No database URL found in environment, using local database")
    DB_URL = "postgres://postgres:postgres@localhost:5432/postgres"

if DB_PASSWORD and 'password=' not in DB_URL:
    # Add password to URL if it's provided separately (Vercel style)
    if '://' in DB_URL:
        prefix, rest = DB_URL.split('://')
        if '@' in rest:
            user_host = rest.split('@')
            if ':' in user_host[0]:
                user = user_host[0].split(':')[0]
            else:
                user = user_host[0]
            DB_URL = f"{prefix}://{user}:{DB_PASSWORD}@{user_host[1]}"

# Clean Supabase connection string - remove invalid parameters
if 'supa' in DB_URL:
    # Remove any query parameters that psycopg2 doesn't understand
    if '?' in DB_URL:
        base_url, query_params = DB_URL.split('?', 1)

        # Keep only standard PostgreSQL parameters
        valid_params = []
        for param in query_params.split('&'):
            # List of valid psycopg2 parameters
            if param.split('=')[0] in ['sslmode', 'connect_timeout', 'application_name']:
                valid_params.append(param)

        # Reconstruct the URL with only valid parameters
        if valid_params:
            DB_URL = f"{base_url}?{'&'.join(valid_params)}"
        else:
            DB_URL = base_url


# Blacklist support
BLACKLIST_TTL_SECONDS = 120  # small TTL to reduce DB hits
_blacklist_cache = set()
_blacklist_cache_fetched_at = None


def _normalize_identifier(identifier: str) -> str:
    """Normalize a player identifier (e.g., username) for consistent comparisons."""
    if not identifier:
        return ''
    try:
        return str(identifier).strip().lower()
    except Exception:
        return ''


def _load_blacklist_from_db() -> set:
    """Load the set of blacklisted player identifiers from the database table 'blacklist'."""
    try:
        conn = get_db_connection()
        if not conn:
            return set()
        blacklisted = set()
        with conn.cursor() as cur:
            cur.execute('''
                SELECT LOWER(identifier) AS identifier
                FROM blacklist
            ''')
            rows = cur.fetchall()
            for row in rows:
                ident = row.get('identifier')
                if ident:
                    blacklisted.add(ident)
        conn.close()
        return blacklisted
    except Exception as e:
        # If the table doesn't exist yet or any error occurs, fail softly
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        print(f"Error loading blacklist from database: {e}")
        return set()


def get_blacklisted_identifiers() -> set:
    """Get the set of blacklisted identifiers with a short-lived in-memory cache."""
    global _blacklist_cache, _blacklist_cache_fetched_at
    try:
        now = datetime.now()
        if _blacklist_cache_fetched_at and (now - _blacklist_cache_fetched_at).total_seconds() < BLACKLIST_TTL_SECONDS:
            return _blacklist_cache
        db_set = _load_blacklist_from_db()
        _blacklist_cache = db_set
        _blacklist_cache_fetched_at = now
        return _blacklist_cache
    except Exception as e:
        print(f"Error getting blacklisted identifiers: {e}")
        return set()


def is_blacklisted(identifier: str) -> bool:
    """Return True if the given identifier (e.g., username) is blacklisted in the database."""
    norm = _normalize_identifier(identifier)
    if not norm:
        return False
    return norm in get_blacklisted_identifiers()

def get_db_connection():
    """Establish a connection to the PostgreSQL database"""
    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def create_tables():
    """Create the necessary tables if they don't exist yet"""
    try:
        conn = get_db_connection()
        if not conn:
            return False

        with conn.cursor() as cur:
            # Player cache table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS player_cache (
                    username VARCHAR(64) PRIMARY KEY,
                    guild VARCHAR(64),
                    highest_level INTEGER,
                    timestamp TIMESTAMP
                )
            ''')

            # Metadata table to store app information
            cur.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    key VARCHAR(64) PRIMARY KEY,
                    value JSONB
                )
            ''')

            # Mythic items table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS mythic_items (
                    mythic_name VARCHAR(255) PRIMARY KEY,
                    price INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Blacklist table for player identifiers to exclude from results
            cur.execute('''
                CREATE TABLE IF NOT EXISTS blacklist (
                    identifier VARCHAR(64) PRIMARY KEY,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

        conn.close()
        return True
    except Exception as e:
        print(f"Error creating tables: {e}")
        return False

def save_player_to_cache(username, guild, highest_level):
    """Save a player's data to the cache"""
    try:
        conn = get_db_connection()
        if not conn:
            return False

        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO player_cache (username, guild, highest_level, timestamp)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (username)
                DO UPDATE SET
                    guild = %s,
                    highest_level = %s,
                    timestamp = %s
            ''', (
                username, guild, highest_level, datetime.now(),
                guild, highest_level, datetime.now()
            ))

        conn.close()
        return True
    except Exception as e:
        print(f"Error saving player to cache: {e}")
        return False

def get_player_from_cache(username):
    """Get a player's data from the cache"""
    try:
        conn = get_db_connection()
        if not conn:
            return None

        with conn.cursor() as cur:
            cur.execute('''
                SELECT username, guild, highest_level, timestamp
                FROM player_cache
                WHERE username = %s
            ''', (username,))

            player = cur.fetchone()

        conn.close()
        return dict(player) if player else None
    except Exception as e:
        print(f"Error getting player from cache: {e}")
        return None

def get_all_players_from_cache():
    """Get all players from the cache"""
    try:
        conn = get_db_connection()
        if not conn:
            return {}

        cache = {}
        with conn.cursor() as cur:
            cur.execute('''
                SELECT username, guild, highest_level, timestamp
                FROM player_cache
            ''')

            for row in cur.fetchall():
                cache[row['username']] = {
                    'guild': row['guild'],
                    'highest_level': row['highest_level'],
                    'timestamp': row['timestamp'].isoformat() if row['timestamp'] else None
                }

        conn.close()
        return cache
    except Exception as e:
        print(f"Error getting all players from cache: {e}")
        return {}

def is_cache_valid(timestamp):
    """Check if cached data is still valid"""
    if not timestamp:
        return False

    try:
        # Calculate expiration time (48 hours ago)
        expiration_time = datetime.now() - timedelta(hours=48)

        # Parse the timestamp
        if isinstance(timestamp, str):
            cache_time = datetime.fromisoformat(timestamp)
        else:
            cache_time = timestamp

        # Return True if cached data is newer than expiration time
        return cache_time > expiration_time
    except Exception as e:
        print(f"Error checking cache validity: {e}")
        return False

def get_cache_size():
    """Get the number of players in the cache"""
    try:
        conn = get_db_connection()
        if not conn:
            return 0

        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM player_cache')
            count = cur.fetchone()['count']

        conn.close()
        return count
    except Exception as e:
        print(f"Error getting cache size: {e}")
        return 0

def get_valid_cache_count():
    """Get the number of players with valid cache"""
    try:
        conn = get_db_connection()
        if not conn:
            return 0

        with conn.cursor() as cur:
            cur.execute('''
                SELECT COUNT(*)
                FROM player_cache
                WHERE timestamp > %s
            ''', (datetime.now() - timedelta(hours=48),))

            count = cur.fetchone()['count']

        conn.close()
        return count
    except Exception as e:
        print(f"Error getting valid cache count: {e}")
        return 0

def clear_expired_cache():
    """Remove expired entries from the cache"""
    try:
        conn = get_db_connection()
        if not conn:
            return False

        with conn.cursor() as cur:
            cur.execute('''
                DELETE FROM player_cache
                WHERE timestamp < %s
            ''', (datetime.now() - timedelta(hours=48),))

            deleted_count = cur.rowcount
            print(f"Cleared {deleted_count} expired cache entries")

        conn.close()
        return True
    except Exception as e:
        print(f"Error clearing expired cache: {e}")
        return False

def save_mythic_item(mythic_name, price):
    """Save or update a mythic item's price"""
    try:
        conn = get_db_connection()
        if not conn:
            return False

        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO mythic_items (mythic_name, price, timestamp)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (mythic_name)
                DO UPDATE SET
                    price = %s,
                    timestamp = CURRENT_TIMESTAMP
            ''', (mythic_name, price, price))

        conn.close()
        return True
    except Exception as e:
        print(f"Error saving mythic item: {e}")
        return False

def get_mythic_items():
    """Get all mythic items and their prices"""
    try:
        conn = get_db_connection()
        if not conn:
            return {}

        items = {}
        with conn.cursor() as cur:
            cur.execute('''
                SELECT mythic_name, price, timestamp
                FROM mythic_items
            ''')

            for row in cur.fetchall():
                items[row['mythic_name']] = {
                    'price': row['price'],
                    'timestamp': row['timestamp'].isoformat() if row['timestamp'] else None
                }

        conn.close()
        return items
    except Exception as e:
        print(f"Error getting mythic items: {e}")
        return {}

# Initialize the database tables on module import
create_tables()