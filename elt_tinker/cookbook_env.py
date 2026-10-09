"""Thin bridge to cookbook's native tools, multi-turn renderer and EnvGroupBuilder."""

import asyncio
import json
from pathlib import Path

from tinker_cookbook.renderers.base import Renderer
from tinker_cookbook.rl.rollout_strategy import RolloutResult, RolloutStrategy
from tinker_cookbook.rl.rollouts import do_single_rollout
from tinker_cookbook.rl.types import EnvGroupBuilder
from tinker_cookbook.tool_use import build_agent_tool_env
from tinker_cookbook.tool_use.agent_tool_message_env import AgentToolMessageEnv
from tinker_cookbook.tool_use.tools import simple_tool_result, tool

from elt_tinker.environment import Episode, safe_error
from elt_tinker.errors import InfrastructureError
from elt_tinker.feedback import compact_tool_feedback
from elt_tinker.task import Task, write_json


async def complete_sync(function, *arguments):
    """Finish an already-issued synchronous DB operation before cancellation cleans it up."""
    work = asyncio.create_task(asyncio.to_thread(function, *arguments))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await asyncio.gather(work, return_exceptions=True)
        raise


class CompleteGroup(RolloutStrategy):
    """No retries: drain all issued rollouts before propagating a group failure."""

    async def execute(self, env_group_builder, policy):
        envs = await env_group_builder.make_envs()
        results = await asyncio.gather(
            *(do_single_rollout(policy, env) for env in envs), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return RolloutResult(trajectories=results, envs=envs, errors=[])


class ELTTools:
    def __init__(self, episode: Episode, compact_feedback: bool = False):
        self.episode = episode
        self.compact_feedback = compact_feedback
        self.message_env: AgentToolMessageEnv | None = None
        self.lock = asyncio.Lock()

    async def _call(self, name: str, arguments: dict):
        async with self.lock:
            if self.episode.done:
                return simple_tool_result("Episode ended", should_stop=True)
            feedback = await complete_sync(self.episode.act, name, arguments)
            if self.compact_feedback:
                feedback = compact_tool_feedback(feedback)
                if self.message_env is not None:
                    # Fixed cookbook revision: _turn_count increments before tool dispatch.
                    remaining = max(0, self.message_env.max_turns - self.message_env._turn_count)
                    feedback["state"] = {
                        **feedback.get("state", {}),
                        "model_turns_remaining": remaining,
                    }
                    if remaining <= 2 and not self.episode.done:
                        feedback["next_action_hint"] = "Submit your best existing pipeline before the model-turn limit; submission must be explicit."
            return simple_tool_result(
                json.dumps(
                    feedback, default=str,
                    separators=(",", ":") if self.compact_feedback else None,
                ),
                should_stop=self.episode.done,
            )

    @tool
    async def inspect_task(self):
        """Read the task specification, source inventory, column schemas and requested models."""
        return await self._call("inspect_task", {})

    @tool
    async def configure_load(self, table: str, source_kind: str, sync_mode: str = "full_refresh"):
        """Configure extraction/loading for one source. Use its declared kind and full_refresh."""
        return await self._call(
            "configure_load", {"table": table, "source_kind": source_kind, "sync_mode": sync_mode}
        )

    @tool
    async def run_load(self, tables: list[str]):
        """Extract and load named configured tables; [] loads every configured table."""
        return await self._call("run_load", {"tables": tables})

    @tool
    async def query(self, sql: str):
        """Inspect with SELECT; check rows_truncated and prefer focused or aggregate queries."""
        return await self._call("query", {"sql": sql})

    @tool
    async def transform(self, sql: str):
        """Run one CREATE OR REPLACE TABLE/VIEW models.<model> AS SELECT; read raw.<source>."""
        return await self._call("transform", {"sql": sql})

    @tool
    async def submit_pipeline(self):
        """Submit configured loads and successful transformation SQL for fresh replay and grading."""
        return await self._call("submit_pipeline", {})

    def all(self):
        return [
            self.inspect_task,
            self.configure_load,
            self.run_load,
            self.query,
            self.transform,
            self.submit_pipeline,
        ]


def build_elt_env(
    episode: Episode,
    renderer: Renderer,
    max_turns: int = 24,
    context_tokens: int = 32768,
    generation_tokens: int = 2048,
    *,
    compact_feedback: bool = False,
):
    toolbox = ELTTools(episode, compact_feedback=compact_feedback)
    tools = toolbox.all()
    messages = renderer.create_conversation_prefix_with_tools(
        tools=[tool.to_spec() for tool in tools],
        system_prompt=episode.initial_prompt(),
    ) + [
        {
            "role": "user",
            "content": "Inspect the task, implement the full ELT pipeline, and submit it.",
        }
    ]
    if compact_feedback:
        messages[-1]["content"] += (
            f" Budget: {max_turns} model turns, {context_tokens} context tokens; "
            "tool-call and model-turn limits differ. Query feedback previews at most six rows; "
            "use WHERE/aggregates for statistics. Review requested time ranges, entities, "
            "denominators, ties and NULLs before submission."
        )

    async def reward_fn(history):
        grade = episode.grade or await complete_sync(episode.evaluate)
        write_json(episode.output / "messages.json", history)
        if not grade["valid"]:
            # Let sibling trajectories finish before group validation/cleanup. This
            # is SDK bookkeeping only; compute_group_rewards excludes the group.
            return 0.0, {"elt/invalid": 1.0}
        return grade["reward"], {
            "elt/reward": grade["reward"],
            "elt/submitted": int(episode.submitted),
            "elt/all_models_match": int(grade.get("all_models_match", False)),
            "elt/grading_seconds": grade["seconds"],
        }

    environment = build_agent_tool_env(
        renderer=renderer,
        tools=tools,
        initial_messages=messages,
        reward_fn=reward_fn,
        max_turns=max_turns,
        max_tool_calls=episode.max_calls,
        max_trajectory_tokens=context_tokens,
        # The actual completer still caps generation and clips it to remaining context.
        # Reserve only a short submission turn instead of prematurely stopping at 4096.
        max_generation_tokens=min(128, generation_tokens) if compact_feedback else generation_tokens,
        failed_parse_reward=0.0,
        context_overflow_reward=0.0,
    )
    # Cookbook defaults to parallel tool execution. Loads/transformations share state.
    environment.message_env.tool_execution = "sequential"
    toolbox.message_env = environment.message_env
    return environment


class ELTGroupBuilder(EnvGroupBuilder):
    def __init__(
        self,
        task: Task,
        destination: str,
        output: Path,
        renderer: Renderer,
        group_size: int,
        operator_config: dict,
        max_turns: int = 24,
        context_tokens: int = 32768,
        generation_tokens: int = 2048,
        *,
        field_feedback: bool = False,
        compact_feedback: bool = False,
    ):
        self.task, self.destination, self.output, self.renderer = (
            task,
            destination,
            output,
            renderer,
        )
        self.group_size, self.operator_config = group_size, operator_config
        self.field_feedback = field_feedback
        self.compact_feedback = compact_feedback
        self.max_turns, self.context_tokens, self.generation_tokens = (
            max_turns,
            context_tokens,
            generation_tokens,
        )
        self.episodes: list[Episode] = []

    async def make_envs(self):
        environments = []
        for index in range(self.group_size):
            episode = await complete_sync(self._create_episode, index)
            environments.append(
                build_elt_env(
                    episode,
                    self.renderer,
                    self.max_turns,
                    self.context_tokens,
                    self.generation_tokens,
                    compact_feedback=self.compact_feedback,
                )
            )
        return environments

    def _create_episode(self, index):
        episode = Episode(
            self.task,
            self.destination,
            self.output / f"episode_{index}",
            self.operator_config,
            field_feedback=self.field_feedback,
        )
        # Track it in the same sync operation, even if the awaiting caller is cancelled.
        self.episodes.append(episode)
        return episode

    async def cleanup(self):
        failures = []
        for episode in self.episodes:
            try:
                await complete_sync(episode.close)
            except Exception as error:
                failures.append({"episode": str(episode.output), "error": safe_error(error)})
        if failures:
            write_json(self.output / "cleanup_errors.json", failures)
            raise InfrastructureError("Warehouse cleanup failed; see cleanup_errors.json")

    async def compute_group_rewards(self, trajectory_group, env_group):
        for episode, environment in zip(self.episodes, env_group, strict=True):
            write_json(episode.output / "messages.json", environment.message_env.history)
            if episode.grade is None:
                episode.grade = await complete_sync(episode.evaluate)
        if any(episode.invalid_reason for episode in self.episodes):
            raise InfrastructureError("An episode was invalid; excluding the entire group")
        return [(0.0, {}) for _ in trajectory_group]

    def logging_tags(self):
        return ["elt", self.task.spec["id"], self.destination]
