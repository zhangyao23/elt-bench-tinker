# Implementation design: ELT-Bench execution-verified RL

## Task and scope

The environment trains a tool-using language model to inspect an ELT task, configure and execute extraction/loading, implement transformations, use database feedback, and explicitly submit a replayable pipeline. The initial official task is **shipping**: five sources (PostgreSQL, MongoDB, S3, REST API, Parquet), two models (drivers/trucks). DuckDB provides credential-free integration; Snowflake is the first official destination.

Task/schema/evaluation code is pinned to ELT-Bench elt_bench++ commit `fcf3129df49ce5ce63ff46b2bbbbba1eaf9ec055`. Warehouse-specific v5 references come from dataset revision `52a48541547a4340e19e54386350740acca80146`. Public task schemas match that dataset; downloaded source counts match the official inventory, and an offline manually authored DuckDB diagnostic matches all 16 required output columns. This diagnostic is not a credentialed model rollout.

## Stateful execution and submission

Each single-use episode owns independent raw/model namespaces, load configuration, successful transformation statements, feedback history and limits. Tools are `inspect_task`, `configure_load`, `run_load`, `query`, `transform`, `submit_pipeline`. Native source clients and Snowflake write_pandas perform extraction/loading; full refresh makes retries idempotent. This implementation replaces Airbyte's control plane with bounded native clients and uses SQL transformations rather than dbt scaffolding.

The submitted versioned pipeline contains task identity/digest, destination, load configuration and successful SQL in execution order. Real credentials stay in operator-side configuration/environment. A separate CLI reconstructs a pipeline in a new process, with no dependency on the original episode's source cache. Grading first verifies interactive raw content, then uses a fresh source reader and empty destination state to replay and query results. A conversational completion without explicit submission receives no credit.

## Execution-derived reward

The grader uses the pinned official evaluation SQL, sort helper and column comparator. Results are freshly queried; the upstream cached-output-CSV driver is not used. Stage-1 counts and exact canonical raw-content checks preserve NULLs, numerical driver representations and duplicate multiplicity. Successful source extraction is bound to pinned content digests where available.

The terminal reward is zero for an unsubmitted/incomplete/wrong-raw pipeline; after the loading gate, it is the fraction of required output columns that match reference values, with row-count checks. Official per-model/all-model correctness and extra-column diagnostics are reported separately. Extra columns remain allowed as in the original benchmark. No tool-call or self-reported-success reward is added. SQL/data errors are usable execution feedback; source/network/resource/grader failures make the episode invalid (`reward=null`), excluding its entire training group.

## Cookbook and optimization

The recipe reuses fixed cookbook commit `1c03a20fdda98156ccbec15728c0e2764b5a0bc3`: tool definitions, model-specific renderer, native multi-turn environment, group rollouts, advantage computation, trajectory conversion and train_step. Stateful tools execute sequentially within an episode; independent episodes may run concurrently. Cookbook masks prompt/tool observations and preserves sampled action log-probabilities.

We use cookbook's group-mean-centered advantages and importance_sampling loss, rather than claim an exact reproduction of DeepSeekMath's standard-deviation-normalized PPO/KL objective. Constant-reward groups are skipped. A successful step requires nonzero advantages plus awaited real Tinker forward_backward and optim_step results. The acknowledgement, training data, refreshed sampler and checkpoint provide evidence; offline compatibility tests never count as optimization.

## Isolation and efficiency

SQL is parsed structurally and limited to approved queries/functions/relations and model-table CREATE AS SELECT. No shell, arbitrary files/URLs, database switching, raw writes or multi-statement actions are exposed. DuckDB disables external access; Snowflake uses a separate restricted agent role with secondary roles disabled. Native source containers bind only localhost and use immutable image digests. Raw/model/reference permissions remain subject to the pending credentialed test.

Limits cover SQL/feedback bytes, rows, source sizes, SQL time, tools, turns and context/generation tokens. A conservative rate-card preflight refuses a Tinker run exceeding the operator's token budget; storage/warehouse/taxes are separate. Timing covers extraction, loading, replay, grading, rollout and optimization. Only source fingerprints are reused within a reader; final warehouse results are never cached for scoring.

## Validation status

Credential-free integration, independent-process replay and native cookbook/renderer compatibility pass locally. All five shipping source readers have passed actual content checks, including the local S3 city object. A manually authored pipeline using these sources, DuckDB loading/transformations, fresh execution grading and a separate-process replay matches all 16 required output columns against v5 Snowflake GT. This is a local integration diagnostic, not a model-generated rollout. Snowflake execution, an official credentialed model rollout and real Tinker optimization remain unverified. Account setup and a read-only capabilities check do not satisfy these gates. See verification.md for evidence and limitations; credentials and operator budget records are kept outside the repository.
