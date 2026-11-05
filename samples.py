"""Deterministic illustrative inputs. No API calls or GitHub writes."""
import json
from review import run_review

JOB = {"repo":"sample/rc3-test","title":"DEMO-1 Add health endpoint","number":7,"branch":"DEMO-1-health",
       "base":"main","sha":"a"*40,"base_sha":"b"*40}
CRITERIA = "GET /health returns JSON with status equal to ok."
def sample_diff(passes):
    line = '+    return {"status": "ok"}' if passes else '+    return {"state": "ready"}'
    return ('diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,0 +1,3 @@\n'
            '+@app.get("/health")\n+def health():\n'+line+'\n').encode()


class Example:
    def __init__(self, passes):
        self.passes = passes
    def current(self):
        return {"state":"open","title":JOB["title"],"head":{"ref":JOB["branch"],"sha":JOB["sha"]},
                "base":{"ref":"main","sha":JOB["base_sha"]}}
    def diff(self):
        return sample_diff(self.passes)
    def ticket(self,key):
        return {"key":key,"title":"Health endpoint","description":"Expose a readiness response.","criteria":CRITERIA}
    def evaluate(self,ticket,diff):
        return json.dumps({"fulfilled":self.passes,"feedback":'app.py returns {"status":"ok"} from GET /health.'
            if self.passes else 'app.py returns state=ready. Return the required JSON key status with value ok.'})


def simulate(passes):
    # Simulation receives explicit local policy. Never modify the process environment in a web session.
    result = run_review(JOB, Example(passes), policy=(False,JOB["repo"],"main"))
    return {**result, "mode":"Simulated","diff":sample_diff(passes).decode(),"effects":[]}
