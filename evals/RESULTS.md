# Agent eval results

bv_bevy forest world, toolset `full`, default model settings (no `--think`),
2026-10-12. `@pitzer-v100`: Ollama 0.32.0 on 4× V100 32 GB. `@ascend-a100`:
Ollama 0.40.3 on one A100 40 GB. Each cell is passes / scored attempts; ERROR
rows (setup or server failures) are not scored. Regenerate with
`uv run scripts/eval_report.py --markdown`.

| scenario | ollama:gpt-oss:120b @pitzer-v100 | ollama:gpt-oss:20b @ascend-a100 | ollama:gpt-oss:20b @pitzer-v100 | ollama:llama3.3:70b @pitzer-v100 | ollama:qwen3-coder:30b @pitzer-v100 | ollama:qwen3.5:122b @pitzer-v100 | ollama:qwen3.6:35b @ascend-a100 | ollama:qwen3.6:35b @pitzer-v100 | ollama:qwen3.8:27b @ascend-a100 | ollama:qwen3.8:27b @pitzer-v100 | ollama:qwen3:32b @pitzer-v100 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| takeoff | 1/1 | 1/1 | 3/3 | 1/1 | 1/1 | 1/1 | 1/1 | 3/3 | 2/2 | err×1 | 1/1 |
| forward_turn | 1/1 | 1/1 | 3/3 | 1/1 | 1/1 | 1/1 | 1/1 | 3/3 | 2/2 | err×1 | 1/1 |
| square | 1/1 | 1/1 | 3/3 | 1/1 | 1/1 | 1/1 | 1/1 | 3/3 | 2/2 | - | 1/1 |
| draw_house | 0/1 | 1/1 | 2/2 | 0/1 | 0/1 | 1/1 | 1/1 | 3/3 | 2/2 | - | 0/1 |
| find_person | 1/1 | 1/1 | 2/2 | 1/1 | 1/1 | 1/1 | 1/1 | 3/3 | 2/2 | - | 1/1 |
| route_limit | 1/1 | 1/1 | 2/2 | 0/1 | 1/1 | 1/1 | 1/1 | 3/3 | 2/2 | - | 1/1 |
| rtl | 1/1 | 1/1 | 2/2 | 1/1 | 1/1 | 1/1 | 1/1 | 3/3 | 2/2 | - | 1/1 |
| move_from_ground | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| rotate_360 | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| speed | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| triangle_pauses | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| conversation | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| remember_place | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| count_people | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| impossible | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| battery | - | - | 1/1 | - | - | - | - | 2/2 | 2/2 | - | - |
| TOTAL | 6/7 (86%) | 7/7 (100%) | 26/26 (100%) | 5/7 (71%) | 6/7 (86%) | 7/7 (100%) | 7/7 (100%) | 39/39 (100%) | 32/32 (100%) | 0/0 | 6/7 (86%) |
| avg s / scenario | 50 | 92 | 38 | 42 | 40 | 53 | 63 | 47 | 45 | - | 77 |

## Notes

- **Three models passed every scored attempt:**
  - `qwen3.6:35b`: 46/46 (two full runs on V100, quick screens on V100 and A100).
  - `qwen3.8:27b`: 32/32 (two full runs on A100). Its replies were the clearest.
  - `gpt-oss:20b`: 33/33 (one full run on V100, quick screens on V100 and A100).

  Any of the three is a good choice for Builder Night.
- **`qwen3.8:27b` needs an A100/H100 and a newer Ollama** than OSC's module
  (0.32.0). The official 0.40.3 image runs it: copy it from
  `/fs/scratch/PAS3560/allenthomas/ollama/ollama_latest.sif` and set
  `OLLAMA_VERSION=0.40.3` so the job script uses `OLLAMA_SIF` instead of the
  module. On V100s it ran at about 0.1 tokens/s and every request timed out;
  on the A100 it ran at about 30 tokens/s.
- **Ascend's `debug-nextgen` partition** (A100, 1 h limit) started
  immediately; regular A100/H100 queues were 1.5–12 days out.
- **A100 times per scenario are inflated.** One 40 GB card holds one model at
  a time, so the first request of each model includes about 1–2 min of
  loading. Most of each scenario is flight time either way.
- `qwen3.5:122b` passed its quick screen (7/7) but is 81 GB and slower.
- `qwen3:32b`, `gpt-oss:120b` and `qwen3-coder:30b` failed only
  `draw_house`. The first two ran before the `draw` description said
  pictures in the sky are usually vertical; `qwen3-coder:30b`'s failure was a
  width-check bug in the eval, since fixed.
- `llama3.3:70b` was weakest (5/7).
- **Caveat:** every scenario uses the same wording each time. Next step: a
  held-out set of rephrased, vague and out-of-scope requests, with more
  repeats.

# vLLM results (2026-10-12)

OSC vLLM 0.23.0 module on Ascend `debug-quad`. Each model ran on **one A100
80 GB**, since the debug QOS allows 1 GPU per job. The Qwens ran as their
official FP8 versions so they fit on one card. Server flags:

- Qwen: `--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3`, agent `--vision`
- gpt-oss: `--enable-auto-tool-choice --tool-call-parser openai`
- All: `--max-model-len 32768`

## Accuracy (same 16 scenarios)

| scenario | openai:Qwen/Qwen3.6-35B-A3B-FP8 @ascend-a100x1-vllm | openai:Qwen/Qwen3.6-35B-A3B-FP8 @ascend-a100x1-vllm-prefixcache | openai:Qwen/Qwen3.6-35B-A3B-FP8 think=False @ascend-a100x1-vllm | openai:Qwen/Qwen3.8-27B-FP8 @ascend-a100x1-vllm | openai:openai/gpt-oss-20b @ascend-a100x1-vllm |
|---|---|---|---|---|---|
| takeoff | 1/1 | 1/1 | 1/1 | err×1 | 1/1 (+1err) |
| move_from_ground | 1/1 | 1/1 | 1/1 | err×1 | 2/2 |
| forward_turn | 1/1 | 1/1 | 0/1 | 1/1 | 2/2 |
| rotate_360 | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| speed | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| square | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| triangle_pauses | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| draw_house | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| conversation | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| remember_place | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| find_person | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| count_people | 1/1 | 1/1 | 1/1 | 1/1 | 1/2 |
| route_limit | 1/1 | 1/1 | 1/1 | 1/1 | 1/2 |
| impossible | 1/1 | 1/1 | 1/1 | 1/1 | 1/2 |
| battery | 1/1 | 1/1 | 1/1 | 1/1 | 1/2 |
| rtl | 1/1 | 1/1 | 1/1 | 1/1 | 2/2 |
| TOTAL | 16/16 (100%) | 16/16 (100%) | 15/16 (94%) | 14/14 (100%) | 27/31 (87%) |
| avg s / scenario | 36 | 34 | 34 | 68 | 38 |

- **Qwen3.6-35B-A3B-FP8** (thinking on, default): 32/32 over two full runs,
  one with prefix caching. With thinking off it scored 15/16: asked to "turn
  right", it turned left.
- **Qwen3.8-27B-FP8**: 14/14 scored (2 ERRORs were the simulator refusing to
  arm). Accurate but about twice as slow (68 s per scenario).
- **gpt-oss-20b**: 27/31. One real reasoning lapse: it flew a route before
  planning it, then misreported the distance. The rest were fixed during
  testing, mostly on the agent side:
  - vLLM's gpt-oss parser sent junk `{"": ...}` arguments to no-argument
    tools and once leaked `<|channel|>` into a tool name; the agent now strips
    both.
  - The survey split one mannequin into two clusters (merge radius now 6 m).
  - The route check now judges the outcome (all points within 100 m) rather
    than requiring a planning tool. One row was rescored.

## Many users on one server (`scripts/load_test.py`)

N simulated users at once, each running real multi-step agent requests (full
prompt and 43 tools, ~9k tokens; tool results faked). Seconds: p50 / p95.

| Model (1× A100) | users | model call | whole request | peak KV use | max queued |
|---|---|---|---|---|---|
| gpt-oss-20b | 1 | 0.6 / 1.2 | 1.4 / 3.4 | 0.3% | 0 |
| | 10 | 1.1 / 7.9 | 4.7 / 10.1 | 0.5% | 0 |
| | 20 | 2.0 / 12.0 | 8.5 / 22.3 | 1.0% | 0 |
| Qwen3.6-FP8, prefix caching on | 1 | 0.7 / 2.8 | 2.8 / 8.4 | 0.7% | 0 |
| | 10 | 2.6 / 12.1 | 11.5 / 22.1 | 3.4% | 2 |
| | 20 | 3.4 / 27.5 | 14.8 / 225 | 5.9% | 8 |
| Qwen3.6-FP8, default | 20 | 8.6 / 57.5 | 38.1 / 82.3 | 14.9% | 17 |
| Qwen3.6-FP8, thinking off | 20 | 9.9 / 24.7 | 26.7 / 60.1 | 12.7% | 17 |
| Qwen3.8-27B-FP8 | 10 | 17.9 / 99.7 | 116 / 677 | 18.8% | 9 |

- **KV cache is not the limit.** One A100 80 GB held 1.5–2.35M tokens of KV
  cache (vLLM's estimate: 46–72 simultaneous full 32k conversations); peak
  use in the tests was at most 19%. The limit is compute.
- **Qwen 3.6/3.8 need `--enable-prefix-caching` explicitly.** Their hybrid
  (Mamba-style) layers make vLLM turn prompt caching off by default: the hit
  rate was 0% versus 98% for gpt-oss, so each call reprocessed the whole
  ~9k-token prompt. With the flag (mamba cache mode `align`) the hit rate was
  92% and calls under load were 2–3× faster.
- **Qwen3.8-27B (dense) is too slow** to share on one A100.
