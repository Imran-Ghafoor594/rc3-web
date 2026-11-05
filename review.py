"""Integration boundary and fail-closed action controller for a single review."""
import json
import os
import re
from pathlib import Path

import httpx

PROMPT = Path(__file__).with_name("prompts").joinpath("review.txt").read_text()
LIMIT = 60000
CONTEXT = "rc3/criteria-review"


class Stop(ValueError):
    """Safe user-facing failure."""


def setting(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise Stop(f"Configure {name} on the webhook service.")
    return value


def request(method, url, service, **kwargs):
    try:
        r = httpx.request(method, url, timeout=35, **kwargs)
        r.raise_for_status()
        return r
    except httpx.HTTPStatusError as exc:
        raise Stop(f"{service} returned HTTP {exc.response.status_code}.") from None
    except httpx.HTTPError:
        raise Stop(f"{service} connection failed or timed out.") from None


def ticket_key(title, branch):
    keys = set(re.findall(r"(?<![A-Z0-9])[A-Z][A-Z0-9_]+-[1-9][0-9]*(?![0-9])", title+" "+branch))
    if len(keys) != 1:
        raise Stop("Conflicting Jira keys: "+", ".join(sorted(keys)) if keys else "No Jira key in title or branch.")
    return keys.pop()


def rich(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if value.get("type") == "text":
            return value.get("text", "")
        sep = "" if value.get("type") == "paragraph" else "\n"
        return sep.join(filter(None, (rich(v) for v in value.get("content", [])))).strip()
    return ""


def verdict(raw):
    def unique(pairs):
        if len(set(k for k, _ in pairs)) != len(pairs):
            raise Stop("Duplicate LLM JSON keys.")
        return dict(pairs)
    try:
        data = json.loads(raw, object_pairs_hook=unique)
    except (TypeError, json.JSONDecodeError):
        raise Stop("LLM response is not valid JSON.") from None
    if (type(data) is not dict or set(data) != {"fulfilled", "feedback"} or
        type(data["fulfilled"]) is not bool or type(data["feedback"]) is not str or
        not 0 < len(data["feedback"].strip()) <= 12000):
        raise Stop("Invalid verdict schema: expected Boolean fulfilled and nonempty string feedback.")
    return data


class Services:
    def __init__(self, repo, number):
        self.base = f"https://api.github.com/repos/{repo}"
        self.pr = f"/pulls/{number}"

    def github(self, method, path, **kwargs):
        headers = {"Authorization": "Bearer "+setting("GH_REVIEW_TOKEN"),
                   "Accept": kwargs.pop("accept", "application/vnd.github+json")}
        return request(method, self.base+path, "GitHub", headers=headers, **kwargs)

    def current(self):
        return self.github("GET", self.pr).json()

    def ticket(self, key):
        field = setting("JIRA_AC_FIELD")
        r = request("GET", setting("JIRA_URL").rstrip("/")+"/rest/api/3/issue/"+key, "Jira",
                    auth=(setting("JIRA_EMAIL"), setting("JIRA_TOKEN")),
                    params={"fields": ",".join(["summary", "description", field])})
        f = r.json()["fields"]
        return {"key": key, "title": rich(f.get("summary")), "description": rich(f.get("description")),
                "criteria": rich(f.get(field))}

    def diff(self):
        headers = {"Authorization":"Bearer "+setting("GH_REVIEW_TOKEN"), "Accept":"application/vnd.github.diff"}
        try:
            with httpx.stream("GET", self.base+self.pr, headers=headers, timeout=35) as response:
                if response.status_code != 200:
                    raise Stop(f"GitHub diff returned HTTP {response.status_code}.")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(data)+len(chunk)>LIMIT:
                        raise Stop(f"Diff exceeds {LIMIT} bytes.")
                    data.extend(chunk)
                return bytes(data)
        except httpx.HTTPError:
            raise Stop("GitHub diff connection failed or timed out.") from None

    def evaluate(self, ticket, diff):
        payload = {"model": setting("LLM_MODEL"), "response_format": {"type": "json_object"},
                   "messages": [{"role":"system","content":PROMPT},
                                {"role":"user","content":json.dumps({"ticket":ticket,"diff":diff})}]}
        r = request("POST", setting("LLM_BASE_URL").rstrip("/")+"/chat/completions", "LLM",
                    headers={"Authorization":"Bearer "+setting("LLM_KEY")}, json=payload)
        return r.json()["choices"][0]["message"]["content"]

    def status(self, sha, state):
        self.github("POST", "/statuses/"+sha, json={"state":state, "context":CONTEXT,
                    "description":{"failure":"Criteria missing or review stopped", "pending":"Review in progress",
                                   "success":"All criteria satisfied"}[state]})

    def comment(self, sha, feedback):
        self.github("POST", self.pr+"/reviews", json={"commit_id":sha,"event":"COMMENT","body":feedback})

    def merge(self, sha):
        r = self.github("PUT", self.pr+"/merge", json={"sha":sha,"merge_method":"merge"})
        if r.json().get("merged") is not True:
            raise Stop("GitHub did not confirm the merge.")


def run_review(job, svc=None, *, policy=None):
    sha = job["sha"]
    live, target, branch = policy if policy is not None else (
        os.getenv("LIVE_MODE") == "true", os.getenv("TEST_REPO",""), os.getenv("TARGET_BRANCH",""))
    out = {**job,"mode":"Live" if live else "Dry run","ticket":{}, "verdict":None, "feedback":"",
           "action":"Stopped","effects":[]}
    safe_target = False
    svc = svc or Services(job["repo"], job["number"])

    def current():
        p = svc.current()
        if (p["state"] != "open" or p.get("draft") or p["head"]["sha"] != sha or
            p["title"] != job["title"] or p["head"]["ref"] != job["branch"] or
            p["base"]["ref"] != job["base"] or p["base"]["sha"] != job["base_sha"]):
            raise Stop("PR head, base, title, branch or open/ready state changed. Submit a fresh event.")

    try:
        if job["repo"].lower() != target.lower() or job["base"] != branch:
            raise Stop("Outside the configured test repository or target branch.")
        current()
        safe_target = True
        if live:
            svc.status(sha, "pending")
            out["effects"].append("Pending status posted")
        key = ticket_key(job["title"], job["branch"])
        out["ticket"] = {"key":key}
        ticket = out["ticket"] = svc.ticket(key)
        if not ticket["criteria"].strip():
            raise Stop("Ticket has no acceptance criteria in the configured field.")
        diff = svc.diff()
        if not diff.strip() or len(diff) > LIMIT:
            raise Stop(f"Empty or oversized diff ({len(diff)} bytes; maximum {LIMIT}).")
        if not diff.startswith(b"diff --git ") or b"@@ " not in diff:
            raise Stop("Diff has no reviewable text hunks.")
        if any(line.startswith((b"Binary files ", b"GIT binary patch")) for line in diff.splitlines()):
            raise Stop("Binary changes cannot be verified by this text reviewer.")
        current()
        decision = verdict(svc.evaluate(ticket, diff.decode("utf-8")))
        out.update(verdict=decision["fulfilled"], feedback=decision["feedback"])
        current()
        if not live:
            out["action"] = "Would merge reviewed commit" if decision["fulfilled"] else "Would comment and fail"
        elif decision["fulfilled"]:
            svc.status(sha, "success")
            out["effects"].append("Success status posted")
            svc.merge(sha)
            out["action"] = "Merged reviewed commit"
        else:
            svc.status(sha, "failure")
            out["effects"].append("Failed status posted")
            svc.comment(sha, decision["feedback"])
            out["action"] = "Failed with PR comment"
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Stop) else "Unexpected or unsupported integration response."
        out["reason"] = reason
        if live and safe_target and "Failed status posted" not in out["effects"]:
            try:
                svc.status(sha, "failure")
                out["effects"].append("Failed status posted")
            except Exception:
                out["effects"].append("Failure status could not be confirmed")
        if live and "Success status posted" in out["effects"]:
            out["action"] = "Merge outcome unconfirmed. Inspect GitHub."
    return out
