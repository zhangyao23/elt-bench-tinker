# Frozen starter submission — 13 October 2026

Release tag: `starter-delivery-20261013`. Package: `elt-tinker 0.3.1`. The tag identifies the frozen code and documentation; original experiment revisions remain listed below. The earlier main revision is preserved by `starter-baseline-20261010`. The default main branch may receive documentation maintenance; the frozen tag remains unchanged.

## Scope and recommended configuration

The deliverable is a stateful ELT-Bench RLVR environment and a real Tinker training recipe. Official coverage is shipping/Snowflake; DuckDB provides credential-free integration. This is not a full-benchmark replication or evidence of improved RL performance.

Use the public `Qwen/Qwen3.5-9B` model for the recommended rollout. This identifier is distinct from `Qwen/Qwen3.5-9B-Base`; "base" in historical experiment notes means the starting public weights, not that separately named model.

| Setting | Frozen value |
|---|---|
| Task / destination | Pinned official shipping / Snowflake |
| Renderer | qwen3_5_disable_thinking |
| Model turns / tool calls | 24 / 48 |
| Context / generation limit | 24576 / 4096 tokens |
| Temperature / top-p / top-k | 1 / 1 / -1 |
| Public field coverage feedback | Enabled |
| Compaction / public-year hints | Disabled / disabled |
| Evaluation seed | 20261010 + sampling request index; service determinism is not guaranteed |
| Reward | Verified matched required columns / all required columns, after submission and complete raw loading |

The command in [reviewer_guide.md](reviewer_guide.md) sets these values explicitly. Loading a trained checkpoint is an optional evaluation path via `--checkpoint-record`; it is not the recommended model based on the current evidence. Training uses independent rollouts without an evaluation seed.

## Retained acceptance evidence

Final local validation: 90 native tests, Ruff, wheel/sdist build and clean installed-wheel integration/replay passed. Correct/incorrect local pipelines received R1/R0.75; independent replay received R1. Standards and Spec review found no blocking issues. These checks added no paid model calls.

| Check | Observed evidence |
|---|---|
| Credential-free integration | Actual extraction/loading, SQL error/repair, submission and separate-process replay |
| Official credentialed rollout | Public-model shipping rollout: 16/16, R1; independent Snowflake replay agreed |
| Real RL optimization | Four real trajectories, rewards [0, 0.875, 0, 0.9375], nonzero group-centered advantages; one acknowledged Tinker update, saved checkpoint and local adapter backup |

Original optimization code: `9def90c`. Three-arm comparison code: `0699e18`; report: `ab4dc8c`. The frozen release additionally contains checkpoint evaluation support. It does not retroactively rerun earlier model experiments.

## Negative results and limits

Compaction-only did not submit; year-hints-only reached 12/16, with zero hints triggered. One later rollout loaded the trained checkpoint but reached 24 turns without submitting: R0, 30 tool calls, seven SQL compilation errors, 9,011 generated tokens and 177.12 seconds. Output-column correctness is unknown without submission. See the [post-update report](post_training_evaluation_20261013.md).

Each comparison arm has one sample, and the training evidence is one small group/update. Training used earlier tool feedback; later evaluation used the repaired environment. No population success rate, causal RL benefit/degradation or general cloud speedup is claimed. Partial column reward is not full task success. Infrastructure failures are reported separately and excluded from training.

Raw data, credentials, private run traces, professor communications and checkpoint addresses are excluded from Git. The public benchmark may be memorized; shared Snowflake agent-role isolation still partly relies on AST validation.

## Reviewer entry and handoff

Start with [implementation_design.md](implementation_design.md) and [reviewer_guide.md](reviewer_guide.md). [verification.md](verification.md) separates current validation from historical evidence. [archived change and experiment log](archive/change_and_experiment_log.md) records fixes and unsuccessful candidates.

No additional paid experiments or checkpoint extensions are part of this freeze. The operator's checkpoint retains the previously approved expiry; a reviewer can run a new small training group using their own approved resources. The repository remains private until the owner decides access and publication. Native Google Doc content/access and the professor's repository access must be confirmed before the owner sends the final message.
