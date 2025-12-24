import os
import time
from dotenv import load_dotenv
from composio import Composio

# Load environment variables
load_dotenv()

# The specific consolidated user entity we are using
PRIMARY_USER_ID = "pg-test-86524ebc-9b1e-4f08-bd20-b77dd71c2df9"

# Tools we want to ensure are connected
REQUIRED_TOOLS = [
    "gmail",
    "googlecalendar",
    "github",
    "slack"
]

def onboard_tools():
    print(f"🚀 Initializing Tool Onboarding for User: {PRIMARY_USER_ID}")
    
    composio = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))
    
    # 1. Fetch current connections for this specific user
    print("🔍 Inspecting existing connections...")
    connected_slugs = []
    try:
        connections = composio.connected_accounts.list(user_ids=[PRIMARY_USER_ID])
        if hasattr(connections, 'items'):
            for item in connections.items:
                 if hasattr(item, 'toolkit') and item.toolkit:
                     # Check status if available
                     is_active = False
                     status = getattr(item, 'status', 'UNKNOWN')
                     
                     if status == 'ACTIVE':
                         is_active = True
                     
                     if is_active:
                         print(f"  ✅ {item.toolkit.slug} is CONNECTED (Status: {status})")
                         connected_slugs.append(item.toolkit.slug)
                     else:
                         print(f"  ⚠️ {item.toolkit.slug} found but status is {status}")
                         
    except Exception as e:
        print(f"❌ Error listing connections: {e}")
        return

    print("-" * 50)

    # Calculate missing tools first
    missing_tools = [t for t in REQUIRED_TOOLS if t not in connected_slugs]
    
    if not missing_tools:
        print("🎉 ALL REQUIRED TOOLS ARE CONNECTED! No further action needed.")
        print("=" * 50)
        return

    # Create a session to use the high-level authorize() API
    # This automatically handles Auth Config lookup
    print(f"\n🔄 Creating Session to authorize: {', '.join(missing_tools)}...")
    session = composio.create(user_id=PRIMARY_USER_ID)

    # 2. Iterate required tools and generate auth links if missing
    for tool_slug in REQUIRED_TOOLS:
        if tool_slug in connected_slugs:
            # print(f"🟢 {tool_slug}: Already connected.") # Optional: limit noise
            continue
            
        print(f"\n🟡 {tool_slug}: NOT connected. Generating Auth Link...")
        
        try:
            # session.authorize returns a ConnectionRequest
            request = session.authorize(tool_slug)
            
            print(f"   👉 ACTION REQUIRED: Click this link to authorize {tool_slug}:")
            print(f"   🔗 {request.redirect_url}")
            
        except Exception as e:
            print(f"   ❌ Failed to generate link for {tool_slug}: {e}")

    print("\n" + "=" * 50)
    print("🏁 Onboarding check complete. If you authorized new tools, please re-run this script to verify.")

if __name__ == "__main__":
    onboard_tools()
