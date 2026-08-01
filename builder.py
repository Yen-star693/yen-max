import discord
import io
from typing import List, Tuple, Optional
from groq import (
    plan_project,
    generate_file,
    ask_general,
    generate_observations,
    generate_structure_note,
    regenerate_file_section,
)
from validator import validate_file, can_validate_syntax, check_unused_imports
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

        # ---- Real observations about the request ----
        tracker.add_line("Analyzing requirements...")
        await status_message.edit(content=tracker.render())

        observations = generate_observations(prompt)

        if observations:
            tracker.complete_last("Requirements analyzed.")
            await status_message.edit(content=tracker.render())

            for obs in observations:
                tracker.add_line(obs)
            await status_message.edit(content=tracker.render())
        else:
            tracker.complete_last("Requirements analyzed.")
            await status_message.edit(content=tracker.render())

        # ---- Planning project structure ----
        tracker.add_line("Planning project structure...")
        await status_message.edit(content=tracker.render())

        filenames = plan_project(prompt)

        if not filenames:
            tracker.complete_last("Failed to plan project structure.")
            await status_message.edit(content=tracker.render())
            return

        structure_note = generate_structure_note(prompt, filenames)
        tracker.complete_last(structure_note)
        await status_message.edit(content=tracker.render())

        # ---- Generate files one at a time, with real validation ----
        files = []

        for filename in filenames:
            file_content = await ProjectBuilder._generate_and_validate_file(
                status_message, tracker, prompt, filename
            )

            if file_content is not None:
                files.append((filename, file_content))

        # ---- Upload ----
        tracker.add_line("Uploading generated files...")
        await status_message.edit(content=tracker.render())

        await ProjectBuilder._upload_files(
            ctx,
            status_message,
            tracker,
            files
        )

    @staticmethod
    async def _generate_and_validate_file(
        status_message: discord.Message,
        tracker: "ProgressTracker",
        prompt: str,
        filename: str
    ) -> Optional[str]:
        """
        Generate a single file and, when a real checker exists for its
        language, actually validate and (once) attempt a real repair.

        Every line printed here corresponds to work that actually ran:
        - "Checking generated code..." only appears for files we can
          actually parse (currently Python, via ast.parse)
        - "Detected a syntax issue..." only appears if ast.parse
          actually raised a SyntaxError, with the real message/line
        - Regeneration only happens, and is only reported, if it
          actually ran

        Args:
            status_message: Discord message to edit
            tracker: Shared progress tracker
            prompt: Original project request
            filename: File to generate

        Returns:
            Final file content, or None if generation failed entirely
        """
        tracker.add_line(f"Generating {filename}...")
        await status_message.edit(content=tracker.render())

        content = generate_file(prompt, filename)

        if not content or content.startswith("API") or content.startswith("Failed"):
            tracker.complete_last(f"Failed to generate {filename}.")
            await status_message.edit(content=tracker.render())
            return None

        tracker.complete_last(f"Generated {filename}.")
        await status_message.edit(content=tracker.render())

        # Only claim to check syntax where a real checker exists
        if not can_validate_syntax(filename):
            return content

        tracker.add_line("Checking generated code...")
        await status_message.edit(content=tracker.render())

        is_valid, error_message, line_number = validate_file(filename, content)

        if is_valid:
            tracker.complete_last("Code check passed.")
            await status_message.edit(content=tracker.render())

            unused = check_unused_imports(content)
            if unused:
                tracker.add_line(
                    f"Unused import{'s' if len(unused) != 1 else ''} detected: {', '.join(unused)}."
                )
                await status_message.edit(content=tracker.render())

            return content

        # A real syntax error was found - report the real details
        location = f" around line {line_number}" if line_number else ""
        tracker.complete_last(f"Detected a syntax issue{location}: {error_message}")
        await status_message.edit(content=tracker.render())

        tracker.add_line("Regenerating the affected file...")
        await status_message.edit(content=tracker.render())

        fixed_content = regenerate_file_section(
            prompt, filename, content, error_message, line_number
        )

        if not fixed_content or fixed_content.startswith("API"):
            tracker.complete_last(f"Could not regenerate {filename}.")
            await status_message.edit(content=tracker.render())
            return content  # fall back to original rather than losing the file

        # Recheck the regenerated version for real
        still_valid, recheck_error, recheck_line = validate_file(filename, fixed_content)

        if still_valid:
            tracker.complete_last("Syntax issue resolved.")
            await status_message.edit(content=tracker.render())
            return fixed_content

        # Regeneration didn't actually fix it - say so honestly
        tracker.complete_last(
            f"Regeneration did not resolve the issue: {recheck_error}"
        )
        await status_message.edit(content=tracker.render())
        return fixed_content

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
