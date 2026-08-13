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
{resolved_note_text[:3000] if resolved_note_text else 'Not available'}
"""
