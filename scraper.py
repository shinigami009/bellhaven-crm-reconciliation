from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from models import Facility
from normalization import normalize_name, normalize_street

LOG = logging.getLogger(__name__)


class ScrapeError(RuntimeError):
    pass


def _get(session: requests.Session, url: str, timeout: float) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _community_links(soup: BeautifulSoup, base_url: str) -> set[str]:
    return {
        urljoin(base_url, anchor["href"]).split("#", 1)[0]
        for anchor in soup.select('a[href^="/communities/"]')
    }


def discover_community_urls(base_url: str, session: requests.Session, timeout: float = 15) -> tuple[list[str], list[str]]:
    sources, urls = [], set()
    homepage = _get(session, base_url + "/", timeout)
    sources.append(base_url + "/")
    urls.update(_community_links(homepage, base_url))
    page = 1
    while True:
        url = base_url + "/communities" + (f"?page={page}" if page > 1 else "")
        soup = _get(session, url, timeout)
        sources.append(url)
        urls.update(_community_links(soup, base_url))
        next_link = soup.find("a", string=re.compile(r"Next", re.I))
        if not next_link:
            break
        page += 1
        if page > 100:
            raise ScrapeError("Community pagination did not terminate")
    return sorted(urls), sources


def scrape_facilities(base_url: str, timeout: float = 15) -> tuple[list[Facility], list[str]]:
    session = requests.Session()
    session.headers["User-Agent"] = "BellhavenReconciliation/1.0"
    urls, sources = discover_community_urls(base_url.rstrip("/"), session, timeout)
    scraped_at = datetime.now(timezone.utc).isoformat()
    facilities: list[Facility] = []
    for url in urls:
        soup = _get(session, url, timeout)
        values: dict[str, object] = {}
        for term in soup.select("dt"):
            definition = term.find_next_sibling("dd")
            if definition:
                values[term.get_text(" ", strip=True)] = definition
        name = soup.select_one("h1")
        address_node = values.get("Address")
        care_node = values.get("Care Offerings")
        phone_node = values.get("Phone")
        if not name or not address_node or not care_node:
            raise ScrapeError(f"Missing required detail fields at {url}")
        address_lines = list(address_node.stripped_strings)  # type: ignore[union-attr]
        if len(address_lines) < 2:
            raise ScrapeError(f"Unparseable address at {url}")
        match = re.fullmatch(r"(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", address_lines[-1])
        if not match:
            raise ScrapeError(f"Unparseable city/state/ZIP at {url}")
        offerings = tuple(care_node.stripped_strings)  # type: ignore[union-attr]
        facility_name, street = name.get_text(" ", strip=True), " ".join(address_lines[:-1])
        facilities.append(Facility(
            name=facility_name, street=street, city=match.group(1), state=match.group(2),
            zip=match.group(3), care_offerings=offerings, source_url=url,
            scraped_at=scraped_at, raw_address="\n".join(address_lines),
            normalized_name=normalize_name(facility_name), normalized_street=normalize_street(street),
            phone=phone_node.get_text(" ", strip=True) if phone_node else "",  # type: ignore[union-attr]
        ))
    return facilities, sources
