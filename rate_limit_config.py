"""
Configuration settings for the rate limiting system.
"""

import os
from typing import Dict, Any


class RateLimitConfig:
    """Configuration class for rate limiting settings"""
    
    # Default rate limiting settings
    DEFAULT_DELAY = float(os.getenv('RATE_LIMIT_DEFAULT_DELAY', '0.2'))
    THROTTLE_THRESHOLD = int(os.getenv('RATE_LIMIT_THROTTLE_THRESHOLD', '10'))
    MAX_QUEUE_SIZE = int(os.getenv('RATE_LIMIT_MAX_QUEUE_SIZE', '1000'))
    QUEUE_WORKERS = int(os.getenv('RATE_LIMIT_QUEUE_WORKERS', '5'))
    MAX_RETRIES = int(os.getenv('RATE_LIMIT_MAX_RETRIES', '3'))
    
    # Logging settings
    LOG_LEVEL = os.getenv('RATE_LIMIT_LOG_LEVEL', 'INFO')
    ENABLE_DEBUG_LOGGING = os.getenv('RATE_LIMIT_DEBUG', 'false').lower() == 'true'
    
    # API-specific settings
    WYNNCRAFT_API_SETTINGS = {
        'default_delay': float(os.getenv('WYNNCRAFT_DEFAULT_DELAY', '0.2')),
        'throttle_threshold': int(os.getenv('WYNNCRAFT_THROTTLE_THRESHOLD', '10')),
        'max_retries': int(os.getenv('WYNNCRAFT_MAX_RETRIES', '3')),
    }
    
    NORI_FISH_API_SETTINGS = {
        'default_delay': float(os.getenv('NORI_FISH_DEFAULT_DELAY', '0.5')),
        'throttle_threshold': int(os.getenv('NORI_FISH_THROTTLE_THRESHOLD', '5')),
        'max_retries': int(os.getenv('NORI_FISH_MAX_RETRIES', '2')),
    }
    
    @classmethod
    def get_api_settings(cls, api_name: str) -> Dict[str, Any]:
        """Get settings for a specific API"""
        api_settings = {
            'wynncraft_player_api': cls.WYNNCRAFT_API_SETTINGS,
            'wynncraft_api_v3': cls.WYNNCRAFT_API_SETTINGS,
            'nori_fish_api': cls.NORI_FISH_API_SETTINGS,
        }
        
        return api_settings.get(api_name, {
            'default_delay': cls.DEFAULT_DELAY,
            'throttle_threshold': cls.THROTTLE_THRESHOLD,
            'max_retries': cls.MAX_RETRIES,
        })
    
    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return {
            'default_delay': cls.DEFAULT_DELAY,
            'throttle_threshold': cls.THROTTLE_THRESHOLD,
            'max_queue_size': cls.MAX_QUEUE_SIZE,
            'queue_workers': cls.QUEUE_WORKERS,
            'max_retries': cls.MAX_RETRIES,
            'log_level': cls.LOG_LEVEL,
            'enable_debug_logging': cls.ENABLE_DEBUG_LOGGING,
            'wynncraft_api_settings': cls.WYNNCRAFT_API_SETTINGS,
            'nori_fish_api_settings': cls.NORI_FISH_API_SETTINGS,
        }


# Environment-based configuration loading
def load_config_from_env() -> RateLimitConfig:
    """Load configuration from environment variables"""
    return RateLimitConfig()


# Default configuration instance
config = RateLimitConfig()
