import time
import threading
import queue
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any, Callable
from dataclasses import dataclass
import requests
import re
from concurrent.futures import ThreadPoolExecutor, Future
from rate_limit_config import RateLimitConfig


@dataclass
class RateLimitInfo:
    """Data class to store rate limit information for a specific API endpoint"""
    limit: Optional[int] = None  # Maximum requests per cycle (RateLimit-Limit)
    remaining: Optional[int] = None  # Remaining requests (RateLimit-Remaining)
    reset_time: Optional[datetime] = None  # When rate limit resets (RateLimit-Reset)
    cache_control_ttl: Optional[int] = None  # TTL from Cache-Control header
    expires: Optional[datetime] = None  # Expiration time from Expires header
    last_request_time: Optional[datetime] = None  # When last request was made
    api_version: Optional[str] = None  # API version from Version header
    token: Optional[str] = None  # API token used for this rate limit info
    
    def is_rate_limited(self) -> bool:
        """Check if we're currently rate limited"""
        if self.remaining is not None and self.remaining <= 0:
            if self.reset_time and datetime.now() < self.reset_time:
                return True
        return False
    
    def seconds_until_reset(self) -> int:
        """Get seconds until rate limit resets"""
        if self.reset_time:
            delta = self.reset_time - datetime.now()
            return max(0, int(delta.total_seconds()))
        return 0
    
    def should_throttle(self, threshold: int = 10) -> bool:
        """Check if we should throttle requests based on remaining quota"""
        if self.remaining is not None and self.remaining <= threshold:
            return True
        return False


class TokenManager:
    """Manages API token rotation and rate limiting per token"""

    def __init__(self, tokens: list, cooldown_seconds: int = 60):
        """
        Initialize token manager.

        Args:
            tokens: List of API tokens
            cooldown_seconds: Seconds to wait before retrying exhausted token
        """
        self.tokens = tokens
        self.cooldown_seconds = cooldown_seconds
        self.current_token_index = 0
        self.token_rate_limits: Dict[str, RateLimitInfo] = {}
        self._lock = threading.RLock()

        # Initialize rate limit info for each token
        for token in tokens:
            self.token_rate_limits[token] = RateLimitInfo(token=token)

    def get_current_token(self) -> Optional[str]:
        """Get the currently active token"""
        if not self.tokens:
            return None

        with self._lock:
            return self.tokens[self.current_token_index] if self.current_token_index < len(self.tokens) else None

    def get_available_token(self) -> Optional[str]:
        """
        Get an available token that's not rate limited.
        Rotates to next token if current one is exhausted.
        """
        if not self.tokens:
            return None

        with self._lock:
            # Check if current token is available
            current_token = self.get_current_token()
            if current_token and self._is_token_available(current_token):
                return current_token

            # Try to find an available token
            for i in range(len(self.tokens)):
                token = self.tokens[i]
                if self._is_token_available(token):
                    self.current_token_index = i
                    return token

            # No tokens available, return current token anyway (will handle rate limiting)
            return current_token

    def _is_token_available(self, token: str) -> bool:
        """Check if a token is available (not rate limited)"""
        if token not in self.token_rate_limits:
            return True

        rate_limit_info = self.token_rate_limits[token]

        # If we don't have rate limit info yet, assume it's available
        if rate_limit_info.remaining is None:
            return True

        # Check if token is rate limited
        if rate_limit_info.is_rate_limited():
            return False

        # Check if token should be throttled
        if rate_limit_info.should_throttle(threshold=5):  # Conservative threshold for token rotation
            return False

        return True

    def update_token_rate_limit(self, token: str, rate_limit_info: RateLimitInfo) -> None:
        """Update rate limit information for a specific token"""
        if not token or token not in self.tokens:
            return

        with self._lock:
            # Update the token's rate limit info
            rate_limit_info.token = token
            self.token_rate_limits[token] = rate_limit_info

            # If current token is exhausted, try to rotate
            if token == self.get_current_token() and not self._is_token_available(token):
                self._rotate_to_next_available_token()

    def _rotate_to_next_available_token(self) -> None:
        """Rotate to the next available token"""
        if len(self.tokens) <= 1:
            return

        original_index = self.current_token_index

        # Try each token in sequence
        for i in range(1, len(self.tokens)):
            next_index = (self.current_token_index + i) % len(self.tokens)
            next_token = self.tokens[next_index]

            if self._is_token_available(next_token):
                self.current_token_index = next_index
                return

        # No available tokens found, keep current token
        # (rate limiting will handle the exhausted token)

    def get_token_status(self) -> Dict[str, Any]:
        """Get status of all tokens"""
        with self._lock:
            status = {}
            for token in self.tokens:
                # Mask token for security (show only first 8 and last 4 characters)
                masked_token = f"{token[:8]}...{token[-4:]}" if len(token) > 12 else "***"

                rate_limit_info = self.token_rate_limits.get(token, RateLimitInfo())
                status[masked_token] = {
                    'is_current': token == self.get_current_token(),
                    'is_available': self._is_token_available(token),
                    'limit': rate_limit_info.limit,
                    'remaining': rate_limit_info.remaining,
                    'reset_time': rate_limit_info.reset_time.isoformat() if rate_limit_info.reset_time else None,
                    'seconds_until_reset': rate_limit_info.seconds_until_reset(),
                    'is_rate_limited': rate_limit_info.is_rate_limited(),
                    'last_request_time': rate_limit_info.last_request_time.isoformat() if rate_limit_info.last_request_time else None
                }

            return {
                'tokens': status,
                'current_token_index': self.current_token_index,
                'total_tokens': len(self.tokens),
                'rotation_enabled': len(self.tokens) > 1
            }


@dataclass
class QueuedRequest:
    """Data class for queued requests"""
    url: str
    kwargs: Dict[str, Any]
    future: Future
    priority: int = 0  # Lower numbers = higher priority
    queued_at: datetime = None

    def __post_init__(self):
        if self.queued_at is None:
            self.queued_at = datetime.now()


class RateLimitManager:
    """
    Comprehensive rate limit manager that handles HTTP headers and implements
    intelligent request throttling for API interactions.
    """
    
    def __init__(self, default_delay: Optional[float] = None, throttle_threshold: Optional[int] = None,
                 max_queue_size: Optional[int] = None, queue_workers: Optional[int] = None,
                 config: Optional[RateLimitConfig] = None):
        """
        Initialize the rate limit manager.

        Args:
            default_delay: Default delay between requests when no rate limit info available
            throttle_threshold: Start throttling when remaining requests <= this value
            max_queue_size: Maximum number of requests to queue
            queue_workers: Number of worker threads for processing queued requests
            config: Configuration object to use (defaults to global config)
        """
        # Use provided config or default
        if config is None:
            from rate_limit_config import config as default_config
            config = default_config

        self.config = config
        self.default_delay = default_delay if default_delay is not None else config.DEFAULT_DELAY
        self.throttle_threshold = throttle_threshold if throttle_threshold is not None else config.THROTTLE_THRESHOLD
        self.max_queue_size = max_queue_size if max_queue_size is not None else config.MAX_QUEUE_SIZE
        self.request_timeout = config.REQUEST_TIMEOUT
        self.connect_timeout = config.CONNECT_TIMEOUT
        queue_workers = queue_workers if queue_workers is not None else config.QUEUE_WORKERS

        # Initialize token manager for Wynncraft API
        wynncraft_tokens = config.get_wynncraft_tokens()
        self.token_manager = TokenManager(wynncraft_tokens, config.TOKEN_ROTATION_COOLDOWN) if wynncraft_tokens else None
        self._rate_limits: Dict[str, RateLimitInfo] = {}
        self._lock = threading.RLock()  # Thread-safe access to rate limit data
        self._request_queue = queue.PriorityQueue(maxsize=max_queue_size)
        self._queue_executor = ThreadPoolExecutor(max_workers=queue_workers, thread_name_prefix="RateLimit")
        self._queue_running = True
        self._logger = logging.getLogger(__name__)

        # Setup logging if not already configured
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

        # Start queue processing workers
        for i in range(queue_workers):
            self._queue_executor.submit(self._process_queue)
    
    def _get_endpoint_key(self, url: str) -> str:
        """
        Extract a consistent endpoint key from URL for rate limit tracking.
        Groups similar endpoints together (e.g., different player IDs use same limits).
        """
        # Remove query parameters and normalize
        base_url = url.split('?')[0]
        
        # Group player-specific endpoints
        if 'api.wynncraft.com/v3/player/' in base_url:
            return 'wynncraft_player_api'
        elif 'api.wynncraft.com/v3/' in base_url:
            return 'wynncraft_api_v3'
        elif 'nori.fish' in base_url:
            return 'nori_fish_api'
        else:
            # For other APIs, use the domain
            try:
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                return f"{parsed.netloc}_api"
            except:
                return 'unknown_api'

    def _get_timeout_settings(self, url: str) -> Tuple[float, float]:
        """
        Get timeout settings for a specific endpoint.

        Args:
            url: The URL to get timeout settings for

        Returns:
            Tuple of (connect_timeout, request_timeout)
        """
        endpoint_key = self._get_endpoint_key(url)
        api_settings = self.config.get_api_settings(endpoint_key)

        connect_timeout = api_settings.get('connect_timeout', self.connect_timeout)
        request_timeout = api_settings.get('request_timeout', self.request_timeout)

        return connect_timeout, request_timeout

    def _get_auth_headers(self, url: str) -> Dict[str, str]:
        """
        Get authorization headers for a request.

        Args:
            url: The URL being requested

        Returns:
            Dictionary of headers to include in the request
        """
        headers = {}
        endpoint_key = self._get_endpoint_key(url)

        # Only add auth headers for Wynncraft API endpoints
        if endpoint_key in ['wynncraft_player_api', 'wynncraft_api_v3'] and self.token_manager:
            token = self.token_manager.get_available_token()
            if token:
                headers['Authorization'] = f'Bearer {token}'
                self._logger.debug(f"Using token {token[:8]}... for {endpoint_key}")

        return headers
    
    def parse_headers(self, response: requests.Response) -> RateLimitInfo:
        """
        Parse rate limit and cache headers from HTTP response.
        
        Args:
            response: HTTP response object
            
        Returns:
            RateLimitInfo object with parsed header data
        """
        headers = response.headers
        rate_limit_info = RateLimitInfo()
        
        try:
            # Parse RateLimit headers
            if 'RateLimit-Limit' in headers:
                rate_limit_info.limit = int(headers['RateLimit-Limit'])
            
            if 'RateLimit-Remaining' in headers:
                rate_limit_info.remaining = int(headers['RateLimit-Remaining'])
            
            if 'RateLimit-Reset' in headers:
                # RateLimit-Reset is typically seconds until reset
                reset_seconds = int(headers['RateLimit-Reset'])
                rate_limit_info.reset_time = datetime.now() + timedelta(seconds=reset_seconds)
            
            # Parse Cache-Control header for TTL
            if 'Cache-Control' in headers:
                cache_control = headers['Cache-Control']
                # Look for max-age directive
                max_age_match = re.search(r'max-age=(\d+)', cache_control)
                if max_age_match:
                    rate_limit_info.cache_control_ttl = int(max_age_match.group(1))
            
            # Parse Expires header
            if 'Expires' in headers:
                try:
                    # Parse HTTP date format
                    expires_str = headers['Expires']
                    rate_limit_info.expires = datetime.strptime(
                        expires_str, '%a, %d %b %Y %H:%M:%S %Z'
                    )
                except ValueError:
                    # Try alternative formats
                    try:
                        rate_limit_info.expires = datetime.strptime(
                            expires_str, '%a, %d %b %Y %H:%M:%S GMT'
                        )
                    except ValueError:
                        self._logger.warning(f"Could not parse Expires header: {expires_str}")
            
            # Parse API version
            if 'Version' in headers:
                rate_limit_info.api_version = headers['Version']
            
            # Set last request time
            rate_limit_info.last_request_time = datetime.now()
            
        except (ValueError, TypeError) as e:
            self._logger.warning(f"Error parsing rate limit headers: {e}")
        
        return rate_limit_info

    def _process_queue(self) -> None:
        """Worker method to process queued requests"""
        while self._queue_running:
            try:
                # Get next request from queue (blocks until available)
                priority, request_id, queued_request = self._request_queue.get(timeout=1.0)

                if not self._queue_running:
                    break

                try:
                    # Use the enhanced make_request method with retry logic
                    response = self.make_request(queued_request.url, **queued_request.kwargs)

                    # Set the result
                    queued_request.future.set_result(response)

                except Exception as e:
                    # Set the exception on the future
                    queued_request.future.set_exception(e)
                    self._logger.error(f"Error processing queued request: {e}")
                finally:
                    self._request_queue.task_done()

            except queue.Empty:
                continue  # Timeout waiting for queue item, check if still running
            except Exception as e:
                self._logger.error(f"Error in queue processing worker: {e}")

    def queue_request(self, url: str, priority: int = 0, **kwargs) -> Future:
        """
        Queue a request to be processed with rate limiting.

        Args:
            url: URL to request
            priority: Request priority (lower numbers = higher priority)
            **kwargs: Additional arguments to pass to requests.get()

        Returns:
            Future object that will contain the response

        Raises:
            queue.Full: If the request queue is full
        """
        future = Future()
        request_id = id(future)  # Use future's id as unique identifier
        queued_request = QueuedRequest(url=url, kwargs=kwargs, future=future, priority=priority)

        try:
            # Add to priority queue (priority, unique_id, request)
            self._request_queue.put((priority, request_id, queued_request), block=False)
            self._logger.debug(f"Queued request for {url} with priority {priority}")
            return future
        except queue.Full:
            future.set_exception(queue.Full("Request queue is full"))
            raise

    def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status information"""
        return {
            'queue_size': self._request_queue.qsize(),
            'max_queue_size': self.max_queue_size,
            'queue_full': self._request_queue.full(),
            'queue_empty': self._request_queue.empty()
        }

    def update_rate_limit_info(self, url: str, response: requests.Response, token: Optional[str] = None) -> None:
        """
        Update rate limit information for an endpoint based on response headers.

        Args:
            url: The URL that was requested
            response: HTTP response object
            token: The API token used for this request (if any)
        """
        endpoint_key = self._get_endpoint_key(url)
        new_info = self.parse_headers(response)
        new_info.token = token

        with self._lock:
            if endpoint_key in self._rate_limits:
                # Update existing info, preserving values that weren't in this response
                existing = self._rate_limits[endpoint_key]
                if new_info.limit is not None:
                    existing.limit = new_info.limit
                if new_info.remaining is not None:
                    existing.remaining = new_info.remaining
                if new_info.reset_time is not None:
                    existing.reset_time = new_info.reset_time
                if new_info.cache_control_ttl is not None:
                    existing.cache_control_ttl = new_info.cache_control_ttl
                if new_info.expires is not None:
                    existing.expires = new_info.expires
                if new_info.api_version is not None:
                    existing.api_version = new_info.api_version
                if new_info.token is not None:
                    existing.token = new_info.token
                existing.last_request_time = new_info.last_request_time
            else:
                # Store new rate limit info
                self._rate_limits[endpoint_key] = new_info

        # Update token manager if we have one and this is a Wynncraft API
        if (self.token_manager and token and
            endpoint_key in ['wynncraft_player_api', 'wynncraft_api_v3']):
            self.token_manager.update_token_rate_limit(token, new_info)

        # Log rate limit status
        self._log_rate_limit_status(endpoint_key, new_info)

    def _log_rate_limit_status(self, endpoint_key: str, info: RateLimitInfo) -> None:
        """Log current rate limit status for debugging"""
        if info.remaining is not None and info.limit is not None:
            percentage = (info.remaining / info.limit) * 100
            self._logger.info(
                f"Rate limit status for {endpoint_key}: "
                f"{info.remaining}/{info.limit} remaining ({percentage:.1f}%)"
            )

            if info.should_throttle(self.throttle_threshold):
                self._logger.warning(
                    f"Rate limit threshold reached for {endpoint_key}. "
                    f"Throttling enabled. Reset in {info.seconds_until_reset()}s"
                )

    def get_rate_limit_info(self, url: str) -> Optional[RateLimitInfo]:
        """
        Get current rate limit information for an endpoint.

        Args:
            url: The URL to check rate limits for

        Returns:
            RateLimitInfo object or None if no info available
        """
        endpoint_key = self._get_endpoint_key(url)
        with self._lock:
            return self._rate_limits.get(endpoint_key)

    def calculate_delay(self, url: str) -> float:
        """
        Calculate appropriate delay before making a request to avoid rate limits.

        Args:
            url: The URL that will be requested

        Returns:
            Delay in seconds
        """
        endpoint_key = self._get_endpoint_key(url)

        with self._lock:
            if endpoint_key not in self._rate_limits:
                return self.default_delay

            info = self._rate_limits[endpoint_key]

            # If we're rate limited, wait until reset
            if info.is_rate_limited():
                delay = info.seconds_until_reset()
                self._logger.warning(
                    f"Rate limited for {endpoint_key}. Waiting {delay}s until reset."
                )
                return delay

            # If we should throttle, calculate progressive delay
            if info.should_throttle(self.throttle_threshold):
                if info.remaining is not None and info.limit is not None:
                    # Calculate delay based on remaining quota
                    # More aggressive throttling as we approach the limit
                    remaining_ratio = info.remaining / info.limit
                    if remaining_ratio <= 0.1:  # Less than 10% remaining
                        return 2.0  # 2 second delay
                    elif remaining_ratio <= 0.2:  # Less than 20% remaining
                        return 1.0  # 1 second delay
                    else:
                        return 0.5  # 0.5 second delay

            return self.default_delay

    def is_cache_valid(self, url: str) -> bool:
        """
        Check if cached data for this endpoint is still valid based on cache headers.

        Args:
            url: The URL to check cache validity for

        Returns:
            True if cache is still valid, False otherwise
        """
        endpoint_key = self._get_endpoint_key(url)

        with self._lock:
            if endpoint_key not in self._rate_limits:
                return False

            info = self._rate_limits[endpoint_key]
            now = datetime.now()

            # Check Expires header
            if info.expires and now < info.expires:
                return True

            # Check Cache-Control max-age
            if info.cache_control_ttl and info.last_request_time:
                cache_expires = info.last_request_time + timedelta(seconds=info.cache_control_ttl)
                if now < cache_expires:
                    return True

            return False

    def make_request(self, url: str, max_retries: int = 3, **kwargs) -> requests.Response:
        """
        Make an HTTP request with intelligent rate limiting and retry logic.

        Args:
            url: URL to request
            max_retries: Maximum number of retries for non-rate-limit errors
            **kwargs: Additional arguments to pass to requests.get()

        Returns:
            HTTP response object

        Raises:
            requests.RequestException: If request fails after retries
        """
        last_exception = None

        # Get timeout settings for this endpoint
        connect_timeout, request_timeout = self._get_timeout_settings(url)
        timeout = (connect_timeout, request_timeout)

        # Get authorization headers
        auth_headers = self._get_auth_headers(url)
        current_token = None
        if 'Authorization' in auth_headers:
            # Extract token from Bearer header for tracking
            current_token = auth_headers['Authorization'].replace('Bearer ', '')

        # Merge auth headers with any provided headers
        if 'headers' in kwargs:
            kwargs['headers'].update(auth_headers)
        else:
            kwargs['headers'] = auth_headers

        for attempt in range(max_retries + 1):
            try:
                # Calculate and apply delay
                delay = self.calculate_delay(url)
                if delay > 0:
                    self._logger.info(f"Applying delay of {delay:.2f}s before request to {url}")
                    time.sleep(delay)

                # Make the request with timeout
                self._logger.debug(f"Making request to {url} with timeout {timeout}")
                if current_token:
                    self._logger.debug(f"Using token {current_token[:8]}... for request")

                response = requests.get(url, timeout=timeout, **kwargs)

                # Update rate limit information from response headers
                self.update_rate_limit_info(url, response, current_token)

                # Handle rate limiting with automatic retry
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 5))
                    self._logger.warning(
                        f"Rate limited (429) for {url}. Retrying after {retry_after}s"
                    )

                    # If we have token rotation, try to get a different token
                    if self.token_manager and current_token:
                        new_token = self.token_manager.get_available_token()
                        if new_token and new_token != current_token:
                            self._logger.info(f"Rotating from token {current_token[:8]}... to {new_token[:8]}...")
                            kwargs['headers']['Authorization'] = f'Bearer {new_token}'
                            current_token = new_token

                    time.sleep(retry_after)

                    # Update our rate limit info and try again
                    self.update_rate_limit_info(url, response, current_token)
                    response = requests.get(url, timeout=timeout, **kwargs)
                    self.update_rate_limit_info(url, response, current_token)

                return response

            except requests.Timeout as e:
                last_exception = e
                self._logger.error(
                    f"Request to {url} timed out after {request_timeout}s "
                    f"(attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                if attempt < max_retries:
                    # For timeouts, use a shorter backoff delay
                    backoff_delay = min(5.0, (2 ** attempt))  # Cap at 5 seconds for timeouts
                    self._logger.warning(f"Retrying in {backoff_delay:.1f}s")
                    time.sleep(backoff_delay)
                else:
                    self._logger.error(f"Request timed out after {max_retries + 1} attempts")
                    raise e
            except requests.RequestException as e:
                last_exception = e
                if attempt < max_retries:
                    # Exponential backoff for retries
                    backoff_delay = (2 ** attempt) + (attempt * 0.1)  # 1s, 2.1s, 4.2s, etc.
                    self._logger.warning(
                        f"Request failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {backoff_delay:.1f}s"
                    )
                    time.sleep(backoff_delay)
                else:
                    self._logger.error(f"Request failed after {max_retries + 1} attempts: {e}")
                    raise e

        # This should never be reached, but just in case
        if last_exception:
            raise last_exception
        else:
            raise requests.RequestException(f"Request to {url} failed after {max_retries + 1} attempts")

    def get_status_summary(self) -> Dict[str, Any]:
        """
        Get a summary of current rate limit status for all tracked endpoints.

        Returns:
            Dictionary with rate limit status for each endpoint
        """
        summary = {}

        with self._lock:
            for endpoint_key, info in self._rate_limits.items():
                # Mask token for security if present
                masked_token = None
                if info.token:
                    masked_token = f"{info.token[:8]}...{info.token[-4:]}" if len(info.token) > 12 else "***"

                summary[endpoint_key] = {
                    'limit': info.limit,
                    'remaining': info.remaining,
                    'reset_time': info.reset_time.isoformat() if info.reset_time else None,
                    'seconds_until_reset': info.seconds_until_reset(),
                    'is_rate_limited': info.is_rate_limited(),
                    'should_throttle': info.should_throttle(self.throttle_threshold),
                    'cache_control_ttl': info.cache_control_ttl,
                    'expires': info.expires.isoformat() if info.expires else None,
                    'api_version': info.api_version,
                    'last_request_time': info.last_request_time.isoformat() if info.last_request_time else None,
                    'cache_valid': self.is_cache_valid(endpoint_key),
                    'current_token': masked_token
                }

        # Add token manager status if available
        if self.token_manager:
            summary['token_status'] = self.token_manager.get_token_status()

        return summary

    def reset_rate_limit_info(self, url: Optional[str] = None) -> None:
        """
        Reset rate limit information for a specific endpoint or all endpoints.

        Args:
            url: URL to reset info for, or None to reset all
        """
        with self._lock:
            if url:
                endpoint_key = self._get_endpoint_key(url)
                if endpoint_key in self._rate_limits:
                    del self._rate_limits[endpoint_key]
                    self._logger.info(f"Reset rate limit info for {endpoint_key}")
            else:
                self._rate_limits.clear()
                self._logger.info("Reset all rate limit information")

    def shutdown(self) -> None:
        """Shutdown the rate limit manager and clean up resources"""
        self._queue_running = False

        # Wait for queue to be processed
        try:
            self._request_queue.join()
        except:
            pass

        # Shutdown the executor
        self._queue_executor.shutdown(wait=True)
        self._logger.info("Rate limit manager shutdown complete")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()


# Global instance for easy access
from rate_limit_config import config
rate_limit_manager = RateLimitManager(config=config)
