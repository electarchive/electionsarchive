import fcntl
import json
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

import anthropic
import httpx
from anthropic.types import Message
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages import MessageBatchIndividualResponse
from anthropic.types.messages.batch_create_params import Request

from ea import raw
from ea.store import DATA

ROOT = DATA / "batch"
JOBS = ROOT / "jobs"
ACCOUNTS = ROOT / "accounts.json"
APPROVALS = ROOT / "approvals.json"
PROXY_KEY = "anthropic-batch-proxy"
PRICES = {"claude-haiku-5-5": (0.05, 0.25), "claude-sonnet-5-5": (1.0, 5.0), "claude-opus-5-5": (5.0, 25.0)}
MAX_REQUESTS = 100_000
MAX_BYTES = 200_000_000
MARGIN = 0.95
ATTEMPTS = 3

type Want = tuple[MessageCreateParamsNonStreaming, float]


class Sent(TypedDict):
    id: str
    account: str
    at: float
    keys: dict[str, str]
    estimate: float
    spent: NotRequired[float]
    ended: NotRequired[float]


class Job(TypedDict):
    source: str
    name: str
    batches: list[Sent]
    attempts: dict[str, int]
    failed: dict[str, str]


class Account(TypedDict):
    usd: float
    since: float
    billed: NotRequired[float]
    billed_at: NotRequired[float]


def client(account: str) -> anthropic.Anthropic:
    if os.environ.get("ANTHROPIC_API_KEY") != PROXY_KEY:
        raise SystemExit("BLOCKED: run through the proxy: anthropic-batch uv run ea ...")
    return anthropic.Anthropic(default_headers={"x-batch-account": account}, max_retries=6)


def path(source: str, name: str) -> Path:
    return JOBS / source / f"{name}.json"


@contextmanager
def opened(source: str, name: str) -> Generator[Job]:
    file = path(source, name)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job: Job = (
            json.loads(file.read_text())
            if file.exists()
            else {"source": source, "name": name, "batches": [], "attempts": {}, "failed": {}}
        )
        yield job
        partial = file.with_suffix(".part")
        partial.write_text(json.dumps(job, indent=1))
        partial.replace(file)


def jobs(prefix: str = "") -> list[Job]:
    out: list[Job] = []
    for file in sorted(JOBS.glob("**/*.json")):
        job: Job = json.loads(file.read_text())
        if f"{job['source']}/{job['name']}/".startswith(f"{prefix}/" if prefix else ""):
            out.append(job)
    return out


def cost(sent: Sent) -> float:
    return sent.get("spent", sent["estimate"])


def dollars(message: Message) -> float:
    price_in, price_out = PRICES[message.model]
    usage = message.usage
    prompt = (
        usage.input_tokens
        + (usage.cache_creation_input_tokens or 0) * 1.25
        + (usage.cache_read_input_tokens or 0) * 0.1
    )
    return (prompt * price_in + usage.output_tokens * price_out) / 1e6


def accounts() -> dict[str, Account]:
    return json.loads(ACCOUNTS.read_text()) if ACCOUNTS.exists() else {}


def committed(name: str, account: Account) -> float:
    ledger = sum(
        cost(sent)
        for job in jobs()
        for sent in job["batches"]
        if sent["account"] == name and sent["at"] >= account["since"]
    )
    return max(ledger, account.get("billed", 0.0))


def approvals() -> dict[str, float]:
    return json.loads(APPROVALS.read_text()) if APPROVALS.exists() else {}


def allowance(source: str, name: str) -> tuple[str, float] | None:
    full = f"{source}/{name}/"
    granted = [prefix for prefix in approvals() if full.startswith(f"{prefix}/")]
    if not granted:
        return None
    prefix = max(granted, key=len)
    used = sum(cost(sent) for job in jobs(prefix) for sent in job["batches"])
    return prefix, approvals()[prefix] - used


def collect(job: Job) -> list[str]:
    notes: list[str] = []
    for sent in job["batches"]:
        if "ended" in sent:
            continue
        api = client(sent["account"])
        status = api.messages.batches.retrieve(sent["id"])
        if status.processing_status != "ended":
            counts = status.request_counts
            notes.append(f"{sent['id'][-8:]} {status.processing_status}, {counts.processing} processing")
            continue
        spent = 0.0
        for line in api.get(
            f"/v1/messages/batches/{sent['id']}/results",
            cast_to=str,
            options={"headers": {"Accept": "application/binary"}},
        ).splitlines():
            result = MessageBatchIndividualResponse.model_validate_json(line)
            key = sent["keys"][result.custom_id]
            if result.result.type == "succeeded":
                message = result.result.message
                spent += dollars(message)
                raw.store(
                    job["source"],
                    key,
                    message.model_dump_json().encode(),
                    suffix=".json",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/json",
                )
                job["failed"].pop(key, None)
                continue
            job["attempts"][key] = job["attempts"].get(key, 0) + 1
            job["failed"][key] = (
                result.result.error.error.message[:300] if result.result.type == "errored" else result.result.type
            )
        sent["spent"] = spent
        sent["ended"] = time.time()
        notes.append(f"{sent['id'][-8:]} ended, ${spent:.3f}")
    return notes


def pick(projected: float) -> str:
    room: list[tuple[float, str]] = []
    for name, account in accounts().items():
        left = account["usd"] * MARGIN - committed(name, account)
        if left >= projected:
            room.append((left, name))
    if not room:
        raise SystemExit(f"BLOCKED: budget; no account has ${projected:.2f} left (ea batch status)")
    return min(room)[1]


def ensure(source: str, name: str, wanted: dict[str, Want], *, approved: float | None = None) -> tuple[bool, str]:
    with opened(source, name) as job:
        notes = collect(job)
        if any("ended" not in sent for sent in job["batches"]):
            return False, f"waiting for batch:{source}/{name} ({'; '.join(notes)})"
        held = raw.keys(source)
        todo = {key: want for key, want in wanted.items() if key not in held and job["attempts"].get(key, 0) < ATTEMPTS}
        gave_up = [key for key in wanted if key not in held and key not in todo]
        if not todo:
            done = f"{len(wanted) - len(gave_up)} of {len(wanted)} read"
            return True, "; ".join(
                [done, *notes, *(f"gave up on {key}: {job['failed'].get(key, '')}" for key in gave_up)]
            )
        projected = sum(estimate for _, estimate in todo.values())
        grant = ("", approved) if approved is not None else allowance(source, name)
        if grant is None or grant[1] < projected:
            have = "nothing approved" if grant is None else f"${grant[1]:.2f} left of the approval for {grant[0]}"
            raise SystemExit(
                f"BLOCKED: decision; {len(todo)} requests for {source}/{name}, about ${projected:.2f}, {have}; "
                f"approve it with `ea batch approve {source}/{name} <usd>`"
            )
        account = pick(projected)
        api = client(account)
        groups: list[list[tuple[str, Want]]] = [[]]
        size = 0
        for key, want in todo.items():
            weight = len(json.dumps(want[0]))
            if groups[-1] and (size + weight > MAX_BYTES or len(groups[-1]) >= MAX_REQUESTS):
                groups.append([])
                size = 0
            groups[-1].append((key, want))
            size += weight
        for group in groups:
            requests = [Request(custom_id=f"r{index}", params=want[0]) for index, (_, want) in enumerate(group)]
            created = api.messages.batches.create(requests=requests)
            job["batches"].append(
                {
                    "id": created.id,
                    "account": account,
                    "at": time.time(),
                    "keys": {f"r{index}": key for index, (key, _) in enumerate(group)},
                    "estimate": sum(want[1] for _, want in group),
                }
            )
        return False, (
            f"submitted {len(todo)} requests (about ${projected:.2f}, account {account}) as batch:{source}/{name}; "
            f"return the next step as a unit with depends_on batch:{source}/{name}"
        )


def poll() -> str:
    notes: list[str] = []
    for job in jobs():
        if all("ended" in sent for sent in job["batches"]):
            continue
        with opened(job["source"], job["name"]) as live:
            found = collect(live)
        notes.append(f"{job['source']}/{job['name']}: {'; '.join(found)}")
    return "\n".join(notes) or "no open batches"


def ended(job: str) -> bool:
    source, _, name = job.partition("/")
    file = path(source, name)
    return file.exists() and all("ended" in sent for sent in json.loads(file.read_text())["batches"])


def cancel(job: str) -> str:
    source, _, name = job.partition("/")
    with opened(source, name) as live:
        for sent in live["batches"]:
            if "ended" not in sent:
                client(sent["account"]).messages.batches.cancel(sent["id"])
        notes = collect(live)
    return "; ".join(notes) or "nothing open"


def approve(prefix: str, usd: float) -> str:
    ROOT.mkdir(parents=True, exist_ok=True)
    granted = approvals()
    granted[prefix] = usd
    APPROVALS.write_text(json.dumps(granted, indent=1))
    return f"approved ${usd:.2f} for {prefix}"


def account(name: str, usd: float) -> str:
    ROOT.mkdir(parents=True, exist_ok=True)
    books = accounts()
    books[name] = {"usd": usd, "since": time.time()}
    ACCOUNTS.write_text(json.dumps(books, indent=1))
    return f"account {name}: ${usd:.2f} from now"


def billed() -> str:
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if base is None or os.environ.get("ANTHROPIC_API_KEY") != PROXY_KEY:
        raise SystemExit("BLOCKED: run through the proxy: anthropic-batch uv run ea batch billed")
    books = accounts()
    lines: list[str] = []
    for name, record in books.items():
        cents = 0.0
        page: str | None = None
        while True:
            params = [
                ("starting_at", datetime.fromtimestamp(record["since"], UTC).strftime("%Y-%m-%dT00:00:00Z")),
                ("limit", "31"),
            ]
            if page is not None:
                params.append(("page", page))
            response = httpx.get(
                f"{base}/v1/organizations/cost_report",
                params=params,
                headers={"x-api-key": PROXY_KEY, "anthropic-version": "2023-06-01", "x-batch-account": name},
                timeout=60,
            )
            if response.status_code == 403:
                lines.append(f"{name}: no admin key stored")
                break
            response.raise_for_status()
            body = response.json()
            for bucket in body["data"]:
                for result in bucket["results"]:
                    cents += float(result["amount"])
            if not body["has_more"]:
                record["billed"] = cents / 100
                record["billed_at"] = time.time()
                lines.append(f"{name}: billed ${cents / 100:.2f} since the cap was set (daily buckets lag by hours)")
                break
            page = body["next_page"]
    ACCOUNTS.write_text(json.dumps(books, indent=1))
    return "\n".join(lines) or "no accounts set"


def status() -> str:
    lines: list[str] = []
    for name, record in accounts().items():
        used = committed(name, record)
        lines.append(f"{name}: ${used:.2f} of ${record['usd']:.2f} (stops at {MARGIN:.0%})")
    for prefix, usd in approvals().items():
        used = sum(cost(sent) for job in jobs(prefix) for sent in job["batches"])
        lines.append(f"approved {prefix}: ${used:.2f} of ${usd:.2f}")
    for job in jobs():
        waiting = [sent for sent in job["batches"] if "ended" not in sent]
        if waiting:
            lines.append(f"open batch:{job['source']}/{job['name']}: {len(waiting)} batches")
    return "\n".join(lines) or "no accounts, approvals, or jobs"
