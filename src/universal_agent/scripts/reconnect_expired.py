
import os
import sys
import argparse

# Add project root to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from composio import Composio

def reconnect_expired(specific_slugs=None):
    # Load API key
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.environ.get("COMPOSIO_API_KEY")

    if not api_key:
        print("❌ No COMPOSIO_API_KEY found.")
        return

    print(f"🔑 Using API Key: {api_key[:5]}...{api_key[-5:]}")
    
    client = Composio(api_key=api_key)
    
    try:
        # Use COMPOSIO_USER_ID if available, otherwise fallback to inference
        user_id = os.getenv("COMPOSIO_USER_ID")
        if not user_id:
            print("\n🔍 Scanning for expired connections to infer User ID...")
            connections = client.connected_accounts.list()
            if connections and hasattr(connections, 'items') and connections.items:
                user_id = connections.items[0].user_id
        
        if not user_id:
            user_id = "default"
            
        print(f"👤 User ID: {user_id}")
        
        # Create session
        session = client.create(user_id=user_id)
        
        # Toolkits to skip
        blacklist = {"firecrawl", "jira", "exa", "composio", "semanticscholar"}

        if specific_slugs:
            pending = [s for s in specific_slugs if s not in blacklist]
            if not pending:
                print("ℹ️ All requested toolkits are blacklisted.")
                return
            print(f"🎯 Target Toolkits: {', '.join(pending)}")
        else:
            print("\n🔍 Scanning for pending/expired connections (excluding blacklisted)...")
            # Check toolkits in session
            toolkits = session.toolkits()
            
            pending = []
            for toolkit in toolkits.items:
                if toolkit.slug in blacklist:
                    continue
                    
                is_active = False
                if toolkit.connection and hasattr(toolkit.connection, 'is_active'):
                    is_active = toolkit.connection.is_active
                
                if not is_active:
                    pending.append(toolkit.slug)
        
        if not pending:
             print("✅ No pending toolkits found.")
             return

        print(f"\n⚠️ Generating links for {len(pending)} toolkits...\n")

        for slug in pending:
            try:
                # session.authorize() generates a Connect Link
                connection_request = session.authorize(slug)
                url = connection_request.redirect_url
                print(f"👉 Connect/Reconnect {slug}:")
                print(f"   {url}\n")
            except Exception as e:
                # Some toolkits don't need auth (e.g. composio, codeinterpreter)
                if "does not require authentication" in str(e):
                    print(f"ℹ️ {slug} does not require authentication.")
                else:
                    print(f"❌ Failed to generate link for {slug}: {e}")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("slugs", nargs="*", help="Specific toolkit slugs to connect")
    args = parser.parse_args()
    
    reconnect_expired(specific_slugs=args.slugs if args.slugs else None)
