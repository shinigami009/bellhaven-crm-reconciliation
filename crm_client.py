from __future__ import annotations

import logging
import time
from typing import Any

import requests

from models import Account

LOG = logging.getLogger(__name__)


class CRMError(RuntimeError):
    pass


class CRMClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0, retries: int = 3):
        if not token:
            raise ValueError("BELLHAVEN_API_TOKEN is required")
        self.base_url = base_url.rstrip("/") + "/api/v1"
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = self.base_url + path
        for attempt in range(self.retries + 1):
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise CRMError(f"{method} {path} failed: {exc}") from exc
                time.sleep(0.5 * (2**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
            if not response.ok:
                raise CRMError(f"{method} {path} returned {response.status_code}: {response.text[:500]}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise CRMError(f"{method} {path} returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise CRMError(f"{method} {path} returned an unexpected response")
            return payload
        raise CRMError(f"{method} {path} failed")

    def list_accounts(self, **filters: Any) -> list[Account]:
        page, accounts = 1, []
        while True:
            params = {**filters, "page": page, "page_size": 50}
            payload = self._request("GET", "/accounts", params=params)
            data = payload.get("data")
            if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
                raise CRMError("Account list response is missing data[]")
            accounts.extend(Account.from_dict(x) for x in data)
            total = int(payload.get("total", len(accounts)))
            if len(accounts) >= total or not data:
                return accounts
            page += 1

    def get_account(self, account_id: str) -> Account:
        return Account.from_dict(self._request("GET", f"/accounts/{account_id}"))

    def create_account(self, values: dict[str, Any]) -> Account:
        payload = self._request("POST", "/accounts", json=values)
        candidate = payload.get("data", payload)
        if isinstance(candidate, dict) and all(name in candidate for name in Account.__dataclass_fields__):
            return Account.from_dict(candidate)
        account_id = candidate.get("account_id") if isinstance(candidate, dict) else None
        account_id = account_id or payload.get("account_id") or payload.get("id")
        if not account_id:
            raise CRMError("Create response did not include the new account ID")
        return self.get_account(str(account_id))

    def update_account(self, account_id: str, values: dict[str, Any]) -> Account:
        self._request("PATCH", f"/accounts/{account_id}", json=values)
        return self.get_account(account_id)
