"""Check the Tableau connection from the command line: sign in, list projects, sign out.

    cd apps/api && .venv/Scripts/python -m app.tableau_check

Reads the same settings as the API (root .env). Never prints the token secret.
"""

from __future__ import annotations

import sys

from .config import get_settings
from .tableau_publisher import RestTableauPublisher


def main() -> int:
    s = get_settings()
    secret_set = bool(s.tableau_pat_secret.get_secret_value())
    print(f"Server:      {s.tableau_server_url or '(not set: TABLEAU_SERVER_URL)'}")
    print(f"Site:        {s.tableau_site_content_url or '(default site)'}")
    print(f"Token name:  {s.tableau_pat_name or '(not set: TABLEAU_PAT_NAME)'}")
    print(f"Token secret:{' set' if secret_set else ' (not set: TABLEAU_PAT_SECRET)'}")
    print(f"Mode:        {s.tableau_mode}, enabled={s.tableau_enabled}")
    if s.tableau_mode != "rest" or not s.tableau_credentials_configured:
        print("\nNot ready: set TABLEAU_ENABLED=true, TABLEAU_MODE=rest, TABLEAU_SERVER_URL, TABLEAU_PAT_NAME and TABLEAU_PAT_SECRET in the root .env.")
        return 2

    pub = RestTableauPublisher(s)
    result = pub.test_connection()
    print(f"\n{result['message']}")
    if not result.get("signed_in"):
        return 1
    if result.get("product_version"):
        print(f"Tableau {result['product_version']}, REST API {result.get('rest_api_version')}")
    print("\nProjects visible to this token:")
    for p in pub.list_projects():
        mark = "  <- target" if p["id"] == s.tableau_project_id else ""
        print(f"  {p['name']:<40} {p['id']}{mark}")
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
