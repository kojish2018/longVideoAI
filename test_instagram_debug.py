"""Debug script to test Instagram API credentials."""
from dotenv import load_dotenv
import os
import requests

load_dotenv()

API_BASE = "https://graph.facebook.com/v21.0"

app_id = os.getenv("INSTAGRAM_APP_ID", "")
app_secret = os.getenv("INSTAGRAM_APP_SECRET", "")
access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
business_account_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

print("=== Testing Instagram API Credentials ===\n")

# Test 1: Verify access token
print("1. Testing access token validity...")
test_url = f"{API_BASE}/me"
resp = requests.get(test_url, params={"access_token": access_token}, timeout=10)
print(f"   Status: {resp.status_code}")
if resp.ok:
    data = resp.json()
    print(f"   ✅ Access token is valid")
    print(f"   User: {data.get('name', 'N/A')}")
else:
    error_data = resp.json() if resp.text else {}
    error = error_data.get("error", {})
    print(f"   ❌ Access token error: {error.get('message', 'Unknown')}")
    print(f"   Code: {error.get('code', 'N/A')}")
    print(f"   Type: {error.get('type', 'N/A')}")

print()

# Test 2: Verify business account access
if business_account_id:
    print("2. Testing business account access...")
    account_url = f"{API_BASE}/{business_account_id}"
    resp = requests.get(
        account_url,
        params={
            "fields": "id,username,account_type",
            "access_token": access_token,
        },
        timeout=10,
    )
    print(f"   Status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(f"   ✅ Business account accessible")
        print(f"   Account ID: {data.get('id', 'N/A')}")
        print(f"   Username: {data.get('username', 'N/A')}")
        print(f"   Account Type: {data.get('account_type', 'N/A')}")
    else:
        error_data = resp.json() if resp.text else {}
        error = error_data.get("error", {})
        print(f"   ❌ Business account error: {error.get('message', 'Unknown')}")
        print(f"   Code: {error.get('code', 'N/A')}")
        print(f"   Type: {error.get('type', 'N/A')}")
else:
    print("2. ⚠️  INSTAGRAM_BUSINESS_ACCOUNT_ID not set")

print()

# Test 3: Check permissions
print("3. Checking access token permissions...")
debug_url = f"{API_BASE}/debug_token"
resp = requests.get(
    debug_url,
    params={
        "input_token": access_token,
        "access_token": access_token,
    },
    timeout=10,
)
print(f"   Status: {resp.status_code}")
if resp.ok:
    data = resp.json()
    debug_data = data.get("data", {})
    print(f"   ✅ Token debug info retrieved")
    print(f"   App ID: {debug_data.get('app_id', 'N/A')}")
    print(f"   User ID: {debug_data.get('user_id', 'N/A')}")
    print(f"   Expires at: {debug_data.get('expires_at', 'N/A')}")
    print(f"   Scopes: {debug_data.get('scopes', [])}")
    
    # Check if instagram_content_publish is in scopes
    scopes = debug_data.get("scopes", [])
    if "instagram_content_publish" in scopes:
        print("   ✅ instagram_content_publish permission found")
    else:
        print("   ⚠️  instagram_content_publish permission NOT found")
        print(f"   Available scopes: {scopes}")
else:
    error_data = resp.json() if resp.text else {}
    error = error_data.get("error", {})
    print(f"   ❌ Debug token error: {error.get('message', 'Unknown')}")

print("\n=== Summary ===")
print("If access token is invalid, regenerate it from Facebook Developer Portal")
print("If permissions are missing, check app permissions in Developer Portal")

