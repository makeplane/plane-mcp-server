# spike/ — measurement tooling

Throwaway scripts that produced the numbers in
[`docs/tool-consolidation-plan.md`](../docs/tool-consolidation-plan.md). Not part of the
shipped package (`pyproject.toml` packages `plane_mcp*` only).

The consolidated tool surface these measure now lives in
[`plane_mcp/tools_v2/`](../plane_mcp/tools_v2/README.md).

| Script | What it does |
|---|---|
| `bench/check_v2.py` | Registers every v2 module both ways; catches import errors and name mismatches |
| `bench/measure_all.py` | Reproduces the full A/B/C/BD/D payload table |
| `bench/live_smoke.py` | One read-only call against every tool on a live workspace |
| `bench/probe_model_tokens.py` | **Not yet run.** Measures real model-facing cost; needs `ANTHROPIC_API_KEY` |
| `bench/compress.py` / `bench/verify.py` | Lossless schema compressor + its proof. Only relevant if variant BD is built |

```bash
.venv/bin/python spike/bench/check_v2.py      # 29 ok, 0 failing
.venv/bin/python spike/bench/measure_all.py
.venv/bin/python spike/bench/live_smoke.py    # needs .env.test.local
```
