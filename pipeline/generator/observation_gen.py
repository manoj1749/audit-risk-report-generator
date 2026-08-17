"""Structured flag -> parsed observation (Layer 5).

Default path (config.LOCAL_LLM_USE_TEMPLATES=True): observation/
recommendation/standard_reference are built deterministically by
pipeline/generator/templates.py from the flag's already-fully-structured
evidence dict — every one of Layer 4's ~21 flag types already carries a
known governing standard (baked into standard_query at the point each check
was written) and a formulaic fact pattern, so there's no open-ended
judgment left for a model to make there. The one model call that remains
(config.LOCAL_LLM_NARRATIVE_ADDENDUM) is narrow: given the templated fact
and the note text, decide whether the note explains it, and add one
sentence if it doesn't — genuine reasoning over unstructured text, not
formatting.

Legacy path (config.LOCAL_LLM_USE_TEMPLATES=False, or any flag_id without a
template): the original full-generation call, kept as a working fallback.
Runs entirely in-process (no API key, no external service), via one of two
interchangeable backends selected by config.LLM_BACKEND: Apple's mlx-lm on
Apple Silicon, or llama.cpp (a GGUF build of the same model) everywhere else,
e.g. Hugging Face Spaces. The model only ever sees a FLAG + retrieved STANDARD
TEXT + NOTE CONTENT and is instructed to cite only figures/references present
in that input — it never sees the raw document, so it cannot introduce numbers
that weren't already deterministically extracted by Layers 1-4.
"""
import asyncio
import json
import os
import re
import threading

from loguru import logger

import config
from models.flags import AuditFlag
from models.report import ObservationResult, RetrievedChunk
from pipeline.generator.prompt_builder import SYSTEM_PROMPT, build_user_message
from pipeline.generator.templates import build_templated_text
from pipeline.retrieval.standards_retriever import retrieve_for_flag

_model = None
_tokenizer = None  # mlx backend only; llama.cpp handles chat templating internally
_model_load_lock = threading.Lock()

# A single local model instance is not safe for concurrent generate() calls —
# Streamlit can have multiple sessions (tabs, users, or a straggler thread from
# a session the browser already abandoned) hitting this module at once. Every
# generation call is serialized process-wide through this lock.
_generation_lock = threading.Lock()

# Opt-in parallel path (config.LOCAL_LLM_N_WORKERS > 1, llama_cpp backend
# only): one dedicated Llama instance per worker instead of the single global
# _model above, so each worker's own thread can generate independently
# without contending for _generation_lock. Never touched when N_WORKERS<=1.
_model_pool: list = []
_model_pool_lock = threading.Lock()
_worker_locks: list[threading.Lock] = []

_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_NUMBER_TOKEN_PATTERN = re.compile(r"-?[\d,]+\.?\d*")
_STANDARD_NAME_PATTERN = re.compile(r"^(Ind\s*AS\s*\d+|AS\s*\d+|SA\s*\d+|SQC\s*\d+)", re.IGNORECASE)
_PARA_NUM_PATTERN = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")
_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the "
    "JSON object described above — no code fences, no commentary, no extra text."
)


def _new_draft_model():
    """Prompt-lookup speculative decoding (llama_cpp backend only): scans the
    prompt itself for n-gram matches to speculate upcoming tokens instead of
    decoding strictly one at a time. No second model, no extra memory — and a
    good fit here specifically because SYSTEM_PROMPT forces the model to copy
    figures/citations verbatim out of the prompt, which is exactly the
    repeated-substring pattern this exploits. num_pred_tokens=2 is the
    CPU-appropriate setting (the library's own default of 10 is GPU-tuned).
    One instance per Llama object — it's stateless but not thread-shared."""
    if not config.LOCAL_LLM_SPECULATIVE_DECODING:
        return None
    from llama_cpp.llama_speculative import LlamaPromptLookupDecoding

    return LlamaPromptLookupDecoding(num_pred_tokens=2)


def _get_model():
    """Lazily load the local model once per process (module-level singleton,
    same pattern as the embedding model cache in line_item_mapper.py)."""
    global _model, _tokenizer
    if _model is None:
        with _model_load_lock:
            if _model is None:  # re-check: another thread may have loaded it while we waited
                if config.LLM_BACKEND == "mlx":
                    from mlx_lm import load

                    logger.info(f"Loading local LLM {config.LOCAL_LLM_MODEL} via mlx (first call only)...")
                    _model, _tokenizer = load(config.LOCAL_LLM_MODEL)
                else:
                    from llama_cpp import Llama

                    if config.LOCAL_LLM_MODEL_PATH and os.path.exists(config.LOCAL_LLM_MODEL_PATH):
                        # Baked into the image at build time (see Dockerfile) — load
                        # straight from disk, no Hub API call. from_pretrained() below
                        # hits huggingface.co on every single call just to resolve the
                        # file listing, even when the file's already local; that API
                        # has a hard per-IP rate limit (500 req/5min, unauthenticated)
                        # that repeated deploys/restarts on the same egress IP can blow
                        # through, breaking generation entirely with a 429.
                        logger.info(
                            f"Loading local LLM from baked-in file {config.LOCAL_LLM_MODEL_PATH} "
                            "via llama.cpp (first call only)..."
                        )
                        _model = Llama(
                            model_path=config.LOCAL_LLM_MODEL_PATH,
                            n_ctx=config.LOCAL_LLM_CTX_TOKENS,
                            n_threads=config.LOCAL_LLM_N_THREADS,
                            n_gpu_layers=config.LOCAL_LLM_N_GPU_LAYERS,
                            draft_model=_new_draft_model(),
                            verbose=False,
                        )
                    else:
                        logger.info(
                            f"Loading local LLM {config.LOCAL_LLM_GGUF_REPO}/{config.LOCAL_LLM_GGUF_FILE} "
                            "via llama.cpp (first call only)..."
                        )
                        _model = Llama.from_pretrained(
                            repo_id=config.LOCAL_LLM_GGUF_REPO,
                            filename=config.LOCAL_LLM_GGUF_FILE,
                            additional_files=config.LOCAL_LLM_GGUF_ADDITIONAL_FILES,
                            n_ctx=config.LOCAL_LLM_CTX_TOKENS,
                            n_threads=config.LOCAL_LLM_N_THREADS,
                            n_gpu_layers=config.LOCAL_LLM_N_GPU_LAYERS,
                            draft_model=_new_draft_model(),
                            verbose=False,
                        )
    return _model, _tokenizer


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


def _extract_standard_name(source_filename: str) -> str:
    """Pull a short canonical standard name (e.g. 'Ind AS 109') from a source PDF filename."""
    stem = source_filename.rsplit(".", 1)[0]
    match = _STANDARD_NAME_PATTERN.match(stem)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return re.split(r"[-_]", stem)[0].strip()


def _validate_standard_reference(standard_reference: str, chunks: list[RetrievedChunk]) -> str:
    """Guardrail against citation hallucination.

    The system prompt tells the model to only cite a standard/paragraph that
    literally appears in the retrieved text, but a small local model doesn't
    always obey that — e.g. it has cited a nonexistent 'Ind AS 77'. This
    checks the model's claim against what was actually retrieved: the cited
    standard name must be one of the retrieved sources, and any cited
    paragraph number must literally appear in the retrieved text. If either
    check fails, the reference is replaced with a safe citation built only
    from the real retrieved source names — never from the model's own claim.
    """
    if not chunks:
        return standard_reference

    combined_text = " ".join(c.text for c in chunks)
    candidate_names = [_extract_standard_name(c.source) for c in chunks]
    name_matches = any(name and name.lower() in standard_reference.lower() for name in candidate_names)

    para_tokens = _PARA_NUM_PATTERN.findall(standard_reference)
    paras_grounded = all(tok in combined_text for tok in para_tokens) if para_tokens else True

    if name_matches and paras_grounded:
        return standard_reference

    logger.warning(
        f"standard_reference {standard_reference!r} not grounded in retrieved text "
        f"(name_match={name_matches}, paragraphs_grounded={paras_grounded}) — "
        f"replacing with a citation built from actual retrieved sources: {candidate_names}"
    )
    unique_names = list(dict.fromkeys(n for n in candidate_names if n))
    return " / ".join(unique_names) if unique_names else standard_reference


def _format_note_ref(note_ids: list[str]) -> str:
    """Format a flag's note_ids deterministically, e.g. ['7f','14a'] -> 'Notes 7(f), 14(a)'.

    Never LLM-generated — note_ids come straight from the deterministic flag rule,
    so there's no hallucination risk here, unlike standard_reference.
    """
    if not note_ids:
        return ""
    formatted = []
    for note_id in note_ids:
        match = re.match(r"^(\d+)([a-zA-Z]*)$", note_id.strip())
        if match:
            number, letter = match.groups()
            formatted.append(f"{number}({letter})" if letter else number)
        else:
            formatted.append(note_id)
    label = "Note" if len(formatted) == 1 else "Notes"
    return f"{label} {', '.join(formatted)}"


def _chat_completion(
    model, tokenizer, system_prompt: str, user_message: str, max_tokens: int
) -> str:
    """The actual model call, given an already-loaded model/tokenizer — no
    global lookup, so this works identically for the single global _model
    and for a pooled worker's own dedicated instance. system_prompt/
    max_tokens are parameters (not hardcoded to the full-generation
    SYSTEM_PROMPT) so this is reusable for the much smaller addendum call
    the templated path makes — see _generate_addendum."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    if config.LLM_BACKEND == "mlx":
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        return generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=make_sampler(temp=config.LOCAL_LLM_TEMPERATURE),
        )
    else:
        # NOT combined with a grammar: confirmed broken (garbage/crashing
        # output — see llama-cpp-python issue #1770) when draft_model and
        # grammar are both set on v0.3.x, because grammar's logits masking
        # and the speculative-decoding verification path don't agree on the
        # logits array shape. Pick one; speculative decoding's real,
        # measured payoff on this workload outweighs grammar's mostly-
        # theoretical one here (we already see zero JSON-parse retries).
        response = model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=config.LOCAL_LLM_TEMPERATURE,
        )
        return response["choices"][0]["message"]["content"]


def _run_generation(system_prompt: str, user_message: str, max_tokens: int) -> str:
    model, tokenizer = _get_model()
    with _generation_lock:
        return _chat_completion(model, tokenizer, system_prompt, user_message, max_tokens)


def _get_pooled_model(worker_idx: int):
    """One dedicated Llama instance per worker (llama_cpp backend only — the
    parallel path never activates for mlx). Each gets an even split of
    LOCAL_LLM_N_THREADS so total threads across all workers matches the real
    CPU allocation exactly — oversubscribing threads is what turns "faster"
    into "everything grinds to a halt", which is the failure mode this is
    deliberately sized to avoid."""
    global _model_pool, _worker_locks
    with _model_pool_lock:
        if not _model_pool:
            _model_pool = [None] * config.LOCAL_LLM_N_WORKERS
            _worker_locks = [threading.Lock() for _ in range(config.LOCAL_LLM_N_WORKERS)]
        if _model_pool[worker_idx] is None:
            from llama_cpp import Llama

            threads = max(1, config.LOCAL_LLM_N_THREADS // config.LOCAL_LLM_N_WORKERS)
            kwargs = dict(
                n_ctx=config.LOCAL_LLM_CTX_TOKENS,
                n_threads=threads,
                n_gpu_layers=config.LOCAL_LLM_N_GPU_LAYERS,
                draft_model=_new_draft_model(),
                verbose=False,
            )
            logger.info(
                f"Loading local LLM worker {worker_idx + 1}/{config.LOCAL_LLM_N_WORKERS} "
                f"(n_threads={threads})..."
            )
            if config.LOCAL_LLM_MODEL_PATH and os.path.exists(config.LOCAL_LLM_MODEL_PATH):
                _model_pool[worker_idx] = Llama(model_path=config.LOCAL_LLM_MODEL_PATH, **kwargs)
            else:
                _model_pool[worker_idx] = Llama.from_pretrained(
                    repo_id=config.LOCAL_LLM_GGUF_REPO,
                    filename=config.LOCAL_LLM_GGUF_FILE,
                    additional_files=config.LOCAL_LLM_GGUF_ADDITIONAL_FILES,
                    **kwargs,
                )
    return _model_pool[worker_idx]


def _run_generation_pooled(
    worker_idx: int, system_prompt: str, user_message: str, max_tokens: int
) -> str:
    model = _get_pooled_model(worker_idx)
    # Locked per-worker (not the global _generation_lock) so worker A
    # generating doesn't block worker B — each worker owns its own instance,
    # this just guards against this exact worker somehow being invoked twice
    # at once (shouldn't happen given round-robin dispatch, cheap to be sure).
    with _worker_locks[worker_idx]:
        return _chat_completion(model, None, system_prompt, user_message, max_tokens)


def _build_result(raw: str, flag: AuditFlag, chunks: list[RetrievedChunk]) -> ObservationResult | None:
    data = _parse_json_response(raw)
    if data is None:
        return None
    try:
        # risk_rating is never the model's call — it's already decided by the
        # deterministic rule that triggered this flag. Overriding here (rather
        # than trusting the model's own JSON field) is what keeps High/Medium/Low
        # calibration matching pipeline/analytics/flags.py exactly.
        data["risk_rating"] = flag.severity
        data["standard_reference"] = _validate_standard_reference(
            data.get("standard_reference", ""), chunks
        )
        data["note_ref"] = _format_note_ref(flag.note_ids)
        result = ObservationResult(**data, flag_id=flag.flag_id, evidence=flag.evidence)
        _warn_on_number_mismatch(result.observation, flag.evidence, flag.flag_id)
        return result
    except Exception as e:
        logger.warning(f"Model output didn't match ObservationResult schema for {flag.flag_id}: {e}")
        return None


def _build_standard_reference(chunks: list[RetrievedChunk]) -> str:
    """Deterministic citation built only from retrieved source names — the
    same safe-fallback logic _validate_standard_reference uses when an LLM's
    claim doesn't check out, used here directly as the only source (no LLM
    claim to validate against in the templated path, so no grounding risk
    to begin with)."""
    candidate_names = [_extract_standard_name(c.source) for c in chunks]
    unique_names = list(dict.fromkeys(n for n in candidate_names if n))
    return " / ".join(unique_names) if unique_names else ""


async def _generate_addendum(fact: str, resolved_note: str) -> str:
    """The one model call left in the templated path: does the note text
    explain `fact`, or is it silent on the cause? Much smaller prompt than
    the old full-generation call (no evidence JSON, no retrieved standard
    text) and a much smaller output (one sentence or nothing), so this is
    cheap even though it's still a real LLM call. Returns '' if there's no
    note text to check, the model says NONE, or generation fails for any
    reason — an addendum is always optional, never required for a valid
    ObservationResult."""
    if not resolved_note:
        return ""
    from pipeline.generator.prompt_builder import SYSTEM_PROMPT_ADDENDUM, build_addendum_message

    message = build_addendum_message(fact, resolved_note)
    try:
        if config.LLM_BACKEND == "mlx":
            raw = _run_generation(SYSTEM_PROMPT_ADDENDUM, message, 60)
        else:
            raw = await asyncio.to_thread(_run_generation, SYSTEM_PROMPT_ADDENDUM, message, 60)
    except Exception as e:
        logger.error(f"Addendum generation failed: {e}")
        return ""

    text = raw.strip().strip('"')
    return "" if not text or text.upper() == "NONE" else text


async def _build_templated_result(
    flag: AuditFlag,
    chunks: list[RetrievedChunk],
    resolved_note: str,
    templated: tuple[str, str],
) -> ObservationResult:
    observation, recommendation = templated
    if config.LOCAL_LLM_NARRATIVE_ADDENDUM:
        addendum = await _generate_addendum(observation, resolved_note)
        if addendum:
            observation = f"{observation} {addendum}"
    return ObservationResult(
        flag_id=flag.flag_id,
        area=flag.area,
        observation=observation,
        risk_rating=flag.severity,
        standard_reference=_build_standard_reference(chunks),
        note_ref=_format_note_ref(flag.note_ids),
        recommendation=recommendation,
        evidence=flag.evidence,
    )


async def generate_observation(
    flag: AuditFlag,
    chunks: list[RetrievedChunk],
    resolved_note: str,
) -> ObservationResult | None:
    if config.LOCAL_LLM_USE_TEMPLATES:
        templated = build_templated_text(flag)
        if templated is not None:
            return await _build_templated_result(flag, chunks, resolved_note, templated)
        # No template for this flag_id (shouldn't normally happen — the
        # registry covers every check in flags.py/consistency.py — but if a
        # new check is ever added without a matching template, fall through
        # to the full-LLM path below rather than silently dropping the
        # observation).

    _get_model()
    user_message = build_user_message(flag, chunks, resolved_note)

    for attempt in range(2):
        message = user_message if attempt == 0 else user_message + _RETRY_SUFFIX
        try:
            if config.LLM_BACKEND == "mlx":
                # Called synchronously (not via asyncio.to_thread) so every
                # mlx/Metal call in the process happens on the exact same OS
                # thread every time — mlx's GPU command queue is not
                # guaranteed safe across arbitrary thread hand-offs (a real,
                # reproduced bug: asyncio.to_thread() here used to hang the
                # pipeline after 4-5 flags once the thread pool round-robined
                # calls onto a different worker thread).
                raw = _run_generation(SYSTEM_PROMPT, message, config.LOCAL_LLM_MAX_TOKENS)
            else:
                # llama.cpp has no such constraint, and its ctypes calls into
                # libllama.so release the GIL during the actual C-level
                # compute — asyncio.to_thread() here lets a single generation
                # call (which can run for minutes) happen off the main event
                # loop, so Gradio's own heartbeat/housekeeping requests can
                # still be served while it's in flight. Without this, a long
                # enough call blocks the loop for its whole duration
                # regardless of Cloud Run's --concurrency setting (that only
                # controls how many requests get routed to the container —
                # it can't help if the one process handling them is
                # synchronously stuck), and Gradio's client can eventually
                # treat the resulting heartbeat failures as a server restart
                # and force-reload the page mid-run (confirmed: this
                # recurred even at concurrency=10 on a real long run).
                raw = await asyncio.to_thread(
                    _run_generation, SYSTEM_PROMPT, message, config.LOCAL_LLM_MAX_TOKENS
                )
        except Exception as e:
            logger.error(f"Local generation failed for flag {flag.flag_id}: {e}")
            return None

        result = _build_result(raw, flag, chunks)
        if result is not None:
            return result
        # else: schema/JSON parse failed, fall through to retry

    logger.error(f"JSON parse failed for flag {flag.flag_id} after retry")
    return None


def _generate_observation_pooled(
    worker_idx: int,
    flag: AuditFlag,
    chunks: list[RetrievedChunk],
    resolved_note: str,
) -> ObservationResult | None:
    """Same logic as the full-LLM branch of generate_observation, but
    against a specific pooled worker instance instead of the single global
    _model. Synchronous (not async) so it can run directly inside a
    ThreadPoolExecutor worker thread. NOTE: this is the LOCAL_LLM_N_WORKERS>1
    path (config.LOCAL_LLM_N_WORKERS defaults to 1, so it's dormant by
    default) and does not currently branch on LOCAL_LLM_USE_TEMPLATES — it
    always does full-LLM generation, since combining pooled workers with the
    templated path hasn't been needed (templated generation is cheap enough
    on one worker) or built yet."""
    user_message = build_user_message(flag, chunks, resolved_note)

    for attempt in range(2):
        message = user_message if attempt == 0 else user_message + _RETRY_SUFFIX
        try:
            raw = _run_generation_pooled(worker_idx, SYSTEM_PROMPT, message, config.LOCAL_LLM_MAX_TOKENS)
        except Exception as e:
            logger.error(f"Local generation failed for flag {flag.flag_id}: {e}")
            return None

        result = _build_result(raw, flag, chunks)
        if result is not None:
            return result

    logger.error(f"JSON parse failed for flag {flag.flag_id} after retry")
    return None


async def generate_all_observations(
    flags: list[AuditFlag],
    notes: dict,
    xref_graph,
    progress_cb=None,
) -> list[ObservationResult]:
    """Generate observations for every flag.

    By default (config.LOCAL_LLM_N_WORKERS=1, or the mlx backend, which never
    uses this path) a single local model instance can't usefully serve
    concurrent requests — generation is compute-bound on one accelerator, and
    mlx's GPU command queue isn't safe across arbitrary thread hand-offs — so
    flags are processed one at a time, synchronously, on the calling thread.

    With config.LOCAL_LLM_N_WORKERS > 1 on the llama_cpp backend, flags are
    instead distributed round-robin across that many independent model
    instances (see _get_pooled_model) and generated concurrently via a
    thread pool — each instance gets an even share of LOCAL_LLM_N_THREADS,
    so this doesn't oversubscribe the real CPU allocation.

    `progress_cb(done, total)`, if given, fires after each flag completes —
    CPU-only generation can take minutes per flag, so callers (e.g. the UI)
    need a way to show real progress instead of one opaque "generating..."
    message for the whole batch. Under the parallel path, "done" counts
    completions, not dispatch order, since multiple flags finish out of order.
    """
    order = {"High": 0, "Medium": 1, "Low": 2}
    use_pool = config.LLM_BACKEND != "mlx" and config.LOCAL_LLM_N_WORKERS > 1

    if not use_pool:
        results: list[ObservationResult] = []
        for i, flag in enumerate(flags, 1):
            chunks = retrieve_for_flag(flag)
            resolved = xref_graph.get_resolved_note(flag.note_ids[0]) if flag.note_ids else ""
            result = await generate_observation(flag, chunks, resolved)
            if result is not None:
                results.append(result)
            if progress_cb:
                progress_cb(i, len(flags))
        results.sort(key=lambda x: order[x.risk_rating])
        return results

    # Parallel path: resolve retrieval (cheap, CPU-light) up front for every
    # flag, then hand the actual generation calls to a small thread pool.
    import concurrent.futures

    prepared = []
    for flag in flags:
        chunks = retrieve_for_flag(flag)
        resolved = xref_graph.get_resolved_note(flag.note_ids[0]) if flag.note_ids else ""
        prepared.append((flag, chunks, resolved))

    results = []
    done_count = 0
    progress_lock = threading.Lock()

    def _worker_task(item):
        idx, (flag, chunks, resolved) = item
        worker_idx = idx % config.LOCAL_LLM_N_WORKERS
        result = _generate_observation_pooled(worker_idx, flag, chunks, resolved)
        nonlocal done_count
        with progress_lock:
            done_count += 1
            if progress_cb:
                progress_cb(done_count, len(flags))
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.LOCAL_LLM_N_WORKERS) as executor:
        for result in executor.map(_worker_task, enumerate(prepared)):
            if result is not None:
                results.append(result)

    results.sort(key=lambda x: order[x.risk_rating])
    return results
