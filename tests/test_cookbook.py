"""Real cookbook/renderer compatibility; scripted tokens are not model sampling or optimization."""

import asyncio
import json

import pytest

pytest.importorskip("tinker_cookbook")
import tinker
import torch
from tinker_cookbook import tokenizer_utils
from tinker_cookbook.completers import TokensWithLogprobs
from tinker_cookbook.renderers import get_renderer
from tinker_cookbook.rl.data_processing import assemble_training_data, compute_advantages
from tinker_cookbook.rl.types import Trajectory, TrajectoryGroup, Transition

from elt_tinker.cookbook_env import ELTGroupBuilder, build_elt_env
from elt_tinker.environment import Episode
from elt_tinker.errors import InfrastructureError
from elt_tinker.local_fixture import GOOD_SQL, local_task


@pytest.fixture(scope="module")
def renderer():
    # Public tokenizer download/cached files only. No API access or model weights.
    tokenizer = tokenizer_utils.get_tokenizer("Qwen/Qwen3.5-4B")
    return get_renderer("qwen3_5_disable_thinking", tokenizer)


def tool_action(renderer, name, arguments):
    parameters = "".join(
        f"<parameter={key}>\n{json.dumps(value) if isinstance(value, (list, dict)) else value}\n</parameter>\n"
        for key, value in arguments.items()
    )
    text = f"<tool_call>\n<function={name}>\n{parameters}</function>\n</tool_call><|im_end|>"
    return renderer.tokenizer.encode(text, add_special_tokens=False)


def test_native_token_environment_feedback_and_training_masks(tmp_path, renderer):
    async def exercise():
        task = local_task(tmp_path / "task")
        episode = Episode(task, "duckdb", tmp_path / "run")
        environment = build_elt_env(episode, renderer, context_tokens=32768, generation_tokens=2048)
        observation, _ = await environment.initial_observation()
        actions = [
            ("inspect_task", {}),
            ("configure_load", {"table": "customers", "source_kind": "file"}),
            ("configure_load", {"table": "orders", "source_kind": "file"}),
            ("run_load", {"tables": []}),
            ("transform", {"sql": GOOD_SQL.replace("o.amount", "o.missing")}),
            ("query", {"sql": "SELECT * FROM raw.orders"}),
            ("transform", {"sql": GOOD_SQL}),
            ("submit_pipeline", {}),
        ]
        transitions = []
        try:
            for name, arguments in actions:
                tokens = tool_action(renderer, name, arguments)
                result = await environment.step(tokens)
                transitions.append(
                    Transition(
                        observation,
                        TokensWithLogprobs(tokens, [-0.5] * len(tokens)),
                        result.reward,
                        result.episode_done,
                        result.metrics,
                    )
                )
                observation = result.next_observation
            assert result.episode_done and result.reward == 1
            assert not episode.trace[4]["feedback"]["ok"]
            trajectory = Trajectory(transitions, observation)
            group = TrajectoryGroup([trajectory], [0.0], [{}])
            data, _ = assemble_training_data([group], [torch.tensor([0.25])])
            assert data
            action_tokens = sum(len(t.ac.tokens) for t in transitions)
            actual_mask = sum(int(d.loss_fn_inputs["mask"].to_torch().sum()) for d in data)
            assert actual_mask == action_tokens
            for datum in data:
                mask = datum.loss_fn_inputs["mask"].to_torch()
                logprobs = datum.loss_fn_inputs["logprobs"].to_torch()
                advantages = datum.loss_fn_inputs["advantages"].to_torch()
                assert bool((logprobs[mask == 1] == -0.5).all())
                assert bool((advantages[mask == 0] == 0).all())
                assert bool((advantages[mask == 1] == 0.25).all())
            assert compute_advantages([group])[0].tolist() == [0.0]
        finally:
            episode.close()

    asyncio.run(exercise())


def test_invalid_episode_excludes_whole_group(tmp_path, renderer):
    async def exercise():
        task = local_task(tmp_path / "task")
        builder = ELTGroupBuilder(task, "duckdb", tmp_path / "group", renderer, 2, {})
        try:
            environments = await builder.make_envs()
            builder.episodes[0].invalid_reason = "Source unavailable"
            with pytest.raises(InfrastructureError):
                await builder.compute_group_rewards([], environments)
        finally:
            await builder.cleanup()

    asyncio.run(exercise())


def test_constant_rewards_produce_no_learning_signal():
    group = TrajectoryGroup(
        [Trajectory([], tinker.ModelInput.from_ints([])) for _ in range(4)], [0.5] * 4, [{}] * 4
    )
    assert compute_advantages([group])[0].tolist() == [0.0] * 4
