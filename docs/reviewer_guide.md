# Reviewer guide: ELT-Bench + Tinker

## Scope and reading order

The implementation covers official **shipping** with five native source readers, Snowflake execution and a Tinker RL recipe. DuckDB provides credential-free integration. Start with [implementation design](implementation_design.md), then use the commands below. Detailed experiment results are in [the three-arm report](ablation_results_20261010.md); upstream revisions and licenses are in [THIRD_PARTY.md](../THIRD_PARTY.md).

Run evidence: original RL acceptance code `9def90c`; three-arm rollout/interface code `0699e18`; recorded comparison results `ab4dc8c`, in the preserved experiment history. The checkpoint evaluation entry was added on 13 October and its runtime hashes are retained with the new result. Documentation-only revisions do not count as additional experiments. The default `main` branch is the maintained reviewer entry. `starter-delivery-20261013` preserves the frozen 0.3.1 release; `starter-baseline-20261010` preserves the earlier baseline. See the [document index](README.md) for current reports and archived stage records.

| Acceptance check | Evidence and scope |
|---|---|
| Credential-free integration | Real local extraction, loading, SQL error/repair, submission and separate-process replay pass. Frozen 0.3.1: 90 native local tests, Lint, build and clean installed-wheel integration/replay passed. |
| Official credentialed rollout | Qwen3.5-9B completed shipping in Snowflake: 16/16 columns, R1, reproduced independently. This is one successful base-model rollout. |
| Real Tinker RL optimization | One acknowledged step used warehouse-derived rewards [0, 0.875, 0, 0.9375], nonzero group-centered advantages and 39,631 generated action tokens. One subsequent evaluation loaded that checkpoint but reached 24 turns without submitting (R0); no improvement has been demonstrated. |

Private raw run data and credentials are excluded from Git. The repository contains the implementation, reproduction instructions and aggregate findings. Local evidence paths listed in reports identify retained operator artifacts; those files are not expected to exist in a fresh clone.

To evaluate your completed training checkpoint, reuse the rollout command below with `--checkpoint-record <completed training.json>`. This verifies the record, loads saved weights and checks the server model/rank before sampling, without another optimization step. Keep task, feedback and sampling limits fixed for comparisons. The [single post-update report](post_training_evaluation_20261013.md) records the negative result and its limitations.

## 1. Credential-free integration

The verified command path is Windows PowerShell with Python 3.13.9; the package declares Python >=3.11. On Unix, the venv executable is `.venv/bin/python` and environment-variable syntax differs; these platform variants were not separately validated.

From the repository root, with Python and uv available:

```powershell
uv venv .venv
uv pip sync --python .venv/Scripts/python.exe requirements-local.lock
uv pip install --python .venv/Scripts/python.exe -e .
.venv/Scripts/python.exe -m elt_tinker.local_integration --output runs/reviewer_local_01
```

Expected output starts with `PASS`; correct/wrong pipelines receive R1/R0.75. Inspect `acceptance.json`, the tool traces and the independent replay under the output directory. This check needs no cloud credentials, Docker, Tinker or model weights. Use a new output directory for each run.

## 2. Official source and warehouse preparation

The following preparation is for the two credentialed checks. Install the locked client dependencies and pinned cookbook:

```powershell
$env:SETUPTOOLS_SCM_PRETEND_VERSION = '0.0.0+1c03a20'
uv pip install --python .venv/Scripts/python.exe -r requirements-training.lock
uv pip install --python .venv/Scripts/python.exe 'tinker-cookbook @ https://github.com/thinking-machines-lab/tinker-cookbook/archive/1c03a20fdda98156ccbec15728c0e2764b5a0bc3.zip'
Remove-Item Env:SETUPTOOLS_SCM_PRETEND_VERSION
.venv/Scripts/python.exe scripts/prepare_official.py
.venv/Scripts/python.exe scripts/prepare_sources.py
.venv/Scripts/python.exe scripts/audit_sources.py
```

The preparation scripts can download pinned public upstream material in a fresh clone; they do not require the author's private material bundle. Source archive downloads use HTTP Range extraction for shipping.

Start the isolated source services using Docker Compose:

```powershell
docker compose -f infra/compose.yaml up -d
```

In a separate terminal, keep `.venv/Scripts/python.exe infra/source_api.py` running. Back in the first terminal:

```powershell
$env:ELT_POSTGRES_PASSWORD = 'local_test_only'
$env:ELT_MONGODB_URI = 'mongodb://elt:local_test_only@127.0.0.1:58717/?authSource=admin&directConnection=true'
.venv/Scripts/python.exe scripts/seed_sources.py
```

Require all five readers to match pinned content. The published source-service evidence used WSL/Podman; `scripts/source_services.py --help` describes that optional route. Its default distro name is author-specific: pass your own `--distro` rather than assuming it exists. Source services use localhost only. After a restart, check content and reseed missing data; LocalStack's S3 object is not assumed persistent.

For a dedicated Snowflake account/database, review `infra/snowflake_setup.sql`. Generate your own key pair with `scripts/prepare_keypair.py`, run the setup and generated public-key registration SQL in an administrator session, and retain the private key locally. Supply `TINKER_API_KEY`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_AGENT_ROLE` and either `SNOWFLAKE_PRIVATE_KEY_FILE` or `SNOWFLAKE_PASSWORD` to the execution process. On Windows, `scripts/configure_credentials.ps1` provides local setup and process-scoped loading. It does not invoke sampling or training.

The loader and agent roles are separate; do not grant the agent administrator/loader inheritance or production-data access. Task references are private environment-side inputs, not model tools. Resource capability checks are available via `scripts/check_resources.py --cloud`; authentication alone is not a rollout.

## 3. Official rollout and a real optimization step

Only run after approving model/warehouse resources and a total spend cap. The following `$10` examples describe a new reviewer's checkout, not authorization to consume the author's account. Replace the cap with your approved amount. Keep the same budget ledger across calls; existing experiments must retain their existing ledger and approved cap.

The successful comparison configuration had public field coverage enabled, compaction disabled and year hints disabled:

```powershell
.venv/Scripts/python.exe -m elt_tinker.recipe --mode rollout --model Qwen/Qwen3.5-9B --public data/official/shipping/public --hidden data/official/shipping/hidden --destination snowflake --operator-config infra/source_config.example.json --output runs/reviewer_rollout_01 --max-turns 24 --context-tokens 24576 --generation-tokens 4096 --renderer qwen3_5_disable_thinking --field-feedback --no-condition-feedback --seed 20261010 --budget-ledger runs/reviewer_budget.json --max-cost-usd 10
.venv/Scripts/python.exe -m elt_tinker.recipe --mode train --model Qwen/Qwen3.5-9B --public data/official/shipping/public --hidden data/official/shipping/hidden --destination snowflake --operator-config infra/source_config.example.json --output runs/reviewer_train_01 --steps 1 --group-size 4 --max-batches 1 --max-turns 24 --context-tokens 24576 --generation-tokens 4096 --renderer qwen3_5_disable_thinking --field-feedback --no-condition-feedback --budget-ledger runs/reviewer_budget.json --max-cost-usd 10
```

Seeded sampling is rollout-only; it does not guarantee service-level bitwise determinism. The train command illustrates the current recipe, not an exact repetition of the original pre-feedback training group. If all rewards are equal, it may stop without an update; do not count that as optimization or rerun outside the approved budget.

Inspect `training.json` for status, actual reward and completed steps; submitted episodes save pipeline, tool trace, grade and warehouse query results. For training, require `optimization_acknowledgement.json` and checkpoint confirmation. A recorded rollout does not itself establish a completed optimizer step.

To replay a model-submitted pipeline independently:

```powershell
.venv/Scripts/python.exe -m elt_tinker.replay --public data/official/shipping/public --hidden data/official/shipping/hidden --pipeline runs/reviewer_rollout_01/batch_0/episode_0/pipeline.json --operator-config infra/source_config.example.json --output runs/reviewer_replay_01
```

The replay CLI exits 1 for a valid but not fully correct pipeline; inspect `valid` and `reward` separately. Unsubmitted rollouts are not manually submitted or scored after the fact. Stop the source API with Ctrl+C and run `docker compose -f infra/compose.yaml down` after reviewing. Episode warehouse resources are cleaned in `finally`; inspect any `cleanup_errors.json`.

## Limits and findings

In the three single-rollout trials, the common configuration scored 16/16, compaction-only did not submit and year-hints-only scored 12/16 with zero hints triggered. These do not demonstrate optimization benefits or stable success rates. Compaction/year hints remain experimental. The environment uses terminal outcome rewards; public field feedback is an observation, not an additional reward. Shared Snowflake agent roles still rely partly on AST checks for cross-episode isolation; public answer memorization remains a benchmark limitation.
