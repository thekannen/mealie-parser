from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from requests import RequestException, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass
class MealieClient:
    base_url: str
    api_token: str
    timeout_seconds: int = 30
    retries: int = 3
    backoff_seconds: float = 0.4

    def __post_init__(self) -> None:
        self.session = self._build_session()

    def _build_session(self) -> Session:
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": _authorization_value(self.api_token),
                "Accept": "application/json",
            }
        )
        retry = Retry(
            total=self.retries,
            connect=self.retries,
            read=self.retries,
            backoff_factor=self.backoff_seconds,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST", "PATCH"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_unparsed_recipe_slugs(self, page_size: int) -> list[str]:
        slugs: list[str] = []
        page = 1
        while True:
            payload = self._request(
                "GET",
                "/recipes",
                params={"page": page, "perPage": page_size},
            )
            items = payload.get("items", [])
            slugs.extend(
                item["slug"] for item in items if not item.get("hasParsedIngredients")
            )
            if page >= payload.get("total_pages", page):
                break
            page += 1
        return slugs

    def get_recipe(self, slug: str) -> dict[str, Any]:
        return self._request("GET", f"/recipes/{slug}")

    def parse_ingredients(
        self, ingredients: list[str], strategy: str
    ) -> list[dict[str, Any]]:
        payload = {"strategy": strategy, "ingredients": ingredients}
        data = self._request("POST", "/parser/ingredients", json=payload)
        if not isinstance(data, list):
            raise requests.HTTPError(
                f"Unexpected parser response type: {type(data).__name__}"
            )
        return data

    def create_food(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/foods", json={"name": name})

    def patch_recipe_ingredients(
        self, slug: str, ingredients: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/recipes/{slug}",
            json={"recipeIngredient": ingredients},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json,
                timeout=self.timeout_seconds,
            )
        except RequestException as exc:
            raise requests.HTTPError(f"{method} {url} failed: {exc}") from exc

        if response.status_code >= 400:
            details = _truncate(response.text)
            try:
                details = _summarize_error_payload(response.json())
            except ValueError:
                pass
            raise requests.HTTPError(
                f"{method} {url} failed ({response.status_code}): {details}",
                response=response,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise requests.HTTPError(
                f"{method} {url} returned non-JSON response"
            ) from exc


def _authorization_value(token: str) -> str:
    value = token.strip()
    if value.lower().startswith("bearer "):
        return value
    return f"Bearer {value}"


def _summarize_error_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return _truncate(detail)

        if isinstance(detail, dict):
            message = detail.get("message")
            exception = str(detail.get("exception") or "").strip()

            if "duplicate key value violates unique constraint" in exception.lower():
                return "duplicate key violation"

            if message and exception:
                return _truncate(f"{message}: {exception}")
            if message:
                return _truncate(str(message))
            return _truncate(str(detail))

        return _truncate(str(payload))

    if isinstance(payload, list):
        return _truncate(str(payload))

    return _truncate(str(payload))


def _truncate(value: str, max_len: int = 220) -> str:
    text = value.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len-3]}..."
