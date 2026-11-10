import os

import requests


class CatalogUnavailable(Exception):
    pass


def get_tool(tool_id):
    """Return the tool from the catalog service, or None if it does not exist."""
    base_url = os.environ.get("CATALOG_URL", "http://catalog:8000").rstrip("/")
    timeout = float(os.environ.get("CATALOG_TIMEOUT_S", "2"))
    try:
        response = requests.get(f"{base_url}/tools/{tool_id}", timeout=timeout)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        raise CatalogUnavailable(
            "Catalog service unavailable, so the tool cannot be checked. Try again shortly."
        ) from error
