import discord
import io
from typing import List, Tuple
from groq import plan_project, generate_file, ask_general, generate_dev_note
from analyzer import RequestAnalyzer

# Discord edits are rate-limited; don't hammer the API on every tiny update
MIN_EDIT_INTERVAL_SECONDS = 0.0  # placeholder if throttling is added later


class ProgressTracker:
    """
    Tracks a growing log of Developer Note lines and renders them
    as Discord subtext (using the "-#" prefix Discord renders as
    small/dim text).

    Lines are appended, never removed. Earlier lines can be marked
    complete (swapped from an in-progress phrasing to a completed one).
    """

    HEADER = "**Developer Note**"

    def __init__(self):
        self.lines: List[str] = []

    def add_line(self, text: str) -> None:
        """Append a new in-progress status line."""
        self.lines.append(text)

    def complete_last(self, completed_text: str) -> None:
        """Replace the most recent line with its completed phrasing."""
        if self.lines:
            self.lines[-1] = completed_text
        else:
            self.lines.append(completed_text)

    def replace_last(self, text: str) -> None:
        """Replace the most recent line without marking it complete."""
        if self.lines:
            self.lines[-1] = text
        else:
            self.lines.append(text)

    def render(self) -> str:
        """Render the full Developer Note block as Discord subtext."""
        body = "\n".join(f"-# {line}" for line in self.lines)
        return f"{self.HEADER}\n{body}" if body else self.HEADER


class ProjectBuilder:
    """Orchestrates the multi-file project building process."""

    @staticmethod
    async def build_project(
        ctx,
        prompt: str,
        status_message: discord.Message
    ) -> None:
        """
        Build a multi-file project, editing status_message continuously
        with real progress as each stage actually completes.

        Args:
            ctx: Discord context
            prompt: User's build request
            status_message: Message to edit with progress
        """
        tracker = ProgressTracker()

        # ---- Reading request ----
        tracker.add_line("Reading request...")
        await status_message.edit(content=tracker.render())

        tracker.complete_last("Reading request.")
        await status_message.edit(content=tracker.render())

        # ---- Understanding project (dev note) ----
        tracker.add_line("Understanding project requirements...")
        await status_message.edit(content=tracker.render())

        understanding_note = generate_dev_note(prompt, stage="understanding")
        completed_understanding = (
            understanding_note if understanding_note
            else "Understood project requirements."
        )
        tracker.complete_last(completed_understanding)
        await status_message.edit(content=tracker.render())

        # ---- Planning project structure ----
        tracker.add_line("Planning project structure...")
        await status_message.edit(content=tracker.render())

        filenames = plan_project(prompt)

        if not filenames:
            tracker.complete_last("Failed to plan project structure.")
            await status_message.edit(content=tracker.render())
            return

        planning_note = generate_dev_note(
            prompt,
            stage="planning",
            context=", ".join(filenames)
        )
        completed_planning = (
            planning_note if planning_note
            else f"Planned project structure ({len(filenames)} files)."
        )
        tracker.complete_last(completed_planning)
        await status_message.edit(content=tracker.render())

        # ---- Generate files one at a time, real progress ----
        files = []

        for filename in filenames:
            tracker.add_line(f"Generating {filename}...")
            await status_message.edit(content=tracker.render())

            content = generate_file(prompt, filename)

            if content and not content.startswith("API") and not content.startswith("Failed"):
                files.append((filename, content))
                tracker.complete_last(f"Generated {filename}.")
            else:
                tracker.complete_last(f"Failed to generate {filename}.")

            await status_message.edit(content=tracker.render())

        # ---- Upload ----
        tracker.add_line("Uploading project...")
        await status_message.edit(content=tracker.render())

        await ProjectBuilder._upload_files(
            ctx,
            status_message,
            tracker,
            files
        )

    @staticmethod
    async def _upload_files(
        ctx,
        status_message: discord.Message,
        tracker: ProgressTracker,
        files: List[Tuple[str, str]]
    ) -> None:
        """
        Upload generated files to Discord as separate attachments.

        Args:
            ctx: Discord context
            status_message: Message to edit
            tracker: Progress tracker
            files: List of (filename, content) tuples
        """
        if not files:
            tracker.complete_last("No files were generated.")
            await status_message.edit(content=tracker.render())
            return

        try:
            discord_files = []

            for filename, content in files:
                file_bytes = content.encode('utf-8')
                discord_file = discord.File(
                    io.BytesIO(file_bytes),
                    filename=filename
                )
                discord_files.append(discord_file)

            await ctx.send(
                f"Generated {len(files)} file(s):",
                files=discord_files
            )

            tracker.complete_last("Uploaded project.")
            await status_message.edit(content=tracker.render())

        except Exception as e:
            tracker.complete_last(f"Failed to upload files: {str(e)}")
            await status_message.edit(content=tracker.render())


class GeneralResponder:
    """Handles general (non-project) requests."""

    @staticmethod
    async def respond(
        ctx,
        prompt: str,
        status_message: discord.Message
    ) -> None:
        """
        Respond to a general question.
        
        Args:
            ctx: Discord context
            prompt: User's question
            status_message: Message to edit with response
        """
        from config import MAX_MESSAGE_LENGTH

        response = ask_general(prompt)

        if not response or response.startswith("API"):
            await status_message.edit(content="Failed to generate response.")
            return

        # Handle long responses
        if len(response) > MAX_MESSAGE_LENGTH:
            # Split and send as file
            file = discord.File(
                io.BytesIO(response.encode('utf-8')),
                filename="response.txt"
            )
            await status_message.edit(
                content="Response was too long, sending as file:"
            )
            await ctx.send(file=file)
        else:
            await status_message.edit(content=response)

