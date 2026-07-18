"""Finder rules R9-R14 — statistical channel (Agent C).

Contract (docs/CONTRACTS.md §3, PLAN §3.2): exposes
    RULES = [{"rule_id": "R09", "fn": callable(con, thresholds) -> list[candidate_dict]}, ...]

Design notes
------------
- The dossier has NO usable counter-account column (PLAN §3.1 P0). R9 therefore derives
  debit-account x credit-account co-occurrence pairs *within each entry_id line set*
  (per-entry unique account sets; entries with > MAX_ENTRY_LINES lines are excluded so
  no cartesian blow-up on the 86-line opening-balance entry).
- All rules reference only semantic aliases (gl.entry_id, gl.hb_account, ...), never raw
  declared column names.
- R13 (Benford) NEVER stands alone as an accusation: candidates carry
  metrics.corroboration_only = true and, where trivially joinable, the list of other
  candidates (from this module's in-process registry) touching the same entity.
- Time-of-day / weekend signals (R14) are only emitted when the hour distribution has
  real variance; the practice set's timestamps are uniform synthetic noise and are
  auto-detected and skipped.
- LLM plausibility rating in R9 is OPTIONAL: without OPENAI_API_KEY the deterministic
  support-based tiering is used and every candidate carries metrics.llm_used = false.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import statistics
from collections import defaultdict

# ----------------------------------------------------------------------------- params
R9_SUPPORT_K = 5            # pair support <= k  -> rare
R9_MIN_MARGINAL = 10        # both accounts individually appear in >= this many entries
R9_MAX_ENTRY_LINES = 20     # skip mega entries (opening balance carry-forward)

R10_MIN_N = 6               # series length
R10_MAX_REL_MAD = 0.10      # low-CV series only (MAD/median)
R10_MIN_REL_DEV = 0.80      # |x - median| / median outlier cut

R11_MIN_ROUND = 3           # >= this many round-1000 entries in group
R11_MIN_SHARE_CP = 0.30     # counterparty-group round share
R11_MIN_RATIO_USER = 20.0   # user-group share vs ledger baseline
R11_AMOUNT_FLOOR = 1000.0

R12_MIN_N = 20
R12_ROBUST_Z = 8.0
R12_TOP_ROWS = 5

R13_MIN_N = 100
R13_MIN_RATIO = 1.4         # entity first-digit MAD vs ledger-wide baseline MAD
R13_MAX_CANDIDATES = 6

R14_MIN_SUM = 10000.0
R14_HOUR_CV_MIN = 0.5       # emit time-based candidates only above this variance
R14_WEEKEND_TOL = 0.05      # |share - 2/7| below this => uniform synthetic noise

_BENFORD = [math.log10(1.0 + 1.0 / d) for d in range(1, 10)]

# In-process registry: entity_key -> [candidate_id, ...] (filled by R9-R12, R14; read by R13)
_FLAGGED: dict = defaultdict(list)


# ----------------------------------------------------------------------------- helpers
def _account_names(con) -> dict:
    try:
        return dict(con.execute("SELECT account, name FROM gl_accounts").fetchall())
    except Exception:
        return {}


def _entry_source_ids(con, entry_ids) -> list:
    if not entry_ids:
        return []
    ph = ",".join("?" for _ in entry_ids)
    rows = con.execute(
        f"SELECT DISTINCT source_id FROM gl WHERE entry_id IN ({ph}) ORDER BY source_id",
        list(entry_ids),
    ).fetchall()
    return [r[0] for r in rows]


def _vendor_customer_label(con, sub_account: str) -> str:
    for table in ("vendors", "customers"):
        try:
            r = con.execute(
                f"SELECT name FROM {table} WHERE account = ?", [sub_account]
            ).fetchone()
            if r:
                return r[0]
        except Exception:
            pass
    return sub_account


def _mk(rule_id, rule_name, seq, anomaly_type, tier, entity_key, entity_label,
        entry_ids, source_ids, metrics, denominators, description):
    cand = {
        "candidate_id": f"{rule_id}-{seq:04d}",
        "rule_id": rule_id,
        "rule_name": rule_name,
        "channel": "statistical",
        "anomaly_type": anomaly_type,
        "risk_tier": tier,
        "entity_key": entity_key,
        "entity_label": entity_label,
        "entry_ids": sorted(entry_ids),
        "source_ids": sorted(source_ids),
        "metrics": metrics,
        "denominators": denominators,
        "description": description,
    }
    _FLAGGED[entity_key].append(cand["candidate_id"])
    return cand


def _llm_rate_pairs(pairs):
    """Optional LLM plausibility rating for R9 pairs (three-way constrained choice).

    Deterministic fallback: returns ({}, False) whenever no API key is configured or
    anything at all goes wrong. Rating never changes the support-based tier upward
    beyond 'medium'; it is stored as a metric only.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return {}, False
    try:  # pragma: no cover - only runs when a key is present
        from openai import OpenAI

        try:
            from financeaudit.core.config import OPENAI_MODEL_FAST as _model
        except Exception:
            _model = "gpt-5.2-mini"
        client = OpenAI()
        listing = "\n".join(
            f"{i}: debit {d} ({dn}) / credit {c} ({cn})"
            for i, (d, dn, c, cn) in enumerate(pairs)
        )
        rsp = client.chat.completions.create(
            model=_model,
            messages=[{
                "role": "user",
                "content": (
                    "For each German GL debit/credit account co-occurrence, answer ONLY with a JSON "
                    'object {"ratings": [{"i": <index>, "rating": "plausible|questionable|implausible", '
                    '"basis": "<short accounting basis>"}]}. Pairs:\n' + listing
                ),
            }],
            response_format={"type": "json_object"},
            timeout=30,
        )
        data = json.loads(rsp.choices[0].message.content)
        out = {int(r["i"]): {"rating": r.get("rating"), "basis": r.get("basis")}
               for r in data.get("ratings", []) if r.get("rating") in
               ("plausible", "questionable", "implausible")}
        return out, True
    except Exception:
        return {}, False


# ----------------------------------------------------------------------------- R9
def r09_rare_account_cooccurrence(con, thresholds):
    names = _account_names(con)
    rows = con.execute(
        """
        WITH e AS (SELECT entry_id FROM gl GROUP BY 1 HAVING COUNT(*) <= ?),
        d AS (SELECT DISTINCT entry_id, hb_account FROM gl JOIN e USING(entry_id) WHERE amount > 0),
        c AS (SELECT DISTINCT entry_id, hb_account FROM gl JOIN e USING(entry_id) WHERE amount < 0),
        p AS (SELECT d.entry_id, d.hb_account da, c.hb_account ca FROM d JOIN c USING(entry_id)),
        freq AS (SELECT hb_account, COUNT(DISTINCT entry_id) n FROM gl GROUP BY 1)
        SELECT p.da, p.ca, COUNT(*) support, LIST(p.entry_id ORDER BY p.entry_id), f1.n, f2.n
        FROM p JOIN freq f1 ON f1.hb_account = p.da JOIN freq f2 ON f2.hb_account = p.ca
        GROUP BY p.da, p.ca, f1.n, f2.n
        ORDER BY p.da, p.ca
        """,
        [R9_MAX_ENTRY_LINES],
    ).fetchall()

    population = len(rows)
    hits = [r for r in rows
            if r[2] <= R9_SUPPORT_K and r[4] >= R9_MIN_MARGINAL and r[5] >= R9_MIN_MARGINAL]

    llm_ratings, llm_used = _llm_rate_pairs(
        [(da, names.get(da, ""), ca, names.get(ca, "")) for da, ca, *_ in hits]
    )

    out = []
    for i, (da, ca, support, entry_ids, n_d, n_c) in enumerate(hits):
        tier = "medium" if support <= 2 else "low"
        amt = con.execute(
            "SELECT ROUND(SUM(amount),2) FROM gl WHERE amount > 0 AND hb_account = ? AND entry_id IN ("
            + ",".join("?" for _ in entry_ids) + ")",
            [da] + list(entry_ids),
        ).fetchone()[0]
        rating = llm_ratings.get(i)
        out.append(_mk(
            "R09", "rare_account_cooccurrence", len(out) + 1, "local", tier,
            f"account_pair:{da}|{ca}",
            f"{da} {names.get(da, '')} (debit) x {ca} {names.get(ca, '')} (credit)",
            entry_ids, _entry_source_ids(con, entry_ids),
            {"support": support, "marginal_entries_debit": n_d, "marginal_entries_credit": n_c,
             "support_k": R9_SUPPORT_K, "min_marginal": R9_MIN_MARGINAL,
             "sum_debit_amount": amt, "llm_used": llm_used,
             "llm_plausibility": rating},
            {"population_size": population, "rule_hits": len(hits),
             "peers_with_expected_evidence": 0},
            (f"The debit/credit account combination {da}x{ca} occurs in only {support} of 8,391 "
             f"journal entries although each account individually appears in {n_d} and {n_c} "
             "entries respectively."),
        ))
    return out


# ----------------------------------------------------------------------------- R10
def r10_recurring_series_deviation(con, thresholds):
    rows = con.execute(
        """
        WITH e AS (SELECT entry_id FROM gl GROUP BY 1 HAVING COUNT(*) <= ?),
        sig AS (SELECT entry_id,
                       string_agg(DISTINCT hb_account || CASE WHEN amount > 0 THEN '+' ELSE '-' END,
                                  ',' ORDER BY hb_account || CASE WHEN amount > 0 THEN '+' ELSE '-' END) s
                FROM gl JOIN e USING(entry_id) GROUP BY entry_id),
        cp AS (SELECT entry_id, COALESCE(MAX(sub_account), 'txt:' || MIN(text)) c FROM gl GROUP BY entry_id),
        amt AS (SELECT entry_id, SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) a,
                       MIN(posting_date) pd FROM gl GROUP BY entry_id)
        SELECT sig.s, cp.c, LIST(amt.a ORDER BY amt.pd, sig.entry_id),
               LIST(sig.entry_id ORDER BY amt.pd, sig.entry_id)
        FROM sig JOIN cp USING(entry_id) JOIN amt USING(entry_id)
        GROUP BY 1, 2 HAVING COUNT(*) >= ?
        """,
        [R9_MAX_ENTRY_LINES, R10_MIN_N],
    ).fetchall()

    population = len(rows)
    out = []
    for sig, cp, amts, entry_ids in rows:
        med = statistics.median(amts)
        if med <= 0:
            continue
        rel_mad = statistics.median([abs(a - med) for a in amts]) / med
        if rel_mad > R10_MAX_REL_MAD:
            continue  # series not stable enough to call deviations
        outliers = [(e, a, abs(a - med) / med)
                    for a, e in zip(amts, entry_ids) if abs(a - med) / med >= R10_MIN_REL_DEV]
        if not outliers:
            continue  # constant series (e.g. flat monthly AfA) yields no candidate - by design
        cp_label = _vendor_customer_label(con, cp) if not cp.startswith("txt:") else cp[4:]
        o_ids = [e for e, _, _ in outliers]
        out.append(_mk(
            "R10", "recurring_series_deviation", len(out) + 1, "contextual", "low",
            f"series:{sig}|{cp}",
            f"recurring entries {sig} with {cp_label}",
            o_ids, _entry_source_ids(con, o_ids),
            {"series_n": len(amts), "series_median": round(med, 2),
             "series_rel_mad": round(rel_mad, 4),
             "outliers": [{"entry_id": e, "entry_amount": round(a, 2),
                           "rel_deviation": round(d, 3),
                           "delta_within_series": round(a - med, 2)} for e, a, d in outliers],
             "series_entry_ids": list(entry_ids)},
            {"population_size": population, "rule_hits": -1,
             "peers_with_expected_evidence": 0},
            (f"Within an otherwise stable recurring series of {len(amts)} entries "
             f"(median {med:,.2f}, relative MAD {rel_mad:.3f}) for {cp_label}, "
             f"{len(outliers)} entry(ies) deviate by at least {int(R10_MIN_REL_DEV*100)}% from the series median."),
        ))
    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# ----------------------------------------------------------------------------- R11
def r11_round_amount_concentration(con, thresholds):
    base_round, base_n = con.execute(
        """
        WITH e AS (SELECT entry_id,
                          MAX(CASE WHEN abs(amount) >= ? AND abs(amount) = floor(abs(amount))
                                    AND CAST(abs(amount) AS BIGINT) % 1000 = 0 THEN 1 ELSE 0 END) rnd
                   FROM gl GROUP BY 1)
        SELECT SUM(rnd), COUNT(*) FROM e
        """,
        [R11_AMOUNT_FLOOR],
    ).fetchone()
    baseline = (base_round or 0) / base_n if base_n else 0.0

    ent = con.execute(
        """
        SELECT entry_id,
               MAX(CASE WHEN abs(amount) >= ? AND abs(amount) = floor(abs(amount))
                         AND CAST(abs(amount) AS BIGINT) % 1000 = 0 THEN 1 ELSE 0 END) rnd,
               MAX(sub_account) cp, MIN(user_id) u,
               SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) gross
        FROM gl GROUP BY entry_id
        """,
        [R11_AMOUNT_FLOOR],
    ).fetchall()

    groups = {"counterparty": defaultdict(list), "user": defaultdict(list)}
    for entry_id, rnd, cp, user, gross in ent:
        if cp:
            groups["counterparty"][cp].append((entry_id, rnd, gross))
        groups["user"][user].append((entry_id, rnd, gross))

    out = []
    population = sum(len(g) for g in groups.values())
    for kind, g in groups.items():
        for key, items in sorted(g.items()):
            n = len(items)
            r = sum(x[1] for x in items)
            share = r / n if n else 0.0
            ratio = share / baseline if baseline > 0 else float("inf")
            if r < R11_MIN_ROUND:
                continue
            if kind == "counterparty" and share < R11_MIN_SHARE_CP:
                continue
            if kind == "user" and ratio < R11_MIN_RATIO_USER:
                continue
            round_entries = [e for e, rnd, _ in items if rnd]
            sum_round = sum(gr for _, rnd, gr in items if rnd)
            if kind == "counterparty":
                ek, label = f"vendor_or_customer:{key}", _vendor_customer_label(con, key)
            else:
                ek, label = f"user:{key}", key
            out.append(_mk(
                "R11", "round_amount_concentration", len(out) + 1, "contextual", "low",
                ek, label,
                round_entries, _entry_source_ids(con, round_entries),
                {"group_kind": kind, "n_entries": n, "n_round_entries": r,
                 "round_share": round(share, 4), "ledger_baseline_share": round(baseline, 5),
                 "ratio_vs_baseline": round(ratio, 1), "sum_round_entry_gross": round(sum_round, 2),
                 "round_multiple": 1000, "amount_floor": R11_AMOUNT_FLOOR},
                {"population_size": population, "rule_hits": -1,
                 "peers_with_expected_evidence": 0},
                (f"{r} of {n} entries for {kind} {label} contain amounts that are exact multiples "
                 f"of 1,000 EUR, a concentration of {share:.0%} versus a ledger baseline of "
                 f"{baseline:.2%}."),
            ))
    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# ----------------------------------------------------------------------------- R12
def r12_global_amount_outliers(con, thresholds):
    names = _account_names(con)
    rows = con.execute(
        """
        SELECT hb_account, abs(amount), entry_id, source_id
        FROM gl WHERE posting_type <> 'Vortrag'
        ORDER BY hb_account, entry_id
        """
    ).fetchall()
    acc = defaultdict(list)
    for a, v, e, sid in rows:
        acc[a].append((v, e, sid))

    out = []
    population = 0
    for a, vs in sorted(acc.items()):
        vals = [v for v, _, _ in vs]
        if len(vals) < R12_MIN_N:
            continue
        population += 1
        med = statistics.median(vals)
        mad = statistics.median([abs(v - med) for v in vals])
        if mad == 0:
            continue
        flagged = [(0.6745 * (v - med) / mad, v, e, sid) for v, e, sid in vs
                   if abs(0.6745 * (v - med) / mad) >= R12_ROBUST_Z]
        if not flagged:
            continue
        flagged.sort(key=lambda t: -abs(t[0]))
        top = flagged[:R12_TOP_ROWS]
        entry_ids = sorted({e for _, _, e, _ in top})
        out.append(_mk(
            "R12", "global_amount_outliers", len(out) + 1, "global", "low",
            f"account:{a}", f"{a} {names.get(a, '')}",
            entry_ids, sorted({sid for _, _, _, sid in top}),
            {"n_lines": len(vals), "median_abs_amount": round(med, 2), "mad": round(mad, 2),
             "robust_z_threshold": R12_ROBUST_Z, "n_outlier_lines": len(flagged),
             "population_filter": "posting_type <> 'Vortrag' (opening balances excluded)",
             "top_outliers": [{"robust_z": round(z, 1), "abs_amount": round(v, 2),
                               "entry_id": e} for z, v, e, _ in top],
             "verify_only": True},
            {"population_size": population, "rule_hits": -1,
             "peers_with_expected_evidence": 0},
            (f"Account {a} contains {len(flagged)} line amounts that are extreme relative to the "
             f"account's typical magnitude (robust z >= {R12_ROBUST_Z:.0f}); listed for "
             "verification only."),
        ))
    for c in out:
        c["denominators"]["population_size"] = population
        c["denominators"]["rule_hits"] = len(out)
    return out


# ----------------------------------------------------------------------------- R13
def _first_digit_mad(vals):
    digs = []
    for v in vals:
        v = abs(v)
        if v < 1:
            continue
        digs.append(int(str(v).lstrip("0.").replace(".", "")[0]))
    if len(digs) < R13_MIN_N:
        return None, len(digs)
    obs = [digs.count(d) / len(digs) for d in range(1, 10)]
    return sum(abs(o - b) for o, b in zip(obs, _BENFORD)) / 9.0, len(digs)


def r13_benford_divergence(con, thresholds):
    names = _account_names(con)
    all_amounts = [r[0] for r in con.execute(
        "SELECT amount FROM gl WHERE posting_type <> 'Vortrag'").fetchall()]
    ledger_mad, _ = _first_digit_mad(all_amounts)
    if ledger_mad is None or ledger_mad == 0:
        return []

    entities = []
    for a, in con.execute("SELECT DISTINCT hb_account FROM gl ORDER BY 1").fetchall():
        vals = [r[0] for r in con.execute(
            "SELECT amount FROM gl WHERE hb_account = ? AND posting_type <> 'Vortrag'", [a]).fetchall()]
        m, n = _first_digit_mad(vals)
        if m is not None:
            entities.append((f"account:{a}", f"{a} {names.get(a, '')}", m, n))
    for u, in con.execute("SELECT DISTINCT user_id FROM gl ORDER BY 1").fetchall():
        vals = [r[0] for r in con.execute(
            "SELECT amount FROM gl WHERE user_id = ? AND posting_type <> 'Vortrag'", [u]).fetchall()]
        m, n = _first_digit_mad(vals)
        if m is not None:
            entities.append((f"user:{u}", u, m, n))

    population = len(entities)
    flagged = sorted(
        [e for e in entities if e[2] / ledger_mad >= R13_MIN_RATIO],
        key=lambda e: -(e[2] / ledger_mad),
    )[:R13_MAX_CANDIDATES]

    out = []
    for ek, label, m, n in flagged:
        joins = sorted({cid for cid in _FLAGGED.get(ek, []) if not cid.startswith("R13-")})
        out.append(_mk(
            "R13", "benford_divergence", len(out) + 1, "contextual", "low",
            ek, label,
            [], [],
            {"first_digit_mad": round(m, 4), "ledger_baseline_mad": round(ledger_mad, 4),
             "ratio_vs_ledger": round(m / ledger_mad, 2), "n_amounts": n,
             "corroboration_only": True,
             "joins_candidates_same_entity": joins},
            {"population_size": population, "rule_hits": len(flagged),
             "peers_with_expected_evidence": 0},
            (f"The first-digit distribution of amounts for {label} diverges from the Benford "
             f"expectation {m/ledger_mad:.1f}x more than the ledger-wide baseline; corroboration "
             "signal only, not a standalone indicator."),
        ))
    return out


# ----------------------------------------------------------------------------- R14
def r14_user_account_novelty(con, thresholds):
    names = _account_names(con)
    max_pd = con.execute("SELECT MAX(posting_date) FROM gl").fetchone()[0]
    if max_pd is None:
        return []
    late_from = _dt.date(max_pd.year if max_pd.month >= 10 else max_pd.year - 1, 10, 1)

    combos = con.execute(
        """
        SELECT user_id, hb_account, MIN(posting_date) fd, SUM(abs(amount)) s, COUNT(*) n,
               LIST(DISTINCT entry_id)
        FROM gl WHERE posting_type <> 'Vortrag'
        GROUP BY 1, 2
        """
    ).fetchall()
    population = len(combos)
    hits = sorted([c for c in combos if c[2] >= late_from and c[3] >= R14_MIN_SUM],
                  key=lambda c: -c[3])

    out = []
    for user, acct, fd, s, n, entry_ids in hits:
        entry_ids = sorted(entry_ids)[:20]
        out.append(_mk(
            "R14", "user_account_novelty", len(out) + 1, "contextual", "low",
            f"user_account:{user}|{acct}", f"{user} x {acct} {names.get(acct, '')}",
            entry_ids, _entry_source_ids(con, entry_ids),
            {"first_posting_date": str(fd), "late_window_start": str(late_from),
             "sum_abs_amount": round(s, 2), "n_lines": n,
             "prior_history_before_window": False},
            {"population_size": population, "rule_hits": len(hits),
             "peers_with_expected_evidence": 0},
            (f"User {user} posted to account {acct} for the first time on {fd} "
             f"(no earlier activity in the fiscal year), with a cumulative absolute volume of "
             f"{s:,.2f} EUR."),
        ))

    # Time-of-day / weekend picture: only meaningful if the timestamp distribution has
    # real variance. Practice-set timestamps are uniform synthetic noise -> auto-skip.
    hours = con.execute(
        """
        SELECT CAST(substr(entry_time, 1, 2) AS INT) h, COUNT(*)
        FROM gl WHERE posting_type <> 'Vortrag' AND entry_time IS NOT NULL AND length(entry_time) >= 2
        GROUP BY 1
        """
    ).fetchall()
    counts = [c for _, c in hours]
    hour_cv = (statistics.pstdev(counts) / statistics.mean(counts)) if len(counts) > 1 else 0.0
    weekend_share = con.execute(
        "SELECT COUNT(*) FILTER (WHERE dayofweek(posting_date) IN (0, 6)) * 1.0 / COUNT(*) FROM gl"
    ).fetchone()[0]
    time_signal = hour_cv >= R14_HOUR_CV_MIN and abs(weekend_share - 2.0 / 7.0) > R14_WEEKEND_TOL
    if time_signal:
        wk = con.execute(
            """
            SELECT user_id, COUNT(DISTINCT entry_id) n, SUM(abs(amount)) s, LIST(DISTINCT entry_id)
            FROM gl WHERE dayofweek(posting_date) IN (0, 6) AND posting_type <> 'Vortrag'
            GROUP BY 1 HAVING SUM(abs(amount)) >= ? ORDER BY s DESC LIMIT 5
            """,
            [R14_MIN_SUM],
        ).fetchall()
        for user, n, s, entry_ids in wk:
            entry_ids = sorted(entry_ids)[:20]
            out.append(_mk(
                "R14", "user_account_novelty", len(out) + 1, "contextual", "low",
                f"user:{user}", user,
                entry_ids, _entry_source_ids(con, entry_ids),
                {"signal": "weekend_posting", "n_weekend_entries": n,
                 "sum_abs_amount": round(s, 2), "hour_cv": round(hour_cv, 3),
                 "weekend_share": round(weekend_share, 3)},
                {"population_size": population, "rule_hits": len(hits),
                 "peers_with_expected_evidence": 0},
                f"User {user} recorded {n} journal entries on weekends totalling {s:,.2f} EUR.",
            ))
    return out


# ----------------------------------------------------------------------------- export
RULES = [
    {"rule_id": "R09", "rule_name": "rare_account_cooccurrence", "channel": "statistical",
     "fn": r09_rare_account_cooccurrence},
    {"rule_id": "R10", "rule_name": "recurring_series_deviation", "channel": "statistical",
     "fn": r10_recurring_series_deviation},
    {"rule_id": "R11", "rule_name": "round_amount_concentration", "channel": "statistical",
     "fn": r11_round_amount_concentration},
    {"rule_id": "R12", "rule_name": "global_amount_outliers", "channel": "statistical",
     "fn": r12_global_amount_outliers},
    {"rule_id": "R14", "rule_name": "user_account_novelty", "channel": "statistical",
     "fn": r14_user_account_novelty},
    # R13 deliberately last: it reads the in-process registry filled by the other rules
    # so Benford divergence can be attached to already-flagged entities.
    {"rule_id": "R13", "rule_name": "benford_divergence", "channel": "statistical",
     "fn": r13_benford_divergence, "corroboration_only": True},
]


if __name__ == "__main__":  # self-test against the real build DB
    import duckdb

    from financeaudit.core.config import BUILD_DIR, DB_PATH

    thr_path = BUILD_DIR / "thresholds.json"
    thresholds = json.loads(thr_path.read_text()) if thr_path.exists() else {}
    con = duckdb.connect(str(DB_PATH), read_only=True)
    total = 0
    for rule in RULES:
        cands = rule["fn"](con, thresholds)
        total += len(cands)
        print(f"{rule['rule_id']} {rule['rule_name']}: {len(cands)} candidates")
        for c in cands:
            print(f"   {c['candidate_id']} [{c['risk_tier']}] {c['entity_key']} :: {c['description'][:110]}")
    print(f"TOTAL statistical candidates: {total}")
