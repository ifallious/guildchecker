"""
Basic tests for the rate limiting system.
Run with: python test_rate_limiting.py
"""

import time
import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import requests

from rate_limit_manager import RateLimitManager, RateLimitInfo, TokenManager


class TestRateLimitInfo(unittest.TestCase):
    """Test the RateLimitInfo data class"""
    
    def test_is_rate_limited(self):
        """Test rate limit detection"""
        info = RateLimitInfo()
        
        # Not rate limited when remaining > 0
        info.remaining = 5
        info.reset_time = datetime.now() + timedelta(seconds=60)
        self.assertFalse(info.is_rate_limited())
        
        # Rate limited when remaining <= 0 and reset time in future
        info.remaining = 0
        self.assertTrue(info.is_rate_limited())
        
        # Not rate limited when reset time has passed
        info.reset_time = datetime.now() - timedelta(seconds=10)
        self.assertFalse(info.is_rate_limited())
    
    def test_seconds_until_reset(self):
        """Test reset time calculation"""
        info = RateLimitInfo()
        
        # Test future reset time
        info.reset_time = datetime.now() + timedelta(seconds=30)
        seconds = info.seconds_until_reset()
        self.assertGreater(seconds, 25)
        self.assertLess(seconds, 35)
        
        # Test past reset time
        info.reset_time = datetime.now() - timedelta(seconds=10)
        self.assertEqual(info.seconds_until_reset(), 0)
    
    def test_should_throttle(self):
        """Test throttling threshold"""
        info = RateLimitInfo()
        
        # Should throttle when remaining <= threshold
        info.remaining = 5
        self.assertTrue(info.should_throttle(threshold=10))
        
        # Should not throttle when remaining > threshold
        info.remaining = 15
        self.assertFalse(info.should_throttle(threshold=10))


class TestRateLimitManager(unittest.TestCase):
    """Test the RateLimitManager class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.manager = RateLimitManager(default_delay=0.1, throttle_threshold=5)
    
    def tearDown(self):
        """Clean up after tests"""
        self.manager.shutdown()
    
    def test_get_endpoint_key(self):
        """Test endpoint key generation"""
        # Test Wynncraft player API
        url1 = "https://api.wynncraft.com/v3/player/testuser?fullResult"
        url2 = "https://api.wynncraft.com/v3/player/anotheruser?fullResult"
        self.assertEqual(
            self.manager._get_endpoint_key(url1),
            self.manager._get_endpoint_key(url2)
        )
        self.assertEqual(self.manager._get_endpoint_key(url1), 'wynncraft_player_api')
        
        # Test other Wynncraft API
        url3 = "https://api.wynncraft.com/v3/guild/someguild"
        self.assertEqual(self.manager._get_endpoint_key(url3), 'wynncraft_api_v3')
        
        # Test Nori Fish API
        url4 = "https://nori.fish/api/lootpool"
        self.assertEqual(self.manager._get_endpoint_key(url4), 'nori_fish_api')
    
    def test_parse_headers(self):
        """Test header parsing"""
        # Mock response with rate limit headers
        mock_response = Mock()
        mock_response.headers = {
            'RateLimit-Limit': '120',
            'RateLimit-Remaining': '100',
            'RateLimit-Reset': '60',
            'Cache-Control': 'max-age=300',
            'Version': 'v3.3'
        }
        
        info = self.manager.parse_headers(mock_response)
        
        self.assertEqual(info.limit, 120)
        self.assertEqual(info.remaining, 100)
        self.assertEqual(info.cache_control_ttl, 300)
        self.assertEqual(info.api_version, 'v3.3')
        self.assertIsNotNone(info.reset_time)
        self.assertIsNotNone(info.last_request_time)
    
    def test_calculate_delay(self):
        """Test delay calculation"""
        url = "https://api.wynncraft.com/v3/player/test"
        
        # Default delay when no rate limit info
        delay = self.manager.calculate_delay(url)
        self.assertEqual(delay, 0.1)  # default_delay
        
        # Test with rate limit info
        info = RateLimitInfo()
        info.remaining = 50
        info.limit = 120
        self.manager._rate_limits['wynncraft_player_api'] = info
        
        delay = self.manager.calculate_delay(url)
        self.assertEqual(delay, 0.1)  # Should use default when not throttling
        
        # Test throttling
        info.remaining = 3  # Below threshold of 5
        delay = self.manager.calculate_delay(url)
        self.assertGreater(delay, 0.1)  # Should be higher than default
    
    @patch('requests.get')
    def test_make_request_success(self, mock_get):
        """Test successful request"""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'RateLimit-Remaining': '100'}
        mock_get.return_value = mock_response
        
        url = "https://api.wynncraft.com/v3/player/test"
        response = self.manager.make_request(url)
        
        self.assertEqual(response.status_code, 200)
        mock_get.assert_called_once()
    
    @patch('requests.get')
    @patch('time.sleep')
    def test_make_request_rate_limited(self, mock_sleep, mock_get):
        """Test rate limited request with retry"""
        # First response: rate limited
        rate_limited_response = Mock()
        rate_limited_response.status_code = 429
        rate_limited_response.headers = {'Retry-After': '5'}

        # Second response: success
        success_response = Mock()
        success_response.status_code = 200
        success_response.headers = {'RateLimit-Remaining': '100'}

        mock_get.side_effect = [rate_limited_response, success_response]

        url = "https://api.wynncraft.com/v3/player/test"
        response = self.manager.make_request(url)

        self.assertEqual(response.status_code, 200)
        mock_sleep.assert_called_with(5)  # Should sleep for retry_after
        self.assertEqual(mock_get.call_count, 2)  # Should retry once

    @patch('requests.get')
    def test_make_request_timeout(self, mock_get):
        """Test request timeout handling"""
        # Mock timeout exception
        mock_get.side_effect = requests.Timeout("Request timed out")

        url = "https://api.wynncraft.com/v3/player/test"

        with self.assertRaises(requests.Timeout):
            self.manager.make_request(url, max_retries=1)

        # Should have tried twice (initial + 1 retry)
        self.assertEqual(mock_get.call_count, 2)

    def test_get_timeout_settings(self):
        """Test timeout settings for different endpoints"""
        # Test Wynncraft API
        wynncraft_url = "https://api.wynncraft.com/v3/player/test"
        connect_timeout, request_timeout = self.manager._get_timeout_settings(wynncraft_url)
        self.assertIsInstance(connect_timeout, float)
        self.assertIsInstance(request_timeout, float)
        self.assertGreater(request_timeout, connect_timeout)

        # Test Nori Fish API
        nori_url = "https://nori.fish/api/test"
        connect_timeout2, request_timeout2 = self.manager._get_timeout_settings(nori_url)
        self.assertIsInstance(connect_timeout2, float)
        self.assertIsInstance(request_timeout2, float)


class TestTokenManager(unittest.TestCase):
    """Test the TokenManager class"""

    def setUp(self):
        """Set up test fixtures"""
        self.tokens = ['token1', 'token2', 'token3']
        self.token_manager = TokenManager(self.tokens, cooldown_seconds=60)

    def test_get_current_token(self):
        """Test getting current token"""
        current = self.token_manager.get_current_token()
        self.assertEqual(current, 'token1')  # Should start with first token

    def test_get_available_token(self):
        """Test getting available token"""
        available = self.token_manager.get_available_token()
        self.assertIn(available, self.tokens)

    def test_token_rotation(self):
        """Test token rotation when current token is exhausted"""
        # Simulate exhausted token
        from rate_limit_manager import RateLimitInfo
        from datetime import datetime, timedelta

        exhausted_info = RateLimitInfo()
        exhausted_info.remaining = 0
        exhausted_info.reset_time = datetime.now() + timedelta(seconds=60)

        self.token_manager.update_token_rate_limit('token1', exhausted_info)

        # Should rotate to next available token
        available = self.token_manager.get_available_token()
        self.assertNotEqual(available, 'token1')
        self.assertIn(available, ['token2', 'token3'])

    def test_token_status(self):
        """Test token status reporting"""
        status = self.token_manager.get_token_status()

        self.assertIn('tokens', status)
        self.assertIn('current_token_index', status)
        self.assertIn('total_tokens', status)
        self.assertEqual(status['total_tokens'], 3)
        self.assertTrue(status['rotation_enabled'])

    def test_empty_token_list(self):
        """Test token manager with no tokens"""
        empty_manager = TokenManager([])
        self.assertIsNone(empty_manager.get_current_token())
        self.assertIsNone(empty_manager.get_available_token())


class TestRateLimitManagerWithTokens(unittest.TestCase):
    """Test RateLimitManager with token authentication"""

    def setUp(self):
        """Set up test fixtures"""
        # Mock config with tokens
        from rate_limit_config import RateLimitConfig

        # Create a mock config
        self.mock_config = RateLimitConfig()
        self.mock_config.WYNNCRAFT_API_TOKENS = 'token1,token2,token3'

        self.manager = RateLimitManager(config=self.mock_config)

    def tearDown(self):
        """Clean up after tests"""
        self.manager.shutdown()

    def test_auth_headers_generation(self):
        """Test authorization header generation"""
        wynncraft_url = "https://api.wynncraft.com/v3/player/test"
        headers = self.manager._get_auth_headers(wynncraft_url)

        if self.manager.token_manager:
            self.assertIn('Authorization', headers)
            self.assertTrue(headers['Authorization'].startswith('Bearer '))

        # Non-Wynncraft URL should not get auth headers
        other_url = "https://example.com/api/test"
        headers = self.manager._get_auth_headers(other_url)
        self.assertNotIn('Authorization', headers)

    @patch('requests.get')
    def test_make_request_with_token(self, mock_get):
        """Test making request with token authentication"""
        if not self.manager.token_manager:
            self.skipTest("No tokens configured")

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'RateLimit-Remaining': '100', 'RateLimit-Limit': '120'}
        mock_get.return_value = mock_response

        url = "https://api.wynncraft.com/v3/player/test"
        response = self.manager.make_request(url)

        self.assertEqual(response.status_code, 200)

        # Check that Authorization header was included
        call_args = mock_get.call_args
        headers = call_args[1].get('headers', {})
        self.assertIn('Authorization', headers)
        self.assertTrue(headers['Authorization'].startswith('Bearer '))

    def test_token_status_in_summary(self):
        """Test that token status is included in status summary"""
        if not self.manager.token_manager:
            self.skipTest("No tokens configured")

        summary = self.manager.get_status_summary()
        self.assertIn('token_status', summary)
    
    def test_get_status_summary(self):
        """Test status summary generation"""
        # Add some rate limit info
        info = RateLimitInfo()
        info.limit = 120
        info.remaining = 100
        info.last_request_time = datetime.now()
        self.manager._rate_limits['test_api'] = info
        
        summary = self.manager.get_status_summary()
        
        self.assertIn('test_api', summary)
        self.assertEqual(summary['test_api']['limit'], 120)
        self.assertEqual(summary['test_api']['remaining'], 100)
    
    def test_queue_status(self):
        """Test queue status reporting"""
        status = self.manager.get_queue_status()
        
        self.assertIn('queue_size', status)
        self.assertIn('max_queue_size', status)
        self.assertIn('queue_full', status)
        self.assertIn('queue_empty', status)


def run_integration_test():
    """Run a simple integration test with actual API calls"""
    print("Running integration test...")
    
    # Create a rate limit manager
    manager = RateLimitManager(default_delay=0.5)
    
    try:
        # Test with a simple API endpoint (httpbin for testing)
        test_url = "https://httpbin.org/delay/1"
        
        print(f"Making request to {test_url}")
        start_time = time.time()
        
        response = manager.make_request(test_url)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"Request completed in {duration:.2f}s")
        print(f"Status code: {response.status_code}")
        
        # Check rate limit status
        status = manager.get_status_summary()
        print(f"Rate limit status: {status}")
        
        print("Integration test completed successfully!")
        
    except Exception as e:
        print(f"Integration test failed: {e}")
    finally:
        manager.shutdown()


if __name__ == '__main__':
    print("Running rate limiting system tests...")
    
    # Run unit tests
    unittest.main(argv=[''], exit=False, verbosity=2)
    
    print("\n" + "="*50)
    
    # Run integration test
    run_integration_test()
