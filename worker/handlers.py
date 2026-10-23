"""Job handlers: one function per service-task type in the BPMN.

Each handler gets the job's variables and returns the variables to merge back into the
process. They are deliberately boring: print a line, write a journal row. In production
these would call Slack and Postgres. Keep them idempotent: Zeebe retries on failure.
"""
from __future__ import annotations

import os
import uuid

import classify
import journal


def log(job: dict, msg: str) -> None:
    print(f"[{job['type']:<22}] case {job['variables'].get('threadId', '?'):<10} {msg}", flush=True)


def classify_area(job: dict) -> dict:
    area, source = classify.classify(job["variables"].get("text", ""))
    log(job, f"area={area} ({source})")
    return {"area": area, "areaSource": source}


def open_journal_row(job: dict) -> dict:
    v = job["variables"]
    case_id = "C-" + uuid.uuid4().hex[:6]
    row = journal.upsert(v["threadId"], caseId=case_id, status="open", channel=v.get("channel"),
                         author=v.get("author"), onCall=v.get("onCall"), area=v.get("area"),
                         openedAt=journal.now(), processInstanceKey=job["processInstanceKey"])
    log(job, f"journal row opened as {case_id} (on-call: {v.get('onCall')})")
    return {"caseId": case_id, "openedAt": row["openedAt"]}


def nudge_on_call(job: dict) -> dict:
    v = job["variables"]
    esc = v.get("escalation") or {}
    n = int(v.get("reminderIndex", 0)) + 1
    log(job, f"reminder #{n} level={esc.get('level')} -> DM to {esc.get('notify')} (on-call {v.get('onCall')})")
    journal.upsert(v["threadId"], reminders=n, lastEscalation=esc.get("level"))
    return {"lastReminderAt": journal.now()}


def mark_case_rejected(job: dict) -> dict:
    v = job["variables"]
    journal.upsert(v["threadId"], status="rejected", closedAt=journal.now())
    log(job, "marked rejected (not a support case); row kept so the scanner does not re-open it")
    return {}


def mark_case_stale(job: dict) -> dict:
    v = job["variables"]
    journal.upsert(v["threadId"], status="stale", closedAt=journal.now())
    log(job, "no signal after all reminders; flagged stale for the weekly report")
    return {}


def add_to_monday_digest(job: dict) -> dict:
    v = job["variables"]
    journal.add_to_digest({"threadId": v["threadId"], "caseId": v.get("caseId"), "author": v.get("author")})
    log(job, f"CSAT not answered in {v.get('csatTimeout')}; queued for the Monday digest to {v.get('author')}")
    return {}


def close_case(job: dict) -> dict:
    v = job["variables"]
    journal.upsert(v["threadId"], status="closed", closedAt=journal.now(),
                   csat=v.get("csatRating"), csatComment=v.get("csatComment"),
                   survey={k: v.get(k) for k in ("surveyArea", "surveyType", "surveyOutcome",
                                                 "surveySolver", "surveyCouldSolveAlone")})
    log(job, f"closed: csat={v.get('csatRating')} outcome={v.get('surveyOutcome')} solver={v.get('surveySolver')}")
    return {}


def lead_weekly_report(job: dict) -> dict:
    rows = journal.all_rows()
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    csat = [r.get("csat") for r in rows if r.get("csat") in ("up", "down", "unsure")]
    positive = sum(1 for c in csat if c == "up")
    surveys = sum(1 for r in rows if (r.get("survey") or {}).get("surveyOutcome"))
    solvers: dict[str, int] = {}
    for r in rows:
        s = (r.get("survey") or {}).get("surveySolver")
        if s:
            solvers[s] = solvers.get(s, 0) + 1
    lines = [
        "=== Weekly support report (to: team lead) ===",
        f"cases: {len(rows)}  by status: {by_status}",
        f"CSAT answers: {len(csat)}  positive: {positive}  in digest: {len(journal.digest())}",
        f"duty surveys filled: {surveys}  solvers: {solvers}",
        f"stale cases: {[r['threadId'] for r in rows if r.get('status') == 'stale']}",
    ]
    print("\n".join(lines), flush=True)
    return {"reportLines": lines, "sentTo": os.environ.get("LEAD_HANDLE", "@platform-lead")}


HANDLERS = {
    "classify-area": classify_area,
    "open-journal-row": open_journal_row,
    "nudge-on-call": nudge_on_call,
    "mark-case-rejected": mark_case_rejected,
    "mark-case-stale": mark_case_stale,
    "add-to-monday-digest": add_to_monday_digest,
    "close-case": close_case,
    "lead-weekly-report": lead_weekly_report,
}
