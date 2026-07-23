"""
Simple test to verify the Textual UI integration works.
"""

import sys
import os

def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")
    
    try:
        import textual
        print("✓ Textual is available")
    except ImportError:
        print("✗ Textual is not available (install with: pip install textual>=0.44.0)")
        return False
    
    try:
        from claw_textual_ui import ClawChatApp, CommandPalette, ModelSelector
        print("✓ claw_textual_ui module imports successfully")
    except ImportError as e:
        print(f"✗ Failed to import claw_textual_ui: {e}")
        return False
    
    try:
        from claw_ui import list_ollama_models, validate_ollama_model
        print("✓ claw_ui module imports successfully")
    except ImportError as e:
        print(f"✗ Failed to import claw_ui: {e}")
        return False
    
    return True

def test_basic_functionality():
    """Test basic functionality of the Textual UI components."""
    print("\nTesting basic functionality...")
    
    try:
        from claw_textual_ui import ClawChatApp, CommandPalette, ModelSelector
        
        # Test CommandPalette creation
        commands = [
            {"name": "/help", "description": "Show help"},
            {"name": "/models", "description": "List models"},
        ]
        palette = CommandPalette(commands)
        print("✓ CommandPalette can be created")
        
        # Test ModelSelector creation
        models = [
            {"name": "llama3.2:1b", "size": 1000000},
            {"name": "qwen2.5-coder:7b", "size": 7000000},
        ]
        selector = ModelSelector(models)
        print("✓ ModelSelector can be created")
        
        # Test app creation (without running it)
        # We can't actually test the app without a real agent, but we can test the class
        print("✓ ClawChatApp class is available")
        
        return True
    except Exception as e:
        print(f"✗ Error during functionality test: {e}")
        return False

def main():
    """Run all tests."""
    print("=" * 60)
    print("Claw Coder Textual UI Integration Test")
    print("=" * 60)
    
    if not test_imports():
        print("\n❌ Import tests failed")
        return 1
    
    if not test_basic_functionality():
        print("\n❌ Functionality tests failed")
        return 1
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    print("\nTo use the new Textual UI:")
    print("  1. Install Textual: pip install textual>=0.44.0")
    print("  2. Run: claw chat --ui textual")
    print("  3. Or use: claw --ui textual chat")
    print("\nKeyboard shortcuts in Textual UI:")
    print("  • Ctrl+P: Show command palette")
    print("  • Ctrl+M: Show model selector")
    print("  • Ctrl+R: Clear chat")
    print("  • ↑/↓: Scroll through chat")
    print("  • Ctrl+C: Quit")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())