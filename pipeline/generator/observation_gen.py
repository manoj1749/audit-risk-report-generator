"""Local, free, open-weight narrative generation: structured flag -> parsed observation.

Runs entirely in-process via Apple's mlx-lm (no API key, no external service).
The model only ever sees a FLAG + retrieved STANDARD TEXT + NOTE CONTENT and is
instructed to cite only figures/references present in that input — it never
sees the raw document, so it cannot introduce numbers that weren't already
deterministically extracted by Layers 1-4.
"""
import json
import re
import threading

from loguru import logger

import config
from models.flags import AuditFlag
from models.report import ObservationResult, RetrievedChunk
from pipeline.generator.prompt_builder import SYSTEM_PROMPT, build_user_message
from pipeline.retrieval.standards_retriever import retrieve_for_flag

_model = None
_tokenizer = None
_model_load_lock = threading.Lock()

# A single local model instance is not safe for concurrent generate() calls —
# Streamlit can have multiple sessions (tabs, users, or a straggler thread from
# a session the browser already abandoned) hitting this module at once. Every
# generation call is serialized process-wide through this lock.
_generation_lock = threading.Lock()

_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_NUMBER_TOKEN_PATTERN = re.compile(r"-?[\d,]+\.?\d*")
_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the "
    "JSON object described above — no code fences, no commentary, no extra text."
)


def _get_model():
    """Lazily load the local model once per process (module-level singleton,
    same pattern as the embedding model cache in line_item_mapper.py)."""
    global _model, _tokenizer
    if _model is None:
        with _model_load_lock:
            if _model is None:  # re-check: another thread may have loaded it while we waited
                from mlx_lm import load

                logger.info(f"Loading local LLM {config.LOCAL_LLM_MODEL} (first call only)...")
                _model, _tokenizer = load(config.LOCAL_LLM_MODEL)
    return _model, _tokenizer


def _build_prompt(tokenizer, user_message: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def _parse_json_response(raw: str) -> dict | None:
    cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_PATTERN.search(cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _warn_on_number_mismatch(observation_text: str, evidence: dict, flag_id: str) -> None:
    """Best-effort diagnostic (non-blocking): a small local model can occasionally
    mis-transcribe a large figure (e.g. shift a decimal/comma) even though the
    instruction is to only use provided numbers. Log a warning so a reviewer can
    catch it — the UI's evidence expander always shows the real ground-truth value
    alongside the narrative regardless of this check."""
    from utils.text_utils import parse_indian_number

    obs_values = [
        v for tok in _NUMBER_TOKEN_PATTERN.findall(observation_text)
        if (v := parse_indian_number(tok)) is not None
    ]
    for key in ("current", "prior"):
        val = evidence.get(key)
        if isinstance(val, (int, float)) and abs(val) >= 100:
            if not any(abs(v - val) <= max(abs(val), 1) * 0.01 for v in obs_values):
                logger.warning(
                    f"{flag_id}: evidence {key}={val} not matched (within 1%) in the "
                    f"generated observation text — possible transcription error, verify manually"
                )


def _run_generation(prompt: str) -> str:
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = _get_model()
    with _generation_lock:
        return generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=config.LOCAL_LLM_MAX_TOKENS,
            sampler=make_sampler(temp=config.LOCAL_LLM_TEMPERATURE),
        )


async def generate_observation(
    flag: AuditFlag,
    chunks: list[RetrievedChunk],
    resolved_note: str,
) -> ObservationResult | None:
    _, tokenizer = _get_model()
    user_message = build_user_message(flag, chunks, resolved_note)

    for attempt in range(2):
        message = user_message if attempt == 0 else user_message + _RETRY_SUFFIX
        prompt = _build_prompt(tokenizer, message)
        try:
            # Called synchronously (not via asyncio.to_thread) so every mlx/Metal
            # call in the process happens on this one thread — mlx's GPU command
            # queue is not guaranteed safe across arbitrary thread hand-offs, and
            # there's no real concurrency to gain since generation is already
            # serialized on one local model instance.
            raw = _run_generation(prompt)
        except Exception as e:
            logger.error(f"Local generation failed for flag {flag.flag_id}: {e}")
            return None

        data = _parse_json_response(raw)
        if data is not None:
            try:
                result = ObservationResult(**data, flag_id=flag.flag_id, evidence=flag.evidence)
                _warn_on_number_mismatch(result.observation, flag.evidence, flag.flag_id)
                return result
            except Exception as e:
                logger.warning(f"Model output didn't match ObservationResult schema for {flag.flag_id}: {e}")
                # fall through to retry

    logger.error(f"JSON parse failed for flag {flag.flag_id} after retry")
    return None


async def generate_all_observations(
    flags: list[AuditFlag],
    notes: dict,
    xref_graph,
) -> list[ObservationResult]:
    """Generate observations for every flag.

    Unlike a remote API, a single local model instance can't usefully serve
    concurrent requests — generation is compute-bound on one accelerator, and
    mlx's GPU command queue isn't safe across arbitrary thread hand-offs — so
    flags are processed one at a time, synchronously, on the calling thread.
    """
    results: list[ObservationResult] = []

    for flag in flags:
        chunks = retrieve_for_flag(flag)
        resolved = xref_graph.get_resolved_note(flag.note_ids[0]) if flag.note_ids else ""
        result = await generate_observation(flag, chunks, resolved)
        if result is not None:
            results.append(result)

    order = {"High": 0, "Medium": 1, "Low": 2}
    results.sort(key=lambda x: order[x.risk_rating])

    return results
