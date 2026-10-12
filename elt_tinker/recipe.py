"""Actual Tinker rollout/GRPO step. No mock service or local optimization path exists."""

import argparse
import asyncio
import importlib.metadata
import json
import math
import os
import time
from dataclasses import asdict
from pathlib import Path

import tinker
from tinker_cookbook import model_info, tokenizer_utils
from tinker_cookbook.completers import TinkerTokenCompleter, TokensWithLogprobs
from tinker_cookbook.renderers import get_renderer
from tinker_cookbook.rl.data_processing import assemble_training_data, compute_advantages
from tinker_cookbook.rl.rollouts import do_group_rollout
from tinker_cookbook.rl.train import train_step
from tinker_cookbook.rl.types import Trajectory, TrajectoryGroup, Transition

from elt_tinker.cookbook_env import CompleteGroup, ELTGroupBuilder
from elt_tinker.environment import safe_error
from elt_tinker.errors import InfrastructureError
from elt_tinker.task import Task, read_json, write_json


class SeededSamplingClient:
    """Keep cookbook completion unchanged; attach a reproducible per-request seed."""

    def __init__(self, client, seed: int):
        self.client, self.next_seed = client, seed

    async def sample_async(self, **kwargs):
        kwargs["sampling_params"] = kwargs["sampling_params"].model_copy(
            update={"seed": self.next_seed}
        )
        self.next_seed += 1
        return await self.client.sample_async(**kwargs)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["rollout", "train"], required=True)
    parser.add_argument("--model", required=True, help="An account-supported Tinker model ID")
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--hidden", type=Path, required=True)
    parser.add_argument("--destination", choices=["duckdb", "snowflake"], required=True)
    parser.add_argument("--operator-config", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New private run directory")
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument(
        "--max-batches", type=int, default=3, help="Stop if rewards remain constant"
    )
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--generation-tokens", type=int, default=2048)
    parser.add_argument("--context-tokens", type=int, default=32768)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--renderer", help="Default: cookbook's model-specific recommendation")
    parser.add_argument(
        "--field-feedback", action="store_true",
        help="Opt-in public output-column coverage feedback; terminal reward is unchanged",
    )
    parser.add_argument(
        "--compact-feedback", action="store_true",
        help="Six-row previews, accurate model-turn warnings and short submission reserve",
    )
    parser.add_argument(
        "--no-condition-feedback", dest="condition_feedback", action="store_false",
        help="Disable public-year review hints while retaining field coverage feedback",
    )
    parser.add_argument(
        "--seed", type=int,
        help="Rollout-only sampling seed; each request uses seed + request index",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        required=True,
        help="Approved total reservation cap across all runs sharing the budget ledger",
    )
    parser.add_argument("--budget-ledger", type=Path, default=Path("runs/tinker_budget.json"))
    parser.add_argument("--resume-from", type=Path, help="Reuse a saved group before any optimizer call; no resampling")
    parser.add_argument(
        "--checkpoint-record", type=Path,
        help="Rollout-only: evaluate the trained checkpoint in a completed training.json",
    )
    args = parser.parse_args()
    if args.checkpoint_record and (args.mode != "rollout" or args.resume_from):
        parser.error("--checkpoint-record requires rollout mode without --resume-from")
    return args


def load_evaluation_checkpoint(args, task):
    if args.checkpoint_record is None:
        return None
    source = read_json(args.checkpoint_record)
    steps = source.get("optimization_steps_completed")
    if (
        source.get("mode") != "train"
        or source.get("status") != "optimization_completed"
        or not isinstance(steps, int)
        or steps < 1
    ):
        raise ValueError("Evaluation requires a completed real optimization record")
    if source.get("model") != args.model or source.get("rank") != args.rank:
        raise ValueError("Evaluation model/rank differ from the trained checkpoint")
    if source.get("provenance") != task.spec.get("provenance", {}):
        raise ValueError("Evaluation task provenance differs from the training task")
    path = source.get("checkpoint", "")
    if not isinstance(path, str) or not path.startswith("tinker://") or "/weights/" not in path:
        raise ValueError("Completed record has no Tinker training-state checkpoint")
    return {"checkpoint": path, "rank": source["rank"], "optimization_steps_completed": steps}


async def create_evaluation_sampler(service, model, checkpoint):
    if checkpoint is None:
        return service.create_sampling_client(base_model=model)
    client = await service.create_training_client_from_state_async(checkpoint["checkpoint"])
    info = await client.get_info_async()
    if (info.model_data.model_name or info.model_name) != model or info.lora_rank != checkpoint["rank"]:
        raise ValueError("Loaded checkpoint model/rank differ from the evaluation configuration")
    # Sampling only: no optimizer request and no new persistent checkpoint.
    return await client.save_weights_and_get_sampling_client_async()


async def run(args):
    if not os.getenv("TINKER_API_KEY"):
        raise ValueError(
            "TINKER_API_KEY is absent. Provide it locally, never in a transcript/repository."
        )
    if not 1 <= args.steps <= args.max_batches <= 3 or not 1 <= args.group_size <= 8:
        raise ValueError("Require 1 <= steps <= max-batches and 1 <= group-size <= 8")
    if args.mode == "train" and args.group_size < 2:
        raise ValueError("Centered GRPO requires at least two independent rollouts")
    if args.seed is not None and (args.mode != "rollout" or args.seed < 0):
        raise ValueError("A nonnegative seed is supported only for single-rollout evaluation")
    if not 1 <= args.max_turns <= 32 or not 64 <= args.generation_tokens <= 4096:
        raise ValueError("Require 1..32 turns and 64..4096 generation tokens per turn")
    if (
        not 2048 <= args.context_tokens <= 65536
        or not math.isfinite(args.max_cost_usd)
        or args.max_cost_usd <= 0
    ):
        raise ValueError("Require 2048..65536 context tokens and a positive approved budget")
    task = Task.load(args.public, args.hidden)
    checkpoint = load_evaluation_checkpoint(args, task)
    if task.spec.get("official") and args.destination != "snowflake":
        raise ValueError(
            "This official bundle pins Snowflake GT. Local DuckDB is a separate integration gate."
        )
    if args.destination == "snowflake":
        required = [
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_ROLE",
            "SNOWFLAKE_AGENT_ROLE",
        ]
        missing = [key for key in required if not os.getenv(key)]
        if not os.getenv("SNOWFLAKE_PASSWORD") and not os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"):
            missing.append("SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_FILE")
        if missing:
            raise ValueError("Missing warehouse settings: " + ", ".join(missing))
    # Public rate card only; no Tinker service request is issued before budget check.
    import requests

    response = requests.get(
        "https://tinker-docs.thinkingmachines.ai/tinker/models.json", timeout=30
    )
    response.raise_for_status()
    rates = next((rate for rate in response.json() if rate["tinker_id"] == args.model), None)
    if rates is None:
        raise ValueError("No current public rate card for selected model; refusing unbounded spend")
    worst_cost = token_budget(args, rates)
    # One persistent checkpoint, expiring after 24h. Use a full-model-sized upper
    # bound (24 bytes/parameter) although Tinker checkpoints store LoRA state.
    storage_cost = (
        int(rates["params"]) * 24 / 1_000_000_000 * 0.10 / 30 if args.mode == "train" else 0.0
    )
    if worst_cost + storage_cost > args.max_cost_usd:
        raise ValueError(
            f"Conservative token cost ${worst_cost:.2f} exceeds approved ${args.max_cost_usd:.2f}; reduce batches/turns/group/context"
        )
    args.output.mkdir(parents=True, exist_ok=False)
    operator = read_json(args.operator_config) if args.operator_config else {}
    budget = reserve_budget(
        args.budget_ledger, args.max_cost_usd, worst_cost + storage_cost, args.output
    )
    record = {
        "mode": args.mode,
        "model": args.model,
        "destination": args.destination,
        "official": task.spec.get("official", False),
        "provenance": task.spec.get("provenance", {}),
        "group_size": args.group_size if args.mode == "train" else 1,
        "requested_steps": args.steps,
        "max_batches": args.max_batches,
        "optimization_steps_completed": 0,
        "weights_source": "trained_checkpoint" if checkpoint else "public_model",
        "rank": args.rank,
        "max_turns": args.max_turns,
        "context_tokens": args.context_tokens,
        "generation_tokens": args.generation_tokens,
        "field_feedback": args.field_feedback,
        "compact_feedback": args.compact_feedback,
        "condition_feedback": args.condition_feedback,
        "sampling_seed": args.seed,
        "generation_reserve_tokens": min(128, args.generation_tokens) if args.compact_feedback else args.generation_tokens,
        "status": "started",
        "batches": [],
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("tinker", "tinker-cookbook", "torch")
        },
    }
    record.update(
        max_cost_usd=args.max_cost_usd,
        conservative_token_cost_usd=worst_cost,
        conservative_checkpoint_storage_usd=storage_cost,
        budget_reservation=budget,
        checkpoint_ttl_seconds=86400,
        rate_card=rates,
        budget_excludes="warehouse and taxes; rate-card changes require a new run",
    )
    if checkpoint:
        record["evaluation_checkpoint"] = {
            **checkpoint, "record": str(args.checkpoint_record.resolve()),
        }
    write_json(args.output / "training.json", record)
    try:
        service = tinker.ServiceClient()
        capabilities = await service.get_server_capabilities_async()
        supported = next(
            (model for model in capabilities.supported_models if model.model_name == args.model),
            None,
        )
        if (
            supported is None
            or not supported.sampleable
            or (args.mode == "train" and not supported.trainable)
        ):
            raise ValueError("Requested model is not in this account's server capabilities")
        if args.context_tokens > supported.max_context_length:
            raise ValueError("Requested context exceeds account model limit")
        tokenizer = tokenizer_utils.get_tokenizer(args.model)
        renderer_name = args.renderer or model_info.get_recommended_renderer_name(args.model)
        renderer = get_renderer(renderer_name, tokenizer)
        record["renderer"] = renderer_name
        write_json(args.output / "training.json", record)
        if args.mode == "train":
            from tinker_cookbook import hyperparam_utils

            training_client = await service.create_lora_training_client_async(
                base_model=args.model, rank=args.rank
            )
            sampling_client = await training_client.save_weights_and_get_sampling_client_async()
            learning_rate = args.learning_rate or hyperparam_utils.get_lr(args.model)
        else:
            training_client = None
            sampling_client = await create_evaluation_sampler(service, args.model, checkpoint)
        for batch_index in range(args.max_batches if args.mode == "train" else 1):
            batch_output = args.output / f"batch_{batch_index}"
            builder = ELTGroupBuilder(
                task,
                args.destination,
                batch_output,
                renderer,
                args.group_size if args.mode == "train" else 1,
                operator,
                args.max_turns,
                args.context_tokens,
                args.generation_tokens,
                field_feedback=args.field_feedback,
                compact_feedback=args.compact_feedback,
                condition_feedback=args.condition_feedback,
            )
            policy = TinkerTokenCompleter(
                SeededSamplingClient(sampling_client, args.seed)
                if args.seed is not None else sampling_client,
                max_tokens=args.generation_tokens,
                context_window=args.context_tokens,
            )
            sample_start = time.monotonic()
            print(f"Starting {args.mode} batch {batch_index}; {builder.group_size} rollout(s)", flush=True)
            try:
                group = await do_group_rollout(builder, policy, strategy=CompleteGroup())
            except InfrastructureError as error:
                record["batches"].append(
                    {
                        "batch": batch_index,
                        "status": "invalid_group_excluded",
                        "error": safe_error(error),
                    }
                )
                raise
            rewards = group.get_total_rewards()
            batch = {
                "batch": batch_index,
                "rewards": rewards,
                "rollout_seconds": time.monotonic() - sample_start,
                "episodes": [str(episode.output) for episode in builder.episodes],
                "sampled_tokens": sum(
                    len(t.ac.tokens)
                    for trajectory in group.trajectories_G
                    for t in trajectory.transitions
                ),
            }
            record["batches"].append(batch)
            print(f"Batch {batch_index} execution rewards: {rewards}", flush=True)
            write_json(
                batch_output / "trajectories.json",
                [
                    {
                        "stop_reason": trajectory.stop_reason,
                        "final_ob": trajectory.final_ob.model_dump(mode="json"),
                        "transitions": [
                            {
                                "observation": t.ob.model_dump(mode="json"),
                                "action_tokens": t.ac.tokens,
                                "logprobs": t.ac.maybe_logprobs,
                                "reward": t.reward,
                                "metrics": t.metrics,
                            }
                            for t in trajectory.transitions
                        ],
                    }
                    for trajectory in group.trajectories_G
                ],
            )
            if args.mode == "rollout":
                grade = builder.episodes[0].grade
                if not grade or not grade["valid"]:
                    raise InfrastructureError("Official rollout had no valid execution grade")
                record["status"] = "rollout_completed"
                record.update(
                    task_solved=grade.get("all_models_match", False),
                    pipeline_submitted=grade["submitted"],
                    reward=grade["reward"],
                )
                break
            advantages = compute_advantages([group])
            batch["advantages"] = advantages[0].tolist()
            if not any(value != 0 for value in batch["advantages"]):
                batch["status"] = "constant_rewards_no_update"
                write_json(args.output / "training.json", record)
                continue
            data, metadata = assemble_training_data([group], advantages)
            if not data:
                raise ValueError("No sampled action tokens available for optimization")
            write_json(
                batch_output / "training_data.json",
                {"datums": [serialize_datum(datum) for datum in data], "metadata": metadata},
            )
            # Cookbook masks prompts/tool observations, uses sampled logprobs and
            # pipelines real forward_backward + optim_step; returned futures are awaited.
            metrics = await optimize_data(data, training_client, learning_rate, batch_output)
            record["optimization_steps_completed"] += 1
            batch.update(status="optimization_acknowledged", optimizer_metrics=metrics)
            write_json(args.output / "training.json", record)
            # Refresh sampler after each actual update, as required by cookbook.
            sampling_client = await training_client.save_weights_and_get_sampling_client_async()
            if record["optimization_steps_completed"] >= args.steps:
                record["status"] = "optimization_completed"
                break
        if args.mode == "train" and record["optimization_steps_completed"] < args.steps:
            raise RuntimeError(
                "Bounded rollout budget exhausted without enough nonzero-advantage updates"
            )
        if training_client is not None:
            saved = await (
                await training_client.save_state_async("elt-final", ttl_seconds=86400)
            ).result_async()
            record["checkpoint"] = saved.path
    except asyncio.CancelledError:
        record.update(status="cancelled", error="Operator or caller cancelled the run")
        raise
    except Exception as error:
        record.update(status="failed", error=safe_error(error))
        raise
    finally:
        write_json(args.output / "training.json", record)
    print(
        f"{record['status']}; acknowledged optimization steps: {record['optimization_steps_completed']}"
    )


def token_budget(args, rates):
    groups = args.max_batches if args.mode == "train" else 1
    size = args.group_size if args.mode == "train" else 1
    turns = groups * size * args.max_turns

    def price(key):
        value = float(rates[key].lstrip("$"))
        if not math.isfinite(value) or value < 0:
            raise ValueError("Rate card prices must be finite and nonnegative")
        return value

    # Worst case even if renderer breaks prefix sharing: one context-length datum per turn.
    total = turns * args.context_tokens * price("prefill")
    total += turns * args.generation_tokens * price("sample")
    if args.mode == "train":
        total += turns * args.context_tokens * price("train")
    return total / 1_000_000


def reserve_budget(path: Path, limit: float, cost: float, output: Path) -> dict:
    """Reserve worst-case spend atomically; failures retain their reservation."""
    if not all(math.isfinite(value) and value >= 0 for value in (limit, cost)) or limit == 0:
        raise ValueError("Budget must be finite and positive")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        ledger = read_json(path) if path.exists() else {"limit_usd": limit, "reservations": []}
        if ledger["limit_usd"] != limit:
            raise ValueError("Ledger limit differs; do not change the approved cap implicitly")
        previous = [entry["reserved_usd"] for entry in ledger["reservations"]]
        if not all(math.isfinite(value) and value >= 0 for value in previous):
            raise ValueError("Invalid budget ledger")
        reserved = sum(previous)
        if cost > limit - reserved:
            raise ValueError(f"Insufficient cumulative budget: ${limit - reserved:.2f} remains")
        entry = {"output": str(output.resolve()), "reserved_usd": cost}
        ledger["reservations"].append(entry)
        temporary = path.with_suffix(path.suffix + ".tmp")
        write_json(temporary, ledger)
        os.replace(temporary, path)
        return {**entry, "ledger": str(path), "remaining_usd": limit - reserved - cost}
    finally:
        os.close(descriptor)
        lock.unlink()


def serialize_datum(datum: tinker.Datum) -> dict:
    """SDK 0.32 uses dataclasses for Datum/TensorData, Pydantic for ModelInput."""
    result = {
        "model_input": datum.model_input.model_dump(mode="json"),
        "loss_fn_inputs": {
            key: {"data": value.data, "dtype": value.dtype, "shape": value.shape,
                  "sparse_crow_indices": value.sparse_crow_indices,
                  "sparse_col_indices": value.sparse_col_indices}
            for key, value in datum.loss_fn_inputs.items()
        },
    }
    for field in ("model_input_spans", "loss_fn_input_spans"):
        spans = getattr(datum, field)
        result[field] = None if spans is None else [asdict(span) for span in spans]
    return result


async def optimize_data(data, training_client, learning_rate, output):
    write_once(output / "optimization_attempt.json", {
        "phase": "before_first_training_request", "datums": len(data),
        "loss_fn": "importance_sampling", "learning_rate": learning_rate,
    })
    metrics = {}
    started = time.monotonic()
    logprobs = await train_step(
        data, training_client, learning_rate, num_substeps=1,
        loss_fn="importance_sampling", metrics=metrics,
    )
    write_json(output / "optimization_acknowledgement.json", {
        "forward_backward_and_optim_step_awaited": True,
        "returned_logprob_sequences": len(logprobs),
        "optimizer_metrics": metrics,
        "seconds": time.monotonic() - started,
    })
    return metrics


async def resume_optimization(args):
    """Repair a pre-optimizer failure using all original real trajectories and logprobs."""
    from tinker_cookbook import hyperparam_utils

    original = read_json(args.resume_from / "training.json")
    if args.mode != "train" or original["mode"] != "train" or not os.getenv("TINKER_API_KEY"):
        raise ValueError("Resume requires train mode and a locally loaded Tinker key")
    if original["optimization_steps_completed"] or list(args.resume_from.glob("**/optimization_acknowledgement.json")):
        raise ValueError("Optimizer may already have run; refusing a duplicate update")
    if list(args.resume_from.glob("**/optimization_attempt.json")):
        raise ValueError("A training request may have been issued; reconcile it before recovery")
    if original["model"] != args.model:
        raise ValueError("Resume must use the original model")
    if args.steps != 1 or original["requested_steps"] != 1:
        raise ValueError("Recovery supports one pending update only")
    if args.rank != original.get("rank", args.rank):
        raise ValueError("Resume must use the original LoRA rank")
    task = Task.load(args.public, args.hidden)
    if task.spec.get("provenance", {}) != original["provenance"]:
        raise ValueError("Task provenance changed")
    reservation = original["budget_reservation"]
    if args.resume_from.resolve() != Path(reservation["output"]).resolve():
        raise ValueError("A copied run cannot reuse the original budget reservation")
    if Path(reservation["ledger"]).resolve() != args.budget_ledger.resolve():
        raise ValueError("Resume must use the existing budget reservation")
    ledger = read_json(args.budget_ledger)
    if ledger["limit_usd"] != args.max_cost_usd or not any(
        entry["output"] == reservation["output"] and entry["reserved_usd"] == reservation["reserved_usd"]
        for entry in ledger["reservations"]
    ):
        raise ValueError("Original approved reservation is missing")
    batch = original["batches"][-1]
    batch_source = args.resume_from / f"batch_{batch['batch']}"
    if (batch_source / "training_data.json").exists() or original.get("error") != "'Datum' object has no attribute 'model_dump'":
        raise ValueError("Recovery requires a proven pre-optimizer serialization failure")
    payload = read_json(batch_source / "trajectories.json")
    trajectories = []
    for index, entry in enumerate(payload):
        grade = read_json(batch_source / f"episode_{index}/grade.json")
        if not grade["valid"] or grade["reward"] != batch["rewards"][index]:
            raise ValueError("Saved execution grade does not agree with the group")
        transitions = [Transition(
            tinker.ModelInput.model_validate(item["observation"]),
            TokensWithLogprobs(item["action_tokens"], item["logprobs"]),
            item["reward"], False, item["metrics"],
        ) for item in entry["transitions"]]
        final_ob = tinker.ModelInput.model_validate(entry["final_ob"]) if "final_ob" in entry else tinker.ModelInput.from_ints([])
        # Older records omitted final_ob; it is not used by assemble_training_data.
        trajectories.append(Trajectory(transitions, final_ob, entry["stop_reason"]))
    group = TrajectoryGroup(trajectories, [0.0] * len(trajectories), [{}] * len(trajectories))
    if group.get_total_rewards() != batch["rewards"]:
        raise ValueError("Trajectory rewards changed")
    advantages = compute_advantages([group])
    if not bool((advantages[0] != 0).any()):
        raise ValueError("Saved group has no learning signal")
    data, metadata = assemble_training_data([group], advantages)
    args.output.mkdir(parents=True, exist_ok=False)
    record = {**original, "status": "resuming", "optimization_steps_completed": 0,
              "resumed_from": str(args.resume_from.resolve()), "resampled": False,
              "training_client": "fresh initial LoRA; original sampling logprobs retained for importance sampling"}
    record["original_failure"] = record.pop("error", None)
    write_json(args.output / "training_data.json", {"datums": [serialize_datum(d) for d in data], "metadata": metadata})
    write_json(args.output / "training.json", record)
    claim_resume(args.resume_from, args.output)
    try:
        service = tinker.ServiceClient()
        client = await service.create_lora_training_client_async(base_model=args.model, rank=args.rank)
        metrics = await optimize_data(data, client, args.learning_rate or hyperparam_utils.get_lr(args.model), args.output)
        record.update(optimization_steps_completed=1, status="optimization_acknowledged", optimizer_metrics=metrics)
        write_json(args.output / "training.json", record)
        await client.save_weights_and_get_sampling_client_async()
        saved = await (await client.save_state_async("elt-final", ttl_seconds=86400)).result_async()
        record.update(status="optimization_completed", checkpoint=saved.path)
    except Exception as error:
        record.update(status="failed", error=safe_error(error))
        raise
    finally:
        write_json(args.output / "training.json", record)
    print("optimization_completed; reused original execution-derived rewards and sampled logprobs")


def claim_resume(original: Path, output: Path):
    # An exclusive receipt prevents concurrent/repeated spending of the same reservation.
    write_once(original / "optimization_resume_claim.json", {"output": str(output.resolve())})


def write_once(path: Path, payload: dict):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream)


def main():
    args = arguments()
    try:
        asyncio.run(resume_optimization(args) if args.resume_from else run(args))
    except Exception as error:
        raise SystemExit(safe_error(error)) from None


if __name__ == "__main__":
    main()
