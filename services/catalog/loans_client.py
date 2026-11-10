import os

import requests


class LoansUnavailable(Exception):
    pass


def has_open_loan(tool_id):
    base_url = os.environ.get("LOANS_URL", "http://loans:8000").rstrip("/")
    timeout = float(os.environ.get("LOANS_TIMEOUT_S", "2"))
    try:
        response = requests.get(
            f"{base_url}/loans",
            params={"tool_id": tool_id},
            timeout=timeout,
        )
        response.raise_for_status()
        return len(response.json()) > 0
    except (requests.RequestException, ValueError) as error:
        raise LoansUnavailable(
            "Loans service unavailable, so the tool cannot be confirmed as not out. Try again shortly."
        ) from error
