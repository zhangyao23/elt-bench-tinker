"""Actual Tinker rollout/GRPO step. No mock service or local optimization path exists."""

import argparse
import asyncio
import importlib.metadata
import os
import time
from pathlib import Path

import tinker
from tinker_cookbook import hyperparam_utils, model_info, tokenizer_utils
from tinker_cookbook.completers import TinkerTokenCompleter
from tinker_cookbook.renderers import get_renderer
from tinker_cookbook.rl.data_processing import assemble_training_data, compute_advantages
from tinker_cookbook.rl.rollouts import do_group_rollout
from tinker_cookbook.rl.train import train_step

from elt_tinker.cookbook_env import ELTGroupBuilder
from elt_tinker.environment import safe_error
from elt_tinker.errors import InfrastructureError
from elt_tinker.task import Task, read_json, write_json


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
        "--max-cost-usd",
        type=float,
        required=True,
        help="Operator-approved token budget; conservative preflight, not a billing-service hard cap",
    )
    return parser.parse_args()


async def run(args):
    if not os.getenv("TINKER_API_KEY"):
        raise ValueError(
            "TINKER_API_KEY is absent. Provide it locally, never in a transcript/repository."
        )
    if not 1 <= args.steps <= args.max_batches <= 3 or not 1 <= args.group_size <= 8:
        raise ValueError("Require 1 <= steps <= max-batches and 1 <= group-size <= 8")
    if args.mode == "train" and args.group_size < 2:
        raise ValueError("Centered GRPO requires at least two independent rollouts")
    if not 1 <= args.max_turns <= 32 or not 64 <= args.generation_tokens <= 4096:
        raise ValueError("Require 1..32 turns and 64..4096 generation tokens per turn")
    if not 2048 <= args.context_tokens <= 65536 or args.max_cost_usd <= 0:
        raise ValueError("Require 2048..65536 context tokens and a positive approved budget")
    task = Task.load(args.public, args.hidden)
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
    if worst_cost > args.max_cost_usd:
        raise ValueError(
            f"Conservative token cost ${worst_cost:.2f} exceeds approved ${args.max_cost_usd:.2f}; reduce batches/turns/group/context"
        )
    args.output.mkdir(parents=True, exist_ok=False)
    operator = read_json(args.operator_config) if args.operator_config else {}
    record = {
        "mode": args.mode,
        "model": args.model,
        "destination": args.destination,
        "official": task.spec.get("official", False),
        "provenance": task.spec.get("provenance", {}),
        "group_size": args.group_size,
        "requested_steps": args.steps,
        "max_batches": args.max_batches,
        "optimization_steps_completed": 0,
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
        rate_card=rates,
        budget_excludes="warehouse, storage, taxes; rate-card changes require a new run",
    )
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
        renderer = get_renderer(
            args.renderer or model_info.get_recommended_renderer_name(args.model), tokenizer
        )
        if args.mode == "train":
            training_client = await service.create_lora_training_client_async(
                base_model=args.model, rank=args.rank
            )
            sampling_client = await training_client.save_weights_and_get_sampling_client_async()
        else:
            training_client = None
            sampling_client = service.create_sampling_client(base_model=args.model)
        learning_rate = args.learning_rate or hyperparam_utils.get_lr(args.model)
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
            )
            policy = TinkerTokenCompleter(
                sampling_client,
                max_tokens=args.generation_tokens,
                context_window=args.context_tokens,
            )
            sample_start = time.monotonic()
            try:
                group = await do_group_rollout(builder, policy)
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
            write_json(
                batch_output / "trajectories.json",
                [
                    {
                        "stop_reason": trajectory.stop_reason,
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
                if not grade or not grade["valid"] or not grade.get("all_models_match"):
                    raise RuntimeError(
                        "Official rollout executed but task was not fully solved; retain failure evidence"
                    )
                record["status"] = "rollout_completed"
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
                {"datums": [datum.model_dump(mode="json") for datum in data], "metadata": metadata},
            )
            # Cookbook masks prompts/tool observations, uses sampled logprobs and
            # pipelines real forward_backward + optim_step; returned futures are awaited.
            metrics = {}
            update_start = time.monotonic()
            logprobs = await train_step(
                data,
                training_client,
                learning_rate,
                num_substeps=1,
                loss_fn="importance_sampling",
                metrics=metrics,
            )
            write_json(
                batch_output / "optimization_acknowledgement.json",
                {
                    "forward_backward_and_optim_step_awaited": True,
                    "returned_logprob_sequences": len(logprobs),
                    "optimizer_metrics": metrics,
                    "seconds": time.monotonic() - update_start,
                },
            )
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
            saved = await (await training_client.save_state_async("elt-final")).result_async()
            record["checkpoint"] = saved.path
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
        return float(rates[key].lstrip("$"))

    # Worst case even if renderer breaks prefix sharing: one context-length datum per turn.
    total = turns * args.context_tokens * price("prefill")
    total += turns * args.generation_tokens * price("sample")
    if args.mode == "train":
        total += turns * args.context_tokens * price("train")
    return total / 1_000_000


def main():
    args = arguments()
    try:
        asyncio.run(run(args))
    except Exception as error:
        raise SystemExit(safe_error(error)) from None


if __name__ == "__main__":
    main()
