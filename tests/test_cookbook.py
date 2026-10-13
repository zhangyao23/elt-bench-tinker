"""Real cookbook/renderer compatibility; scripted tokens are not model sampling or optimization."""

import asyncio
import json
from types import SimpleNamespace

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
from elt_tinker.recipe import create_evaluation_sampler, load_evaluation_checkpoint


@pytest.mark.parametrize("trained, loaded_model, loaded_rank", [
    (False, "Qwen/Qwen3.5-9B", 16), (True, "Qwen/Qwen3.5-9B", 16),
    (True, "other-model", 16), (True, "Qwen/Qwen3.5-9B", 8),
])
def test_evaluation_sampler_loads_weights_without_optimization(trained, loaded_model, loaded_rank):
    calls = []
    model = "Qwen/Qwen3.5-9B"
    checkpoint = {"checkpoint": "tinker://test/weights/updated", "rank": 16} if trained else None
    sampler = object()

    class Client:
        async def get_info_async(self):
            return tinker.types.GetInfoResponse(
                model_id="test", model_data=tinker.types.ModelData(model_name=loaded_model),
                is_lora=True, lora_rank=loaded_rank,
            )

        async def save_weights_and_get_sampling_client_async(self):
            calls.append("sample_loaded_weights")
            return sampler

    class Service:
        def create_sampling_client(self, *, base_model):
            calls.append(("sample_public_model", base_model))
            return sampler

        async def create_training_client_from_state_async(self, path):
            calls.append(("load_checkpoint", path))
            return Client()

    if trained and (loaded_model != model or loaded_rank != 16):
        with pytest.raises(ValueError, match="Loaded checkpoint model/rank"):
            asyncio.run(create_evaluation_sampler(Service(), model, checkpoint))
        assert calls == [("load_checkpoint", checkpoint["checkpoint"])]
        return
    assert asyncio.run(create_evaluation_sampler(Service(), model, checkpoint)) is sampler
    assert calls == (
        [("load_checkpoint", checkpoint["checkpoint"]), "sample_loaded_weights"]
        if trained else [("sample_public_model", model)]
    )


@pytest.mark.parametrize("changed", [
    {}, {"status": "failed"}, {"optimization_steps_completed": 0},
    {"model": "other"}, {"rank": 8}, {"provenance": {"task": "other"}},
    {"checkpoint": "tinker://test/sampler_weights/unverified"},
])
def test_evaluation_checkpoint_rejects_unconfirmed_or_mismatched_records(tmp_path, changed):
    source = {
        "mode": "train", "status": "optimization_completed", "optimization_steps_completed": 1,
        "model": "Qwen/Qwen3.5-9B", "rank": 16, "provenance": {"task": "pinned"},
        "checkpoint": "tinker://test/weights/updated", **changed,
    }
    record = tmp_path / "training.json"
    record.write_text(json.dumps(source), encoding="utf-8")
    args = SimpleNamespace(checkpoint_record=record, model="Qwen/Qwen3.5-9B", rank=16)
    task = SimpleNamespace(spec={"provenance": {"task": "pinned"}})
    if changed:
        with pytest.raises(ValueError):
            load_evaluation_checkpoint(args, task)
    else:
        result = load_evaluation_checkpoint(args, task)
        assert result["checkpoint"] == source["checkpoint"] and result["rank"] == 16


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


@pytest.mark.parametrize("compact_feedback", [False, True])
def test_native_token_environment_feedback_and_training_masks(tmp_path, renderer, compact_feedback):
    async def exercise():
        task = local_task(tmp_path / "task")
        episode = Episode(task, "duckdb", tmp_path / "run")
        environment = build_elt_env(
            episode, renderer, context_tokens=32768, generation_tokens=2048,
            compact_feedback=compact_feedback,
        )
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


def test_sdk_dataclass_datum_serialization_preserves_tensors():
    from elt_tinker.recipe import serialize_datum

    datum = tinker.Datum(
        model_input=tinker.ModelInput.from_ints([1, 2]),
        loss_fn_inputs={"logprobs": torch.tensor([-0.5, -0.25]), "advantages": torch.tensor([0.0, 0.25])},
    )
    payload = json.loads(json.dumps(serialize_datum(datum)))
    restored = tinker.Datum(
        model_input=tinker.ModelInput.model_validate(payload["model_input"]),
        loss_fn_inputs={k: tinker.TensorData(**v) for k, v in payload["loss_fn_inputs"].items()},
    )
    assert restored.model_input.model_dump() == datum.model_input.model_dump()
    for key in datum.loss_fn_inputs:
        assert torch.equal(restored.loss_fn_inputs[key].to_torch(), datum.loss_fn_inputs[key].to_torch())


def test_uncertain_training_request_cannot_be_issued_twice(tmp_path, monkeypatch):
    from elt_tinker import recipe

    issued = []

    async def lost_response(*args, **kwargs):
        assert (tmp_path / "optimization_attempt.json").exists()
        issued.append(True)
        raise RuntimeError("unit fault: response lost after sending")

    monkeypatch.setattr(recipe, "train_step", lost_response)

    async def exercise():
        with pytest.raises(RuntimeError):
            await recipe.optimize_data([], None, 1e-5, tmp_path)
        with pytest.raises(FileExistsError):
            await recipe.optimize_data([], None, 1e-5, tmp_path)

    asyncio.run(exercise())
    assert len(issued) == 1


def test_group_failure_waits_for_sibling_before_cleanup(monkeypatch):
    from elt_tinker import cookbook_env

    async def exercise():
        finished = asyncio.Event()

        class Builder:
            async def make_envs(self):
                return ["failed", "sibling"]

            async def cleanup(self):
                assert finished.is_set()

        async def scripted_rollout(policy, env):
            if env == "failed":
                raise InfrastructureError("unit test service failure")
            await asyncio.sleep(0.02)
            finished.set()
            return object()

        monkeypatch.setattr(cookbook_env, "do_single_rollout", scripted_rollout)
        builder = Builder()
        try:
            with pytest.raises(InfrastructureError):
                await cookbook_env.CompleteGroup().execute(builder, None)
        finally:
            await builder.cleanup()

    asyncio.run(exercise())


def test_cancelled_sync_work_finishes_before_cleanup():
    import threading

    from elt_tinker.cookbook_env import complete_sync

    async def exercise():
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def operation():
            started.set()
            assert release.wait(2)
            finished.set()

        work = asyncio.create_task(complete_sync(operation))
        await asyncio.to_thread(started.wait, 2)
        work.cancel()
        await asyncio.sleep(0)
        assert not work.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert finished.is_set()

    asyncio.run(exercise())


def test_compact_query_keeps_submission_space_and_actual_turn_budget(tmp_path, renderer):
    async def exercise(compact):
        label = "compact" if compact else "full"
        episode = Episode(local_task(tmp_path / label / "task"), "duckdb", tmp_path / label / "run")
        try:
            from elt_tinker.local_fixture import load_all

            load_all(episode)
            assert episode.act("transform", {"sql": GOOD_SQL})["ok"]
            env = build_elt_env(
                episode, renderer, max_turns=3, context_tokens=3000,
                generation_tokens=512, compact_feedback=compact,
            )
            await env.initial_observation()
            value = "contextmarker" * 60
            sql = f"SELECT c.id, '{value}' AS preview FROM raw.customers c CROSS JOIN raw.orders o"
            result = await env.step(tool_action(renderer, "query", {"sql": sql}))
            assert len(episode.trace[-1]["feedback"]["result"]["rows"]) == 12
            if not compact:
                assert result.episode_done and result.metrics["context_overflow"] == 1
                assert not episode.submitted
                return
            assert not result.episode_done and result.next_observation.length + 128 <= 3000
            message = env.message_env.history[-1]
            feedback = json.loads(message["content"])
            assert len(feedback["result"]["rows"]) == 6
            assert feedback["state"]["model_turns_remaining"] == 2
            assert feedback["state"]["tool_calls_remaining"] > 2
            assert "explicit" in feedback["next_action_hint"]
            submitted = await env.step(tool_action(renderer, "submit_pipeline", {}))
            assert submitted.episode_done and submitted.reward == 1 and episode.submitted
        finally:
            episode.close()

    asyncio.run(exercise(False))
    asyncio.run(exercise(True))


def test_actual_completer_clips_generation_to_context_without_service():
    from types import SimpleNamespace

    from tinker_cookbook.completers import TinkerTokenCompleter

    seen = []

    class RecordingClient:
        async def sample_async(self, *, prompt, num_samples, sampling_params):
            seen.append(sampling_params.max_tokens)
            return SimpleNamespace(sequences=[SimpleNamespace(tokens=[1], logprobs=[-0.5], stop_reason="stop")])

    async def exercise():
        policy = TinkerTokenCompleter(RecordingClient(), max_tokens=512, context_window=3000)
        result = await policy(tinker.ModelInput.from_ints([1] * 2800), stop=[2])
        assert result.tokens == [1] and seen == [200]

    asyncio.run(exercise())


def test_seed_adapter_preserves_native_completion_and_request_parameters():
    from types import SimpleNamespace

    from tinker_cookbook.completers import TinkerTokenCompleter

    from elt_tinker.recipe import SeededSamplingClient

    seen = []

    class RecordingClient:
        async def sample_async(self, *, prompt, num_samples, sampling_params):
            seen.append((prompt, num_samples, sampling_params))
            return SimpleNamespace(sequences=[SimpleNamespace(
                tokens=[1], logprobs=[-0.5], stop_reason="stop",
            )])

    async def exercise():
        policy = TinkerTokenCompleter(
            SeededSamplingClient(RecordingClient(), 20261010),
            max_tokens=512, context_window=3000,
        )
        prompt = tinker.ModelInput.from_ints([1] * 2800)
        for _ in range(2):
            result = await policy(prompt, stop=[2])
            assert result.tokens == [1] and result.logprobs == [-0.5]
        assert [params.seed for _, _, params in seen] == [20261010, 20261011]
        for actual_prompt, count, params in seen:
            assert actual_prompt is prompt and count == 1
            assert params.max_tokens == 200 and params.temperature == 1
            assert params.stop == [2] and params.top_p == 1 and params.top_k == -1

    asyncio.run(exercise())


def test_ablation_flags_reach_episode_without_service(tmp_path, monkeypatch):
    from elt_tinker.recipe import arguments

    monkeypatch.setattr("sys.argv", [
        "elt-train", "--mode", "rollout", "--model", "unit-model",
        "--public", "public", "--hidden", "hidden", "--destination", "duckdb",
        "--output", "run", "--max-cost-usd", "1", "--field-feedback",
        "--no-condition-feedback", "--seed", "20261010",
    ])
    args = arguments()
    assert args.field_feedback and not args.condition_feedback
    assert not args.compact_feedback and args.seed == 20261010
    builder = ELTGroupBuilder(
        local_task(tmp_path / "task"), "duckdb", tmp_path / "run", None, 1, {},
        field_feedback=args.field_feedback, condition_feedback=args.condition_feedback,
    )
    try:
        episode = builder._create_episode(0)
        assert episode.field_feedback and not episode.condition_feedback
    finally:
        asyncio.run(builder.cleanup())
