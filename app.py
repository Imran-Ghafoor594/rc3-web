import json
import os
from pathlib import Path
import httpx
import pandas as pd
import streamlit as st

from analyzer import Unavailable, analyze, terminal
from samples import simulate

st.set_page_config(page_title="RC3 · Developer tools", layout="wide")
page = st.sidebar.radio("Task 4",["PR Reviewer","Repository Analyzer"])
EVIDENCE = Path(__file__).with_name("evidence")


def show_review(r):
    mode = r.get("mode", "Live" if r.get("received_live") else "Dry run")
    {"Simulated":st.info,"Dry run":st.warning,"Live":st.success}[mode](mode)
    st.subheader(f"{r['repo']} · PR #{r['number']}")
    st.text(r["title"])
    st.caption(f"{r['branch']} → {r['base']} · reviewed/event head")
    st.code(r["sha"], language=None)
    if r.get("ticket"):
        t = r["ticket"]
        st.subheader(t["key"]+" · "+t.get("title",""))
        st.text(t.get("description",""))
        st.markdown("**Acceptance criteria**")
        st.text(t.get("criteria","Unavailable"))
    if r.get("diff"):
        st.code(r["diff"],language="diff")
    decision = r.get("verdict")
    st.metric("Verdict","Pass" if decision is True else "Fail" if decision is False else "Unavailable")
    if r.get("feedback"):
        st.text(r["feedback"])
    if r.get("reason"):
        st.error(r["reason"])
    st.write("**Action**",r.get("action",r.get("state")))
    if r.get("effects"):
        st.text(" · ".join(r["effects"]))


def reviewer():
    st.title("PR Reviewer")
    sample, results = st.tabs(["Simulated cases","Webhook results"])
    with sample:
        case = st.radio("Example pull request",["Missing requirement","Requirements fulfilled"],horizontal=True)
        st.caption("Fixed Jira, diff and model response. No GitHub actions.")
        show_review(simulate(case=="Requirements fulfilled"))
    with results:
        st.caption("Read-only view of the configured test repository. Latest 30 deliveries.")
        if st.button("Refresh reviews"):
            st.session_state.pop("reviews",None)
            url,key = os.getenv("REVIEW_API_URL"),os.getenv("RESULTS_KEY")
            if not (url and key):
                st.info("Configure the webhook service URL and read key to view deliveries.")
            else:
                try:
                    with st.spinner("Fetching review results…"):
                        res = httpx.get(url.rstrip("/")+"/reviews",headers={"Authorization":"Bearer "+key},timeout=15)
                        res.raise_for_status()
                        st.session_state.reviews = res.json()
                except (httpx.HTTPError,ValueError):
                    st.error("Review service unavailable. Check its URL, read key and deployment.")
        rows = st.session_state.get("reviews")
        if rows:
            idx = st.selectbox("Delivery",range(len(rows)),format_func=lambda i:f"#{rows[i]['number']} · {rows[i]['state']} · {rows[i]['delivery'][:8]}")
            show_review(rows[idx])
        else:
            st.caption("No results loaded. Refresh after sending a signed PR event.")


def analyzer_page():
    st.title("Repository Analyzer")
    url = st.text_input("Public GitHub repository URL",placeholder="https://github.com/owner/repository")
    if st.button("Analyze repository",type="primary"):
        st.session_state.pop("report",None)
        try:
            with st.spinner("Reading GitHub tree, manifests, source samples and commit history…"):
                st.session_state.report = analyze(url)
        except Unavailable as exc:
            st.error(str(exc))
    saved = sorted(EVIDENCE.glob("*.json"))
    if saved:
        with st.expander("Open a saved repository observation"):
            choice = st.selectbox("Saved report",saved,format_func=lambda p:p.stem)
            if st.button("Open saved report"):
                st.session_state.report = json.loads(choice.read_text())
    if "report" not in st.session_state:
        st.info("Analyze a repository to see its evidence and download the report.")
        return
    r = st.session_state.report
    st.subheader(r["url"].removeprefix("https://github.com/"))
    st.caption(f"{r['branch']} · {r['commit']} · observed {r['observed_utc']}")
    summary,cli = st.tabs(["Report","Terminal report"])
    output = terminal(r)
    with summary:
        for note in r["notes"]:
            st.warning(note)
        for k in ["Tech stack","File structure","Code complexity","Dependency health","Contribution activity"]:
            with st.expander(k,expanded=k in {"Code complexity","Contribution activity"}):
                v = r[k]
                if isinstance(v,str):
                    st.info(v)
                elif k=="Contribution activity":
                    st.caption(v["window"])
                    st.bar_chart(pd.DataFrame({"commits":v["weekly"]}),y="commits")
                    st.write("Top authors",v["top authors"])
                elif k=="Code complexity":
                    st.metric("Sample score / 100",v["score"])
                    st.write(v["formula"],v["reason"])
                    st.caption(v["coverage"])
                    st.dataframe(v["files"])
                elif k=="Tech stack":
                    for label, evidence in v.items():
                        st.caption(label)
                        if isinstance(evidence,dict) and evidence:
                            st.dataframe([{"Language":lang,"Bytes":count} for lang,count in evidence.items()],hide_index=True)
                        elif isinstance(evidence,list) and evidence:
                            st.text("\n".join(evidence))
                        else:
                            st.write(evidence or "No evidence returned.")
                elif k=="File structure":
                    st.metric("Observed files",v["observed files"])
                    st.caption("Partial tree" if v["truncated"] else "Complete GitHub tree response")
                    st.dataframe(v["folders"],hide_index=True)
                    st.dataframe([{"File type":ext,"Files":n} for ext,n in v["types"].items()],hide_index=True)
                elif k=="Dependency health":
                    st.caption(v["policy"])
                    checks = v["checks"]
                    if isinstance(checks,str):
                        st.info(checks)
                    else:
                        for path in v["observed manifests"]:
                            st.text(path)
                            record = checks.get(path,"Outside the eight-manifest sample.")
                            if isinstance(record,str):
                                st.caption(record)
                            else:
                                st.caption(record["scope"])
                                if record["dependencies"]:
                                    st.dataframe(record["dependencies"],hide_index=True)
        st.download_button("Download full JSON",json.dumps(r,ensure_ascii=False,indent=2),
                           file_name=r["url"].split("/")[-1]+".json",mime="application/json")
    with cli:
        st.code(output,language=None)
        st.download_button("Download terminal report",output,file_name=r["url"].split("/")[-1]+".txt")

if page=="PR Reviewer":
    reviewer()
else:
    analyzer_page()
