"""Thin bridge to cookbook's native tools, multi-turn renderer and EnvGroupBuilder."""

import asyncio
import json
from pathlib import Path

from tinker_cookbook.renderers.base import Renderer
from tinker_cookbook.rl.types import EnvGroupBuilder
from tinker_cookbook.tool_use import build_agent_tool_env
from tinker_cookbook.tool_use.tools import simple_tool_result, tool

from elt_tinker.environment import Episode, safe_error
from elt_tinker.errors import InfrastructureError
from elt_tinker.task import Task, write_json


class ELTTools:
    def __init__(self, episode: Episode):
        self.episode = episode
        self.lock = asyncio.Lock()

    async def _call(self, name: str, arguments: dict):
        async with self.lock:
            if self.episode.done:
                return simple_tool_result("Episode ended", should_stop=True)
            feedback = await asyncio.to_thread(self.episode.act, name, arguments)
            return simple_tool_result(
                json.dumps(feedback, default=str), should_stop=self.episode.done
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
        """Inspect actual warehouse state with a SELECT; at most 30 rows and 12k characters."""
        return await self._call("query", {"sql": sql})

    @tool
    async def transform(self, sql: str):
        """Run one CREATE OR REPLACE TABLE/VIEW <model> AS SELECT; raw.<source>, models.<output>."""
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
):
    tools = ELTTools(episode).all()
    messages = renderer.create_conversation_prefix_with_tools(
        tools=[tool.to_spec() for tool in tools],
        system_prompt=episode.initial_prompt(),
    ) + [
        {
            "role": "user",
            "content": "Inspect the task, implement the full ELT pipeline, and submit it.",
        }
    ]

    async def reward_fn(history):
        grade = episode.grade or await asyncio.to_thread(episode.evaluate)
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
        max_generation_tokens=generation_tokens,
        failed_parse_reward=0.0,
        context_overflow_reward=0.0,
    )
    # Cookbook defaults to parallel tool execution. Loads/transformations share state.
    environment.message_env.tool_execution = "sequential"
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
    ):
        self.task, self.destination, self.output, self.renderer = (
            task,
            destination,
            output,
            renderer,
        )
        self.group_size, self.operator_config = group_size, operator_config
        self.max_turns, self.context_tokens, self.generation_tokens = (
            max_turns,
            context_tokens,
            generation_tokens,
        )
        self.episodes: list[Episode] = []

    async def make_envs(self):
        environments = []
        for index in range(self.group_size):
            episode = await asyncio.to_thread(
                Episode,
                self.task,
                self.destination,
                self.output / f"episode_{index}",
                self.operator_config,
            )
            self.episodes.append(episode)
            environments.append(
                build_elt_env(
                    episode,
                    self.renderer,
                    self.max_turns,
                    self.context_tokens,
                    self.generation_tokens,
                )
            )
        return environments

    async def cleanup(self):
        failures = []
        for episode in self.episodes:
            try:
                await asyncio.to_thread(episode.close)
            except Exception as error:
                failures.append({"episode": str(episode.output), "error": safe_error(error)})
        if failures:
            write_json(self.output / "cleanup_errors.json", failures)

    async def compute_group_rewards(self, trajectory_group, env_group):
        if any(episode.invalid_reason for episode in self.episodes):
            raise InfrastructureError("An episode was invalid; excluding the entire group")
        return [(0.0, {}) for _ in trajectory_group]

    def logging_tags(self):
        return ["elt", self.task.spec["id"], self.destination]
