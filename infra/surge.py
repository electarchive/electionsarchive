import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ZONE = "0833a358881098a233459fa820d8cc06"
RULESET = "8a41ad8f0bb8470ba56ca9cd47d1e431"
RULE = "dd1698335fe14d84acf0b46ed73765b7"
TOKEN = Path.home() / ".config/ea/cf-surge-token"
STATE = Path.home() / ".local/state/ea-surge.json"
TAIL = 24_000_000
RECENT = 180
CALM = 600
VOLUME = 60
TRIP = 0.15
CLEAR = 0.03
HOLD = 2 * 3600
LONGEST = 24 * 3600
RELAPSE = 3600


def api(method: str, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/zones/{ZONE}/rulesets/{RULESET}{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN.read_text().strip()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        reply = json.load(response)
    if not reply.get("success"):
        raise RuntimeError(f"Cloudflare refused {method} {path}: {reply.get('errors')}")
    return reply["result"]


def rule() -> dict[str, object]:
    rules = api("GET", "").get("rules")
    for entry in rules if isinstance(rules, list) else []:
        if isinstance(entry, dict) and entry.get("id") == RULE:
            return entry
    raise RuntimeError(f"rule {RULE} is missing from ruleset {RULESET}")


def toggle(current: dict[str, object], enabled: bool) -> None:
    body = {key: current[key] for key in ("action", "expression", "description") if key in current}
    api("PATCH", f"/rules/{RULE}", {**body, "enabled": enabled})


def shares(now: float) -> tuple[tuple[int, int], tuple[int, int]]:
    log = subprocess.run(
        ["docker", "exec", "ea-caddy", "tail", "-c", str(TAIL), "/data/access.log"],
        capture_output=True,
        check=True,
    ).stdout
    recent = [0, 0]
    calm = [0, 0]
    for line in log.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        age = now - entry.get("ts", 0)
        if age > CALM or entry.get("request", {}).get("uri", "").startswith("/_app/"):
            continue
        shed = 1 if entry.get("status") == 503 else 0
        calm[0] += 1
        calm[1] += shed
        if age <= RECENT:
            recent[0] += 1
            recent[1] += shed
    return (recent[0], recent[1]), (calm[0], calm[1])


def load() -> dict[str, float]:
    try:
        text = STATE.read_text()
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {}


def save(state: dict[str, float]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def main() -> int:
    now = time.time()
    state = load()
    current = rule()
    enabled = current.get("enabled") is True
    (seen, shed), (calm_seen, calm_shed) = shares(now)
    share = shed / seen if seen > 0 else 0.0
    calm_share = calm_shed / calm_seen if calm_seen > 0 else 0.0
    line = f"503 share {share:.0%} of {seen} in {RECENT}s, {calm_share:.0%} of {calm_seen} in {CALM}s"
    if enabled and "since" not in state:
        state = {**state, "since": now, "hold": state.get("hold", HOLD)}
    if not enabled and seen >= VOLUME and share >= TRIP:
        relapsed = now - state.get("off", 0) < RELAPSE
        hold = min(state.get("hold", HOLD) * 2, LONGEST) if relapsed else HOLD
        toggle(current, True)
        state = {"since": now, "hold": hold, "off": state.get("off", 0)}
        print(f"surge on: {line}; holding {hold / 3600:g} h", flush=True)
    elif enabled and now - state["since"] >= state["hold"] and calm_share < CLEAR:
        toggle(current, False)
        print(f"surge off after {(now - state['since']) / 3600:.1f} h: {line}", flush=True)
        state = {"off": now, "hold": state["hold"]}
    else:
        print(f"surge {'on' if enabled else 'off'}: {line}", flush=True)
    save(state)
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main())
