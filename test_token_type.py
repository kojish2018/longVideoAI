"""Test to identify the correct token type for Instagram Graph API."""
from dotenv import load_dotenv
import os
import requests

load_dotenv()

API_BASE = "https://graph.facebook.com/v21.0"

access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
business_account_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

print("=== Testing Token Type ===\n")

# Test 1: Check if it's a Facebook User token
print("1. Testing as Facebook User token...")
try:
    resp = requests.get(
        f"{API_BASE}/me",
        params={"access_token": access_token},
        timeout=10,
    )
    if resp.ok:
        data = resp.json()
        print(f"   ✅ Valid Facebook User token")
        print(f"   User: {data.get('name', 'N/A')}")
        print(f"   ID: {data.get('id', 'N/A')}")
    else:
        print(f"   ❌ Not a valid Facebook User token")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# Test 2: Check if it's a Page token (needed for Instagram)
print("2. Testing as Facebook Page token...")
try:
    # Get user's pages
    resp = requests.get(
        f"{API_BASE}/me/accounts",
        params={"access_token": access_token},
        timeout=10,
    )
    if resp.ok:
        data = resp.json()
        pages = data.get("data", [])
        if pages:
            print(f"   ✅ Token can access {len(pages)} page(s)")
            for page in pages[:3]:  # Show first 3 pages
                print(f"   Page: {page.get('name', 'N/A')} (ID: {page.get('id', 'N/A')})")
                # Check if this page has Instagram account
                page_id = page.get("id", "")
                if page_id:
                    insta_resp = requests.get(
                        f"{API_BASE}/{page_id}",
                        params={
                            "fields": "instagram_business_account",
                            "access_token": access_token,
                        },
                        timeout=10,
                    )
                    if insta_resp.ok:
                        insta_data = insta_resp.json()
                        insta_account = insta_data.get("instagram_business_account", {})
                        if insta_account:
                            print(f"      → Connected to Instagram: {insta_account.get('id', 'N/A')}")
        else:
            print("   ⚠️  No pages found")
    else:
        print(f"   ❌ Cannot access pages: {resp.status_code}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# Test 3: Try to access Instagram account directly
if business_account_id:
    print(f"3. Testing direct Instagram account access (ID: {business_account_id})...")
    try:
        resp = requests.get(
            f"{API_BASE}/{business_account_id}",
            params={
                "fields": "id,username,account_type",
                "access_token": access_token,
            },
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            print(f"   ✅ Can access Instagram account")
            print(f"   Username: {data.get('username', 'N/A')}")
            print(f"   Account Type: {data.get('account_type', 'N/A')}")
        else:
            error_data = resp.json() if resp.text else {}
            error = error_data.get("error", {})
            print(f"   ❌ Cannot access: {error.get('message', 'Unknown')}")
            print(f"   Code: {error.get('code', 'N/A')}")
            print(f"   Type: {error.get('type', 'N/A')}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

print("\n=== Recommendations ===")
print("For Instagram Reels upload, you need:")
print("1. A Facebook Page access token (not user token)")
print("2. The page must be connected to your Instagram Business account")
print("3. The token must have 'instagram_content_publish' permission")
print("\nTo get a Page token:")
print("1. Go to Graph API Explorer")
print("2. Select your app and page")
print("3. Generate token with 'instagram_content_publish' permission")

