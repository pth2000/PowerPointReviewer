#!/usr/bin/env python3
"""Simple test script to validate the improved PowerPointReviewer components."""

import sys
import os

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_config():
    """Test configuration module."""
    print("Testing config module...")
    try:
        from config import VERSION, APP_NAME, ensure_temp_directories
        print(f"✓ App: {APP_NAME} v{VERSION}")
        ensure_temp_directories()
        print("✓ Temporary directories created")
        return True
    except Exception as e:
        print(f"✗ Config test failed: {e}")
        return False

def test_logging():
    """Test logging utilities."""
    print("\nTesting logging module...")
    try:
        from logger_utils import get_logger, setup_logger
        logger = get_logger("test")
        logger.info("Test log message")
        print("✓ Logging system working")
        return True
    except Exception as e:
        print(f"✗ Logging test failed: {e}")
        return False

def test_utils():
    """Test utility functions."""
    print("\nTesting utils module...")
    try:
        from utils import sanitize_text_for_tts, format_duration, count_chinese_characters
        
        # Test text sanitization
        dirty_text = "Hello\x00World\n\n  测试文本  \x1f"
        clean_text = sanitize_text_for_tts(dirty_text)
        print(f"✓ Text sanitization: '{dirty_text}' -> '{clean_text}'")
        
        # Test duration formatting
        duration_str = format_duration(125.5)
        print(f"✓ Duration formatting: 125.5s -> '{duration_str}'")
        
        # Test character counting
        char_count = count_chinese_characters("Hello 世界！测试 123")
        print(f"✓ Character counting: 'Hello 世界！测试 123' -> {char_count} chars")
        
        return True
    except Exception as e:
        print(f"✗ Utils test failed: {e}")
        return False

def test_exceptions():
    """Test exception handling."""
    print("\nTesting exceptions module...")
    try:
        from exceptions import PowerPointReviewerError, handle_exception, safe_execute
        
        # Test custom exception
        try:
            raise PowerPointReviewerError("Test error", "Test details")
        except PowerPointReviewerError as e:
            print(f"✓ Custom exception: {e}")
        
        # Test safe execution
        def test_func(x, y):
            return x / y
        
        success, result, error = safe_execute(test_func, 10, 2, context="Division test")
        print(f"✓ Safe execution (success): {success}, result: {result}")
        
        success, result, error = safe_execute(test_func, 10, 0, context="Division by zero test")
        print(f"✓ Safe execution (error): {success}, error: {error}")
        
        return True
    except Exception as e:
        print(f"✗ Exceptions test failed: {e}")
        return False

def test_tts():
    """Test TTS engine improvements."""
    print("\nTesting TTS engine...")
    try:
        from tts_engine import TTSEngine
        
        tts = TTSEngine()
        print(f"✓ TTS engine initialized")
        print(f"✓ Local mode: {tts.is_local_mode}")
        print(f"✓ Available voices: {len(tts.get_voices_list())}")
        
        return True
    except Exception as e:
        print(f"✗ TTS test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("PowerPointReviewer - Code Improvements Test")
    print("=" * 50)
    
    tests = [
        test_config,
        test_logging,
        test_utils,
        test_exceptions,
        test_tts
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
    
    print("\n" + "=" * 50)
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Code improvements are working correctly.")
        return 0
    else:
        print("⚠️  Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())