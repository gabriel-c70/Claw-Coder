"""
Test the improved SSH target parsing functionality.
"""

import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workspace import parse_codespace_target

def test_ssh_parsing():
    """Test various SSH target formats."""
    
    test_cases = [
        # (input, expected_output)
        ("ssh user@hostname", "hostname"),  # Extracts hostname from user@hostname
        ("ssh user@hostname:port", "hostname"),  # Removes port
        ("ssh hostname", "hostname"),  # Simple hostname
        ("ssh hostname:port", "hostname"),  # Removes port
        ("user@hostname", "hostname"),  # Removes user
        ("user@hostname:port", "hostname"),  # Removes user and port
        ("hostname", "hostname"),  # Simple hostname
        ("hostname:port", "hostname"),  # Removes port
        ("192.168.1.1", "192.168.1.1"),  # IP address
        ("user@192.168.1.1", "192.168.1.1"),  # IP with user
        ("user@192.168.1.1:22", "192.168.1.1"),  # IP with user and port
        ("ssh://user@hostname", "hostname"),  # SSH URL format
        ("ssh://user@hostname:port", "hostname"),  # SSH URL with port
        ("https://github.com/codespaces/test-codespace?name=test", "cs.test"),  # Codespaces URL
        ("cs.my-codespace", "cs.my-codespace"),  # Codespaces hostname
        ("my-ssh-alias", "my-ssh-alias"),  # SSH config alias
        ("", None),  # Empty input
        ("invalid input with spaces", None),  # Invalid input with spaces
    ]
    
    print("Testing SSH target parsing...")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for input_val, expected in test_cases:
        result = parse_codespace_target(input_val)
        
        if result == expected:
            print(f"✓ PASS: '{input_val}' → '{result}'")
            passed += 1
        else:
            print(f"✗ FAIL: '{input_val}' → '{result}' (expected '{expected}')")
            failed += 1
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(test_ssh_parsing())