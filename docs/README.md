# Document index

Use the current documents below for the maintained `main` branch. The immutable `starter-delivery-20261013` tag retains the original frozen documentation; later documentation maintenance does not rerun model experiments or change their results.

| Read | Purpose |
|---|---|
| [Implementation design](implementation_design.md) | Concise architecture, reward, training and implementation choices for review |
| [Reviewer guide](reviewer_guide.md) | Installation, preparation, three acceptance checks and cleanup |
| [Frozen release](frozen_release.md) | Version, recommended settings, evidence and limits |
| [Verification](verification.md) | Current validation summary, security and efficiency; dated historical checks remain labeled |
| [Three-arm results — 10 October](ablation_results_20261010.md) | Common baseline, compaction-only and year-hints-only observations |
| [Post-update evaluation — 13 October](post_training_evaluation_20261013.md) | Actual checkpoint loading, unsubmitted R0 result and failure analysis |
| [任务、交互与奖励](guide_steps_1_2.md) | Detailed Chinese explanation of shipping, state, tools and reward |
| [中文操作说明](operator_guide.md) | Detailed operator commands, feedback options and private configuration |
| [Third-party sources](../THIRD_PARTY.md) | Pinned revisions, licenses and attribution |

The [archive](archive/README.md) preserves earlier preparation, plans, review reports, experiment protocol and change history. Its old budgets and pending-work statements describe their original dates.

Private evidence paths such as `runs/...` identify retained operator artifacts; they are excluded from Git and will not exist in a fresh clone. The published reports contain aggregate findings and reproduction instructions. Credential-free integration can be reproduced without those private artifacts.
