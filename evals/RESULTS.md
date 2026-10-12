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
