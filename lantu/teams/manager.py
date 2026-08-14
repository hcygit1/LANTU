from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from lantu.teams.backend_detect import BackendDetectionError, detect_backend
from lantu.teams.mailbox import Mailbox, create_message
from lantu.teams.models import (
    AgentTeam,
    BackendType,
    TeammateInfo,
    resolve_team_dir,
    unique_team_name,
)
from lantu.teams.progress import TeammateProgress
from lantu.teams.registry import AgentNameRegistry
from lantu.teams.shared_task import SharedTaskStore
from lantu.teams.spawn_inprocess import InProcessTeammateHandle

if TYPE_CHECKING:
    from lantu.agent import Agent

log = logging.getLogger(__name__)


class TeamError(Exception):
    pass


class TeamManager:
    def __init__(self, worktree_manager: Any = None, trace_manager: Any = None) -> None:
        self._teams: dict[str, AgentTeam] = {}
        self._task_stores: dict[str, SharedTaskStore] = {}
        self._mailboxes: dict[str, Mailbox] = {}
        self._inprocess_handles: dict[str, InProcessTeammateHandle] = {}
        self._pane_ids: dict[str, str] = {}  # agent_id -> pane_id (tmux/iterm2)
        self._detected_backend: BackendType | None = None
        self._worktree_manager = worktree_manager
        self._trace_manager = trace_manager
        self._teammate_team_map: dict[str, str] = {}  # agent_id -> team_name

    def detect_backend(
        self,
        teammate_mode: str = "",
        is_interactive: bool = True,
    ) -> BackendType:
        if self._detected_backend is None:
            self._detected_backend = detect_backend(teammate_mode, is_interactive)
        return self._detected_backend


    def create_team(
        self,
        name: str,
        lead_agent_id: str,
        description: str = "",
        teammate_mode: str = "",
        is_interactive: bool = True,
    ) -> AgentTeam:
        backend = self.detect_backend(teammate_mode, is_interactive)
        slug = unique_team_name(name)
        team_dir = resolve_team_dir(slug)
        team_dir.mkdir(parents=True, exist_ok=True)

        config_path = str(team_dir / "config.json")
        team = AgentTeam(
            name=slug,
            lead_agent_id=lead_agent_id,
            config_path=config_path,
            description=description,
        )
        team.save()

        task_store = SharedTaskStore(team_dir / "tasks.json")
        task_store.init_empty()

        mailbox_dir = team_dir / "mailbox"
        mailbox_dir.mkdir(parents=True, exist_ok=True)
        mailbox = Mailbox(mailbox_dir)

        self._teams[slug] = team
        self._task_stores[slug] = task_store
        self._mailboxes[slug] = mailbox

        log.info("Created team '%s' at %s (backend=%s)", slug, team_dir, backend.value)
        return team


    def get_team(self, name: str) -> AgentTeam | None:
        if name in self._teams:
            return self._teams[name]
        team_dir = resolve_team_dir(name)
        config_path = team_dir / "config.json"
        if config_path.exists():
            team = AgentTeam.load(str(config_path))
            self._teams[name] = team
            return team
        return None

    def get_task_store(self, team_name: str) -> SharedTaskStore | None:
        if team_name in self._task_stores:
            return self._task_stores[team_name]
        team_dir = resolve_team_dir(team_name)
        tasks_path = team_dir / "tasks.json"
        if tasks_path.exists():
            store = SharedTaskStore(tasks_path)
            self._task_stores[team_name] = store
            return store
        return None

    def get_mailbox(self, team_name: str) -> Mailbox | None:
        if team_name in self._mailboxes:
            return self._mailboxes[team_name]
        team_dir = resolve_team_dir(team_name)
        mailbox_dir = team_dir / "mailbox"
        if mailbox_dir.exists():
            mailbox = Mailbox(mailbox_dir)
            self._mailboxes[team_name] = mailbox
            return mailbox
        return None

    def register_member(
        self,
        team_name: str,
        member: TeammateInfo,
    ) -> None:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"Team '{team_name}' not found")
        team.add_member(member)
        team.save()

        AgentNameRegistry.instance().register(member.name, member.agent_id)
        self._teammate_team_map[member.agent_id] = team_name
        log.info("Registered member '%s' (agent=%s) in team '%s'", member.name, member.agent_id, team_name)

    def set_member_idle(self, team_name: str, member_name: str) -> None:
        team = self.get_team(team_name)
        if team is None:
            return
        team.set_member_active(member_name, False)
        team.save()

        mailbox = self.get_mailbox(team_name)
        if mailbox:
            msg = create_message(
                from_agent=member_name,
                to_agent=team.lead_agent_id,
                content=f"Teammate '{member_name}' is now idle (run_to_completion finished).",
                summary=f"{member_name} idle",
                message_type="text",
            )
            mailbox.write(team.lead_agent_id, msg)

    def register_inprocess_handle(self, agent_id: str, handle: InProcessTeammateHandle) -> None:
        self._inprocess_handles[agent_id] = handle

    def register_pane_id(self, agent_id: str, pane_id: str) -> None:
        self._pane_ids[agent_id] = pane_id


    def get_pane_id(self, agent_id: str) -> str | None:
        return self._pane_ids.get(agent_id)

    def delete_team(
        self,
        team_name: str,
        deadline: float | None = None,
        timeout: float = 10.0,
    ) -> None:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"Team '{team_name}' not found")

        active = [m for m in team.members if m.is_active is not False]
        if active:
            names = ", ".join(m.name for m in active)
            raise TeamError(f"Cannot delete team: active members: {names}")

        for member in list(team.members):
            AgentNameRegistry.instance().unregister(member.name)

            handle = self._inprocess_handles.pop(member.agent_id, None)
            if handle and not handle.done:
                handle.cancel()

            pane_id = self._pane_ids.pop(member.agent_id, None)
            remaining = self._remaining_timeout(deadline, timeout)
            if pane_id and remaining > 0:
                self._kill_pane(
                    pane_id, member.backend_type, timeout=remaining
                )

            remaining = self._remaining_timeout(deadline, timeout)
            if member.worktree_path and remaining > 0:
                self._cleanup_worktree(
                    member.worktree_path, deadline=deadline, timeout=remaining
                )

            if self._trace_manager:
                self._trace_manager.remove(member.agent_id)

        remaining = self._remaining_timeout(deadline, timeout)
        if remaining > 0:
            mailbox = self.get_mailbox(team_name)
            if mailbox:
                mailbox.cleanup_all(deadline=deadline)

        remaining = self._remaining_timeout(deadline, timeout)
        if remaining > 0:
            team_dir = resolve_team_dir(team_name)
            self._remove_dir(team_dir, deadline=deadline)
        if self._remaining_timeout(deadline, timeout) <= 0:
            log.warning("Team '%s' external cleanup skipped: deadline exhausted", team_name)

        self._teams.pop(team_name, None)
        self._task_stores.pop(team_name, None)
        self._mailboxes.pop(team_name, None)

        log.info("Deleted team '%s'", team_name)

    async def delete_team_bounded(
        self,
        team_name: str,
        deadline: float,
        timeout: float = 10.0,
    ) -> None:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"Team '{team_name}' not found")

        mailbox = self._mailboxes.get(team_name)
        team_dir = resolve_team_dir(team_name)
        for member in list(team.members):
            team.set_member_active(member.name, False)
            AgentNameRegistry.instance().unregister(member.name)

            handle = self._inprocess_handles.pop(member.agent_id, None)
            if handle and not handle.done:
                handle.cancel()

            pane_id = self._pane_ids.pop(member.agent_id, None)
            remaining = self._remaining_timeout(deadline, timeout)
            if pane_id and remaining > 0:
                await self._run_daemon_cleanup(
                    lambda pane_id=pane_id,
                    backend_type=member.backend_type,
                    cleanup_timeout=remaining: self._kill_pane(
                        pane_id,
                        backend_type,
                        timeout=cleanup_timeout,
                    ),
                    remaining,
                    f"pane cleanup for teammate '{member.name}'",
                )

            remaining = self._remaining_timeout(deadline, timeout)
            if member.worktree_path and remaining > 0:
                await self._run_daemon_cleanup(
                    lambda worktree_path=member.worktree_path,
                    cleanup_deadline=deadline,
                    cleanup_timeout=remaining: self._cleanup_worktree(
                        worktree_path,
                        deadline=cleanup_deadline,
                        timeout=cleanup_timeout,
                    ),
                    remaining,
                    f"worktree cleanup for teammate '{member.name}'",
                )

            self._teammate_team_map.pop(member.agent_id, None)
            if self._trace_manager:
                self._trace_manager.remove(member.agent_id)

        self._teams.pop(team_name, None)
        self._task_stores.pop(team_name, None)
        self._mailboxes.pop(team_name, None)

        remaining = self._remaining_timeout(deadline, timeout)
        if mailbox is not None and remaining > 0:
            await self._run_daemon_cleanup(
                lambda: mailbox.cleanup_all(deadline=deadline),
                remaining,
                f"mailbox cleanup for team '{team_name}'",
            )

        remaining = self._remaining_timeout(deadline, timeout)
        if remaining > 0:
            await self._run_daemon_cleanup(
                lambda: self._remove_dir(team_dir, deadline=deadline),
                remaining,
                f"directory cleanup for team '{team_name}'",
            )
        else:
            log.warning(
                "Team '%s' directory cleanup skipped: deadline exhausted",
                team_name,
            )

        log.info("Deleted team '%s'", team_name)

    async def _run_daemon_cleanup(
        self,
        operation: Callable[[], None],
        timeout: float,
        description: str,
    ) -> bool:
        if timeout <= 0:
            return False
        loop = asyncio.get_running_loop()
        completed: asyncio.Future[None] = loop.create_future()

        def report(error: BaseException | None) -> None:
            def finish() -> None:
                if completed.done():
                    return
                if error is None:
                    completed.set_result(None)
                else:
                    completed.set_exception(error)

            try:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(finish)
            except RuntimeError:
                pass

        def worker() -> None:
            try:
                operation()
            except BaseException as exc:
                report(exc)
            else:
                report(None)

        threading.Thread(
            target=worker,
            name=f"lantu-{description}",
            daemon=True,
        ).start()
        try:
            await asyncio.wait_for(asyncio.shield(completed), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            completed.cancel()
            log.warning("%s exceeded shutdown deadline", description)
            return False
        except asyncio.CancelledError:
            completed.cancel()
            raise
        except BaseException:
            log.warning("%s failed", description, exc_info=True)
            return False

    def list_teams(self) -> list[str]:
        return list(self._teams.keys())

    def get_team_for_teammate(self, agent_id: str) -> str | None:
        if agent_id in self._teammate_team_map:
            return self._teammate_team_map[agent_id]
        for name, team in self._teams.items():
            for m in team.members:
                if m.agent_id == agent_id:
                    return name
        return None


    def drain_lead_mailbox(self) -> list[str]:
        notes: list[str] = []
        for team_name in list(self._teams.keys()):
            team = self.get_team(team_name)
            if team is None:
                continue
            mailbox = self.get_mailbox(team_name)
            if mailbox is None:
                continue
            msgs = mailbox.consume(team.lead_agent_id)
            if not msgs:
                continue
            parts = [f'<team-notification team="{team_name}">']
            for m in msgs:
                parts.append(f"from={m.from_agent}: {m.content}")
            parts.append("</team-notification>")
            notes.append("\n".join(parts))
        return notes

    def has_lead_notifications(self) -> bool:
        for team_name in list(self._teams):
            team = self.get_team(team_name)
            mailbox = self.get_mailbox(team_name)
            if team is not None and mailbox is not None:
                if mailbox.read(team.lead_agent_id):
                    return True
        return False

    def get_all_teammate_progress(self) -> list[TeammateProgress]:
        """Collect progress objects attached to every registered teammate."""
        results: list[TeammateProgress] = []
        for team in self._teams.values():
            for member in team.members:
                if hasattr(member, "progress") and member.progress is not None:
                    results.append(member.progress)
        return results

    def on_teammate_completed(self, agent_id: str) -> None:
        team_name = self.get_team_for_teammate(agent_id)
        if team_name is None:
            return
        team = self.get_team(team_name)
        if team is None:
            return
        member = next((m for m in team.members if m.agent_id == agent_id), None)
        if member:
            self.set_member_idle(team_name, member.name)


    def _kill_pane(
        self, pane_id: str, backend_type: str, timeout: float = 10
    ) -> None:
        try:
            if backend_type == BackendType.TMUX.value:
                from lantu.teams.spawn_tmux import kill_pane
                kill_pane(pane_id, timeout=timeout)
        except Exception as e:
            log.warning("Failed to kill pane %s: %s", pane_id, e)

    @staticmethod
    def _remaining_timeout(deadline: float | None, timeout: float) -> float:
        if deadline is None:
            return timeout
        # Reserve a tiny margin so subprocess timeout never extends past the
        # caller's deadline due to monotonic-clock precision.
        safety_margin = 1e-6
        return max(0.0, min(timeout, deadline - time.monotonic() - safety_margin))

    def _cleanup_worktree(
        self,
        worktree_path: str,
        deadline: float | None = None,
        timeout: float = 10.0,
    ) -> None:
        import subprocess
        remaining = self._remaining_timeout(deadline, timeout)
        if remaining <= 0:
            log.warning("Worktree cleanup skipped for %s: deadline exhausted", worktree_path)
            return
        try:
            subprocess.run(
                ["git", "worktree", "remove", worktree_path, "--force"],
                capture_output=True, timeout=remaining,
            )
        except Exception as e:
            log.warning("git worktree remove failed for %s: %s", worktree_path, e)
            if deadline is not None:
                return
            import shutil
            try:
                if Path(worktree_path).exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
            except Exception:
                pass

    def _remove_dir(self, path: Path, deadline: float | None = None) -> None:
        import shutil
        try:
            if not path.exists():
                return
            if deadline is None:
                shutil.rmtree(path, ignore_errors=True)
                return
            for child in path.iterdir():
                if time.monotonic() >= deadline:
                    return
                if child.is_dir() and not child.is_symlink():
                    self._remove_dir(child, deadline=deadline)
                else:
                    child.unlink(missing_ok=True)
            if time.monotonic() < deadline:
                path.rmdir()
        except Exception as e:
            log.warning("Failed to remove directory %s: %s", path, e)
