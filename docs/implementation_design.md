# ELT-Bench + Tinker: Implementation Design

Yao Zhang | 13 October 2026

Repository: https://github.com/zhangyao23/elt-bench-tinker/tree/starter-delivery-20261013

Frozen package: 0.3.1. Recommended rollout uses the public model with field coverage enabled, compaction and year hints disabled.

Three-arm rollout code: `0699e18`; recorded results: `ab4dc8c`. The original training acceptance used `9def90c`. A checkpoint-only evaluation entry was added on 13 October; its runtime file hashes and actual evidence are retained separately.

## 1. Scope and implementation choices

I built a stateful ELT-Bench environment and Tinker RLVR recipe. The official **shipping** task uses PostgreSQL, MongoDB, S3, HTTP API and Parquet sources, producing drivers and trucks models. DuckDB provides credential-free integration; Snowflake is the official destination. Warehouse adapters isolate destination operations from rollout and reward logic.

I chose native source clients, full-refresh loading and SQL transformations to keep execution and replay explicit without an Airbyte control plane or dbt project. This does not reproduce the paper's full agent stack. ELT-Bench elt_bench++ code, source data, Snowflake references, evaluator and cookbook are pinned; revisions and licenses are documented in the repository.

## 2. Stateful interaction and reward

Tools are `inspect_task`, `configure_load`, `run_load`, `query`, `transform` and `submit_pipeline`. Each episode owns independent raw/model schemas, loading configuration, successful SQL, feedback and limits. Tools execute sequentially within an episode; SQL errors allow repair. Submission or resource limits terminate the rollout.

The submitted pipeline stores task identity, loading configuration and SQL. Grading verifies raw row counts and canonical content, resets the warehouse, and replays extraction/loading/transformation with a fresh source reader. Actual results are queried using pinned official SQL, sorting and comparison. A separate CLI also reconstructs pipelines in a new process.

I use terminal outcome rewards because warehouse correctness is directly verifiable. Unsubmitted or incomplete/wrong-raw pipelines receive 0. Otherwise, reward is matched required columns divided by all required columns, with row-count checks. Full task correctness is reported separately. Feedback adds no process reward. Infrastructure failures invalidate samples and exclude their training group.

## 3. Tinker training

The recipe reuses cookbook tools, renderer, multi-turn environment, rollouts, group-centered advantages, trajectory conversion and training steps. Only generated action tokens are trained; sampled logprobs are preserved. The importance-sampling loss follows cookbook, without claiming an exact DeepSeekMath reproduction. Constant-reward groups are skipped. Optimization requires awaited real forward/backward and optimizer results.

One four-rollout group produced rewards [0, 0.875, 0, 0.9375]. An SDK serialization error occurred before optimization. After repair, the complete real trajectories, observations, rewards and logprobs were reused with a fresh initial learner of the same base/rank. One actual optimization step was acknowledged, and checkpoint/LoRA backup saved. A later single post-update rollout loaded this checkpoint but reached 24 turns without submitting (R0). No improvement has been demonstrated.

## 4. Reward hacking and efficiency

SQL AST validation restricts relations, functions and statements. Credentials and reference answers remain environment-side. Raw writes, arbitrary files/URLs, shell access and multiple statements are not exposed. Snowflake uses a restricted agent role and managed schemas; live probes verified read/write boundaries and cleanup. Episodes share that role, so cross-schema isolation partly relies on AST validation. Public benchmark memorization remains a limitation.

Phase timings cover extraction, loading, SQL, replay and grading. Fingerprint reuse reduced a small local fixture's median from 0.141s to 0.122s over ten runs per variant; cloud speedup is unproven. Final scoring is never cached. Bounded feedback, SQL timeouts and an atomic shared budget ledger limit resource use.

## 5. Validation and findings

- **Local integration:** real extraction/loading, SQL error/repair, submission and independent replay pass without credentials. Frozen 0.3.1: 90 native local tests, Lint, build and clean installed-wheel replay passed. Earlier code CI passed with optional cookbook checks skipped in its minimal environment; this is separate from the full local suite.
- **Official rollout:** Qwen3.5-9B completed shipping in Snowflake with reward 1.0 and 16/16 matched columns; independent-process replay agreed. This is one successful base-model rollout, not full-benchmark coverage or post-training improvement.
- **Real RL optimization:** one acknowledged step used execution-derived rewards and 39,631 generated action tokens; acknowledgement and checkpoint evidence are retained.

Three single-rollout trials with fixed code/model/data/budgets and seed schedule tested the common baseline, compaction only and public-year hints only. Results were 16/16, unsubmitted R0 and 12/16. Year hints triggered zero times; identical inputs with the same seed still yielded different actions. These trials do not establish optimization benefits. Both options remain experimental. Successful and failed runs are documented; private evidence and credentials are excluded from Git.

The single post-update trial used the common configuration: 30 tools, 9,011 generated tokens and 177.12s, versus the historical base-model trial's 19 tools, 3,568 tokens and 91.66s. Output correctness is unknown without submission; this comparison does not establish a population success rate or causal RL effect.
