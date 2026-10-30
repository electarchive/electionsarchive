import heapq
import ipaddress
import mimetypes
import socket
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx
import psycopg

from ea import raw
from ea.store import Connection, connect

URL_LIMIT = 2_048
BYTES_LIMIT = 50 * 1024 * 1024
ROBOTS_LIMIT = 512 * 1024
DEADLINE = 60.0
HOPS = 5
PORTS = {"http": 80, "https": 443}
WAYBACK = "https://web.archive.org"
WAYBACK_DELAYS = (0.0, 60.0, 300.0, 1_800.0, 7_200.0)
WAYBACK_PACE = 5.0
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


def _snapshot(url: str, since: datetime) -> str | None:
    with httpx.Client(timeout=httpx.Timeout(120.0), headers={"User-Agent": raw.USER_AGENT}) as client:
        response = client.get(f"{WAYBACK}/save/{url}")
        for header in ("content-location", "location"):
            found = response.headers.get(header)
            if found is not None and "/web/" in found:
                return urljoin(WAYBACK, found)
        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()
        stamp = since.strftime("%Y%m%d%H%M%S")
        closest = (
            client.get("https://archive.org/wayback/available", params={"url": url, "timestamp": stamp})
            .raise_for_status()
            .json()
            .get("archived_snapshots", {})
            .get("closest")
        )
    if closest is not None and closest.get("available") and str(closest.get("timestamp", "")) >= stamp:
        return str(closest["url"]).replace("http://", "https://", 1)
    return None


_due: list[tuple[float, int, str, str, datetime]] = []
_wake = threading.Condition()
_thread: threading.Thread | None = None


def _wayback() -> None:
    while True:
        with _wake:
            while not _due or _due[0][0] > time.monotonic():
                _wake.wait(None if not _due else _due[0][0] - time.monotonic())
            _, attempt, id, url, since = heapq.heappop(_due)
        try:
            found = _snapshot(url, since)
            if found is not None:
                with connect() as conn:
                    conn.execute("update archive_capture set wayback_url = %s where id = %s", [found, id])
        except httpx.HTTPError, psycopg.Error, ValueError:
            found = None
        if found is None and attempt + 1 < len(WAYBACK_DELAYS):
            _later(id, url, since, attempt + 1)
        time.sleep(WAYBACK_PACE)


def _later(id: str, url: str, since: datetime, attempt: int = 0) -> None:
    global _thread
    with _wake:
        heapq.heappush(_due, (time.monotonic() + WAYBACK_DELAYS[attempt], attempt, id, url, since))
        if _thread is None:
            _thread = threading.Thread(target=_wayback, name="wayback", daemon=True)
            _thread.start()
        _wake.notify()


def resume(conn: Connection) -> None:
    for id, url, since in conn.execute(
        """select id, url, captured_at from archive_capture
           where status = 'captured' and wayback_url is null and captured_at > now() - interval '1 day'
           order by captured_at"""
    ).fetchall():
        _later(str(id), url, since)


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
    if row["status"] == "captured":
        _later(str(found[0]), url, datetime.now(UTC))
    return found[0], str(row["status"]), None if row["error"] is None else str(row["error"])
