"""One-time Google OAuth. Put your OAuth client file at data/google_credentials.json, then run:
    python -m synapse.integrations.google_auth
"""
from google_auth_oauthlib.flow import InstalledAppFlow

from synapse.config import get_settings
from synapse.mcp_servers.google import SCOPES

if __name__ == "__main__":
    s = get_settings()
    cred = s.data_dir / "google_credentials.json"
    if not cred.exists():
        raise SystemExit(f"Missing {cred}. Download an OAuth 'Desktop app' client JSON from Google Cloud Console.")
    creds = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES).run_local_server(port=0)
    s.google_token_path.write_text(creds.to_json())
    print(f"Saved {s.google_token_path}. Restart Synapse to enable Gmail/Calendar tools.")
