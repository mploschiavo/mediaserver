# ADR-0014 — GPU strategy: single-consumer today, time-slicing on demand, upgrade path

**Status:** Accepted (2026-05-10). Single-consumer scheduling is the
default today; time-slicing is gated on a concrete second consumer.
Hardware upgrade path is informational guidance, not a prescription.

Authors: matthew

## Context

The 2026-05-10 GPU rollout landed an NVIDIA Tesla P4 (Pascal, 8 GB,
6th-gen NVENC) on the microk8s cluster. A new
`deploy/k8s/overlays/nvidia/` overlay (commit `4bfbb4f2`, refined
in `68a2d592`) attaches the GPU to the Jellyfin Deployment via
`nvidia.com/gpu: 1` + `runtimeClassName: nvidia`. The controller's
`JellyfinGpu` lifecycle reconciles `system.xml` with
`<HardwareAccelerationType>nvenc</HardwareAccelerationType>` +
hardware decode/encode/tone-mapping on every reconcile tick.

End-state today (verified live):

* Jellyfin's pod has a Tesla P4 attached; `nvidia-smi` from inside
  the pod shows 8 GB VRAM and driver 580.126.20.
* jellyfin-ffmpeg exposes `h264_nvenc`, `hevc_nvenc`, `av1_nvenc`
  encoders (av1_nvenc is listed but the P4 does not actually
  accelerate AV1 encode — see Hardware section).
* Jellyfin is the only GPU consumer in the stack right now. No
  Whisper / Immich / Ollama / Frigate. (Other media-stack services
  — *arr family, qBittorrent, SAB, Maintainerr, Jellyseerr,
  Authelia, Envoy, Homepage — never touch GPU.)

The operator question is twofold:

1. Should we enable NVIDIA device-plugin **time-slicing** so multiple
   pods can share the single GPU?
2. Should we buy a second card or upgrade to a better one (T4 / A2 /
   L4 / consumer RTX)?

This ADR answers both.

## Decision

### Time-slicing: defer until a second consumer is concrete

**We do not enable time-slicing today.** No code change, no operator
action required. Default device-plugin config (one GPU, one consumer)
stays in place.

We enable time-slicing the same week we install the second GPU
consumer, sized to that consumer's VRAM profile (the
[Enablement recipe](#enablement-recipe) section below).

### Hardware: stay on the P4 until the workload mix demands more

**We do not buy a second P4 or upgrade to T4/A2/L4 yet.** The current
load (family Jellyfin, transcoding only) leaves significant P4
headroom (~10–15 concurrent 1080p H.264 / ~6–10 H.265 transcodes;
typical home use is 1–3 simultaneous).

The [Upgrade trajectory](#upgrade-trajectory) section names the
triggers that would justify a card change.

## Why defer time-slicing

The NVIDIA device plugin's `sharing.timeSlicing.replicas` config
advertises one physical GPU as N logical `nvidia.com/gpu` resources.
Pods each requesting `nvidia.com/gpu: 1` get scheduled onto the same
silicon; the GPU's hardware scheduler time-multiplexes between
their CUDA contexts.

Trade-offs that don't disappear by being "just unused":

1. **No memory isolation.** Every sharing pod sees the full 8 GB
   VRAM. If two pods each allocate 6 GB, the second OOMs the
   driver. The replica count is a scheduling fiction, not a memory
   partition — Pascal-class hardware doesn't support MIG.
2. **No compute isolation.** A heavy kernel from one pod blocks the
   others. A Whisper batch can stutter a concurrent Jellyfin stream
   for the duration of its inference window.
3. **Resource accounting becomes fuzzy.** `nvidia-smi` reports
   aggregate usage; the dashboard's `nvidia.com/gpu` capacity number
   becomes a fiction (the node still has 1 GPU even though k8s
   thinks it has N).
4. **Schedule-time fiction can bite rollouts.** k8s will happily
   schedule N pods that each "want a GPU" — they then contend on
   the same card. The mitigation in `deploy/k8s/overlays/nvidia/
   jellyfin-gpu-patch.yaml` (`strategy: Recreate`) protects Jellyfin
   from overlapping itself during rollouts; that protection needs
   to be applied per-workload, not via time-slicing.
5. **Failure blast radius widens.** A driver hang or one pod's
   CUDA error can affect every pod sharing that replica set. With
   one consumer per physical card, the blast radius is one workload.
6. **Tiny per-call overhead even when unused.** CUDA context-switches
   under time-slicing carry microsecond-scale extra work. Irrelevant
   for transcoding (millisecond per frame) — listed for completeness.

**With only Jellyfin in play, the benefit is zero** (Jellyfin gets
the whole card today; enabling time-slicing changes nothing). The
operational cost is small but non-zero (ConfigMap to maintain, ~30s
outage when device-plugin restarts, a replica-count we'd have to
revise anyway once we know real workload VRAM profiles).

**Conclusion:** turn it on when the second consumer is *concrete*,
not when it might be useful "someday."

## Enablement recipe (when the second consumer arrives)

When introducing Whisper / Immich / Ollama / etc.:

### 1. Profile the target workload's VRAM ceiling.

| Workload                     | Typical VRAM | Notes                                                        |
|------------------------------|-------------:|--------------------------------------------------------------|
| Jellyfin transcode (1080p)   | ~500 MB      | ~150 MB per concurrent stream; tone-map adds ~300 MB         |
| Whisper (medium model)       | 2–4 GB       | Bazarr's optional Whisper integration                        |
| Immich face/object detection | 1–3 GB       | Per inference batch                                          |
| Ollama 7B INT4               | ~5 GB        | Smallest practical local LLM                                 |
| Ollama 13B INT4              | ~10 GB       | **Won't fit alongside Jellyfin on 8 GB P4** — needs upgrade  |
| Frigate object detection     | 1–2 GB       | YOLO models; smaller w/ TensorRT                             |

Time-slicing **only works when the sum of concurrent VRAM
allocations is less than physical VRAM** (8 GB on P4). The
replica-count just controls how many pods can be *scheduled*, not
how much memory each can use.

### 2. Apply the ClusterPolicy patch.

NVIDIA GPU Operator exposes time-slicing via the `ClusterPolicy`
CRD:

```yaml
# kubectl edit clusterpolicy/cluster-policy -n gpu-operator-resources
spec:
  devicePlugin:
    config:
      name: time-slicing-config
      default: any
```

Plus a sibling ConfigMap in the same namespace:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: time-slicing-config
  namespace: gpu-operator-resources
data:
  any: |-
    version: v1
    flags:
      migStrategy: none
    sharing:
      timeSlicing:
        renameByDefault: false
        failRequestsGreaterThanOne: true
        resources:
          - name: nvidia.com/gpu
            replicas: 2   # Number of pods that can share the card. Match to your workload's concurrency.
```

`failRequestsGreaterThanOne: true` is important: it stops a buggy
pod requesting `nvidia.com/gpu: 3` from grabbing all replicas; pods
must request exactly 1.

### 3. Apply to Jellyfin's overlay too.

Jellyfin's `Deployment` already requests `nvidia.com/gpu: 1`; no
change needed. The new ConfigMap just means the scheduler can now
fit another pod alongside.

### 4. Roll the device plugin.

```
kubectl delete pod -n gpu-operator-resources -l app=nvidia-device-plugin-daemonset
```

(The GPU operator's reconciler will re-create the pod with the new
config. Expect ~30s outage where the node briefly shows `nvidia.com/gpu`
as missing; Jellyfin's pod doesn't restart but its existing CUDA
contexts may be reset.)

### 5. Add the second workload.

The second consumer's Deployment requests `nvidia.com/gpu: 1`;
scheduler places it on the same node. From the workload's
perspective there's a whole GPU — VRAM contention only shows up at
allocation time.

### 6. Watch for contention.

* Add `dcgm-exporter` metrics (`gpu-operator-resources` namespace
  already runs it) to Grafana to track per-process VRAM and
  utilisation.
* Watch Jellyfin's `Active Sessions` panel for transcode start
  latency creeping up (>2s means the GPU's CUDA scheduler is
  thrashing).

### Replica count rules of thumb

| Card     | Total VRAM | Sensible `replicas` for Jellyfin + 1 small AI workload | Notes |
|----------|-----------:|-------------------------------------------------------:|-------|
| P4 / P40 |       8 GB | 2                                                      | Tight — leaves ~2 GB headroom |
| T4 / A2  |      16 GB | 3–4                                                    | Comfortable |
| L4       |      24 GB | 4–6                                                    | Generous |

Never set `replicas` higher than your concurrent-workloads × VRAM /
card-VRAM. The plugin won't stop you, but you'll see CUDA OOMs at
runtime.

## Other consumers in this stack

For the record, the GPU question across the full service list:

| Service                                                       | Needs GPU? | Why                                                                 |
|---------------------------------------------------------------|------------|----------------------------------------------------------------------|
| **Jellyfin**                                                  | Yes        | NVENC/NVDEC for H.264/H.265 transcode. The current consumer.        |
| Bazarr (with optional Whisper)                                | Optional   | AI subtitle generation. ~2–4 GB VRAM. Not enabled in this stack today. |
| Immich (if added later)                                       | Optional   | Face / object detection. ~1–3 GB VRAM.                              |
| Frigate (if added later)                                      | Optional   | NVR object detection. Wants own GPU long-term.                       |
| Ollama / local LLM (if added later)                           | Yes        | Sized to model; ≥7B INT4 fills a P4.                                 |
| Sonarr / Radarr / Lidarr / Readarr / Prowlarr / qBittorrent / SAB / Unpackerr / FlareSolverr / Maintainerr / Jellyseerr / Homepage / Authelia / Envoy | No | Metadata, search, downloads, web UI, proxy — all CPU-only by design. |

Adding any "optional" workload triggers the
[Enablement recipe](#enablement-recipe).

## Upgrade trajectory

The P4 is the right card for today's load. Conditions that would
justify replacement (not all at once — each row is independent):

| Trigger                                                                    | Upgrade target           | Why                                                                                                            |
|----------------------------------------------------------------------------|--------------------------|----------------------------------------------------------------------------------------------------------------|
| Routinely >10 simultaneous transcoded streams                              | **Tesla T4** (16 GB)     | 2× VRAM, 7th-gen NVENC with better tone-mapping; same TDP envelope                                             |
| HDR → SDR tone-mapping is sluggish (>3–4 streams)                          | T4 or A2                 | Turing's NVENC handles tone-map better than Pascal                                                             |
| Need AV1 *encode* (re-encoding library to save space, AV1-preferred clients) | **L4** (24 GB) or RTX 4060 Ti 16 GB | First Ada-Lovelace cards with NVENC AV1 encode. L4 is ~$2.5k+; 4060 Ti is the consumer equivalent at ~$450 new with display outputs + active cooling. |
| Adding 7B LLM alongside Jellyfin on 8 GB                                   | T4 (16 GB) or A2 (16 GB) | LLM needs ~5 GB, Jellyfin transcoding ~500 MB. 8 GB is too tight.                                              |
| Adding 13B+ LLM (sustained inference)                                      | **A10** (24 GB) or RTX A4000 (16 GB) or 4060 Ti 16 GB | A10 is the datacenter sweet spot for 13B/34B INT4 alongside transcoding — 24 GB + Ampere CUDA cores. A4000/4060 Ti are the consumer alternatives at ⅓ the price. **See A10 caveats below.** |
| Adding heavy mixed AI workload (image gen + LLM + STT)                     | A10 or L4                | 24 GB on one card. A10 has more raw CUDA; L4 has Ada-gen NVENC (AV1) + half the TDP. |
| Multiple GPU consumers, want HARDWARE isolation (not time-slice)           | A30 (Ampere, MIG-capable) or A100 | True per-workload VRAM/compute partitioning. Time-slicing isn't a substitute when VRAM contention is real.     |
| Multiple GPU consumers, "good enough" isolation OK                         | Second card (any)        | Two separate cards = two separate failure domains, no shared-memory OOM risk.                                  |

### A10 caveats — why it's not the default 24 GB pick

The NVIDIA A10 (Ampere, 24 GB, ~$1500–2500 used) sits between the
T4 / A2 tier and the L4 tier. The price gap from T4 → A10 is large
(~$1000); the gap from A10 → L4 is moderate (~$500–1000). What you
get for the A10 premium:

* **Yes:** 24 GB VRAM (vs 16 GB on T4/A2), enough for a 13B INT4
  LLM alongside Jellyfin.
* **Yes:** ~2× CUDA core count → meaningfully faster inference
  vs T4 for LLMs / Stable Diffusion / Whisper-large.
* **No:** Same 7th-gen NVENC/NVDEC as T4 (Turing-gen encoder
  silicon). **No AV1 encode** (Ampere predates AV1 NVENC).
* **No:** No MIG (that's A30 / A100 only).

What the A10 is *worse* at than the alternatives:

* **150 W TDP, passive cooling.** Designed for datacenter chassis
  with high-pressure forced air. In a typical home tower or a
  2U/4U server with stock fans, it WILL thermal-throttle or
  shut down. P4 is 75 W and forgiving; A10 is not. Plan for a
  3D-printed shroud + ducted fan if you're not in a rack.
* **No AV1 encode.** Paying ~$1500+ in 2026 and getting a
  pre-AV1 encoder is a forward-proofing miss. L4 ($2500+) or
  RTX 4060 Ti 16 GB ($450 new) close that gap.

**A10 makes sense only if** (a) you're running 13B+ LLMs on the
regular alongside transcoding, (b) you have datacenter-class
airflow available, (c) you explicitly don't care about AV1
encode, (d) used pricing is closer to $1500 than $2500. The
"otherwise" picks for the same workload class are:

* RTX 4060 Ti 16 GB ($450 new) — half the VRAM, AV1 encode, active
  cooling. Best home-lab default if you can do without the 8 GB
  delta and want display outputs.
* RTX A4000 16 GB ($700 used) — workstation card, single-slot,
  140 W active cooling. Best "datacenter-style without the
  airflow requirement" pick.
* NVIDIA L4 ($2500+) — Ada-gen NVENC (AV1 encode), 24 GB, 72 W
  passive. Best "if I'm spending this much anyway" pick.

The order-of-preference for 24 GB in a home lab is L4 (if budget) >
A4000 (if quiet) > A10 (if you've got rack airflow and a deal).

**Two P4s vs one T4:** two P4s cost ~$200 used, give you 2 × 8 GB
isolated and ~30 concurrent transcodes worth of capacity. One T4
costs $400–600 used, gives you 16 GB of one-card-shared capacity
with 7th-gen NVENC quality. For most home labs the T4 wins — better
per-stream quality matters more than total stream count, and one
card is simpler to schedule.

**Skip the L4 unless AV1 encode is a real need.** AV1 encode is
only worth it if you're space-constrained and your library is
worth re-encoding (movies/TV often aren't — direct-stream is
already optimal). Modern Chrome/Firefox decode AV1 fine, but most
mobile clients still prefer HEVC. AV1 encode also takes
significantly longer than HEVC on the same hardware, so even with
NVENC AV1 you trade quality vs throughput.

## Consequences

**Positive:**

* The "do nothing" path is the default. No ConfigMap drift to
  maintain, no schedule-time fiction in the dashboard, no
  premature isolation concerns.
* When the second consumer arrives, the recipe is mechanical and
  documented. No re-discovery cost.
* The hardware upgrade trajectory has named triggers — operators
  can recognise "I'm in the upgrade zone" without ad-hoc reasoning.

**Negative:**

* The GPU sits idle 95%+ of the time (no live transcodes most
  hours of the day). That's by design for a home stack, not a
  cost concern, but the watt-hours are real (~6 W idle for the P4).
* Future-us might forget this ADR exists and re-discover the same
  trade-offs. Mitigation: cross-ref from any future "add AI workload"
  / "scale GPU" PR description.

**Neutral:**

* The k8s overlay (`deploy/k8s/overlays/nvidia/`), the controller's
  `JellyfinGpu` lifecycle, and the contract job entry are unchanged
  by this ADR. They support single-consumer scheduling today and
  time-slicing when enabled — the same code path serves both.

## Cross-references

* **ADR-0013** — single Job framework, retire `run-legacy-pipeline`.
  The eventual `jellyfin:ensure-hardware-acceleration` contract job
  (Phase 5 backlog there) will tick this same hardware path on
  every reconcile. No change to that plan.
* Commits that landed the current single-consumer GPU support:
  * `4bfbb4f2` — nvidia overlay + controller k8s detection
  * `b9bbb72f` — switch from kubectl shell to k8s python client
  * `68a2d592` — `strategy: Recreate` for single-GPU rollouts
* NVIDIA references:
  * Time-slicing config schema:
    https://github.com/NVIDIA/k8s-device-plugin#shared-access-to-gpus
  * NVENC support matrix:
    https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new

## Revisit triggers

This ADR is reviewed (and likely amended / superseded) when:

* The operator installs a second GPU consumer (Whisper / Immich /
  Ollama / Frigate / similar).
* Jellyfin transcoding hits >10 concurrent streams routinely
  (justifies the upgrade trajectory's "T4" trigger).
* NVIDIA introduces MIG-class isolation on a card price-relevant
  to home labs (currently only A100/H100 — not relevant).
* Anything in this stack starts using AV1 encode (justifies the L4
  trigger or supersedes this ADR with an AV1-specific decision).
