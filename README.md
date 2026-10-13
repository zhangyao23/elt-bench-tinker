# ELT-Bench + Tinker RLVR starter

A stateful tool environment and Tinker training recipe for the official **shipping** task. The model configures extraction/loading, queries and transforms warehouse data, uses execution feedback, and explicitly submits a replayable pipeline. Rewards come from actual warehouse results.

**Start here:** [implementation design](docs/implementation_design.md) → [reviewer guide](docs/reviewer_guide.md) → [validation and limits](docs/verification.md).

The default `main` branch contains the maintained implementation and documentation. The immutable [`starter-delivery-20261013`](https://github.com/zhangyao23/elt-bench-tinker/tree/starter-delivery-20261013) tag preserves package **0.3.1** and its frozen documentation. Earlier baseline: [`starter-baseline-20261010`](https://github.com/zhangyao23/elt-bench-tinker/tree/starter-baseline-20261010).

## Credential-free quick start

Verified with Python 3.13.9 and uv on Windows PowerShell:

```powershell
uv venv .venv
uv pip sync --python .venv/Scripts/python.exe requirements-local.lock
uv pip install --python .venv/Scripts/python.exe -e .
.venv/Scripts/python.exe -m elt_tinker.local_integration --output runs/local_01
```

Use a new output directory. Expected: `PASS`, correct/incorrect pipeline rewards **1 / 0.75**, and correct independent-process replay **1**. This executes extraction, DuckDB loading, SQL error/repair, submission and grading without cloud credentials or model calls. Cloud preparation, rollout, training and cleanup commands are in the [reviewer guide](docs/reviewer_guide.md).

## Observed validation

| Check | Result and scope |
|---|---|
| Local integration | Frozen 0.3.1: **90 native tests**, Lint, build, clean installed-wheel integration and independent replay passed. Minimal CI omits optional cookbook checks. |
| Official credentialed rollout | Public **Qwen/Qwen3.5-9B**, shipping/Snowflake: **16/16 required columns, R1**, independently replayed. One task and one successful sample. |
| Real Tinker RL optimization | **One acknowledged update**, four real trajectories, execution rewards **[0, 0.875, 0, 0.9375]**, nonzero advantages, **39,631 action tokens**. |
| Post-update evaluation | One checkpoint rollout reached **24 turns without submission: R0**. No RL improvement has been demonstrated. |

Recommended rollout: public model, field coverage enabled, compaction and year hints disabled. See [frozen settings](docs/frozen_release.md), [three-arm results](docs/ablation_results_20261010.md) and [post-update evaluation](docs/post_training_evaluation_20261013.md). Single-sample comparisons do not establish success rates or causal effects.

## Implementation and boundaries

- Five native sources: PostgreSQL, MongoDB, S3, HTTP API and Parquet. DuckDB is the local destination; Snowflake is the official destination. Destination adapters isolate warehouse operations.
- Submission verifies raw contents, replays the pipeline from an empty warehouse with a fresh reader, and queries results using pinned official evaluation logic. Invalid infrastructure samples are excluded from training.
- SQL restrictions, warehouse roles and episode schemas protect credentials and references. Shared Snowflake-role isolation partly relies on AST checks; public benchmark memorization remains possible.
- Native loading replaces the paper's Airbyte control plane. This starter does not claim full-benchmark replication, a new RL algorithm or proven optimization benefits.

[Document index](docs/README.md) · [Historical records](docs/archive/README.md) · [Pinned sources and licenses](THIRD_PARTY.md)

Credentials, private run traces, downloaded task data, professor communications and personal records are excluded from Git. The repository remains private; publication and professor access are the owner's decision.
