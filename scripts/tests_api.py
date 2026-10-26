import sys
import os
import json
import asyncio
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from state import state
from runner import (
    is_quota_error,
    is_api_key_invalid_error,
    is_auth_required_error,
    runner
)
from bot import validate_gemini_key

async def run_tests():
    print("========================================")
    print("🚀 RUNNING COMPREHENSIVE API SYSTEM TESTS")
    print("========================================")

    # 1. Error Classification Pattern Tests
    print("\n[TEST 1] Error Classification Patterns...")
    quota_samples = [
        "RESOURCE_EXHAUSTED",
        "429 Too Many Requests",
        "quota exceeded for quota metric",
        "exhausted your capacity on this model",
        "Daily quota reached"
    ]
    for s in quota_samples:
        assert is_quota_error(s), f"Failed to classify quota error: {s}"
    print("  ✅ Quota error pattern detection passed (5/5 samples)")

    invalid_key_samples = [
        "API key not valid. Please pass a valid API key.",
        "INVALID_ARGUMENT",
        "API_KEY_INVALID",
        "Error 400: API key is expired",
        "Invalid API Key"
    ]
    for s in invalid_key_samples:
        assert is_api_key_invalid_error(s), f"Failed to classify invalid key error: {s}"
    print("  ✅ Invalid API key pattern detection passed (5/5 samples)")

    auth_samples = [
        "Authentication required: please visit https://accounts.google.com/o/oauth2/auth",
        "You are not logged into Antigravity.",
        "Please log in to Antigravity"
    ]
    for s in auth_samples:
        assert is_auth_required_error(s), f"Failed to classify auth error: {s}"
    print("  ✅ Auth required pattern detection passed (3/3 samples)")

    # 2. Key Validation Endpoint Test
    print("\n[TEST 2] Gemini Key Validation Endpoint...")
    is_valid, reason = await validate_gemini_key("AIzaSyFakeKey1234567890ForTesting")
    assert not is_valid, "Fake key should be reported as invalid!"
    assert "Invalid" in reason, f"Unexpected reason for fake key: {reason}"
    print(f"  ✅ Validation correctly identified invalid key: {reason}")

    # 3. State Key Pool & Rotation Tests
    print("\n[TEST 3] State Key Pool & Rotation...")
    keys = ["AIzaSyKey1_Dummy", "AIzaSyKey2_Dummy", "AIzaSyKey3_Dummy"]
    state.set_api_keys(keys, sync_render=False)
    assert len(state.api_keys) == 3
    assert state.get_active_api_key() == "AIzaSyKey1_Dummy"
    assert state.active_key_index == 0

    rotated_1 = state.rotate_api_key()
    assert rotated_1 == "AIzaSyKey2_Dummy"
    assert state.active_key_index == 1

    rotated_2 = state.rotate_api_key()
    assert rotated_2 == "AIzaSyKey3_Dummy"
    assert state.active_key_index == 2

    rotated_3 = state.rotate_api_key()
    assert rotated_3 == "AIzaSyKey1_Dummy"
    assert state.active_key_index == 0
    print("  ✅ Pool rotation wrap-around correctly cycled Key 1 -> 2 -> 3 -> 1")

    # 4. Settings.json Configuration Mode Test
    print("\n[TEST 4] agy settings.json configuration...")
    config.configure_agy_settings(use_gemini_api=True)
    settings_p = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    assert settings_p.exists()
    with open(settings_p) as f:
        data = json.load(f)
    assert data.get("modelProvider") == "gemini", "modelProvider was not set to gemini!"
    print("  ✅ configure_agy_settings(use_gemini_api=True) correctly set modelProvider: gemini")

    config.configure_agy_settings(use_gemini_api=False)
    with open(settings_p) as f:
        data = json.load(f)
    assert "modelProvider" not in data, "modelProvider was not removed on False!"
    print("  ✅ configure_agy_settings(use_gemini_api=False) cleanly removed modelProvider for OAuth mode")

    # Clean up test keys
    state.clear_api_keys(sync_render=False)
    assert len(state.api_keys) == 0
    print("  ✅ clear_api_keys cleanly reset pool")

    print("\n========================================")
    print("🎉 ALL 5 TEST SUITES PASSED 100% SUCCESSFULLY!")
    print("========================================")

if __name__ == "__main__":
    asyncio.run(run_tests())
