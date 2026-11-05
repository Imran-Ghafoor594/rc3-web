"""Bounded public GitHub snapshot analyzer, shared by the CLI and Streamlit."""
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import PurePosixPath as Path
import re
import tomllib
from urllib.parse import quote

import httpx
from packaging.requirements import InvalidRequirement, Requirement


class Unavailable(ValueError):
    pass


def repo_name(url):
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+)/?", url.strip(), re.ASCII)
    if not m or any(p in {".","..",""} for p in m.groups()):
        raise Unavailable("Enter a public repository URL such as https://github.com/owner/repository.")
    name = m[2].removesuffix(".git")
    if name in {"",".",".."}:
        raise Unavailable("Invalid repository name.")
    return m[1]+"/"+name


class PublicRepo:
    def __init__(self, name):
        self.base = "https://api.github.com/repos/"+name
        token = os.getenv("ANALYZER_TOKEN")
        self.client = httpx.Client(headers={"Authorization":"Bearer "+token} if token else {}, timeout=18)

    def get(self, path, **params):
        try:
            r = self.client.get(self.base+path, params=params or None)
            if r.status_code in {403,429}:
                raise Unavailable("GitHub access or rate limit; retry later. Reset (Unix UTC): "+
                                  r.headers.get("X-RateLimit-Reset","unavailable"))
            if r.status_code == 404:
                raise Unavailable("Repository or requested resource was not found or is inaccessible.")
            if r.status_code == 409:
                raise Unavailable("Empty repository or GitHub resource conflict.")
            r.raise_for_status()
            return r.json(), "next" in r.links
        except (httpx.HTTPError, ValueError) as e:
            if isinstance(e, Unavailable):
                raise
            raise Unavailable("GitHub response unavailable ("+type(e).__name__+").") from None


MANIFESTS = {"package.json","requirements.txt","pyproject.toml","Cargo.toml","go.mod","pom.xml","Gemfile"}
SOURCE = {".py",".js",".jsx",".ts",".tsx",".go",".rs",".rb",".java",".c",".cpp",".cs",".php",".swift",".kt"}
IGNORE = {"node_modules","vendor","dist","build",".venv"}
PURPOSE = {"src":"source code","tests":"tests","test":"tests","docs":"documentation",
           ".github":"GitHub configuration","static":"static assets","templates":"templates",
           "models":"model-related files","data":"data files","examples":"examples"}
TOOLS = {"Dockerfile":"Docker","compose.yaml":"Docker Compose","package.json":"npm-compatible tooling",
         "pyproject.toml":"Python packaging/config","Cargo.toml":"Cargo","go.mod":"Go modules",
         "requirements.txt":"pip-compatible requirements","tsconfig.json":"TypeScript compiler"}
FRAMEWORKS = {"react","vue","next","django","flask","fastapi","torch","tensorflow","streamlit","express","pytest","vite"}


def dep_rows(path, source):
    filename = Path(path).name
    rows = []
    def add(name, spec, check):
        rows.append({"package":name,"specifier":spec or "(any)","check":check})
    def py(line):
        try:
            r = Requirement(line)
        except InvalidRequirement:
            add(line,"","Unsupported directive or declaration")
            return
        specs = list(r.specifier)
        exact = len(specs)==1 and specs[0].operator in {"==","==="} and "*" not in specs[0].version
        add(r.name, str(r.specifier) or r.url, "External reference" if r.url else
            "Pinned" if exact else "Ranged" if specs else "Unpinned")
    if filename == "requirements.txt":
        for line in source.splitlines():
            line = line.split(" #",1)[0].strip()
            if line and not line.startswith("#"):
                py(line)
    elif filename == "pyproject.toml":
        data = tomllib.loads(source)
        project = data.get("project",{})
        for dep in project.get("dependencies",[]):
            py(dep)
        for group in project.get("optional-dependencies",{}).values():
            for dep in group:
                py(dep)
        if not rows:
            return rows,"No static PEP 621 dependencies found; other tables are outside this check."
    elif filename == "package.json":
        data = json.loads(source)
        for group in ("dependencies","devDependencies","peerDependencies","optionalDependencies"):
            for name, spec in data.get(group,{}).items():
                if re.fullmatch(r"=?v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?", spec):
                    check = "Pinned"
                elif spec in {"*","latest",""}:
                    check = "Unpinned"
                elif re.fullmatch(r"[~^<>=v\d.xX* |\-]+",spec):
                    check = "Ranged"
                else:
                    check = "Other reference or tag"
                add(name,spec,check)
    else:
        return [],"Manifest detected; version syntax not analyzed."
    return rows,"Direct declarations only."


def activity(api, sha, now):
    end = now.replace(hour=0,minute=0,second=0,microsecond=0)
    end -= timedelta(days=end.weekday())
    start = end-timedelta(weeks=8)
    bins = {(start+timedelta(weeks=i)).date().isoformat():0 for i in range(8)}
    authors = Counter()
    seen = set()
    for page in range(1,21):
        commits, more = api.get("/commits", sha=sha, since=start.isoformat(),
                                until=(end-timedelta(seconds=1)).isoformat(), per_page=100,page=page)
        for c in commits:
            if c["sha"] in seen:
                continue
            seen.add(c["sha"])
            date = datetime.fromisoformat(c["commit"]["committer"]["date"].replace("Z","+00:00"))
            if start <= date < end:
                bins[(date.date()-timedelta(days=date.weekday())).isoformat()] += 1
                authors[(c.get("author") or {}).get("login") or c["commit"]["author"]["name"]] += 1
        if not more:
            return {"window":f"{start.date()} to {end.date()} (end exclusive), UTC; committer dates",
                    "weekly":bins,"top authors":dict(authors.most_common(5))}
    raise Unavailable("More than 2,000 commits in eight weeks; partial counts withheld.")


def analyze(url, *, api=None, now=None):
    name = repo_name(url)
    own = api is None
    api = api or PublicRepo(name)
    try:
        return snapshot(name,api,now or datetime.now(timezone.utc))
    finally:
        if own:
            api.client.close()


def snapshot(name, api, now):
    meta = api.get("")[0]
    if meta.get("private") is not False:
        raise Unavailable("Public repositories only.")
    branch = meta["default_branch"]
    head = api.get("/commits/"+quote(branch,safe=""))[0]
    sha = head["sha"]
    report = {"url":"https://github.com/"+name,"observed_utc":now.isoformat(),"branch":branch,"commit":sha}
    notes = []
    try:
        tree = api.get("/git/trees/"+head["commit"]["tree"]["sha"], recursive=1)[0]
        files = {x["path"]:x for x in tree["tree"] if x["type"]=="blob"}
        partial = tree["truncated"]
        folders = Counter((p.split("/")[0] if "/" in p else "(root)") for p in files)
        report["File structure"] = {"observed files":len(files),"truncated":partial,
            "folders":[{"area":p,"files":n,"inferred purpose":PURPOSE.get(p,"root files" if p=="(root)" else "purpose unavailable")}
                       for p,n in sorted(folders.items())],
            "types":dict(Counter(Path(p).suffix.lower() or "(no extension)" for p in files))}
        if partial:
            notes.append("GitHub truncated the tree. File counts, stack and sample cover observed paths only.")
    except Unavailable as e:
        files = {}
        report["File structure"] = "Unavailable: "+str(e)
    stack = {}
    try:
        stack["language bytes (GitHub /languages, current repository totals)"] = api.get("/languages")[0]
    except Unavailable as e:
        stack["language bytes"] = "Unavailable: "+str(e)
    stack["tools and framework evidence"] = findings = []
    for path in sorted(files):
        if Path(path).name in TOOLS:
            findings.append(f"{TOOLS[Path(path).name]} — {path}")
        if path.startswith(".github/workflows/") and Path(path).suffix in {".yml",".yaml"}:
            findings.append("GitHub Actions — "+path)
    report["Tech stack"] = stack
    def content(path):
        item = files[path]
        if item.get("size",999999)>100000:
            raise Unavailable("File exceeds 100 KB analysis limit.")
        blob = api.get("/git/blobs/"+item["sha"])[0]
        if blob.get("encoding")!="base64":
            raise Unavailable("Unsupported GitHub blob encoding.")
        try:
            return base64.b64decode(blob["content"]).decode("utf-8")
        except (UnicodeError,ValueError):
            raise Unavailable("File is not readable UTF-8.") from None

    manifests = sorted(p for p in files if Path(p).name in MANIFESTS and not IGNORE.intersection(Path(p).parts))
    health = {"policy":"Pinned = exact direct version; ranged = constrained set; unpinned = any/latest. Pinning measures reproducibility only. Registry age, security and maintenance were not checked.",
              "observed manifests":manifests,"checks":{}}
    for path in manifests[:8]:
        try:
            rows, note = dep_rows(path,content(path))
            health["checks"][path] = {"scope":note,"dependencies":rows}
            for row in rows:
                if row["package"].lower() in FRAMEWORKS:
                    findings.append(f"{row['package']} — declared in {path}")
        except (Unavailable,ValueError,TypeError,KeyError,AttributeError):
            health["checks"][path] = "Unavailable: manifest could not be fetched or parsed."
    if len(manifests)>8:
        notes.append("Dependency checks limited to first eight recognized manifests in path order.")
    if not manifests:
        health["checks"] = "Unavailable: no recognized manifest in observed paths."
    report["Dependency health"] = health
    eligible = sorted(p for p in files if Path(p).suffix.lower() in SOURCE and not IGNORE.intersection(Path(p).parts)
                      and not p.endswith((".min.js",".d.ts")))
    measurements = []
    for p in eligible[:10]:
        try:
            lines = [line.expandtabs(4) for line in content(p).splitlines() if line.strip()]
            deep = sum(len(s)-len(s.lstrip())>=12 for s in lines)
            measurements.append({"file":p,"nonblank":len(lines),"deep":deep})
        except Unavailable as e:
            measurements.append({"file":p,"reason":str(e)})
    total = sum(f.get("nonblank",0) for f in measurements)
    deep = sum(f.get("deep",0) for f in measurements)
    report["Code complexity"] = {"metric":"Sample indentation complexity (structural proxy, formatting-sensitive)",
        "formula":"100 × lines indented at least 12 columns / nonblank lines; tabs expand to four. Higher means more deeply indented lines. Minimum 10 observed nonblank lines.",
        "score":round(100*deep/total,1) if total>=10 else "Unavailable: fewer than 10 readable nonblank lines",
        "coverage":f"First 10 lexicographic paths from {len(eligible)} observed eligible source files; excludes generated/vendor paths by convention.",
        "reason":f"{deep} deep lines among {total} nonblank sampled lines.","files":measurements}
    try:
        report["Contribution activity"] = activity(api,sha,now)
    except (Unavailable,ValueError,KeyError) as e:
        report["Contribution activity"] = "Unavailable: "+str(e)
    report["notes"] = notes
    return report


def terminal(r):
    lines = [r["url"],f"Observed {r['observed_utc']} | {r['branch']} @ {r['commit']}"]
    for key in ("Tech stack","File structure","Code complexity","Dependency health","Contribution activity"):
        lines += ["",key.upper()]
        value = r[key]
        if key=="Contribution activity" and isinstance(value,dict):
            lines.append(value["window"])
            peak = max(value["weekly"].values(),default=0)
            for week,n in value["weekly"].items():
                lines.append(f"{week} | {'#' * (max(1,round(n*30/max(1,peak))) if n else 0)} {n}")
            lines.append("Bars scaled to peak (30 columns); labels are exact counts.")
            lines.append("Top authors: "+json.dumps(value["top authors"],ensure_ascii=False))
        else:
            lines.append(json.dumps(value,indent=2,ensure_ascii=False) if not isinstance(value,str) else value)
    return "\n".join(lines+r["notes"])+"\n"
