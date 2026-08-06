# app/services/auto_client.py
"""
Supervity Auto client — the backend's only door into the agent platform.

Contract taken verbatim from auto.supervity.ai/docs/api-docs:

    Auth (authentication)
        Authorization: Bearer <jwt or api key>
        x-source: external          <- REQUIRED when using an API key
        x-active-org: <org key>     <- required by most endpoints

    Runs (workflow-runs)
        POST /api/v1/workflow-runs/execute          {workflowId, inputs, envs}
        POST /api/v1/workflow-runs/execute/stream   same body, SSE response
        GET  /api/v1/workflow-runs                  list
        GET  /api/v1/workflow-runs/:runId           {id, status, workflowId, ...}
        status in: scheduled | running | completed | failed | cancelled | waiting

    Human review (user-forms)
        GET  /api/v1/user-forms?status=pending
             -> [{id, workflowId, workflowRunId, activityRunId,
                  workflowStepName, status, reviewedBy, reviewedAt, ...}]
        POST /api/v1/user-forms/:activityRunId/:status   status = approve|reject
             multipart/form-data; submitting SIGNALS THE WORKFLOW TO RESUME.

Nothing here is invented. Where the docs are silent (for example, there is no
documented "resume by run id" endpoint — resumption happens by answering the
user form), this module does not paper over the gap.

Secrets never leave the backend and never appear in logs or error strings.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://auto.supervity.ai"
TIMEOUT = float(os.getenv("SUPERVITY_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("SUPERVITY_MAX_RETRIES", "3"))


class AutoNotConfigured(RuntimeError):
    """Raised when the Auto credentials are absent. Never silently faked."""


class AutoError(RuntimeError):
    """A real failure talking to Auto. Carries a redacted message."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def base_url() -> str:
    return (os.getenv("SUPERVITY_API_BASE_URL") or DEFAULT_BASE).rstrip("/")


def api_key() -> str:
    return os.getenv("SUPERVITY_WORKFLOW_API_KEY", "").strip()


def org_key() -> str:
    return os.getenv("SUPERVITY_ORG_KEY", "").strip()


def orchestrator_id() -> str:
    return os.getenv("SUPERVITY_ORCHESTRATOR_ID", "").strip()


def is_configured() -> bool:
    return bool(api_key())


def redact(text: str) -> str:
    """Strip anything credential-shaped out of a message before it is logged."""
    out = str(text)
    for secret in (api_key(), org_key()):
        if secret and len(secret) > 8:
            out = out.replace(secret, "<redacted>")
    return out[:1500]


def _headers(extra: Optional[Dict[str, str]] = None,
             org_override: Optional[str] = None) -> Dict[str, str]:
    key = api_key()
    if not key:
        raise AutoNotConfigured(
            "SUPERVITY_WORKFLOW_API_KEY is not set. Generate a Workflow API key "
            "at auto.supervity.ai/u/api-keys and put it in .env, then recreate "
            "the backend container (docker compose up -d --force-recreate backend)."
        )
    headers = {
        "Authorization": f"Bearer {key}",
        # Required by the docs whenever an API key (rather than a session JWT)
        # is used, so the platform recognises it as an external caller.
        "x-source": "external",
        "Accept": "application/json",
    }
    org = org_override if org_override is not None else org_key()
    if org:
        headers["x-active-org"] = org
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _request(
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    retries: int = MAX_RETRIES,
    org_override: Optional[str] = None,
) -> Tuple[Any, float]:
    """
    One call to Auto. Returns (parsed_body, latency_ms).

    Retries only on transient conditions — connection errors, 429, and 5xx —
    with exponential backoff. A 4xx is a contract problem and is raised
    immediately rather than hammered.
    """
    url = f"{base_url()}{path}"
    hdrs = _headers(headers, org_override=org_override)
    attempt = 0
    started = time.perf_counter()
    last: Optional[Exception] = None

    while attempt < max(1, retries):
        attempt += 1
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.request(
                    method, url, json=json_body, data=data,
                    params=params, headers=hdrs,
                )

            latency = (time.perf_counter() - started) * 1000

            if r.status_code == 429 or r.status_code >= 500:
                last = AutoError(
                    f"Auto returned HTTP {r.status_code}: "
                    f"{redact(r.text[:400])}",
                    r.status_code,
                )
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise last

            if r.status_code >= 400:
                raise AutoError(
                    f"Auto returned HTTP {r.status_code}: {redact(r.text[:400])}",
                    r.status_code,
                )

            ctype = r.headers.get("content-type", "")
            body: Any = r.json() if "json" in ctype else r.text
            return body, latency

        except httpx.HTTPError as exc:
            last = AutoError(f"{type(exc).__name__}: {redact(str(exc))}")
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            raise last

    raise last or AutoError("Auto request failed for an unknown reason.")


# ---------------------------------------------------------------------------
# Workflow runs
# ---------------------------------------------------------------------------


def execute_workflow(
    workflow_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    envs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Start an Orchestrator run.

        POST /api/v1/workflow-runs/execute
        {"workflowId": "...", "inputs": {...}, "envs": {...}}

    Returns Auto's response verbatim. The caller reads the run id from it —
    this module does NOT synthesise an id when Auto does not supply one.
    """
    wf = (workflow_id or orchestrator_id()).strip()
    if not wf:
        raise AutoNotConfigured(
            "No workflow id. Set SUPERVITY_ORCHESTRATOR_ID in .env to your "
            "ResolveFlow Operations Orchestrator's workflow UUID (the 36-char "
            "id in its editor URL)."
        )

    body: Dict[str, Any] = {"workflowId": wf}
    if inputs:
        body["inputs"] = inputs
    if envs:
        body["envs"] = envs

    payload, latency = _request("POST", "/api/v1/workflow-runs/execute",
                                json_body=body)
    log.info("Auto execute workflow=%s in %.0f ms", wf, latency)
    return {"response": payload, "latency_ms": round(latency, 1)}


def extract_run_id(payload: Any) -> Optional[str]:
    """
    Pull the run id out of Auto's execute response.

    The documented run object uses `id`. Responses may nest it, so a few
    well-known shapes are checked — but if none match we return None rather
    than guessing, and the caller leaves auto_run_id NULL.
    """
    if not isinstance(payload, dict):
        return None
    for key in ("id", "runId", "workflowRunId"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    for nest in ("data", "run", "workflowRun", "result"):
        inner = payload.get(nest)
        if isinstance(inner, dict):
            found = extract_run_id(inner)
            if found:
                return found
    return None


def get_run(auto_run_id: str) -> Dict[str, Any]:
    """
    GET /api/v1/workflow-runs/:runId

    -> {id, status, workflowId, workflowName, inputs, createdAt, updatedAt}
       status in scheduled | running | completed | failed | cancelled | waiting
    """
    payload, _ = _request("GET", f"/api/v1/workflow-runs/{auto_run_id}")
    return payload if isinstance(payload, dict) else {"raw": payload}


def list_runs(limit: int = 20, **filters: Any) -> Any:
    params = {"limit": limit, **filters}
    payload, _ = _request("GET", "/api/v1/workflow-runs", params=params)
    return payload


# ---------------------------------------------------------------------------
# Human review — this is how a WAITING run resumes
# ---------------------------------------------------------------------------


def list_pending_forms(limit: int = 50) -> List[Dict[str, Any]]:
    """
    GET /api/v1/user-forms?status=pending

    Each entry carries workflowRunId and activityRunId. The activityRunId is
    the handle needed to answer the form and resume the run.
    """
    payload, _ = _request(
        "GET", "/api/v1/user-forms", params={"status": "pending", "limit": limit}
    )
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "forms"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def find_form_for_run(auto_run_id: str) -> Optional[Dict[str, Any]]:
    """Locate the pending review form belonging to one Auto run."""
    for form in list_pending_forms():
        if form.get("workflowRunId") == auto_run_id:
            return form
    return None


def submit_form_decision(
    activity_run_id: str,
    approve: bool,
    fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    POST /api/v1/user-forms/:activityRunId/:status   (approve | reject)

    Submitting the form signals the workflow to resume with the reviewer's
    inputs. This is what turns a Command Center decision into an actual
    continuation of the same Auto run.

    The endpoint expects multipart/form-data, so reviewer values are sent as
    form fields rather than JSON.
    """
    status = "approve" if approve else "reject"
    form_fields = {k: str(v) for k, v in (fields or {}).items()}

    payload, latency = _request(
        "POST",
        f"/api/v1/user-forms/{activity_run_id}/{status}",
        data=form_fields or {"decision": status},
        headers={"Accept": "text/html, application/json"},
    )
    log.info("Auto form %s -> %s in %.0f ms", activity_run_id, status, latency)
    return {"submitted": True, "status": status, "latency_ms": round(latency, 1)}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def list_workflows(limit: int = 50, org_override: Optional[str] = None) -> Any:
    """
    GET /api/v1/workflows?limit=N

    -> {"workflows": [{id, name, description, services, createdAt, ...}]}

    Better health probe than listing runs: it needs no filters, and it also
    tells us the Orchestrator's UUID.
    """
    payload, latency = _request(
        "GET", "/api/v1/workflows", params={"limit": limit},
        retries=1, org_override=org_override,
    )
    return payload, latency


def health() -> Dict[str, Any]:
    """
    A real authenticated call, used by the Data Manager.

    Uses GET /api/v1/workflows because the docs state every endpoint needs a
    valid key AND the x-active-org header — so a success here proves both,
    not merely that a key is present.
    """
    if not is_configured():
        return {"ok": False, "configured": False,
                "detail": "SUPERVITY_WORKFLOW_API_KEY is not set."}
    try:
        payload, latency = list_workflows(limit=1)
        count = len(payload.get("workflows", [])) if isinstance(payload, dict) else 0
        return {"ok": True, "configured": True, "latency_ms": round(latency, 1),
                "detail": f"Authenticated: GET /api/v1/workflows returned {count} workflow(s)."}
    except AutoNotConfigured as exc:
        return {"ok": False, "configured": False, "detail": str(exc)}
    except AutoError as exc:
        hint = ""
        if exc.status_code in (400, 401, 403):
            hint = (" The docs require an x-active-org header on every endpoint. "
                    "Run GET /api/agent/auto/diagnose to find the right value for "
                    "SUPERVITY_ORG_KEY.")
        return {"ok": False, "configured": True,
                "detail": redact(str(exc)) + hint, "status_code": exc.status_code}


def diagnose_org_keys(candidates: List[str]) -> Dict[str, Any]:
    """
    Try each candidate x-active-org value against GET /api/v1/workflows and
    report which one the API accepts.

    The docs never say where the org key comes from, so rather than guess we
    probe and report the evidence.
    """
    results = []
    winner = None
    for candidate in candidates:
        label = candidate if candidate else "(blank / header omitted)"
        try:
            payload, latency = list_workflows(limit=5, org_override=candidate)
            names = []
            if isinstance(payload, dict):
                names = [w.get("name") for w in payload.get("workflows", [])][:10]
            results.append({
                "org_key": label, "ok": True,
                "latency_ms": round(latency, 1),
                "workflow_count": len(names), "workflow_names": names,
            })
            if winner is None:
                winner = candidate
        except AutoError as exc:
            results.append({
                "org_key": label, "ok": False,
                "status_code": exc.status_code,
                "error": redact(str(exc))[:300],
            })
        except AutoNotConfigured as exc:
            return {"configured": False, "detail": str(exc), "results": []}

    return {
        "configured": True,
        "working_org_key": winner,
        "instruction": (
            f"Set SUPERVITY_ORG_KEY={winner} in .env, then "
            "docker compose up -d --force-recreate backend"
        ) if winner is not None else (
            "No candidate worked. Send the errors below and we will widen the search."
        ),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Auth-shape diagnostics
# ---------------------------------------------------------------------------


def diagnose_auth(path: str = "/api/v1/workflows") -> Dict[str, Any]:
    """
    Probe every documented way of presenting the credential.

    The docs list three auth transports (cookie, ?token= query param,
    Authorization: Bearer) plus an x-source: external requirement for API
    keys, but do not say which applies to a Workflow API key. Rather than
    guess, try them all against one harmless GET and report exactly what each
    returns.
    """
    key = api_key()
    if not key:
        return {"configured": False,
                "detail": "SUPERVITY_WORKFLOW_API_KEY is not set."}

    org = org_key()
    url = f"{base_url()}{path}"
    base_params = {"limit": 1}

    variants = [
        ("bearer + x-source",
         {"Authorization": f"Bearer {key}", "x-source": "external"}, {}),
        ("bearer only",
         {"Authorization": f"Bearer {key}"}, {}),
        ("x-api-key + x-source",
         {"x-api-key": key, "x-source": "external"}, {}),
        ("x-api-key only",
         {"x-api-key": key}, {}),
        ("api-key header + x-source",
         {"api-key": key, "x-source": "external"}, {}),
        ("query ?token= + x-source",
         {"x-source": "external"}, {"token": key}),
        ("query ?token= only",
         {}, {"token": key}),
        ("cookie token= + x-source",
         {"Cookie": f"token={key}", "x-source": "external"}, {}),
        ("cookie token= only",
         {"Cookie": f"token={key}"}, {}),
        ("bearer + x-source + apiKey query",
         {"Authorization": f"Bearer {key}", "x-source": "external"},
         {"apiKey": key}),
    ]

    results = []
    winner = None

    for label, headers, params in variants:
        hdrs = {"Accept": "application/json", **headers}
        if org:
            hdrs["x-active-org"] = org
        try:
            with httpx.Client(timeout=20) as client:
                r = client.get(url, headers=hdrs,
                               params={**base_params, **params})
            body = r.text[:220]
            entry = {
                "variant": label,
                "status": r.status_code,
                "body": redact(body),
            }
            if 200 <= r.status_code < 300:
                entry["ok"] = True
                if winner is None:
                    winner = label
            else:
                entry["ok"] = False
            results.append(entry)
        except Exception as exc:
            results.append({"variant": label, "ok": False,
                            "error": redact(f"{type(exc).__name__}: {exc}")})

    return {
        "configured": True,
        "endpoint": url,
        "org_header_sent": org or "(none)",
        "key_length": len(key),
        "key_prefix": key[:4] + "…" if len(key) > 8 else "(short)",
        "working_variant": winner,
        "results": results,
    }


def probe_execute_shapes(workflow_id: Optional[str] = None,
                         issue_key: str = "PROBE-0001") -> Dict[str, Any]:
    """
    POST /api/v1/workflow-runs/execute returned 500 for our payload, and the
    docs are ambiguous: the execute request documents `inputs` as a JSON
    object, while the run object documents `inputs` as an array.

    Rather than guess, try each plausible shape and report exactly what Auto
    says. Stops at the first success so we start at most one real run.
    """
    wf = (workflow_id or orchestrator_id()).strip()
    if not wf:
        return {"error": "SUPERVITY_ORCHESTRATOR_ID is not set."}

    shapes = [
        ("no inputs key", {"workflowId": wf}),
        ("inputs = {} (empty object)", {"workflowId": wf, "inputs": {}}),
        ("inputs = [] (empty array)", {"workflowId": wf, "inputs": []}),
        ("inputs = {issue_key}", {"workflowId": wf,
                                  "inputs": {"issue_key": issue_key}}),
        ("inputs = [{name,value}]", {"workflowId": wf,
                                     "inputs": [{"name": "issue_key",
                                                 "value": issue_key}]}),
        ("inputs = [{key,value}]", {"workflowId": wf,
                                    "inputs": [{"key": "issue_key",
                                                "value": issue_key}]}),
        ("inputs = {} + envs = {}", {"workflowId": wf, "inputs": {},
                                     "envs": {}}),
    ]

    results = []
    winner = None
    for label, body in shapes:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.post(
                    f"{base_url()}/api/v1/workflow-runs/execute",
                    json=body, headers=_headers(),
                )
            entry = {"shape": label, "status": r.status_code,
                     "body": redact(r.text[:300])}
            if 200 <= r.status_code < 300:
                entry["ok"] = True
                winner = label
                results.append(entry)
                break
            entry["ok"] = False
            results.append(entry)
        except Exception as exc:
            results.append({"shape": label, "ok": False,
                            "error": redact(f"{type(exc).__name__}: {exc}")})

    return {"workflow_id": wf, "working_shape": winner, "results": results}
