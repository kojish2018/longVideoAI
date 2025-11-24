"""Check available Facebook pages and their Instagram connections."""
from dotenv import load_dotenv
import os
import requests

load_dotenv()

API_BASE = "https://graph.facebook.com/v21.0"

# 現在のトークンを使用（ユーザートークンとして）
access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")

print("=== Checking Facebook Pages ===\n")

if not access_token:
    print("❌ INSTAGRAM_ACCESS_TOKEN not set in .env")
    exit(1)

# Step 1: Check if token is valid and get user info
print("1. Checking token validity...")
try:
    resp = requests.get(
        f"{API_BASE}/me",
        params={"access_token": access_token},
        timeout=10,
    )
    if resp.ok:
        user_data = resp.json()
        print(f"   ✅ Token is valid")
        print(f"   User: {user_data.get('name', 'N/A')}")
        print(f"   User ID: {user_data.get('id', 'N/A')}")
        user_id = user_data.get('id')
    else:
        print(f"   ❌ Token invalid: {resp.status_code}")
        print(f"   Response: {resp.text[:200]}")
        exit(1)
except Exception as e:
    print(f"   ❌ Error: {e}")
    exit(1)

print()

# Step 2: Get user's pages
print("2. Getting user's Facebook pages...")
try:
    resp = requests.get(
        f"{API_BASE}/me/accounts",
        params={
            "access_token": access_token,
            "fields": "id,name,access_token,instagram_business_account",
        },
        timeout=10,
    )
    
    if resp.ok:
        pages_data = resp.json()
        pages = pages_data.get("data", [])
        
        if not pages:
            print("   ⚠️  No pages found")
            print("\n   You need to:")
            print("   1. Create a Facebook Page")
            print("   2. Connect it to your Instagram Business account (kohhbimind)")
            print("   3. Make sure you're an admin of the page")
        else:
            print(f"   ✅ Found {len(pages)} page(s):\n")
            for i, page in enumerate(pages, 1):
                print(f"   Page {i}:")
                print(f"     Name: {page.get('name', 'N/A')}")
                print(f"     Page ID: {page.get('id', 'N/A')}")
                
                # Check Instagram connection
                insta_account = page.get("instagram_business_account", {})
                if insta_account:
                    insta_id = insta_account.get("id", "")
                    print(f"     ✅ Connected to Instagram: {insta_id}")
                    if insta_id == "17841468791526853":
                        print(f"     ✅ This is your target Instagram account!")
                        print(f"     Page Access Token: {page.get('access_token', 'N/A')[:30]}...")
                        print(f"\n   💡 Use this Page Access Token in .env:")
                        print(f"   INSTAGRAM_ACCESS_TOKEN={page.get('access_token', '')}")
                else:
                    print(f"     ❌ Not connected to Instagram")
                print()
    else:
        error_data = resp.json() if resp.text else {}
        error = error_data.get("error", {})
        print(f"   ❌ Error: {error.get('message', 'Unknown')}")
        print(f"   Code: {error.get('code', 'N/A')}")
        print(f"   Type: {error.get('type', 'N/A')}")
        
        if error.get('code') == 190:
            print("\n   ⚠️  Current token is invalid. You need a valid Facebook User token.")
            print("   Get one from: https://developers.facebook.com/tools/explorer/")
            print("   Select 'Reels API uploader' app and generate token")
        
except Exception as e:
    print(f"   ❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Next Steps ===")
print("If no pages found or pages not connected to Instagram:")
print("1. Go to https://www.facebook.com/pages/create")
print("2. Create a Facebook Page")
print("3. Go to Page Settings > Instagram")
print("4. Connect your Instagram account (kohhbimind)")
print("5. Make sure you're an admin of the page")
print("6. Then run this script again with a valid Facebook User token")

