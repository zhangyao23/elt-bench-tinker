# Implementation design: ELT-Bench execution-verified RL

## Task and scope

The environment trains a tool-using language model to inspect an ELT task, configure and execute extraction/loading, implement transformations, use database feedback, and explicitly submit a replayable pipeline. The initial official task is **shipping**: five sources (PostgreSQL, MongoDB, S3, REST API, Parquet), two models (drivers/trucks). DuckDB provides credential-free integration; Snowflake is the first official destination.

Task/schema/evaluation code is pinned to ELT-Bench elt_bench++ commit `fcf3129df49ce5ce63ff46b2bbbbba1eaf9ec055`. Warehouse-specific v5 references come from dataset revision `52a48541547a4340e19e54386350740acca80146`. Public task schemas match that dataset; downloaded source counts match the official inventory, and an offline manually authored DuckDB diagnostic matches all 16 required output columns. This diagnostic is not a credentialed model rollout.

## Stateful execution and submission

Each single-use episode owns independent raw/model namespaces, load configuration, successful transformation statements, feedback history and limits. Tools are `inspect_task`, `configure_load`, `run_load`, `query`, `transform`, `submit_pipeline`. Native source clients and Snowflake write_pandas perform extraction/loading; full refresh makes retries idempotent. This implementation replaces Airbyte's control plane with bounded native clients and uses SQL transformations rather than dbt scaffolding.

Version 0.2 accepts unqualified or logical `models.<output>` CREATE targets and maps them to the episode's model schema. It supports the required pure FIRST_VALUE/EXISTS expressions and the pinned parser's TO_DATE canonical form. Query feedback contains actual cursor type names and ISO dates, preserving NULLs and timezone offsets. Tool state reports configured/loaded sources, successfully created requested model names, statement count, and tool-call limits; it does not infer remaining model turns or tokens.

Optional `--field-feedback` inspects actual output metadata with LIMIT 0 and compares column names to the public task. It reports missing/extra fields and explicitly leaves values unverified. This feedback is an observation, not a process reward. It is disabled by default and never reads reference results. An inspection action error preserves the successful SQL; an infrastructure error still invalidates the episode. Required columns missing from the public specification are not inferred from hidden data.

The submitted versioned pipeline contains task identity/digest, destination, load configuration and successful SQL in execution order. Real credentials stay in operator-side configuration/environment. A separate CLI reconstructs a pipeline in a new process, with no dependency on the original episode's source cache. Grading first verifies interactive raw content, then uses a fresh source reader and empty destination state to replay and query results. A conversational completion without explicit submission receives no credit.

## Execution-derived reward

The grader uses the pinned official evaluation SQL, sort helper and column comparator. Results are freshly queried; the upstream cached-output-CSV driver is not used. Stage-1 counts and exact canonical raw-content checks preserve NULLs, numerical driver representations and duplicate multiplicity. Successful source extraction is bound to pinned content digests where available.

The terminal reward is zero for an unsubmitted/incomplete/wrong-raw pipeline; after the loading gate, it is the fraction of required output columns that match reference values, with row-count checks. Official per-model/all-model correctness and extra-column diagnostics are reported separately. Extra columns remain allowed as in the original benchmark. No tool-call or self-reported-success reward is added. SQL/data errors are usable execution feedback; source/network/resource/grader failures make the episode invalid (`reward=null`), excluding its entire training group.

## Cookbook and optimization

The recipe reuses fixed cookbook commit `1c03a20fdda98156ccbec15728c0e2764b5a0bc3`: tool definitions, model-specific renderer, native multi-turn environment, group rollouts, advantage computation, trajectory conversion and train_step. Stateful tools execute sequentially within an episode; independent episodes may run concurrently. Cookbook masks prompt/tool observations and preserves sampled action log-probabilities.

We use cookbook's group-mean-centered advantages and importance_sampling loss, rather than claim an exact reproduction of DeepSeekMath's standard-deviation-normalized PPO/KL objective. Constant-reward groups are skipped. A successful step requires nonzero advantages plus awaited real Tinker forward_backward and optim_step results. The acknowledgement, training data, refreshed sampler and checkpoint provide evidence; offline compatibility tests never count as optimization.

## Isolation and efficiency

SQL is parsed structurally and limited to approved queries/functions/relations and model-table CREATE AS SELECT. No shell, arbitrary files/URLs, database switching, raw writes or multi-statement actions are exposed. DuckDB disables external access; Snowflake uses a separate restricted agent role with secondary roles disabled. Native source containers bind only localhost and use immutable image digests. Live Snowflake probes verified raw SELECT and model CREATE, rejected raw UPDATE/CREATE, and confirmed schema cleanup. Managed access schemas keep future grants scoped to the loader-owned schemas.

Snowflake episodes share the restricted agent role. Random schemas separate their state, but the role's accumulated per-schema grants mean database permissions alone do not enforce cross-episode isolation; relation validation remains part of that boundary. No observed cross-episode bypass is claimed. Version 0.2 fixes the reproduced logical-target and selected date/window compatibility errors; SQL support remains an explicit allowlist, not unrestricted warehouse SQL. Fixed public benchmark data also leaves answer memorization and hardcoding as evaluation limitations.

Limits cover SQL/feedback bytes, rows, source sizes, SQL time, tools, turns and context/generation tokens. A shared atomic budget ledger reserves conservative token costs and a 24-hour checkpoint storage upper bound across sampling, training and retries; warehouse and taxes are separate. Failures do not automatically release their reservations. Timing covers extraction, loading, replay, grading, rollout and optimization. Only source fingerprints are reused within a reader; final warehouse results are never cached for scoring.

The source-fingerprint optimization was measured on the same small local fixture, with ten executions per variant: median episode time decreased from 0.14098 s to 0.12213 s. This approximately 13% local result does not establish a cloud-training speedup. The real 9B baseline took 113.544 s overall, including 20.099 s for initial loading and 29.412 s for submission/grading; its 23.209 s replay is contained within grading. Source extraction totaled 0.572 s across initial execution and replay. These measurements motivate examining loading and replay round trips before further source-reading optimizations. They do not isolate model-sampling latency.

The first 0.2 candidate's optional field-feedback measurement used twenty executions per mode: median local episode time was 0.12030 s off and 0.13163 s on, with a 0.00243 s median metadata-check phase. These sequential small-fixture measurements include logging and noise; their approximately 9% observed overhead does not establish a cloud latency effect or a model success-rate gain. This is a measured reliability/feedback option, not a claimed speed optimization.

A subsequent ten-execution measurement after the review fixes produced 0.12661 s off and 0.11402 s on. The opposite ordering confirms that these small sequential measurements do not establish a stable overall latency effect. The additional metadata query is real and measured; no timing result is selected as proof of a speedup.

## Validation status

All three execution gates have evidence from the preserved version 0.1 baseline. Its credential-free integration and independent-process replay passed with 46 regression tests. A real Qwen3.5-9B shipping rollout configured/loaded all five sources, generated two transformations and submitted a pipeline in Snowflake. Fresh execution reward was **0.8125 (13/16 required columns)** and separate-process replay reproduced it; the task is not fully solved. Three earlier 4B attempts did not submit and remain recorded failures.

One real training group produced rewards **[0, 0.875, 0, 0.9375]** and group-centered advantages **[-0.453125, 0.421875, -0.453125, 0.484375]**. An SDK dataclass logging incompatibility failed before any optimizer call. After repair, all original real trajectories, execution rewards and sampling logprobs were reused with a fresh initial LoRA learner of the same base model/rank; importance sampling handles sampler/learner mismatch. No resampling or synthetic rewards were used. Cookbook awaited actual forward/backward and optimizer futures; nine logprob sequences returned, and the service verified a training checkpoint for Qwen3.5-9B/rank16. All 39,631 sampled action tokens had training masks; observation tokens had zero advantage.

The recipe defaults to a 24-hour checkpoint TTL to bound storage cost. The demonstrated training checkpoint was subsequently extended to seven days at the operator's request, and its LoRA adapter was backed up locally; the adapter does not include optimizer state. Evidence is private operator data under runs; repository documentation records aggregate outcomes only. No post-update performance evaluation was run, so a single update is not a claim of improved accuracy or generalization.

Version 0.2 is a separate candidate with local regression, built-wheel/independent-process checks and a manual fixture preflight on real trial Snowflake. The manual preflight verifies the new SQL, date/type, schema-feedback, replay and cleanup paths; it is not a new official shipping/model rollout or an RL update. Offline policy parsing of 51 original training actions accepted 32 in the baseline and 43 in the candidate, with no newly rejected action; these are parser compatibility counts, not warehouse execution successes. No new paid Tinker sampling/training or model A/B has been performed for this version.
