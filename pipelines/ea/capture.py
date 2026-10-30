import ipaddress
import mimetypes
import socket
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx

from ea import raw
from ea.store import Connection

URL_LIMIT = 2_048
BYTES_LIMIT = 50 * 1024 * 1024
ROBOTS_LIMIT = 512 * 1024
DEADLINE = 60.0
HOPS = 5
PORTS = {"http": 80, "https": 443}
COLUMNS = ("url", "owner_user_id", "status", "error", "final_url", "raw_sha256", "raw_key", "media_type", "bytes")


class Blocked(Exception):
    pass


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    media_type: str | None
    content: bytes


def _address(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise Blocked(f"{host} does not resolve") from error
    addresses = {str(info[4][0]) for info in infos}
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast:
            raise Blocked(f"{host} resolves to a non-public address")
    return min(addresses, key=lambda address: ":" in address)


def _get(url: str, limit: int, deadline: float) -> Fetched:
    parts = urlsplit(url)
    if parts.scheme not in PORTS:
        raise Blocked("only http and https URLs can be archived")
    if parts.username is not None or parts.password is not None:
        raise Blocked("URLs with credentials can't be archived")
    host = parts.hostname
    if host is None:
        raise Blocked("the URL has no host")
    port = parts.port or PORTS[parts.scheme]
    if port != PORTS[parts.scheme]:
        raise Blocked("only the default http and https ports can be archived")
    address = _address(host, port)
    pinned = f"[{address}]" if ":" in address else address
    target = parts._replace(netloc=f"{pinned}:{port}").geturl()
    with (
        httpx.Client(timeout=httpx.Timeout(min(30.0, max(1.0, deadline - time.monotonic())))) as client,
        client.stream(
            "GET",
            target,
            headers={"Host": parts.netloc, "User-Agent": raw.USER_AGENT},
            extensions={"sni_hostname": host},
        ) as response,
    ):
        chunks: list[bytes] = []
        size = 0
        if not response.is_redirect:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise Blocked(f"the file is larger than {limit // (1024 * 1024)} MB")
                if time.monotonic() > deadline:
                    raise Blocked("the download took longer than a minute")
                chunks.append(chunk)
        location = response.headers.get("location") if response.is_redirect else None
        return Fetched(
            url=urljoin(url, location) if location is not None else url,
            status=response.status_code,
            media_type=response.headers.get("content-type"),
            content=b"".join(chunks),
        )


def _follow(url: str, limit: int, deadline: float) -> Fetched:
    for _ in range(HOPS + 1):
        if len(url) > URL_LIMIT:
            raise Blocked(f"the URL is longer than {URL_LIMIT:,} characters")
        fetched = _get(url, limit, deadline)
        if fetched.url == url:
            return fetched
        url = fetched.url
    raise Blocked(f"the URL redirects more than {HOPS} times")


def _allowed(url: str, deadline: float) -> None:
    parts = urlsplit(url)
    try:
        fetched = _follow(f"{parts.scheme}://{parts.netloc}/robots.txt", ROBOTS_LIMIT, deadline)
    except httpx.HTTPError as error:
        raise Blocked(f"robots.txt could not be read ({type(error).__name__})") from error
    parser = urllib.robotparser.RobotFileParser()
    if fetched.status in (404, 410):
        parser.parse([])
    elif fetched.status == 200:
        text = fetched.content.decode("utf-8", "replace")
        parser.parse([] if "<html" in text[:500].lower() else text.splitlines())
    else:
        raise Blocked(f"robots.txt answered {fetched.status}")
    if not parser.can_fetch(raw.USER_AGENT, url):
        raise Blocked("robots.txt disallows archiving this URL")


_last = 0.0


def capture(conn: Connection, url: str, owner: UUID | None) -> tuple[UUID, str, str | None]:
    global _last
    time.sleep(max(0.0, 1.0 - (time.monotonic() - _last)))
    deadline = time.monotonic() + DEADLINE
    row: dict[str, object] = dict.fromkeys(COLUMNS) | {"url": url, "owner_user_id": owner}
    try:
        if len(url) > URL_LIMIT:
            raise Blocked(f"the URL is longer than {URL_LIMIT:,} characters")
        _allowed(url, deadline)
        fetched = _follow(url, BYTES_LIMIT, deadline)
        if fetched.status != 200:
            raise Blocked(f"the page answered {fetched.status}")
        media_type = fetched.media_type
        suffix = mimetypes.guess_extension((media_type or "").split(";")[0].strip()) or ".bin"
        artifact = raw.store(
            "contrib",
            url,
            fetched.content,
            suffix=suffix,
            retrieved_at=datetime.now(UTC),
            media_type=media_type or "application/octet-stream",
        )
        row |= {
            "status": "captured",
            "final_url": fetched.url,
            "raw_sha256": artifact.sha256,
            "raw_key": artifact.storage_key,
            "media_type": artifact.media_type,
            "bytes": artifact.bytes,
        }
    except Blocked as error:
        row |= {"status": "blocked", "error": str(error)}
    except httpx.HTTPError as error:
        row |= {"status": "failed", "error": f"the download failed ({type(error).__name__})"}
    finally:
        _last = time.monotonic()
    found = conn.execute(
        """insert into archive_capture
             (url, owner_user_id, status, error, final_url, raw_sha256, raw_key, media_type, bytes)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        [row[column] for column in COLUMNS],
    ).fetchone()
    if found is None:
        raise RuntimeError("the archive_capture insert returned no row")
    return found[0], str(row["status"]), None if row["error"] is None else str(row["error"])
