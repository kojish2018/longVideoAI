"""Check Facebook pages with a user-provided token."""
import requests

API_BASE = "https://graph.facebook.com/v21.0"

print("=== Check Facebook Pages ===\n")
print("Please paste your Facebook User token from Graph API Explorer:")
print("(Get it from: https://developers.facebook.com/tools/explorer/)")
print()

user_token = input("Token: ").strip()

if not user_token:
    print("❌ No token provided")
    exit(1)

print("\n=== Checking Pages ===\n")

try:
    # Get user's pages
    resp = requests.get(
        f"{API_BASE}/me/accounts",
        params={
            "access_token": user_token,
            "fields": "id,name,access_token,instagram_business_account",
        },
        timeout=10,
    )
    
    if resp.ok:
        pages_data = resp.json()
        pages = pages_data.get("data", [])
        
        if not pages:
            print("❌ No Facebook pages found")
            print("\n📝 You need to:")
            print("   1. Create a Facebook Page: https://www.facebook.com/pages/create")
            print("   2. Go to Page Settings > Instagram")
            print("   3. Connect your Instagram account (kohhbimind)")
            print("   4. Make sure you're an admin of the page")
        else:
            print(f"✅ Found {len(pages)} page(s):\n")
            for i, page in enumerate(pages, 1):
                print(f"Page {i}: {page.get('name', 'N/A')}")
                print(f"  Page ID: {page.get('id', 'N/A')}")
                
                insta_account = page.get("instagram_business_account", {})
                if insta_account:
                    insta_id = insta_account.get("id", "")
                    print(f"  ✅ Connected to Instagram: {insta_id}")
                    if insta_id == "17841468791526853":
                        print(f"  ✅ This matches your target Instagram account!")
                        page_token = page.get("access_token", "")
                        print(f"\n  📋 Use this Page Access Token in .env:")
                        print(f"  INSTAGRAM_ACCESS_TOKEN={page_token}")
                else:
                    print(f"  ❌ Not connected to Instagram")
                print()
    else:
        error_data = resp.json() if resp.text else {}
        error = error_data.get("error", {})
        print(f"❌ Error: {error.get('message', 'Unknown')}")
        print(f"Code: {error.get('code', 'N/A')}")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

