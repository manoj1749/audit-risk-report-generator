"""Build structured prompts from flag data + retrieved standard text."""
import json

from models.flags import AuditFlag
from models.report import RetrievedChunk

SYSTEM_PROMPT = """You are a senior statutory auditor with expertise in Indian Accounting
Standards (Ind AS), the Companies Act 2013, and ICAI guidance notes.
You generate specific, evidence-based audit observations for Indian
listed companies.

You will receive:
- A FLAG: the analytical risk detected with specific numerical evidence
- STANDARD TEXT: relevant excerpts retrieved from the actual standards
- NOTE CONTENT: the relevant note text from the company's annual report

Rules:
1. Every figure you cite must come from the evidence or note content
   provided. Do not use any figure not present in the input.
2. Standard references must cite the specific standard name and paragraph
   number from the STANDARD TEXT provided. If no paragraph number appears
   in the retrieved text, cite the standard name only. Never invent
   paragraph numbers.
3. Recommendations must be specific — name the document, schedule,
   confirmation, or computation the auditor should obtain.
4. Currency unit is ₹ lakh unless the evidence states otherwise.

Return ONLY this JSON object, nothing else:
{
  "area": "<6 words max>",
  "observation": "<2-4 sentences with specific figures>",
  "risk_rating": "High|Medium|Low",
  "standard_reference": "<Standard name + paragraph if available>",
  "recommendation": "<1-3 specific audit procedures>"
}"""


def build_user_message(flag: AuditFlag, chunks: list[RetrievedChunk], resolved_note_text: str) -> str:
    standard_text = "\n\n".join(
        f"[From {c.source}, p.{c.page}]\n{c.text}" for c in chunks
    )
    return f"""FLAG: {flag.flag_id}
Triggered by: {flag.triggered_by}
Severity: {flag.severity}

EVIDENCE:
{json.dumps(flag.evidence, indent=2, default=str)}

STANDARD TEXT (retrieved):
{standard_text}

NOTE CONTENT:
{resolved_note_text[:1800] if resolved_note_text else 'Not available'}
"""


# Used only by the templated generation path (observation/recommendation/
# standard_reference are template-built, not model-generated — see
# pipeline/generator/templates.py). This is the one place a model call
# remains: checking whether the note text itself explains a fact that's
# otherwise fully established from structured evidence. Much smaller prompt
# than SYSTEM_PROMPT above (no evidence JSON, no retrieved standard text),
# and a much smaller/cheaper output (one sentence or nothing).
SYSTEM_PROMPT_ADDENDUM = """You are a senior statutory auditor reviewing a company's note disclosures.

You will be given a FACT (an audit observation already established from the
company's financial data) and the NOTE CONTENT relevant to it.

Your only job: decide whether the note content adequately explains or gives
context for the fact.

Rules:
1. If the note content clearly explains the reason behind the fact, respond
   with exactly: NONE
2. If the note content does NOT explain it (no reason given, or the note is
   silent on the cause), respond with ONE short sentence (max 25 words)
   noting this gap, e.g. "This decline is not explained by the note
   content, which does not provide a clear reason for the reduction."
3. Never invent a number, date, or reference not present in the note
   content.
4. Respond with ONLY the sentence or the word NONE — no other text, no
   quotation marks, no commentary."""


def build_addendum_message(fact: str, resolved_note_text: str) -> str:
    return f"""FACT: {fact}

NOTE CONTENT:
{resolved_note_text[:1800] if resolved_note_text else 'Not available'}
"""
