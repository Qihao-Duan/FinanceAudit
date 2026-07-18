#!/usr/bin/env python3
"""Generate UI development fixtures matching CONTRACTS.md §4 findings schema.

All row/line numbers, amounts and quotes below were verified against the real
practice dossier (data/practice/) so the standalone source viewer renders
genuine evidence. Wording is neutral (no fraud accusations) per contract.

Run:  /usr/bin/python3 ui/fixtures/make_fixtures.py
Writes: findings.fixture.json, manifest.fixture.json, source_registry.fixture.json
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

GL_FILE = "Sachkonten/Sachkontobuchungen.txt"
VEND_FILE = "Kreditoren/Lieferanten.txt"
STAMM_FILE = "Begleitdokumente/Stammdatenaenderungen_2025.csv"
PERM_FILE = "Begleitdokumente/Berechtigungsauswertung_2025.xlsx"
PLAN_FILE = "Begleitdokumente/Pruefungsplanung_JET_2025.docx"
GESELL_FILE = "Begleitdokumente/Gesellschafterliste_Beteiligungen.csv"
EXPORT_FILE = "Begleitdokumente/Exportprotokoll_GDPdU_2025.pdf"

GL_COLUMNS = [
    "SACHKONTONUMMER", "PERIODENCODE", "STEUERBUCHUNGSREFERENZ",
    "PERIODENZUGEHOERIGKEIT", "BUCHUNGSTYP", "KORREKTUR", "HABENBUCHUNG",
    "BUCHUNGSBETRAG", "BUCHUNGSWAEHRUNG", "BUCHUNGSWERT", "BUCHUNGSTEXT",
    "BUCHUNGSDATUM", "BUCHUNGSNUMMER", "BELEGDATUM", "BELEGNUMMER",
    "SPEZIALBUCHUNG", "ERFASSUNGSNUMMER", "JOURNALZEILE", "GEGENKONTO",
    "DOKUMENT", "BENUTZERKENNUNG", "ERFASSUNGSDATUM", "ERFASSUNGSZEIT",
    "FESTSCHREIBUNG",
]
VEND_COLUMNS = [
    "KONTONUMMER", "USTID", "STRASSE", "PLZ", "ORT", "LAND", "NAME",
    "GRUPPE", "FELD9", "MWST_GRUPPE", "WAEHRUNG",
]

registry = {"files": {}, "sources": {}}

registry["files"] = {
    GL_FILE: {"format": "delimited", "encoding": "cp1252", "delimiter": ";",
              "quotechar": '"', "has_header": False, "columns": GL_COLUMNS},
    VEND_FILE: {"format": "delimited", "encoding": "cp1252", "delimiter": ";",
                "quotechar": '"', "has_header": False, "columns": VEND_COLUMNS},
    STAMM_FILE: {"format": "delimited", "encoding": "cp1252", "delimiter": ";",
                 "quotechar": '"', "has_header": True, "columns": None},
    GESELL_FILE: {"format": "delimited", "encoding": "cp1252", "delimiter": ";",
                  "quotechar": '"', "has_header": True, "columns": None},
    PERM_FILE: {"format": "xlsx", "sheet": "Berechtigungen", "header_row": 3},
    PLAN_FILE: {"format": "docx"},
    EXPORT_FILE: {"format": "pdf"},
}


def sid(file: str, locator: str) -> str:
    """Deterministic 16-hex source id, same shape as the pipeline's."""
    return hashlib.sha1(f"fixture|{file}|{locator}".encode()).hexdigest()[:16]


def reg_row(file: str, row_no: int, label: str) -> str:
    s = sid(file, f"row:{row_no}")
    registry["sources"][s] = {
        "source_id": s, "file": file, "kind": "cell_row", "row_no": row_no,
        "display_locator": f"{file} · Zeile {row_no + 1} · {label}",
    }
    return s


def reg_xlsx(file: str, excel_row: int, label: str) -> str:
    s = sid(file, f"xlsxrow:{excel_row}")
    registry["sources"][s] = {
        "source_id": s, "file": file, "kind": "cell_row", "row_no": excel_row,
        "display_locator": f"{file} · Blatt 'Berechtigungen' · Zeile {excel_row} · {label}",
    }
    return s


def reg_para(file: str, para_no: int, label: str) -> str:
    s = sid(file, f"para:{para_no}")
    registry["sources"][s] = {
        "source_id": s, "file": file, "kind": "para", "para_no": para_no,
        "display_locator": f"{file} · Absatz {para_no + 1} · {label}",
    }
    return s


def reg_page(file: str, page_index: int, label: str) -> str:
    s = sid(file, f"page:{page_index}")
    registry["sources"][s] = {
        "source_id": s, "file": file, "kind": "page", "page_index": page_index,
        "page_label": str(page_index + 1),
        "display_locator": f"{file} · Seite {page_index + 1} · {label}",
    }
    return s


def cell(source_id, file, rows, col, cell_value):
    return {"kind": "cell", "source_id": source_id, "file": file,
            "row_ids": rows, "col": col, "cell_value": cell_value}


def passage(source_id, file, quote, page_index=0, page_label="1",
            precision="quote_rect", para_no=None):
    c = {"kind": "passage", "source_id": source_id, "file": file,
         "page_index": page_index, "page_label": page_label,
         "quote": quote, "locator_precision": precision}
    if para_no is not None:
        c["para_no"] = para_no
    return c


def val(kind, value, unit, formula, operand_refs):
    return {"kind": kind, "value": value, "currency_or_unit": unit,
            "formula": formula, "formula_version": "fixture-v1",
            "input_set_hash": hashlib.sha1(formula.encode()).hexdigest()[:12],
            "operand_refs": operand_refs}


# ---------------------------------------------------------------- F1 rows ---
f1_inv = [  # (gl line, doc_ref, gross, invoice date, pay line, pay date)
    (20163, "ER901416", 53550.00, "19.05.2025", 20164, "21.05.2025"),
    (20168, "ER901417", 71400.00, "04.07.2025", 20169, "06.07.2025"),
    (20173, "ER901418", 45220.00, "15.09.2025", 20174, "17.09.2025"),
    (20178, "ER901419", 61880.00, "10.11.2025", 20179, "12.11.2025"),
    (20183, "ER901420", 63070.00, "18.12.2025", 20184, "20.12.2025"),
]
f1_inv_sids = {ln: reg_row(GL_FILE, ln, f"Kreditorenrechnung {ref} 209101")
               for ln, ref, *_ in f1_inv}
f1_pay_sids = {pl: reg_row(GL_FILE, pl, f"Zahlung {ref} 209101")
               for ln, ref, g, d, pl, pd in f1_inv}
sid_vend_209101 = reg_row(VEND_FILE, 130, "Kreditor 209101 Ratio Consulting GmbH")
sid_stamm_209101 = reg_row(STAMM_FILE, 7, "Neuanlage Kreditor 209101, 12.05.2025")
sid_perm_u05 = reg_xlsx(PERM_FILE, 7, "Benutzer MV-U05 (Einkauf)")
sid_plan_sod = reg_para(PLAN_FILE, 4, "Interne Kontrollen: Vier-Augen-Prinzip")

F1 = {
    "finding_id": "F-0001",
    "title": "Kreditor 209101 Ratio Consulting GmbH — Kontrollumfeld-Anomalie / vendor-control anomaly",
    "scheme": "fictitious_vendor",
    "mechanism": "vendor_subledger_via_gl",
    "anomaly_type": "contextual",
    "reporting_status": "finding",
    "entity_key": "vendor:209101",
    "entity_label": "Ratio Consulting GmbH (209101)",
    "amount_eur": 248000.00,
    "description": ("Kreditor 209101 wurde am 12.05.2025 durch MV-U05 angelegt und durch "
                    "denselben Benutzer genehmigt; fünf Beratungsrechnungen (netto 248.000 EUR) "
                    "wurden jeweils innerhalb von zwei Tagen bezahlt; der im Buchungstext "
                    "zitierte Rahmenvertrag ist im Dossier nicht auffindbar."),
    "claims": [
        {"claim_id": "F-0001-C1", "role": "core", "type": "numerical",
         "assertion": "Kreditor 209101 (Ratio Consulting GmbH) erhielt 2025 fünf Kreditorenrechnungen mit Bruttosumme 295.120,00 EUR (ER901416–ER901420).",
         "value": val("amount", 295120.00, "EUR",
                      "sum(BUCHUNGSBETRAG[ER901416..ER901420, Konto 330000-209101, Kreditorenrechnung]) = 53550.00+71400.00+45220.00+61880.00+63070.00",
                      [f1_inv_sids[ln] for ln, *_ in f1_inv]),
         "citations": [cell(f1_inv_sids[ln], GL_FILE, [ln], "BUCHUNGSBETRAG", f"-{g:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."))
                       for ln, ref, g, *_ in f1_inv],
         "verdict": "supported"},
        {"claim_id": "F-0001-C2", "role": "core", "type": "computational",
         "assertion": "Der Nettowert der fünf Rechnungen beträgt 248.000,00 EUR (Brutto 295.120,00 / 1,19 bei 19% USt).",
         "value": val("amount", 248000.00, "EUR", "295120.00 / 1.19", ["F-0001-C1"]),
         "citations": [cell(f1_inv_sids[20163], GL_FILE, [ln for ln, *_ in f1_inv],
                            "BUCHUNGSBETRAG", "Summe brutto 295.120,00")],
         "verdict": "supported"},
        {"claim_id": "F-0001-C3", "role": "core", "type": "entity_attribute",
         "assertion": "Die Neuanlage des Kreditors 209101 am 12.05.2025 wurde von MV-U05 durchgeführt und von MV-U05 genehmigt (GEAENDERT_VON = GENEHMIGT_VON).",
         "value": None,
         "citations": [cell(sid_stamm_209101, STAMM_FILE, [7], "GEAENDERT_VON/GENEHMIGT_VON", "MV-U05;MV-U05"),
                       cell(sid_vend_209101, VEND_FILE, [130], "NAME", "Ratio Consulting GmbH")],
         "verdict": "supported"},
        {"claim_id": "F-0001-C4", "role": "supporting", "type": "temporal",
         "assertion": "Alle fünf Rechnungen wurden jeweils 2 Tage nach Rechnungsdatum bezahlt (z.B. ER901416: Rechnung 19.05.2025, Zahlung 21.05.2025).",
         "value": val("count", 5, "Zahlungen ≤ 2 Tage", "count(pay_date - inv_date <= 2d)",
                      [f1_pay_sids[pl] for *_, pl, _pd in f1_inv]),
         "citations": [cell(f1_pay_sids[pl], GL_FILE, [pl], "BUCHUNGSDATUM", pd)
                       for *_, pl, pd in f1_inv],
         "verdict": "supported"},
        {"claim_id": "F-0001-C5", "role": "supporting", "type": "entity_attribute",
         "assertion": "MV-U05 (Einkauf) verfügt laut Berechtigungsauswertung zugleich über Buchungs-, Zahlungslauf- und Kreditorenanlage-Rechte.",
         "value": None,
         "citations": [cell(sid_perm_u05, PERM_FILE, [7], "Buchen/Zahlungslauf/Stammdaten", "X/X/X"),
                       passage(sid_plan_sod, PLAN_FILE,
                               "Funktionstrennung zwischen Kreditoren-Stammdaten",
                               para_no=4, precision="quote_rect")],
         "verdict": "supported"},
        {"claim_id": "F-0001-C6", "role": "supporting", "type": "absence",
         "assertion": "Der in allen fünf Buchungstexten referenzierte 'Rahmenvertrag' ist keinem Dokument des Dossiers zuordenbar; zu 209101 existiert kein Wareneingang.",
         "value": None,
         "citations": [cell(f1_inv_sids[20163], GL_FILE, [20163], "BUCHUNGSTEXT",
                            "Beratungsleistungen lt. Rahmenvertrag")],
         "verdict": "supported"},
    ],
    "expected_evidence": ["Rahmenvertrag Beratungsleistungen", "Unabhängige Freigabe der Kreditorenanlage",
                          "Leistungsnachweise zu ER901416–ER901420"],
    "missing_evidence": [
        {"item": "Rahmenvertrag Beratungsleistungen 209101", "status": "not_provided",
         "note": "Im Buchungstext referenziert; im Dossier nicht enthalten."},
        {"item": "Leistungsnachweise Beratung", "status": "not_provided", "note": "Dienstleistung — Wareneingangsliste umfasst nur Material/Logistik (population scope)."},
    ],
    "pbc_requests": ["Rahmenvertrag mit Ratio Consulting GmbH (209101)",
                     "Leistungsnachweise / Stundenaufstellungen zu ER901416–ER901420",
                     "Bankverbindungsnachweis und Zahlungsbelege (Kontoauszüge) zu AZ6602859–AZ6602863"],
    "counterevidence": [],
    "innocence_checked": [
        {"predicate": "Wareneingang/Abnahme vorhanden?", "checked": True,
         "result": "Kein Eintrag zu 209101 in Wareneingangsliste_2025 (Scope: Material/Logistik, Dienstleistungen nicht enthalten).",
         "outcome": "no_counterevidence"},
        {"predicate": "Vertragsreferenz auflösbar?", "checked": True,
         "result": "'Rahmenvertrag' in keinem Begleitdokument auffindbar.", "outcome": "no_counterevidence"},
        {"predicate": "Unabhängige Genehmigung?", "checked": True,
         "result": "Stammdatenänderung: Ersteller = Genehmiger (MV-U05).", "outcome": "no_counterevidence"},
        {"predicate": "Normale Kontohistorie?", "checked": True,
         "result": "Erste Aktivität 12.05.2025; ausschließlich die fünf Beratungsrechnungen.", "outcome": "no_counterevidence"},
    ],
    "parser_coverage": "complete",
    "denominators": {"population_size": 8391, "rule_hits": 1,
                     "peers_with_expected_evidence": 0, "defender_rejected_hits": 0},
    "disposition": "report",
    "llm_used": False,
}

# ---------------------------------------------------------------- F4 rows ---
f4 = [(20206, "AZ6602865", 9780.00, "08:59:37"),
      (20208, "AZ6602866", 9820.00, "07:28:30"),
      (20210, "AZ6602867", 9750.00, "09:57:16"),
      (20212, "AZ6602868", 9690.00, "18:55:07")]
f4_sids = {ln: reg_row(GL_FILE, ln, f"Zahlung {ref} 200007")
           for ln, ref, *_ in f4}
sid_plan_limit = reg_para(PLAN_FILE, 4, "Zahlungsfreigaben ab 10.000 EUR")
sid_perm_u11 = reg_xlsx(PERM_FILE, 10, "Benutzer MV-U11 (Zahlungsverkehr)")

F4 = {
    "finding_id": "F-0002",
    "title": "Kreditor 200007 — vier Teilzahlungen unter Freigabegrenze am selben Tag / threshold splitting",
    "scheme": "threshold_splitting",
    "mechanism": "payment_layer_gl",
    "anomaly_type": "rule_based",
    "reporting_status": "finding",
    "entity_key": "vendor:200007",
    "entity_label": "Kreditor 200007",
    "amount_eur": 39040.00,
    "description": ("Am 14.10.2025 wurden vier Zahlungen (9.780 / 9.820 / 9.750 / 9.690 EUR) an "
                    "Kreditor 200007 unter derselben Belegnummer SAMMEL-200007 erfasst; jede "
                    "Einzelzahlung liegt unter der Freigabegrenze von 10.000 EUR, die Summe "
                    "(39.040 EUR) darüber."),
    "claims": [
        {"claim_id": "F-0002-C1", "role": "core", "type": "numerical",
         "assertion": "Am 14.10.2025 wurden vier Zahlungen an Kreditor 200007 mit Gesamtsumme 39.040,00 EUR gebucht (Belegnummer jeweils SAMMEL-200007, Benutzer MV-U11).",
         "value": val("amount", 39040.00, "EUR",
                      "9780.00 + 9820.00 + 9750.00 + 9690.00",
                      [f4_sids[ln] for ln, *_ in f4]),
         "citations": [cell(f4_sids[ln], GL_FILE, [ln], "BUCHUNGSBETRAG",
                            f"{amt:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."))
                       for ln, ref, amt, t in f4],
         "verdict": "supported"},
        {"claim_id": "F-0002-C2", "role": "core", "type": "comparative",
         "assertion": "Jede der vier Einzelzahlungen (max. 9.820,00 EUR) liegt unter der Grenze von 10.000 EUR, ab der laut Prüfungsplanung eine zweite Freigabe erforderlich ist; die Gesamtsumme liegt darüber.",
         "value": val("amount", 10000.00, "EUR", "max(9780, 9820, 9750, 9690) < 10000 <= 39040",
                      [f4_sids[20208], sid_plan_limit]),
         "citations": [passage(sid_plan_limit, PLAN_FILE,
                               "Zahlungsfreigaben ab 10.000 EUR erfordern eine zweite Freigabe",
                               para_no=4, precision="quote_rect"),
                       cell(f4_sids[20208], GL_FILE, [20208], "BUCHUNGSBETRAG", "9.820,00")],
         "verdict": "supported"},
        {"claim_id": "F-0002-C3", "role": "supporting", "type": "entity_attribute",
         "assertion": "Alle vier Zahlungen tragen dieselbe Belegnummer SAMMEL-200007 — konsistent mit einer einzelnen zugrunde liegenden Verbindlichkeit.",
         "value": None,
         "citations": [cell(f4_sids[ln], GL_FILE, [ln], "BELEGNUMMER", "SAMMEL-200007")
                       for ln, *_ in f4[:2]],
         "verdict": "supported"},
        {"claim_id": "F-0002-C4", "role": "supporting", "type": "entity_attribute",
         "assertion": "Buchender Benutzer MV-U11 (Zahlungsverkehr) besitzt Zahlungslauf-Rechte, aber keine Freigaberechte.",
         "value": None,
         "citations": [cell(sid_perm_u11, PERM_FILE, [10], "Zahlungslauf", "X")],
         "verdict": "supported"},
    ],
    "expected_evidence": ["Ursprüngliche Lieferantenrechnung(en) zu SAMMEL-200007",
                          "Sammelzahlungs- oder Ratenzahlungsvereinbarung", "Zweitfreigabe"],
    "missing_evidence": [
        {"item": "Originalrechnung zu SAMMEL-200007", "status": "not_provided",
         "note": "Belegnummer verweist auf Sammelbeleg, keine Einzelrechnung im Dossier."},
        {"item": "Zweitfreigabe / Vier-Augen-Nachweis", "status": "not_provided",
         "note": "Freigabe-Log ist journalbezogen; Zahlungsfreigaben nicht enthalten (coverage unproven)."},
    ],
    "pbc_requests": ["Originalrechnung(en) und Zahlungsavis zu SAMMEL-200007 (Kreditor 200007)",
                     "Nachweis einer Sammelzahlungs-Autorisierung oder Ratenvereinbarung"],
    "counterevidence": [],
    "innocence_checked": [
        {"predicate": "Sammelzahlungs-Autorisierung vorhanden?", "checked": True,
         "result": "Keine Autorisierung im Dossier auffindbar.", "outcome": "no_counterevidence"},
        {"predicate": "Ratenzahlungsvereinbarung?", "checked": True,
         "result": "Kein Vertragsdokument im Dossier.", "outcome": "no_counterevidence"},
        {"predicate": "Gemeinsame Ursprungsschuld?", "checked": True,
         "result": "Identische Belegnummer SAMMEL-200007 auf allen vier Zahlungen (spricht für eine einzelne Schuld; erhöht Einstufung).",
         "outcome": "corroborates"},
    ],
    "parser_coverage": "complete",
    "denominators": {"population_size": 4212, "rule_hits": 1,
                     "peers_with_expected_evidence": 0, "defender_rejected_hits": 0},
    "disposition": "report",
    "llm_used": False,
}

# ---------------------------------------------------------------- F2 rows ---
f2 = [  # (asset line, net, text line, text, belegnr)
    (20186, 28000.00, 20187, "Reparatur Konfektioniermaschine Linie 2", "ER901421"),
    (20189, 34000.00, 20190, "Austausch Hydraulikaggregat Presse 3", "ER901422"),
    (20192, 15500.00, 20193, "Instandsetzung Förderband Halle II", "ER901423"),
    (20195, 41000.00, 20196, "Generalüberholung Stanzautomat", "ER901424"),
    (20198, 12800.00, 20199, "Reparatur Kälteanlage Lager", "ER901425"),
    (20201, 19500.00, 20202, "Austausch Antriebssteuerung Palettierer", "ER901426"),
]
f2_asset_sids = {ln: reg_row(GL_FILE, ln, f"Aktivierung {ref}") for ln, n, tl, t, ref in f2}
f2_text_sids = {tl: reg_row(GL_FILE, tl, f"USt-Zeile {ref}") for ln, n, tl, t, ref in f2}
sid_plan_k3 = reg_para(PLAN_FILE, 5, "Selektionskriterium K3: reparaturtypische Bezeichnung")

F2 = {
    "finding_id": "F-0003",
    "title": "Sechs Anlagenzugänge mit reparaturtypischer Bezeichnung (netto 150.800 EUR) / repair-named capitalizations",
    "scheme": "expense_capitalization",
    "mechanism": "asset_layer_gl",
    "anomaly_type": "contextual",
    "reporting_status": "verify",
    "entity_key": "account:040000/060000",
    "entity_label": "Anlagenzugänge ER901421–ER901426",
    "amount_eur": 150800.00,
    "description": ("Sechs Anlagenzugänge 2025 (netto 150.800 EUR, ER901421–ER901426) tragen "
                    "Buchungstexte mit reparaturtypischer Bezeichnung (Reparatur/Austausch/"
                    "Instandsetzung/Generalüberholung). Alternative Erklärung — aktivierungs"
                    "fähiger Komponententausch — ist ohne technische Beurteilung nicht "
                    "ausschließbar; Einstufung: zu verifizieren."),
    "claims": [
        {"claim_id": "F-0003-C1", "role": "core", "type": "numerical",
         "assertion": "Sechs Anlagenzugänge (ER901421–ER901426) summieren sich auf netto 150.800,00 EUR.",
         "value": val("amount", 150800.00, "EUR",
                      "28000.00 + 34000.00 + 15500.00 + 41000.00 + 12800.00 + 19500.00",
                      [f2_asset_sids[ln] for ln, *_ in f2]),
         "citations": [cell(f2_asset_sids[ln], GL_FILE, [ln], "BUCHUNGSBETRAG",
                            f"{n:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."))
                       for ln, n, *_ in f2],
         "verdict": "supported"},
        {"claim_id": "F-0003-C2", "role": "core", "type": "entity_attribute",
         "assertion": "Die zugehörigen USt-/Kreditorzeilen derselben Belege tragen reparaturtypische Buchungstexte (Reparatur, Austausch, Instandsetzung, Generalüberholung).",
         "value": val("count", 6, "Belege", "count(text matches repair-typical vocabulary)",
                      [f2_text_sids[tl] for _, _, tl, *_ in f2]),
         "citations": [cell(f2_text_sids[tl], GL_FILE, [tl], "BUCHUNGSTEXT", t)
                       for _, _, tl, t, _ in f2],
         "verdict": "supported"},
        {"claim_id": "F-0003-C3", "role": "supporting", "type": "comparative",
         "assertion": "Das Selektionskriterium K3 der Prüfungsplanung adressiert genau dieses Muster (Zugänge im Anlagevermögen mit reparaturtypischer Bezeichnung).",
         "value": None,
         "citations": [passage(sid_plan_k3, PLAN_FILE,
                               "Zugänge im Anlagevermögen mit reparaturtypischer Bezeichnung",
                               para_no=5, precision="quote_rect")],
         "verdict": "supported"},
    ],
    "expected_evidence": ["Technische Beurteilung / Aktivierungsentscheidung je Beleg",
                          "Anlagenkarten mit AfA-Beginn", "Lieferantenrechnungen ER901421–ER901426"],
    "missing_evidence": [
        {"item": "Technische Beurteilung der sechs Maßnahmen", "status": "not_provided",
         "note": "Keine Aktivierungsbegründung im Dossier."},
    ],
    "pbc_requests": ["Rechnungen und technische Beschreibung zu ER901421–ER901426",
                     "Aktivierungsrichtlinie (Komponentenansatz) der Gesellschaft"],
    "counterevidence": [],
    "innocence_checked": [
        {"predicate": "Aktivierungsfähiger Komponententausch?", "checked": True,
         "result": "Nicht ausschließbar — keine technische Dokumentation im Dossier; daher Einstufung 'verify', keine Absichtsaussage.",
         "outcome": "inconclusive"},
        {"predicate": "AfA normal gestartet?", "checked": True,
         "result": "Planmäßige AfA 2025 unverändert 34.800,38 EUR × 12 — keine Anpassung an Zugänge erkennbar.",
         "outcome": "no_counterevidence"},
    ],
    "parser_coverage": "complete",
    "denominators": {"population_size": 61, "rule_hits": 6,
                     "peers_with_expected_evidence": 55, "defender_rejected_hits": 0},
    "disposition": "report",
    "llm_used": False,
}

# ---------------------------------------------------------------- D5 rows ---
sid_gesell = reg_row(GESELL_FILE, 5, "Verbundene Unternehmen, Zeile 6")
sid_vend_209113 = reg_row(VEND_FILE, 142, "Kreditor 209113")

D5 = {
    "finding_id": "F-0004",
    "title": "Kreditor 209113 — Identitätskonflikt zwischen Gesellschafterliste und Kreditorenstamm / entity conflict",
    "scheme": "related_party",
    "mechanism": "masterdata_layer",
    "anomaly_type": "contextual",
    "reporting_status": "observation",
    "entity_key": "vendor:209113",
    "entity_label": "Kreditor 209113",
    "amount_eur": 220000.00,
    "description": ("Die Gesellschafterliste ordnet das Personenkonto Kreditor 209113 der "
                    "Schwestergesellschaft Muster Verpackung Austria GmbH zu; der Kreditoren"
                    "stamm führt 209113 als Muster Beteiligungs GmbH, Wien (Muttergesellschaft). "
                    "Beide Dokumente stammen aus demselben Dossier; der Konflikt betrifft die "
                    "Zuordnung der Konzernumlage von 220.000 EUR."),
    "claims": [
        {"claim_id": "F-0004-C1", "role": "core", "type": "entity_attribute",
         "assertion": "Die Gesellschafterliste (Zeile 6) weist 'Personenkonto Kreditor 209113' der Muster Verpackung Austria GmbH (Schwestergesellschaft) zu.",
         "value": None,
         "citations": [cell(sid_gesell, GESELL_FILE, [5], "BEMERKUNG", "Personenkonto Kreditor 209113")],
         "verdict": "supported"},
        {"claim_id": "F-0004-C2", "role": "core", "type": "entity_attribute",
         "assertion": "Der Kreditorenstamm führt Konto 209113 unter dem Namen 'Muster Beteiligungs GmbH, Wien' (laut Gesellschafterliste die Muttergesellschaft).",
         "value": None,
         "citations": [cell(sid_vend_209113, VEND_FILE, [142], "NAME", "Muster Beteiligungs GmbH, Wien")],
         "verdict": "supported"},
        {"claim_id": "F-0004-C3", "role": "core", "type": "comparative",
         "assertion": "Die beiden Dokumente ordnen dasselbe Personenkonto 209113 zwei verschiedenen Konzerngesellschaften zu — ein Dokumentenkonflikt innerhalb des Dossiers.",
         "value": None,
         "citations": [cell(sid_gesell, GESELL_FILE, [5], "NAME", "Muster Verpackung Austria GmbH (AUT)"),
                       cell(sid_vend_209113, VEND_FILE, [142], "NAME", "Muster Beteiligungs GmbH, Wien")],
         "verdict": "supported"},
    ],
    "expected_evidence": ["Konzernumlagevertrag mit Verteilungsschlüssel", "Klärung der Kontenzuordnung 209113"],
    "missing_evidence": [
        {"item": "Konzernumlagevertrag", "status": "not_provided",
         "note": "Umlagebasis nicht im Dossier."},
    ],
    "pbc_requests": ["Konzernumlagevertrag inkl. Verteilungsschlüssel",
                     "Bestätigung, welcher Konzerngesellschaft Konto 209113 zugeordnet ist"],
    "counterevidence": [],
    "innocence_checked": [
        {"predicate": "Vereinbarung + Umlagebasis + Angabe konsistent?", "checked": True,
         "result": "Vertrag nicht im Dossier; Konsistenz nicht prüfbar.", "outcome": "inconclusive"},
        {"predicate": "Entität eindeutig identifiziert?", "checked": True,
         "result": "Widersprüchliche Zuordnung in zwei Dossier-Dokumenten (USt-ID AT847854029 im Stamm).",
         "outcome": "counterevidence_conflict"},
    ],
    "parser_coverage": "complete",
    "denominators": {"population_size": 2, "rule_hits": 1,
                     "peers_with_expected_evidence": 0, "defender_rejected_hits": 0},
    "disposition": "observation",
    "llm_used": False,
}

# ---------------------------------------------------------------- D1 rows ---
d1_rows = [(20214, "040000-000197", 480000.00, "Acquisition"),
           (20215, "147000", 91200.00, "Investition Produktionslinie lt. Investitionsantrag IA-2025-04"),
           (20216, "330000-200003", -571200.00, "Investition Produktionslinie lt. Investitionsantrag IA-2025-04")]
d1_sids = {ln: reg_row(GL_FILE, ln, f"ER901435 Zeile {ln}") for ln, *_ in d1_rows}
sid_export_page = reg_page(EXPORT_FILE, 0, "Exportprotokoll, Dateiliste")

D1 = {
    "finding_id": "F-0005",
    "title": "Anlagenzugang 480.000 EUR (ER901435) — referenzierter Investitionsantrag IA-2025-04 nicht im Dossier / unresolved reference",
    "scheme": "controls_breach",
    "mechanism": "asset_layer_gl",
    "anomaly_type": "rule_based",
    "reporting_status": "verify",
    "entity_key": "doc:IA-2025-04",
    "entity_label": "Investitionsantrag IA-2025-04 (ER901435)",
    "amount_eur": 480000.00,
    "description": ("Der Anlagenzugang über 480.000 EUR netto (Beleg ER901435, 17.06.2025) "
                    "verweist im Buchungstext auf 'Investitionsantrag IA-2025-04'. Das "
                    "referenzierte Dokument ist im Dossier nicht enthalten; die Kernaussage "
                    "zur Genehmigung ist damit nicht verifizierbar (insufficient evidence)."),
    "claims": [
        {"claim_id": "F-0005-C1", "role": "supporting", "type": "numerical",
         "assertion": "Beleg ER901435 vom 17.06.2025 aktiviert 480.000,00 EUR (netto) auf Konto 040000-000197; Bruttoverbindlichkeit 571.200,00 EUR bei Kreditor 200003.",
         "value": val("amount", 480000.00, "EUR", "571200.00 / 1.19",
                      [d1_sids[20214], d1_sids[20216]]),
         "citations": [cell(d1_sids[20214], GL_FILE, [20214], "BUCHUNGSBETRAG", "480.000,00"),
                       cell(d1_sids[20216], GL_FILE, [20216], "BUCHUNGSBETRAG", "-571.200,00")],
         "verdict": "supported"},
        {"claim_id": "F-0005-C2", "role": "core", "type": "existence",
         "assertion": "Der im Buchungstext referenzierte 'Investitionsantrag IA-2025-04' liegt als genehmigendes Dokument im Dossier vor.",
         "value": None,
         "citations": [cell(d1_sids[20215], GL_FILE, [20215], "BUCHUNGSTEXT",
                            "Investition Produktionslinie lt. Investitionsantrag IA-2025-04"),
                       passage(sid_export_page, EXPORT_FILE,
                               "Muster Verpackungen GmbH – Exportprotokoll Datenträgerüberlassung",
                               precision="quote_rect")],
         "verdict": "unverifiable"},
    ],
    "expected_evidence": ["Investitionsantrag IA-2025-04 mit Genehmigungsvermerk"],
    "missing_evidence": [
        {"item": "Investitionsantrag IA-2025-04", "status": "not_provided",
         "note": "Referenziert im Buchungstext; nicht Teil der Datenträgerüberlassung (siehe Dateiliste im Exportprotokoll)."},
    ],
    "pbc_requests": ["Investitionsantrag IA-2025-04 inkl. Genehmigungskette",
                     "Abnahmeprotokoll / Inbetriebnahmenachweis der Produktionslinie"],
    "counterevidence": [],
    "innocence_checked": [
        {"predicate": "Genehmigungsdokument auffindbar?", "checked": True,
         "result": "IA-2025-04 in keinem Dossier-Dokument enthalten; Kernaussage unverifiable — Vorgang in Quarantäne, keine Wertung.",
         "outcome": "inconclusive"},
    ],
    "parser_coverage": "complete",
    "denominators": {"population_size": 61, "rule_hits": 1,
                     "peers_with_expected_evidence": 55, "defender_rejected_hits": 0},
    "disposition": "quarantine",
    "llm_used": False,
}

findings = [F1, F4, F2, D5, D1]

manifest = {
    "generated": "fixture",
    "extractor_version": "fixture-v1",
    "files": [
        {"file": "Sachkonten/Sachkontobuchungen.txt", "expected_units": 20258, "parsed_units": 20258,
         "parse_coverage": "complete", "population_scope": "GL 2025, vollständig (Soll=Haben=345.350.778,06)"},
        {"file": "Kreditoren/Lieferanten.txt", "expected_units": 150, "parsed_units": 150,
         "parse_coverage": "complete", "population_scope": "Kreditorenstamm"},
        {"file": "Kreditoren/Lieferantenbuchungen.txt", "expected_units": 4212, "parsed_units": 4212,
         "parse_coverage": "complete", "population_scope": "Kreditoren-Nebenbuch (Summe -13.208.855,00)"},
        {"file": "Debitoren/Kundenbuchungen.txt", "expected_units": 8570, "parsed_units": 8570,
         "parse_coverage": "complete", "population_scope": "Debitoren-Nebenbuch (Summe +13.412.543,05)"},
        {"file": "Begleitdokumente/Freigabe-Log_Journale_2025.csv", "expected_units": 91, "parsed_units": 91,
         "parse_coverage": "complete",
         "population_scope": "Journalbezogen; Abdeckung der Buchungsmasse UNBEWIESEN — nicht als Vollständigkeitsnachweis verwenden"},
        {"file": "Begleitdokumente/Wareneingangsliste_2025.csv", "expected_units": 214, "parsed_units": 214,
         "parse_coverage": "complete", "population_scope": "Nur Material/Logistik; KEINE Dienstleistungen"},
        {"file": "Begleitdokumente/Stammdatenaenderungen_2025.csv", "expected_units": 38, "parsed_units": 38,
         "parse_coverage": "complete", "population_scope": "Stammdatenänderungen 2025"},
        {"file": "Begleitdokumente/Berechtigungsauswertung_2025.xlsx", "expected_units": 12, "parsed_units": 12,
         "parse_coverage": "complete", "population_scope": "Benutzerberechtigungen per 31.12.2025"},
        {"file": "Begleitdokumente/Pruefungsplanung_JET_2025.docx", "expected_units": 9, "parsed_units": 9,
         "parse_coverage": "complete", "population_scope": "Prüfungsplanung (Schwellenwerte)"},
        {"file": "Begleitdokumente/Exportprotokoll_GDPdU_2025.pdf", "expected_units": 1, "parsed_units": 1,
         "parse_coverage": "complete", "population_scope": "Exportprotokoll mit SHA-256 je Datei"},
        {"file": "Begleitdokumente/Gesellschafterliste_Beteiligungen.csv", "expected_units": 6, "parsed_units": 6,
         "parse_coverage": "complete", "population_scope": "Gesellschafter und verbundene Unternehmen"},
    ],
    "hash_verification": "8/8 GDPdU-Textdateien: SHA-256 stimmt mit Exportprotokoll überein",
    "gl_balance": {"soll": 345350778.06, "haben": 345350778.06, "difference": 0.0},
    "lock_date": "2026-01-20",
    "thresholds": {"overall_materiality": 400000, "performance_materiality": 300000,
                   "jet_sampling": 25000, "payment_dual_approval": 10000},
}


def main():
    (HERE / "findings.fixture.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "manifest.fixture.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "source_registry.fixture.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"findings: {len(findings)}  sources: {len(registry['sources'])}")


if __name__ == "__main__":
    main()
