from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


USER_AGENT = (
    "Mozilla/5.0 (compatible; PersonalRSSFeed/1.0; "
    "+https://github.com/elenates/Personal-RSS-feeds)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "cs,en;q=0.8",
}


@dataclass
class Item:
    title: str
    url: str
    description: str = ""
    published: datetime | None = None


def fetch(url: str) -> BeautifulSoup:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def absolute(base: str, url: str) -> str:
    return urljoin(base, url)


def guid(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def parse_date(text: str) -> datetime | None:
    if not text:
        return None

    text = clean(text)

    try:
        result = date_parser.parse(
            text,
            dayfirst=True,
            fuzzy=True,
        )

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    except (ValueError, OverflowError):
        return None


def add_item(
    items: list[Item],
    title: str,
    url: str,
    description: str = "",
    published: datetime | None = None,
):
    title = clean(title)
    url = url.strip()

    if not title or not url:
        return

    if url.startswith("#"):
        return

    if url.lower().startswith("javascript:"):
        return

    if any(item.url == url for item in items):
        return

    items.append(
        Item(
            title=title,
            url=url,
            description=clean(description),
            published=published,
        )
    )


# ---------------------------------------------------------------------------
# MZV
# ---------------------------------------------------------------------------

def parse_mzv() -> tuple[str, str, list[Item]]:
    source = (
        "https://mzv.gov.cz/jnp/cz/"
        "informace_pro_cizince/aktuality/index.html"
    )

    soup = fetch(source)
    items: list[Item] = []

    for link in soup.find_all("a", href=True):
    href = absolute(source, link["href"])
    title = clean(link.get_text(" ", strip=True))

    if title and (
        "/informace_pro_cizince/aktuality/" in href
        or ".mobi" in href
        or "index" in href
    ):
        print("MZV LINK:", repr(title), "=>", href)

    # Only links to actual articles in the Aktuality directory.
    # This deliberately excludes navigation, search and parent pages.
    for link in soup.find_all("a", href=True):

        href = absolute(source, link["href"])

        if "/informace_pro_cizince/aktuality/" not in href:
            continue

        if href.rstrip("/").endswith("/aktuality"):
            continue

        if href.endswith("/aktuality/index.html"):
            continue

        title = clean(link.get_text(" ", strip=True))

        if not title:
            continue

        # Find the nearest reasonably-sized container.
        container = link

        for _ in range(6):
            if not container.parent:
                break

            container = container.parent
            text = clean(container.get_text(" ", strip=True))

            if len(text) >= len(title) + 20:
                break

        text = clean(container.get_text(" ", strip=True))

        # MZV typically has:
        #
        # 16.07.2026 / 12:40 |
        # Aktualizováno: 21.07.2026 / 13:17
        #
        # We want the UPDATED date.
        updated_match = re.search(
            r"Aktualizováno:\s*"
            r"(\d{1,2}\.\d{1,2}\.\d{4})"
            r"\s*/\s*"
            r"(\d{1,2}:\d{2})",
            text,
            re.IGNORECASE,
        )

        published_match = re.search(
            r"(\d{1,2}\.\d{1,2}\.\d{4})"
            r"\s*/\s*"
            r"(\d{1,2}:\d{2})",
            text,
        )

        if updated_match:
            date_text = (
                f"{updated_match.group(1)} "
                f"{updated_match.group(2)}"
            )
        elif published_match:
            date_text = (
                f"{published_match.group(1)} "
                f"{published_match.group(2)}"
            )
        else:
            date_text = ""

        published = parse_date(date_text)

        # Remove title and metadata from description.
        description = text

        description = description.replace(title, "", 1)

        description = re.sub(
            r"\d{1,2}\.\d{1,2}\.\d{4}\s*/\s*\d{1,2}:\d{2}",
            "",
            description,
        )

        description = re.sub(
            r"Aktualizováno:\s*"
            r"\d{1,2}\.\d{1,2}\.\d{4}"
            r"\s*/\s*\d{1,2}:\d{2}",
            "",
            description,
            flags=re.IGNORECASE,
        )

        description = re.sub(
            r"více\s*►",
            "",
            description,
            flags=re.IGNORECASE,
        )

        add_item(
            items,
            title,
            href,
            description[:2000],
            published,
        )

    print(f"MZV: found {len(items)} items")

    return (
        "MZV - Aktuality pro cizince",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# TMBK / Seznam Zprávy
# ---------------------------------------------------------------------------

def parse_tmbk() -> tuple[str, str, list[Item]]:
    source = "https://www.seznamzpravy.cz/autor/tmbk-1312"

    soup = fetch(source)
    items: list[Item] = []

    for link in soup.find_all("a", href=True):

        title = clean(link.get_text(" ", strip=True))

        if not title.startswith("TMBK:"):
            continue

        href = absolute(source, link["href"])

        if "seznamzpravy.cz" not in href:
            continue

        # Look around the article for a date.
        container = link

        for _ in range(5):
            if not container.parent:
                break

            container = container.parent

            text = clean(container.get_text(" ", strip=True))

            if len(text) > len(title) + 20:
                break

        date_match = re.search(
            r"\d{1,2}\.\s*\d{1,2}\.\s*\d{4}"
            r"(?:\s+\d{1,2}:\d{2})?",
            text,
        )

        published = (
            parse_date(date_match.group(0))
            if date_match
            else None
        )

        add_item(
            items,
            title,
            href,
            "",
            published,
        )

    print(f"TMBK: found {len(items)} items")

    return (
        "TMBK - Seznam Zprávy",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# Skalní mlýn
# ---------------------------------------------------------------------------

def parse_skalni_mlyn() -> tuple[str, str, list[Item]]:
    source = "https://www.skalnimlyn.cz/akce-a-novinky"

    soup = fetch(source)
    items: list[Item] = []

    print("SKALNI MLÝN LINKS:")

    for link in soup.find_all("a", href=True):
        title = clean(link.get_text(" ", strip=True))
        href = absolute(source, link["href"])

        if title:
            print("SKALNI:", repr(title), "=>", href)

    # The events on this page are not represented by h3/h4 headings.
    # They are regular text blocks following "Nadcházející akce".
    marker = None

    for element in soup.find_all(["h2", "h3"]):
        if clean(element.get_text(" ", strip=True)) == "Nadcházející akce":
            marker = element
            break

    if not marker:
        print("Skalní mlýn: 'Nadcházející akce' not found")
        return (
            "Hotel Skalní mlýn - Nadcházející akce",
            source,
            [],
        )

    # Find all text-containing elements after the marker.
    #
    # We identify event titles by looking for the characteristic
    # date blocks immediately before them.
    #
    # Current page contains:
    #
    # 19
    # 09/2026
    # Den otevřených dveří Císařské jeskyně
    #
    # 12
    # 09/2026
    # Skrytá krása kamenů
    #
    # etc.

    all_elements = list(soup.find_all(True))

    try:
        marker_index = all_elements.index(marker)
    except ValueError:
        marker_index = 0

    after = all_elements[marker_index + 1:]

    month_year_re = re.compile(
        r"^\d{1,2}/\d{4}$"
    )

    day_re = re.compile(
        r"^\d{1,2}$"
    )

    events = []

    for i, element in enumerate(after):

        text = clean(element.get_text(" ", strip=True))

        if not text:
            continue

        # We need a small element containing only a day number.
        if not day_re.fullmatch(text):
            continue

        # Look immediately around this element for "MM/YYYY".
        month_year = None

        for next_element in after[i + 1:i + 8]:

            next_text = clean(
                next_element.get_text(" ", strip=True)
            )

            if month_year_re.fullmatch(next_text):
                month_year = next_text
                break

        if not month_year:
            continue

        # Find the next meaningful short text element.
        title = None
        title_element = None

        for next_element in after[i + 1:i + 25]:

            next_text = clean(
                next_element.get_text(" ", strip=True)
            )

            if not next_text:
                continue

            if month_year_re.fullmatch(next_text):
                continue

            # Time/location/description should not be considered
            # a title. Event titles on this page are usually short
            # and don't contain these characteristic phrases.
            if re.search(
                r"\bod\s+\d|"
                r"\bdo\s+\d|"
                r"\bhodin\b|"
                r"^Skalní mlýn,|"
                r"^Býčí skála$|"
                r"^Ostrovský žleb,|"
                r"^Areál u jeskyně",
                next_text,
                re.IGNORECASE,
            ):
                continue

            # Ignore the section heading.
            if next_text in {
                "Novinky",
                "Články a zprávy",
                "Kalendář akcí",
            }:
                continue

            # A description is normally much longer.
            if len(next_text) > 180:
                continue

            title = next_text
            title_element = next_element
            break

        if not title:
            continue

        # Avoid duplicates caused by nested HTML elements.
        if any(event["title"] == title for event in events):
            continue

        # The full event date is normally repeated in the description.
        #
        # Example:
        # "Dne ... proběhne ... v sobotu 19. 9. 2026 ..."
        #
        # Extract that date if possible.
        container_text = ""

        # Find a parent containing the title and enough surrounding text.
        parent = title_element

        for _ in range(8):

            if not parent.parent:
                break

            parent = parent.parent

            candidate = clean(
                parent.get_text(" ", strip=True)
            )

            if len(candidate) > len(title) + 80:
                container_text = candidate
                break

        full_date = None

        date_match = re.search(
            r"\b(\d{1,2})\.\s*"
            r"(\d{1,2})\.\s*"
            r"(20\d{2})\b",
            container_text,
        )

        if date_match:
            full_date = parse_date(
                f"{date_match.group(1)}."
                f"{date_match.group(2)}."
                f"{date_match.group(3)}"
            )

        # If no full date was found, construct one from the
        # visible day/month/year.
        if not full_date:
            day = int(text)
            month, year = month_year.split("/")

            full_date = parse_date(
                f"{day}.{month}.{year}"
            )

        description = container_text

        if description:
            description = description.replace(
                title,
                "",
                1,
            )

        # The page itself is the canonical source for these events.
        # Individual event cards do not necessarily have their own URL.
        event_url = source + "#" + re.sub(
            r"[^a-z0-9]+",
            "-",
            title.lower(),
        ).strip("-")

        events.append(
            {
                "title": title,
                "url": event_url,
                "description": description[:2500],
                "published": full_date,
            }
        )

    for event in events:
        add_item(
            items,
            event["title"],
            event["url"],
            event["description"],
            event["published"],
        )

    print(f"Skalní mlýn: found {len(items)} items")

    return (
        "Hotel Skalní mlýn - Nadcházející akce",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# Svět energie
# ---------------------------------------------------------------------------

def parse_svet_energie() -> tuple[str, str, list[Item]]:
    source = "https://www.svetenergie.cz/cs/kalendar-akci"

    soup = fetch(source)
    items: list[Item] = []

    for link in soup.find_all("a", href=True):

        label = clean(link.get_text(" ", strip=True))

        if label.upper() != "ZJISTIT VÍCE":
            continue

        href = absolute(source, link["href"])

        if href == source:
            continue

        # Find event card.
        container = link

        for _ in range(8):
            if not container.parent:
                break

            container = container.parent

            text = clean(container.get_text(" ", strip=True))

            if len(text) > 100:
                break

        if not container:
            continue

        text = clean(container.get_text(" ", strip=True))

        # Get the first heading inside the event card.
        title = ""

        for heading in container.find_all(["h2", "h3", "h4"]):

            candidate = clean(
                heading.get_text(" ", strip=True)
            )

            if candidate:
                title = candidate
                break

        if not title:
            continue

        # Try several date formats.
        date_patterns = [
        # 27. 6. – 31. 8. 2026
        r"\d{1,2}\.\s*\d{1,2}\.\s*[-–]\s*"
        r"\d{1,2}\.\s*\d{1,2}\.\s*\d{4}",

        # 4., 11., 18. a 25. září 2026
        r"\d{1,2}\.,(?:\s*\d{1,2}\.,)*\s*"
        r"(?:\d{1,2}\.\s*)?[A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýž]+"
        r"\s+\d{4}",

        # 12. září 2026
        r"\d{1,2}\.\s*"
        r"[A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýž]+"
        r"\s+\d{4}",

        # 12. 9. 2026
        r"\d{1,2}\.\s*\d{1,2}\.\s*\d{4}",
        ]

        date_text = ""

        for pattern in date_patterns:

            match = re.search(pattern, text)

            if match:
                date_text = match.group(0)
                break

        published = parse_date(date_text)

        description = re.sub(
            r"ZJISTIT VÍCE",
            "",
            text,
            flags=re.IGNORECASE,
        )

        description = description.replace(title, "", 1)

        add_item(
            items,
            title,
            href,
            description[:2500],
            published,
        )

    print(f"Svět energie: found {len(items)} items")

    return (
        "Svět energie - Kalendář akcí",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# RSS writer
# ---------------------------------------------------------------------------

def write_feed(
    path: Path,
    title: str,
    source: str,
    items: list[Item],
):
    path.parent.mkdir(parents=True, exist_ok=True)

    # Newest first.
    items = sorted(
        items,
        key=lambda item: (
            item.published
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    rss = Element(
        "rss",
        {"version": "2.0"},
    )

    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = title
    SubElement(channel, "link").text = source
    SubElement(channel, "description").text = (
        "Generated personal RSS feed"
    )
    SubElement(channel, "generator").text = (
        "Personal RSS Feeds"
    )

    for item in items:

        element = SubElement(channel, "item")

        SubElement(element, "title").text = item.title
        SubElement(element, "link").text = item.url

        SubElement(
            element,
            "guid",
            {"isPermaLink": "false"},
        ).text = guid(item.url)

        if item.description:
            SubElement(
                element,
                "description",
            ).text = item.description

        if item.published:
            SubElement(
                element,
                "pubDate",
            ).text = format_datetime(item.published)

    ElementTree(rss).write(
        path,
        encoding="utf-8",
        xml_declaration=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    output = Path("docs/feeds")

    parsers = [
        ("mzv", parse_mzv),
        ("tmbk", parse_tmbk),
        ("skalni-mlyn", parse_skalni_mlyn),
        ("svet-energie", parse_svet_energie),
    ]

    for name, parser in parsers:

        print()
        print("=" * 60)
        print(name)
        print("=" * 60)

        try:

            title, source, items = parser()

            path = output / f"{name}.xml"

            write_feed(
                path,
                title,
                source,
                items,
            )

            print(f"Written: {path}")
            print(f"Items: {len(items)}")

        except Exception as error:

            print(
                f"ERROR while processing {name}: "
                f"{type(error).__name__}: {error}"
            )

            raise


if __name__ == "__main__":
    main()
