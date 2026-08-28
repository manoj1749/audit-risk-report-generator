#!/usr/bin/env bash
# Deploy audit-risk-report-generator to Cloud Run. NOT run automatically —
# review the settings below, then run this script yourself when ready.
#
# One-time setup before the first run:
#   gcloud auth login
#   gcloud config set project "$PROJECT_ID"          # or pass --project below
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#       artifactregistry.googleapis.com
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-project-ce055269-beff-44ce-bf8}"
# Migrated from us-central1 on 2026-08-19: L4 GPU quota was unavailable in US
# regions (Google Cloud Support case 74424203, stockout), and asia-southeast1
# also cuts latency for our actual users (India) vs us-central1. The
# us-central1 service/domain-mapping were left running as a fallback rather
# than deleted -- audit.m4n0j.dev's DNS CNAME target is unchanged
# (ghs.googlehosted.com.), only the Cloud Run domain-mapping resource moved,
# so no DNS changes were needed for the cutover.
REGION="${REGION:-asia-southeast1}"
SERVICE_NAME="${SERVICE_NAME:-audit-risk-report-generator}"

# GPU (NVIDIA L4) was attempted but is blocked: this project's Cloud Run GPU
# quota (run.googleapis.com/nvidia_l4_gpu_allocation_no_zonal_redundancy) is
# hard-capped at 0 by Google — `gcloud alpha services quota update` on it
# returns COMMON_QUOTA_CONSUMER_OVERRIDE_TOO_HIGH (max=0), confirming this
# needs a manual quota-increase request approved through the Cloud Console
# (IAM & Admin > Quotas), not something scriptable, and unlikely to be
# approved same-day on a fresh free-trial project. To retry GPU later: file
# that request, then see git history around this comment for the GPU
# Dockerfile/deploy.sh (nvidia/cuda base, CMAKE_CUDA_ARCHITECTURES=89,
# --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy,
# LOCAL_LLM_N_GPU_LAYERS=-1).
#
# Sized to the real floor, not guessed low: the LLM (Qwen2.5-7B-Q4, ~4.7GB)
# and the embedding model (BAAI/bge-m3, ~2GB) are both resident in memory
# during a run, plus PaddleOCR if the notes doc is scanned, plus Python/Gradio
# overhead. Cloud Run requires CPU to scale with memory (~4Gi per vCPU), so
# cpu=8 supports up to 32Gi; bumped from 4->8 because a real 11-observation
# run measured ~5-6 min/observation at cpu=4 — close enough to the full ~1hr
# run to hit Cloud Run's 3600s request-timeout ceiling (the platform max)
# before finishing.
#
# memory=32Gi (bumped from 16Gi): a real large scanned PDF (~19MB, ~40+
# pages) killed the request silently mid-OCR twice in a row — no Python
# exception, no app-level error logged at all, just "Truncated response
# body" from the platform and a cold restart a few minutes later. That
# signature (silent death, nothing catchable) is consistent with an OOM
# kill: PaddleOCR runs several models at once (doc-orientation, unwarping,
# textline-orientation, detection, recognition) while also holding
# page-image buffers for a many-page document, and 16Gi wasn't accounted
# for that on top of the LLM + embedding model baseline above. Bumped to
# 32Gi (Cloud Run's ceiling at cpu=8) as the fix; not yet re-confirmed
# against the same file post-bump.
#
# LOCAL_LLM_N_WORKERS: tried at 2 (two independent model instances, 4 threads
# each) expecting roughly half the generation time. Measured on a real run:
# no improvement (~31.5min vs. ~29-30min single-worker baseline) — thread
# count was sized correctly (4+4=8, matching vCPU exactly, no
# oversubscription), but CPU-side LLM decoding is memory-bandwidth-bound, not
# core-bound: two model instances just compete for the same shared memory
# bus instead of getting a clean speedup from separate cores. Reverted to 1
# (the original, validated single-instance path) — the code still supports
# N_WORKERS>1 (see observation_gen.py) if it's ever worth revisiting (e.g.
# real batching within one instance, not multiple separate ones), but as
# naive multi-instance parallelism it's not a working lever on CPU. The real
# lever for a meaningful speedup is the GPU quota request above — GPU memory
# bandwidth is several times a CPU's, which is what actually removes this
# bottleneck rather than just adding contention for it.
#
# concurrency=10, NOT 1: Gradio's frontend polls /gradio_api/app_id
# periodically as a heartbeat to detect server restarts. At concurrency=1,
# that heartbeat can never get a request slot while the one instance is busy
# with the actual (long, CPU-bound) generation call — every heartbeat 429s
# for the entire run, and Gradio's client eventually treats sustained
# heartbeat failure as "the app restarted" and force-reloads the page,
# destroying the in-flight session (confirmed: this is what silently killed
# a real end-to-end test after 44 minutes tonight, not a server crash).
# concurrency=10 lets housekeeping requests (heartbeat, static assets)
# through alongside the one heavy computation — actual generation is still
# serialized to one at a time by Gradio's own demo.queue() plus
# observation_gen.py's _generation_lock/_model_load_lock, not by this
# setting, so raising it doesn't risk concurrent-generation bugs.
# timeout=3600 because a large scanned PDF can take 20+ min of OCR (see
# pipeline/extractor/pdf_extractor.py's per-page cache — first run on a given
# file is slow, repeat runs on the same file are ~instant).
# session-affinity + max-instances=3 (raised from 1 on 2026-08-20): Gradio
# uploads a file via one HTTP request then runs the analysis over a separate
# SSE/queue request -- session-affinity keeps both requests from the SAME
# browser session pinned to the SAME instance, which is what actually
# prevents the analysis failing with "No such file" (the upload only exists
# on the instance that received it). max-instances was originally capped at
# 1 out of caution that affinity alone might not hold, but that was never
# actually tested -- it just meant two people using the tool at the same
# time locked each other out entirely ("no available instance", indistinct
# from a broken deploy). Live-tested on a real incident: with max-instances
# raised to 2, a second, unrelated session's full upload-through-report run
# completed correctly (right company name, right figures, no cross-session
# file mix-up) while a 105-page OCR job kept running concurrently on the
# first instance. Set to 3 for a bit more headroom; cost is per-use, not
# always-on (min-instances stays 0), so this only costs anything during
# actual concurrent usage, not continuously.
#
# Built and deployed as two separate steps, not the simpler one-shot
# `gcloud run deploy --source .`: that form silently caps its Cloud Build
# job at 1800s (30 min) no matter what `gcloud config set builds/timeout`
# is set to — confirmed via `gcloud builds describe` on 4 real failed
# deploys, all status TIMEOUT, even after raising builds/timeout to 7200.
# This Dockerfile's own docker-build step alone measured ~20-29 min before
# even reaching the image push, so it was always right at that edge and
# any small slowdown pushed it over with zero useful error message (just
# DEADLINE_EXCEEDED). `gcloud builds submit --timeout=` is a real,
# independently-documented flag that Cloud Build actually honors — split
# the build out to use it, then deploy the already-pushed image (fast, no
# build wait) separately.
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest"

gcloud builds submit \
    --tag "$IMAGE" \
    --timeout=3600s \
    --project "$PROJECT_ID" \
    --region="$REGION" \
    .

# REPORTS_BUCKET is intentionally the same single bucket for every region --
# a unified report history regardless of which region happened to serve a
# given run, not one fragmented list per region.
gcloud run deploy "$SERVICE_NAME" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "$IMAGE" \
    --cpu 8 \
    --memory 32Gi \
    --gpu=0 \
    --set-env-vars LOCAL_LLM_N_THREADS=8,REPORTS_BUCKET=audit-risk-reports-368675610715 \
    --concurrency 10 \
    --timeout 3600 \
    --min-instances 0 \
    --max-instances 3 \
    --session-affinity \
    --allow-unauthenticated

echo
echo "Deployed. URL:"
gcloud run services describe "$SERVICE_NAME" \
    --project "$PROJECT_ID" --region "$REGION" \
    --format='value(status.url)'

# `gcloud run deploy` is documented to atomically deploy AND route 100%
# traffic to the new revision, but this was confirmed to silently fail
# twice in a row on this exact service (2026-08-27/28): the new revision
# built, pushed, and reached Ready, but traffic stayed on a stale revision
# from days earlier -- the command still printed its normal "serving 100
# percent of traffic" success message throughout, so nothing in its own
# output flagged the mismatch. Cause not confirmed (possibly related to
# this service's tagged-but-trafficless revisions from earlier GPU
# testing); rather than chase that, verify the outcome directly and
# self-heal instead of trusting the deploy command's stdout.
SERVICE_JSON=$(gcloud run services describe "$SERVICE_NAME" \
    --project "$PROJECT_ID" --region "$REGION" --format=json)
READY=$(echo "$SERVICE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['status']['latestReadyRevisionName'])")
LIVE=$(echo "$SERVICE_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
live = [t['revisionName'] for t in d['status']['traffic'] if t.get('percent') == 100]
print(live[0] if live else '')
")
if [ "$READY" != "$LIVE" ]; then
    echo
    echo "WARNING: latest ready revision ($READY) is not the one serving 100% of traffic ($LIVE)."
    echo "Fixing by routing traffic to latest..."
    gcloud run services update-traffic "$SERVICE_NAME" \
        --project "$PROJECT_ID" --region "$REGION" --to-latest
fi
