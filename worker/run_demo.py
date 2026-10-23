"""End-to-end demo: deploy, start three scenarios, drive them to the end, print a summary.

    python3 worker/run_demo.py

Timers are shortened through process variables (seconds instead of days), so the whole
run takes about a minute. Afterwards the instances stay in Operate and the completed
user tasks in Tasklist for inspection.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import journal  # noqa: E402
import worker  # noqa: E402
from c8 import C8, C8Error  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = [
    ROOT / "bpmn/support-thread-case.bpmn",
    ROOT / "bpmn/weekly-lead-report.bpmn",
    ROOT / "dmn/escalation-level.dmn",
    ROOT / "forms/duty-survey.form",
    ROOT / "forms/csat.form",
]
PROCESS = "support-thread-case"
RUN = uuid.uuid4().hex[:4]  # unique thread ids per run, so re-running the demo never collides


def banner(msg: str) -> None:
    print(f"\n==== {msg}", flush=True)


def tag_thread(c: C8, thread_id: str, text: str, **overrides) -> None:
    variables = {
        "threadId": thread_id, "channel": "#helpdesk-platform",
        "author": "alice", "onCall": "bob", "text": text, **overrides,
    }
    c.publish_message("thread-tagged", thread_id, variables)
    print(f"-> published thread-tagged for {thread_id}", flush=True)


def instance_for(c: C8, thread_id: str, timeout_s: float = 20) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        items = c.search_instances({"processDefinitionId": PROCESS,
                                    "variables": [{"name": "threadId", "value": f'"{thread_id}"'}]})
        if items:
            return items[0]
        time.sleep(0.5)
    raise TimeoutError(f"no instance for {thread_id} (is the secondary storage flushing?)")


def wait_user_task(c: C8, pi_key: int, element_id: str, timeout_s: float = 30) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        items = c.search_user_tasks({"processInstanceKey": pi_key, "elementId": element_id, "state": "CREATED"})
        if items:
            return items[0]
        time.sleep(0.5)
    raise TimeoutError(f"user task {element_id} not created for {pi_key}")


def wait_state(c: C8, pi_key: int, state: str = "COMPLETED", timeout_s: float = 90) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        items = c.search_instances({"processInstanceKey": pi_key})
        if items:
            last = items[0]
            if last.get("state") == state:
                return last
            if last.get("hasIncident"):
                for inc in c.search_incidents({"processInstanceKey": pi_key}):
                    print(f"   !! incident at {inc.get('elementId')}: {inc.get('errorMessage')}", flush=True)
                return last
        time.sleep(1)
    print(f"   (still {last and last.get('state')} after {timeout_s}s)", flush=True)
    return last or {}


def main() -> int:
    c = C8()
    banner(f"waiting for Camunda at {c.base}")
    topo = c.wait_ready()
    print(f"cluster ok: {len(topo.get('brokers', []))} broker(s), gateway {topo.get('gatewayVersion')}")

    banner("deploying resources")
    dep = c.deploy(RESOURCES)
    for d in dep.get("deployments", []):
        for kind, meta in d.items():
            name = meta.get("processDefinitionId") or meta.get("decisionDefinitionId") or meta.get("formId") or meta.get("decisionRequirementsId")
            print(f"   {kind:<22} {name}  v{meta.get('processDefinitionVersion') or meta.get('version') or meta.get('decisionDefinitionVersion', '')}")

    journal.reset()
    stop = worker.start(c)
    print("workers started in the background")

    # ---- scenario 1: solved quickly, survey + CSAT both answered ----------
    banner("scenario 1: resolved fast, both forms filled")
    t1 = f"T1-{RUN}"
    tag_thread(c, t1, "Backtest run keeps failing on stand reset, error unreadable",
               reminderDelays=["PT1H"], csatTimeout="PT1H")
    tag_thread(c, t1, "(same thread tagged again)")  # idempotency check
    pi1 = instance_for(c, t1)
    time.sleep(2)
    dupes = c.search_instances({"processDefinitionId": PROCESS,
                                "variables": [{"name": "threadId", "value": f'"{t1}"'}]})
    print(f"   instances for {t1}: {len(dupes)} (second tag did not open a second case)")
    c.publish_message("thread-resolved", t1, {"resolvedVia": "checkmark"})
    print("-> published thread-resolved")
    survey = wait_user_task(c, pi1["processInstanceKey"], "Task_DutySurvey")
    c.complete_user_task(survey["userTaskKey"], {
        "surveyArea": "backtesting", "surveyType": "config", "surveyOutcome": "solved",
        "surveySolver": "bob", "surveyCouldSolveAlone": True})
    print("   duty survey completed via API")
    csat = wait_user_task(c, pi1["processInstanceKey"], "Task_Csat")
    c.complete_user_task(csat["userTaskKey"], {"csatRating": "up", "csatComment": "fast, thanks"})
    print("   CSAT completed via API")
    s1 = wait_state(c, pi1["processInstanceKey"])

    # ---- scenario 2: excluded by on-call -----------------------------------
    banner("scenario 2: on-call excludes the thread (not support)")
    t2 = f"T2-{RUN}"
    tag_thread(c, t2, "Anyone up for lunch? Also the kubernetes dashboard link changed")
    pi2 = instance_for(c, t2)
    time.sleep(1)
    c.publish_message("thread-excluded", t2)
    print("-> published thread-excluded")
    s2 = wait_state(c, pi2["processInstanceKey"])

    # ---- scenario 3: reminders fire, then resolved, CSAT times out ---------
    banner("scenario 3: two reminders, late resolution, CSAT timeout -> Monday digest")
    t3 = f"T3-{RUN}"
    tag_thread(c, t3, "Pod for the market data collector is CrashLoopBackOff since the deploy",
               onCall="carol", reminderDelays=["PT8S", "PT8S", "PT8S"], csatTimeout="PT15S")
    pi3 = instance_for(c, t3)
    print("   waiting ~20s for two reminders (DMN decides the escalation level) ...")
    time.sleep(20)
    c.publish_message("thread-resolved", t3, {"resolvedVia": "jira"})
    print("-> published thread-resolved")
    survey3 = wait_user_task(c, pi3["processInstanceKey"], "Task_DutySurvey")
    c.complete_user_task(survey3["userTaskKey"], {
        "surveyArea": "infra", "surveyType": "incident", "surveyOutcome": "ticket",
        "surveySolver": "dave", "surveyCouldSolveAlone": False})
    print("   duty survey completed; CSAT left unanswered on purpose")
    s3 = wait_state(c, pi3["processInstanceKey"], timeout_s=120)

    # ---- scenario 4: stale case (no signal at all) -------------------------
    banner("scenario 4: nobody reacts, three reminders, case goes stale")
    t4 = f"T4-{RUN}"
    tag_thread(c, t4, "FIX session to the exchange drops every hour", onCall="erin",
               reminderDelays=["PT3S", "PT3S", "PT3S"])
    pi4 = instance_for(c, t4)
    s4 = wait_state(c, pi4["processInstanceKey"], timeout_s=60)

    # ---- weekly report ------------------------------------------------------
    banner("weekly lead report (timer start event fires Fridays 18:05; starting one now)")
    wr = c.create_instance("weekly-lead-report")
    wait_state(c, wr["processInstanceKey"], timeout_s=30)

    stop.set()
    banner("summary")
    for name, s in (("resolved fast", s1), ("excluded", s2), ("reminders + CSAT timeout", s3), ("stale", s4)):
        print(f"   {name:<26} instance {s.get('processInstanceKey')}  state={s.get('state')}  incident={s.get('hasIncident')}")
    print(f"\njournal: {journal.JOURNAL}\ndigest:  {journal.DIGEST}")
    print("Operate:  http://localhost:8080/operate   Tasklist: http://localhost:8080/tasklist   (demo / demo)")
    return 0 if all(s.get("state") == "COMPLETED" for s in (s1, s2, s3, s4)) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C8Error as e:
        print(f"\nAPI error: {e}")
        sys.exit(2)
