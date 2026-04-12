"""Discord UI components for the Task Hub review/approval pipeline.

Provides:
  - ReviewActionView  — persistent button row (Approve / Reject / Revise / Later)
  - RejectFeedbackModal — popup capturing rejection reason
  - ReviseNotesModal   — popup capturing revision instructions

Buttons use custom_id patterns like ``review:{action}:{task_id}`` so they
survive bot restarts (discord.py re-links interactions to views registered
in setup_hook via ``bot.add_view(ReviewActionView())``).

See: https://discordpy.readthedocs.io/en/stable/interactions/api.html#persistent-views
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from ..integration import gateway_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_id_from_custom_id(custom_id: str) -> str:
    """Extract task_id from 'review:{action}:{task_id}'."""
    parts = custom_id.split(":", 2)
    return parts[2] if len(parts) >= 3 else ""


async def _update_embed_after_action(
    interaction: discord.Interaction,
    *,
    color: discord.Color,
    status_text: str,
) -> None:
    """Edit the original review embed to reflect the decision."""
    msg = interaction.message
    if not msg or not msg.embeds:
        return
    embed = msg.embeds[0].copy()
    embed.color = color
    embed.set_footer(text=f"{status_text} by {interaction.user.display_name}")
    # Disable all buttons after action
    view = discord.ui.View()
    await interaction.message.edit(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class RejectFeedbackModal(discord.ui.Modal, title="Rejection Feedback"):
    """Popup asking why Kevin is rejecting this task."""

    reason = discord.ui.TextInput(
        label="Why are you rejecting this?",
        style=discord.TextStyle.short,
        placeholder="e.g. irrelevant, too shallow, wrong angle",
        required=False,
        max_length=500,
    )

    def __init__(self, task_id: str) -> None:
        super().__init__()
        self.task_id = task_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        reason_text = str(self.reason.value or "no reason given").strip()
        try:
            await gateway_client.task_action(
                self.task_id,
                "park",
                reason=reason_text,
                agent_id="discord_kevin",
            )
            await interaction.response.send_message(
                f"Rejected and parked. Reason: *{reason_text}*",
                ephemeral=True,
            )
            await _update_embed_after_action(
                interaction,
                color=discord.Color.red(),
                status_text=f"Rejected: {reason_text}",
            )
        except Exception as exc:
            logger.error("Reject action failed for %s: %s", self.task_id, exc)
            await interaction.response.send_message(
                f"Failed to reject task: {exc}",
                ephemeral=True,
            )


class ReviseNotesModal(discord.ui.Modal, title="Revision Notes"):
    """Popup capturing what should be changed before re-running."""

    notes = discord.ui.TextInput(
        label="What should be revised?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what should change...",
        required=True,
        max_length=1000,
    )

    def __init__(self, task_id: str, original_title: str, original_description: str) -> None:
        super().__init__()
        self.task_id = task_id
        self.original_title = original_title
        self.original_description = original_description

    async def on_submit(self, interaction: discord.Interaction) -> None:
        revision_notes = str(self.notes.value or "").strip()
        try:
            # Park the original task
            await gateway_client.task_action(
                self.task_id,
                "park",
                reason=f"revision_requested: {revision_notes}",
                agent_id="discord_kevin",
            )
            # Create a new task with the revision context
            from ..integration.task_hub import create_task_hub_mission
            new_task_id = create_task_hub_mission(
                title=f"[Revision] {self.original_title}",
                description=(
                    f"Revision of task {self.task_id}.\n\n"
                    f"Original description:\n{self.original_description}\n\n"
                    f"Revision instructions from Kevin:\n{revision_notes}"
                ),
                tags=["revision", "discord-review"],
            )
            await interaction.response.send_message(
                f"Original task parked. New revision task created: `{new_task_id}`",
                ephemeral=True,
            )
            await _update_embed_after_action(
                interaction,
                color=discord.Color.gold(),
                status_text=f"Revision requested",
            )
        except Exception as exc:
            logger.error("Revise action failed for %s: %s", self.task_id, exc)
            await interaction.response.send_message(
                f"Failed to create revision: {exc}",
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# Persistent Button View
# ---------------------------------------------------------------------------

class ReviewActionView(discord.ui.View):
    """Persistent button view for task review decisions.

    Uses ``custom_id`` with encoded task_id so buttons survive bot restarts.
    Register once in ``setup_hook()`` via ``bot.add_view(ReviewActionView())``.
    """

    def __init__(self, task_id: str = "", task_data: dict[str, Any] | None = None) -> None:
        super().__init__(timeout=None)  # Persistent — never expires
        self.task_id = task_id
        self.task_data = task_data or {}

        # Only add buttons if we have a real task_id (fresh post).
        # For the persistent re-registration in setup_hook, we pass task_id=""
        # and discord.py matches by custom_id prefix pattern.
        if task_id:
            self._add_buttons(task_id)

    def _add_buttons(self, task_id: str) -> None:
        approve_btn = discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="Approve",
            emoji="\u2705",
            custom_id=f"review:approve:{task_id}",
        )
        approve_btn.callback = self._on_approve
        self.add_item(approve_btn)

        reject_btn = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Reject",
            emoji="\u274c",
            custom_id=f"review:reject:{task_id}",
        )
        reject_btn.callback = self._on_reject
        self.add_item(reject_btn)

        revise_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="Revise",
            emoji="\U0001f4dd",
            custom_id=f"review:revise:{task_id}",
        )
        revise_btn.callback = self._on_revise
        self.add_item(revise_btn)

        later_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Later",
            emoji="\u23f8\ufe0f",
            custom_id=f"review:later:{task_id}",
        )
        later_btn.callback = self._on_later
        self.add_item(later_btn)

    async def _resolve_task_id(self, interaction: discord.Interaction) -> str:
        """Get task_id from instance attr or parse from the button custom_id."""
        if self.task_id:
            return self.task_id
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        return _task_id_from_custom_id(custom_id)

    async def _on_approve(self, interaction: discord.Interaction) -> None:
        task_id = await self._resolve_task_id(interaction)
        if not task_id:
            await interaction.response.send_message("Could not determine task ID.", ephemeral=True)
            return
        try:
            await gateway_client.approve_task(task_id)
            await interaction.response.send_message(
                f"Task `{task_id[:12]}` approved and dispatched.",
                ephemeral=True,
            )
            await _update_embed_after_action(
                interaction,
                color=discord.Color.green(),
                status_text="Approved",
            )
        except Exception as exc:
            logger.error("Approve failed for %s: %s", task_id, exc)
            await interaction.response.send_message(f"Approve failed: {exc}", ephemeral=True)

    async def _on_reject(self, interaction: discord.Interaction) -> None:
        task_id = await self._resolve_task_id(interaction)
        if not task_id:
            await interaction.response.send_message("Could not determine task ID.", ephemeral=True)
            return
        modal = RejectFeedbackModal(task_id)
        await interaction.response.send_modal(modal)

    async def _on_revise(self, interaction: discord.Interaction) -> None:
        task_id = await self._resolve_task_id(interaction)
        if not task_id:
            await interaction.response.send_message("Could not determine task ID.", ephemeral=True)
            return
        title = self.task_data.get("title", "Unknown")
        description = self.task_data.get("description", "")
        modal = ReviseNotesModal(task_id, title, description)
        await interaction.response.send_modal(modal)

    async def _on_later(self, interaction: discord.Interaction) -> None:
        task_id = await self._resolve_task_id(interaction)
        if not task_id:
            await interaction.response.send_message("Could not determine task ID.", ephemeral=True)
            return
        try:
            await gateway_client.task_action(
                task_id,
                "snooze",
                reason="snoozed_via_discord",
                agent_id="discord_kevin",
            )
            await interaction.response.send_message(
                f"Task `{task_id[:12]}` snoozed — will reappear later.",
                ephemeral=True,
            )
            await _update_embed_after_action(
                interaction,
                color=discord.Color.light_grey(),
                status_text="Snoozed",
            )
        except Exception as exc:
            logger.error("Snooze failed for %s: %s", task_id, exc)
            await interaction.response.send_message(f"Snooze failed: {exc}", ephemeral=True)


# ---------------------------------------------------------------------------
# Embed Builder
# ---------------------------------------------------------------------------

def build_review_embed(task: dict[str, Any]) -> discord.Embed:
    """Build a digest card embed for a task awaiting review."""
    title = str(task.get("title") or "Untitled Task")[:256]
    description = str(task.get("description") or "No description")[:2000]
    status = str(task.get("status") or "unknown")
    priority = task.get("priority", 3)
    task_id = str(task.get("task_id") or "?")
    source = str(task.get("source_kind") or "unknown")

    # Color by priority
    if priority >= 4:
        color = discord.Color.red()
    elif priority >= 3:
        color = discord.Color.orange()
    else:
        color = discord.Color.blue()

    embed = discord.Embed(
        title=f"\U0001f4cb {title}",
        description=description,
        color=color,
    )
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Priority", value=str(priority), inline=True)
    embed.add_field(name="Source", value=source, inline=True)
    embed.set_footer(text=f"Task ID: {task_id}")
    return embed
