import codecs
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from PIL import Image

from ea import raw
from ea.store import DATA, Connection, connect

INBOX = DATA / "inbox" / "uploads"
PAGES = DATA / "raw" / "contrib" / "pages"
PAGES_LIMIT = 1_000
PIXELS_LIMIT = 100_000_000
DPI = 150
SIDE = 3_000
PAGE_SECONDS = 60
FILE_SECONDS = 15 * 60
MEMORY = 2 * 1024**3
UNPACKED_LIMIT = 2 * 1024**3
RATIO_LIMIT = 100
ENTRIES_LIMIT = 10_000
INFLATED_LIMIT = 256 * 1024**2
ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA)\b")
ESCAPED = re.compile(rb"#([0-9A-Fa-f]{2})")
STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')

Image.MAX_IMAGE_PIXELS = PIXELS_LIMIT


class Rejected(Exception):
    pass


def _zip(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise Rejected("the zip file is damaged") from error
    entries = archive.infolist()
    unpacked = sum(entry.file_size for entry in entries)
    packed = sum(entry.compress_size for entry in entries) or 1
    if len(entries) > ENTRIES_LIMIT or unpacked > UNPACKED_LIMIT or unpacked / packed > RATIO_LIMIT:
        raise Rejected("the zip file unpacks to more than the archive accepts")
    return archive


def _sniff(path: Path) -> tuple[str, str, str]:
    with path.open("rb") as file:
        head = file.read(65_536)
    if head.startswith(b"%PDF-"):
        return "application/pdf", ".pdf", "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png", "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg", "image"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff", ".tif", "image"
    if head.startswith(b"SQLite format 3\x00"):
        return "application/geopackage+sqlite3", ".gpkg", "data"
    if head.startswith(b"PAR1"):
        return "application/vnd.apache.parquet", ".parquet", "data"
    if head.startswith(b"PK\x03\x04"):
        with _zip(path) as archive:
            names = archive.namelist()
            if "mimetype" in names and archive.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet":
                return "application/vnd.oasis.opendocument.spreadsheet", ".ods", "data"
            if "xl/workbook.xml" in names:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", "data"
            if any(name.lower().endswith(".shp") for name in names):
                return "application/zip", ".zip", "data"
        raise Rejected("the zip file is not a spreadsheet or a zipped shapefile")
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head, final=len(head) < 65_536)
    except UnicodeDecodeError as error:
        raise Rejected("the file type is not one the archive accepts") from error
    if "\x00" in text:
        raise Rejected("the file type is not one the archive accepts")
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("{") and '"type"' in stripped:
        return "application/geo+json", ".geojson", "data"
    if "," in text or "\t" in text or ";" in text:
        return "text/csv", ".csv", "data"
    raise Rejected("the file type is not one the archive accepts")


def _active(content: bytes) -> bool:
    return ACTIVE.search(content) is not None or (
        b"#" in content and ACTIVE.search(ESCAPED.sub(lambda match: bytes([int(match[1], 16)]), content)) is not None
    )


def _inert(content: bytes) -> None:
    if _active(content):
        raise Rejected("the PDF has scripts, launch actions, or embedded files")
    inflated = 0
    for match in STREAM.finditer(content):
        decoder = zlib.decompressobj()
        try:
            chunk = decoder.decompress(match.group(1), INFLATED_LIMIT - inflated)
        except zlib.error:
            continue
        inflated += len(chunk)
        if _active(chunk):
            raise Rejected("the PDF has scripts, launch actions, or embedded files")
        if inflated >= INFLATED_LIMIT:
            raise Rejected("the PDF's compressed streams unpack to more than the archive accepts")


def _run(args: list[str], deadline: float) -> str:
    seconds = int(min(PAGE_SECONDS, deadline - time.monotonic()))
    if seconds < 1:
        raise Rejected("rendering took longer than 15 minutes")
    try:
        done = subprocess.run(
            ["prlimit", f"--as={MEMORY}", "--", "timeout", str(seconds), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise Rejected(f"{args[0]} could not run") from error
    if done.returncode == 124:
        raise Rejected("a page took longer than a minute to render")
    if done.returncode != 0:
        raise Rejected(f"{args[0]} could not read the file")
    return done.stdout


def _count(path: Path, deadline: float) -> int:
    info = _run(["pdfinfo", str(path)], deadline)
    found = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    if found is None:
        raise Rejected("the PDF has no pages")
    count = int(found.group(1))
    if count < 1 or count > PAGES_LIMIT:
        raise Rejected(f"the file has more than {PAGES_LIMIT:,} pages")
    boxes = _run(["pdfinfo", "-f", "1", "-l", str(count), str(path)], deadline)
    sizes = [
        (float(w), float(h)) for w, h in re.findall(r"^Page\s+\d+ size:\s+([\d.]+) x ([\d.]+)", boxes, re.MULTILINE)
    ]
    if len(sizes) != count:
        raise Rejected("the PDF's page sizes could not be read")
    for width, height in sizes:
        if width / 72 * DPI * height / 72 * DPI > PIXELS_LIMIT:
            raise Rejected("a page is larger than 100 megapixels")
    return count


def _words(path: Path, n: int, deadline: float) -> list[dict[str, object]]:
    layout = _run(["pdftotext", "-bbox", "-f", str(n), "-l", str(n), str(path), "-"], deadline)
    page = re.search(r'<page width="([\d.]+)" height="([\d.]+)">', layout)
    if page is None:
        return []
    width, height = float(page.group(1)), float(page.group(2))
    return [
        {
            "text": html.unescape(text),
            "box": [
                round(float(x0) / width, 5),
                round(float(y0) / height, 5),
                round(float(x1) / width, 5),
                round(float(y1) / height, 5),
            ],
        }
        for x0, y0, x1, y1, text in WORD.findall(layout)
    ]


def _pdf(path: Path, folder: Path) -> Iterator[tuple[int, Path, bool]]:
    deadline = time.monotonic() + FILE_SECONDS
    _inert(path.read_bytes())
    for n in range(1, _count(path, deadline) + 1):
        target = folder / str(n)
        _run(
            ["pdftoppm", "-r", str(DPI), "-png", "-f", str(n), "-l", str(n), "-singlefile", str(path), str(target)],
            deadline,
        )
        words = _words(path, n, deadline)
        if words:
            (folder / f"{n}.json").write_text(json.dumps(words, ensure_ascii=False))
            os.chmod(folder / f"{n}.json", 0o644)
        yield n, folder / f"{n}.png", bool(words)


def _shapes(path: Path, folder: Path) -> None:
    try:
        done = subprocess.run(
            [
                "prlimit",
                f"--as={MEMORY}",
                "--",
                "timeout",
                str(FILE_SECONDS),
                sys.executable,
                "-I",
                "-m",
                "ea.boundaries",
                str(path),
                str(folder),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise Rejected("the boundary file could not be read") from error
    if done.returncode == 124:
        raise Rejected("reading the boundaries took longer than 15 minutes")
    if done.returncode == 3:
        raise Rejected(done.stderr.strip().splitlines()[-1])
    if done.returncode != 0:
        raise Rejected("the boundary file could not be read")
    for name in ("shapes.json", "preview.geojson"):
        os.chmod(folder / name, 0o644)


def _image(path: Path, folder: Path) -> Iterator[tuple[int, Path, bool]]:
    try:
        with Image.open(path) as image:
            frames = getattr(image, "n_frames", 1)
            if frames > PAGES_LIMIT:
                raise Rejected(f"the file has more than {PAGES_LIMIT:,} pages")
            for n in range(1, frames + 1):
                image.seek(n - 1)
                if image.width * image.height > PIXELS_LIMIT:
                    raise Rejected("an image is larger than 100 megapixels")
                page = image.convert("RGB")
                page.thumbnail((SIDE, SIDE))
                target = folder / f"{n}.png"
                page.save(target, "PNG")
                yield n, target, False
    except Image.DecompressionBombError, OSError, SyntaxError, EOFError:
        raise Rejected("the image could not be read") from None


def _claim(conn: Connection) -> tuple[UUID, str, str] | None:
    return conn.execute(
        """select id, sha256, filename from upload where status = 'received'
           order by created_at for update skip locked limit 1"""
    ).fetchone()


def _reject(conn: Connection, id: UUID, reason: str) -> None:
    conn.execute("update upload set status = 'rejected', error = %s where id = %s", [reason, id])


def process() -> bool:
    with connect() as conn:
        claimed = _claim(conn)
        if claimed is None:
            return False
        id, sha256, _ = claimed
        inbox = INBOX / str(id)
        try:
            _ready(conn, id, sha256, inbox)
        except Rejected as error:
            _reject(conn, id, str(error))
        except Exception as error:  # noqa: BLE001 - a broken upload is rejected and the worker carries on
            _reject(conn, id, f"the upload could not be processed ({type(error).__name__})")
        finally:
            inbox.unlink(missing_ok=True)
    print(f"upload {id} processed", flush=True)
    return True


def _ready(conn: Connection, id: UUID, sha256: str, inbox: Path) -> None:
    if not inbox.is_file():
        raise Rejected("the uploaded file is missing")
    done = conn.execute(
        "select id, media_type, storage_key, pages from upload where sha256 = %s and status = 'ready' and id <> %s limit 1",
        [sha256, id],
    ).fetchone()
    if done is not None:
        conn.execute(
            "update upload set status = 'ready', media_type = %s, storage_key = %s, pages = %s where id = %s",
            [done[1], done[2], done[3], id],
        )
        conn.execute(
            """insert into upload_page (upload_id, page_number, image_key, width, height, text_layer)
               select %s, page_number, image_key, width, height, text_layer from upload_page where upload_id = %s""",
            [id, done[0]],
        )
        return
    media_type, suffix, kind = _sniff(inbox)
    folder = PAGES / sha256
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    rows: list[tuple[int, str, int, int, bool]] = []
    try:
        if kind != "data":
            for n, image, text in (_pdf if kind == "pdf" else _image)(inbox, folder):
                os.chmod(image, 0o644)
                with Image.open(image) as rendered:
                    rows.append((n, f"raw/contrib/pages/{sha256}/{n}.png", rendered.width, rendered.height, text))
        elif suffix in (".geojson", ".zip", ".gpkg"):
            _shapes(inbox, folder)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    artifact = raw.store(
        "contrib",
        f"upload:{sha256}",
        inbox.read_bytes(),
        suffix=suffix,
        retrieved_at=datetime.now(UTC),
        media_type=media_type,
    )
    os.chmod(DATA / artifact.storage_key, 0o644)
    if artifact.sha256 != sha256:
        raise Rejected("the stored file does not match its checksum")
    conn.execute(
        "update upload set status = 'ready', media_type = %s, storage_key = %s, pages = %s where id = %s",
        [media_type, artifact.storage_key, len(rows) or None, id],
    )
    for n, key, width, height, text in rows:
        conn.execute(
            """insert into upload_page (upload_id, page_number, image_key, width, height, text_layer)
               values (%s, %s, %s, %s, %s, %s)""",
            [id, n, key, width, height, text],
        )


def sweep() -> None:
    with connect() as conn:
        conn.execute(
            """update upload set status = 'rejected', error = 'the upload was not processed within an hour'
               where status = 'received' and created_at < now() - interval '1 hour'"""
        )
        known = {str(id) for (id,) in conn.execute("select id from upload where status = 'received'").fetchall()}
    if not INBOX.is_dir():
        return
    cutoff = time.time() - 3_600
    for path in INBOX.iterdir():
        if path.name.split(".")[0] not in known and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
