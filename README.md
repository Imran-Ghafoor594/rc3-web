# Start here — browser setup

**RC3 AI Hackathon, Task 4. Track A and Track B**, both included per instructor clarification. Deployment pending. The ZIP includes verified public-repository reports and automated test evidence. Live PR evidence and original AI chat exports must be added by the participant.

**Immediate offline demo:** after extraction double-click [evidence/OPEN-DEMO.html](evidence/OPEN-DEMO.html). Expand either simulated PR or either saved repository report. This viewer works without an account.

1. Extract the ZIP. On GitHub, choose **+ → New repository**, name it `rc3-task4-web`, add a README to initialize it, and select **Create repository**.
2. In the repository choose **Add file → Upload files**. Drag the extracted **contents**, including the prompts, tests and evidence folders. Choose **Commit changes**. Check that `render.yaml`, `app.py` and `requirements.txt` are visible at the repository root.
3. In [Render Dashboard](https://dashboard.render.com) select **New → Blueprint**, connect GitHub, permit access to that repository, select it, and review the resources. **The webhook uses a paid web service and a persistent disk.** Review Render's estimate before choosing **Deploy Blueprint**. No account resources have been created for you.

When the two services show **Live**, open the public URL of **rc3-task4-web**. Try both simulated PR cases, or choose **Repository Analyzer → Open a saved repository observation**. For a fresh report paste a public GitHub URL and click **Analyze repository**. The report and terminal tabs each offer a download. These paths work before you configure Jira, the webhook or an LLM. The TXT reports in `evidence/` also open directly after extraction.

For an analyzer/simulations-only deployment without the paid webhook, use [Streamlit Community Cloud](https://share.streamlit.io), sign in with GitHub, choose **Create app → Deploy a public app from GitHub**, select the public source repository, branch and `app.py`, then deploy. Use the full Blueprint for the live PR demonstration.

## Real webhook setup and judging

Follow [LIVE-DEMO.md](LIVE-DEMO.md) for browser steps, secret ownership, ruleset setup and two real PRs. The dashboard displays real test ticket text and feedback publicly. Use a separate public sandbox with invented test data. Never use a production repo or place secrets in a diff, ticket, screenshot or chat. The configured model receives the fetched ticket and code diff.

## Project map

| File | Purpose |
|---|---|
| app.py | Streamlit navigation, samples, HTTP results reader, browser reports and downloads |
| webhook.py | HMAC verification, durable SQLite inbox and single review worker |
| review.py and prompts/review.txt | GitHub, Jira Cloud and OpenAI-compatible API connectors, verdict validation and action gates |
| analyzer.py and analyze.py | One public GitHub analysis engine, shared renderer and thin CLI |
| samples.py | Two offline examples with fixed inputs and model outputs |
| evidence/ | Two real repository observations, full terminal reports, test log, browser check and judge review |
| tests/ | Regression tests for the normal entry points, external API contracts and failures |
| config.example.txt and render.yaml | Environment reference and two-service deployment |

Python 3.12 with Streamlit, FastAPI, Uvicorn, HTTPX, packaging and pandas (browser chart). SQLite, JSON, HMAC, CLI parsing and unit tests use Python's standard library. For examiner reproduction **only**, the CLI accepts `python analyze.py https://github.com/owner/repo` and `python analyze.py --snapshot evidence/plant.json`. Run the regression suite from the project root with `python -m unittest discover -s tests -v`. The browser workflow needs no terminal.

## Measurement and operational limits

The tree, sampled source, manifests and commit history are addressed by commit/tree/blob IDs. The languages API measures current repository totals independently. Activity covers eight completed Monday–Sunday UTC weeks on the named branch's reachable history, grouped by committer date. API pagination follows the next-page signal with a 2,000-commit ceiling. If a page fails or the ceiling is exceeded, counts are unavailable. A measured zero means the completed window contained no matching commits.

Complexity measures the share of deeply indented lines in a bounded source sample, not runtime complexity. Each report gives the formula, counts and paths. A notebook-heavy repository may have substantial code outside this sample. Dependency health measures direct version constraints, with evidence per declaration. Registry freshness and maintenance are outside the measurement. Manifest and source caps are stated in the output. Truncated trees describe observed paths only.

One webhook process owns a durable disk and serializes jobs. Delivery ID and signed-body fingerprint are unique across restarts. Interrupted in-flight jobs require inspecting GitHub, then submitting a fresh event if appropriate. This favors avoiding duplicate writes over automatic recovery after an ambiguous network outcome. Results travel via an authenticated server-side HTTP call. Only the webhook service receives GitHub write credentials.

## Before submission

Use the checklist and five-row assessment in [evidence/JUDGE-REVIEW.md](evidence/JUDGE-REVIEW.md). Export every original AI conversation used for Task 4, **complete and unedited**, as PDF, Markdown or text. Include this whole build session and any subsequent review/fix conversation. If printing to PDF, load the entire conversation and check the first and last messages and all intermediate turns. The handbook treats the original chats as the primary evidence.
