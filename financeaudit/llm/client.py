"""Thin OpenAI Structured-Outputs wrapper (integrator).

Design rules (PLAN §2.3, CONTRACTS §0):
- The LLM NEVER computes amounts, never selects evidence, never touches the
  verdict gate. It only rewrites/narrates facts that are already gated.
- Every call site has a deterministic fallback; failure -> None, caller keeps
  its deterministic output and llm_used stays false.
- Strict json_schema responses; one retry on schema violation.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from financeaudit.core import config
from financeaudit.llm.calllog import log_event


def structured_call(model: str, system: str, user: str, schema_name: str,
                    schema: dict, timeout: int = 60,
                    max_attempts: int = 3) -> Optional[dict]:
    """One Structured-Outputs call with transient-failure retries.

    The gpt-5.6 preview family intermittently returns 401 under capacity
    pressure (~25% of calls, observed 2026-07-18); 401/429/5xx/timeouts are
    treated as transient and retried with backoff. Returns parsed dict or
    None (graceful) after the last attempt."""
    if not config.llm_available():
        return None
    import time
    call_id = f"{os.getpid()}-{int(time.time()*1000)}"
    log_event("llm_request", {"call_id": call_id, "model": model,
                               "schema_name": schema_name,
                               "system": system, "user": user})
    for attempt in range(max_attempts):
        if attempt:
            time.sleep(2 * attempt)
        t0 = time.time()
        out = _one_call(model, system, user, schema_name, schema, timeout,
                        call_id=call_id, attempt=attempt, t0=t0)
        if out is not None:
            return out
    return None


def _one_call(model: str, system: str, user: str, schema_name: str,
              schema: dict, timeout: int, call_id: str = "",
              attempt: int = 0, t0: float = 0.0) -> Optional[dict]:
    import time
    try:
        from openai import OpenAI
        client = OpenAI()
        rsp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True,
                                 "schema": schema},
            },
            timeout=timeout,
        )
        content = rsp.choices[0].message.content
        out = json.loads(content)
        out["_usage"] = {
            "model": model,
            "prompt_tokens": getattr(rsp.usage, "prompt_tokens", None),
            "completion_tokens": getattr(rsp.usage, "completion_tokens", None),
        }
        log_event("llm_response", {"call_id": call_id, "attempt": attempt,
                                    "model": model,
                                    "latency_s": round(time.time() - t0, 3),
                                    "response": content,
                                    "usage": out["_usage"]})
        return out
    except Exception as exc:  # any failure -> deterministic fallback
        log_event("llm_error", {"call_id": call_id, "attempt": attempt,
                                 "model": model,
                                 "latency_s": round(time.time() - t0, 3) if t0 else None,
                                 "error": str(exc)[:400]})
        print(f"[llm] call failed ({model}): {str(exc)[:160]}")
        return None


def extract_digit_tokens(value: Any, acc: Optional[set] = None) -> set:
    """All digit sequences (len>=3) appearing in a JSON-ish object — used to
    verify the LLM introduced no numbers that are absent from its input."""
    import re
    if acc is None:
        acc = set()
    if isinstance(value, dict):
        for v in value.values():
            extract_digit_tokens(v, acc)
    elif isinstance(value, (list, tuple)):
        for v in value:
            extract_digit_tokens(v, acc)
    elif value is not None:
        for tok in re.findall(r"\d[\d.,]{2,}", str(value)):
            t = tok.strip(".,")
            # canonical forms: full digit string AND integer part, so that
            # "248,000.00" == "248,000" == "248000" while any genuinely new
            # number still fails the subset check.
            full = t.replace(",", "").replace(".", "")
            if len(full) < 3:
                # punctuation-adjacency artifacts ("MV-U05," -> "05"); tokens
                # with <3 digits carry no amount information — skip both sides.
                continue
            acc.add(full)
            for sep in (".", ","):
                if sep in t:
                    intpart = t.split(sep)[0].replace(",", "").replace(".", "")
                    if intpart:
                        acc.add(intpart)
    return acc
