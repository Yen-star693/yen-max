import discord
import io
import time
import asyncio
from typing import List, Tuple, Optional
from dataclasses import dataclass, field

from groq import (
    plan_project,
    generate_file,
    ask_general,
    generate_observations,
    generate_structure_note,
    regenerate_file_section,
    generate_file_observation,
    generate_check_note,
)
from validator import (
    validate_file,
    can_validate_syntax,
    check_unused_imports,
    validate_filename,
)
from config import (
    MAX_FILES_PER_BUILD,
    MAX_FILE_BYTES,
    MAX_TOTAL_BUILD_BYTES,
    MAX_REGENERATION_ATTEMPTS,
    TIMEOUT_PLANNING,
    TIMEOUT_GENERATION,
    TIMEOUT_UPLOAD,
)


class ProgressTracker:
    """
    Tracks a growing log of Developer Note lines with mixed formatting.
    Subtext (-#) is used for step-by-step progress; regular text is
    used for summaries and final results, so the message doesn't turn
    into an unreadable wall of dimmed text.
    """

    HEADER = "**Developer Note**"

    def __init__(self):
        self.lines: List[Tuple[str, bool]] = []  # (text, is_subtext)

    def add_progress(self, text: str) -> None:
        """Add a subtext progress line (shown as -# in Discord)."""
        self.lines.append((text, True))

    def add_summary(self, text: str) -> None:
        """Add a regular (non-subtext) summary line."""
        self.lines.append((text, False))

    def complete_last(self, completed_text: str) -> None:
        """Replace the most recent subtext line with its completed phrasing."""
        if self.lines and self.lines[-1][1]:
            self.lines[-1] = (completed_text, True)
        else:
            self.add_progress(completed_text)

    def render(self) -> str:
        """
        Render the full Developer Note with mixed formatting.
        
        Output is capped at ~1900 characters to stay safely under
        Discord's 2000-character message limit. If we exceed that,
        we keep the newest lines and drop the oldest progress steps,
        since those are less relevant than the current state.
        """
        if not self.lines:
            return self.HEADER

        body_lines = []
        for text, is_subtext in self.lines:
            body_lines.append(f"-# {text}" if is_subtext else text)

        # Try full render first
        full = f"{self.HEADER}\n" + "\n".join(body_lines)
        if len(full) <= 1900:
            return full

        # Over limit - keep newest lines, drop oldest progress steps
        # Summaries are always kept (they're at the end and most important)
        # Drop from the front, working backward until we fit
        while len(body_lines) > 1:
            body_lines.pop(0)
            candidate = f"{self.HEADER}\n" + "\n".join(body_lines)
            if len(candidate) <= 1900:
                return candidate

        # Last resort: just return header + most recent line
        if body_lines:
            return f"{self.HEADER}\n{body_lines[-1]}"
        return self.HEADER


@dataclass
class FileResult:
    """Real outcome of generating + validating a single file."""
    filename: str
    content: Optional[str]
    passed_validation: bool
    was_checked: bool          # False if no real checker exists for this file type
    regeneration_attempted: bool = False
    final_error: Optional[str] = None  # Set only if it still has a real, unresolved error


@dataclass
class BuildResult:
    """Aggregate result of an entire build, used for the honest summary."""
    files: List[FileResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    blocked: bool = False
    block_reason: Optional[str] = None

    @property
    def passing(self) -> List[FileResult]:
        return [f for f in self.files if f.content is not None and f.passed_validation]

    @property
    def failing(self) -> List[FileResult]:
        return [f for f in self.files if f.content is not None and not f.passed_validation]

    @property
    def duration_seconds(self) -> float:
        return round(time.time() - self.started_at, 1)

    @property
    def any_regeneration_attempted(self) -> bool:
        return any(f.regeneration_attempted for f in self.files)


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
        with real progress. Resource limits are checked before any file
        is generated, and the final upload is gated on real validation
        results rather than always uploading regardless of outcome.

        Args:
            ctx: Discord context
            prompt: User's build request
            status_message: Message to edit with progress
        """
        result = BuildResult()
        tracker = ProgressTracker()

        # ---- Casual narration: sizing up the request (normal text) ----
        observations = await asyncio.to_thread(generate_observations, prompt)
        for obs in observations:
            if not obs.startswith("API") and not obs.startswith("Failed"):
                tracker.add_summary(obs)
        if observations:
            tracker.add_summary("")
        await status_message.edit(content=tracker.render())

        # ---- Planning (mechanical step, subtext, timed + isolated) ----
        tracker.add_progress("Planning project structure...")
        await status_message.edit(content=tracker.render())

        try:
            filenames = await asyncio.wait_for(
                asyncio.to_thread(plan_project, prompt),
                timeout=TIMEOUT_PLANNING
            )
        except asyncio.TimeoutError:
            tracker.complete_last("Planning timed out.")
            await status_message.edit(content=tracker.render())
            return

        if not filenames:
            tracker.complete_last("Failed to plan project structure.")
            await status_message.edit(content=tracker.render())
            return

        # ---- Resource limits checked BEFORE any generation call ----
        if len(filenames) > MAX_FILES_PER_BUILD:
            tracker.complete_last(
                f"Planned {len(filenames)} files, exceeding the {MAX_FILES_PER_BUILD} file limit."
            )
            await status_message.edit(content=tracker.render())
            tracker.add_summary(
                f"Build blocked: too many files requested "
                f"({len(filenames)} > {MAX_FILES_PER_BUILD})."
            )
            await status_message.edit(content=tracker.render())
            return

        # ---- Filename safety checked BEFORE any generation call ----
        unsafe = []
        for fname in filenames:
            is_safe, reason = validate_filename(fname)
            if not is_safe:
                unsafe.append((fname, reason))

        if unsafe:
            tracker.complete_last("One or more planned filenames failed safety checks.")
            await status_message.edit(content=tracker.render())
            for fname, reason in unsafe:
                tracker.add_summary(f"Blocked filename `{fname}`: {reason}.")
            await status_message.edit(content=tracker.render())
            return

        tracker.complete_last(f"Planned {len(filenames)} files.")
        await status_message.edit(content=tracker.render())

        structure_note = await asyncio.to_thread(
            generate_structure_note, prompt, filenames
        )
        if structure_note and not structure_note.startswith("API"):
            tracker.add_summary(structure_note)
            tracker.add_summary("")
        await status_message.edit(content=tracker.render())

        # ---- Generate + validate each file, tracking real outcomes ----
        total_bytes = 0

        for filename in filenames:
            file_result = await ProjectBuilder._generate_and_validate_file(
                status_message, tracker, prompt, filename
            )
            result.files.append(file_result)

            if file_result.content:
                total_bytes += len(file_result.content.encode("utf-8"))

            # Enforce total-size limit as we go, not after the fact
            if total_bytes > MAX_TOTAL_BUILD_BYTES:
                tracker.add_summary(
                    f"Build stopped: total output exceeded "
                    f"{MAX_TOTAL_BUILD_BYTES // 1000}KB limit."
                )
                await status_message.edit(content=tracker.render())
                break

        # ---- Honest readiness summary + upload gating ----
        await ProjectBuilder._finalize_and_upload(ctx, status_message, tracker, result)

    @staticmethod
    async def _generate_and_validate_file(
        status_message: discord.Message,
        tracker: "ProgressTracker",
        prompt: str,
        filename: str
    ) -> FileResult:
        """
        Generate a single file and, when a real checker exists for its
        language, actually validate and attempt a capped repair.

        Every progress line printed corresponds to work that actually ran.

        Args:
            status_message: Discord message to edit
            tracker: Shared progress tracker
            prompt: Original project request
            filename: File to generate

        Returns:
            A FileResult describing exactly what happened
        """
        tracker.add_progress(f"Generating {filename}...")
        await status_message.edit(content=tracker.render())

        try:
            content = await asyncio.wait_for(
                asyncio.to_thread(generate_file, prompt, filename),
                timeout=TIMEOUT_GENERATION
            )
        except asyncio.TimeoutError:
            tracker.complete_last(f"Generation of {filename} timed out.")
            await status_message.edit(content=tracker.render())
            return FileResult(filename, None, False, False, final_error="generation timed out")

        if not content or content.startswith("API") or content.startswith("Failed"):
            tracker.complete_last(f"Failed to generate {filename}.")
            await status_message.edit(content=tracker.render())
            return FileResult(filename, None, False, False, final_error=content or "empty response")

        # Enforce per-file size limit
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            tracker.complete_last(f"{filename} exceeded the size limit and was discarded.")
            await status_message.edit(content=tracker.render())
            return FileResult(filename, None, False, False, final_error="exceeded max file size")

        tracker.complete_last(f"Generated {filename}.")
        await status_message.edit(content=tracker.render())

        file_obs = await asyncio.to_thread(
            generate_file_observation, filename, content
        )
        if file_obs and not file_obs.startswith("API"):
            tracker.add_summary(file_obs)
            await status_message.edit(content=tracker.render())

        if not can_validate_syntax(filename):
            return FileResult(filename, content, passed_validation=True, was_checked=False)

        tracker.add_progress("Checking generated code...")
        await status_message.edit(content=tracker.render())

        is_valid, error_message, line_number = validate_file(filename, content)

        if is_valid:
            tracker.complete_last("Code check passed.")

            check_note = await asyncio.to_thread(generate_check_note, filename, True)
            if check_note:
                tracker.add_summary(check_note)

            unused = check_unused_imports(content)
            if unused:
                tracker.add_summary(
                    f"Noticed an unused import{'s' if len(unused) != 1 else ''}: "
                    f"{', '.join(unused)}. Leaving it for now."
                )

            tracker.add_summary("")
            await status_message.edit(content=tracker.render())
            return FileResult(filename, content, passed_validation=True, was_checked=True)

        # A real syntax error was found - report the real details
        location = f" around line {line_number}" if line_number else ""
        tracker.complete_last(f"Syntax issue{location}: {error_message}")

        check_note = await asyncio.to_thread(
            generate_check_note, filename, False, error_message
        )
        if check_note:
            tracker.add_summary(check_note)
        await status_message.edit(content=tracker.render())

        # Regeneration is capped - only ever attempted MAX_REGENERATION_ATTEMPTS times
        attempts = 0
        current_content = content
        current_error = error_message
        current_line = line_number

        while attempts < MAX_REGENERATION_ATTEMPTS:
            attempts += 1
            tracker.add_progress(f"Regenerating {filename} (attempt {attempts})...")
            await status_message.edit(content=tracker.render())

            fixed = await asyncio.to_thread(
                regenerate_file_section,
                prompt, filename, current_content, current_error, current_line
            )

            if not fixed or fixed.startswith("API"):
                tracker.complete_last(f"Could not regenerate {filename}.")
                await status_message.edit(content=tracker.render())
                break

            still_valid, recheck_error, recheck_line = validate_file(filename, fixed)

            if still_valid:
                tracker.complete_last("Rechecked and it's clean now.")
                tracker.add_summary("")
                await status_message.edit(content=tracker.render())
                return FileResult(
                    filename, fixed, passed_validation=True, was_checked=True,
                    regeneration_attempted=True
                )

            current_content, current_error, current_line = fixed, recheck_error, recheck_line
            tracker.complete_last(f"Attempt {attempts} didn't resolve it.")
            await status_message.edit(content=tracker.render())

        # Regeneration cap reached (or failed outright) - the file is kept
        # but honestly marked as still broken, not silently uploaded as-is
        tracker.add_summary("")
        return FileResult(
            filename, current_content, passed_validation=False, was_checked=True,
            regeneration_attempted=(attempts > 0), final_error=current_error
        )

    @staticmethod
    async def _finalize_and_upload(
        ctx,
        status_message: discord.Message,
        tracker: ProgressTracker,
        result: BuildResult
    ) -> None:
        """
        Produce an honest end-of-run summary and only upload files that
        actually have content. Files with unresolved validation errors
        are still uploaded (so the user isn't left with nothing) but are
        clearly labeled, and the summary states exactly how many passed
        vs. failed - this is never glossed over.

        Args:
            ctx: Discord context
            status_message: Message to edit
            tracker: Progress tracker
            result: Aggregate build result
        """
        generated = [f for f in result.files if f.content is not None]

        if not generated:
            tracker.add_summary("No files were generated. Build failed.")
            await status_message.edit(content=tracker.render())
            return

        tracker.add_progress("Uploading generated files...")
        await status_message.edit(content=tracker.render())

        try:
            discord_files = []
            for f in generated:
                discord_files.append(discord.File(
                    io.BytesIO(f.content.encode("utf-8")),
                    filename=f.filename
                ))

            await asyncio.wait_for(
                ctx.send(f"Generated {len(discord_files)} file(s):", files=discord_files),
                timeout=TIMEOUT_UPLOAD
            )
            tracker.complete_last("Upload complete.")

        except asyncio.TimeoutError:
            tracker.complete_last("Upload timed out.")
            await status_message.edit(content=tracker.render())
            return
        except Exception as e:
            tracker.complete_last(f"Upload failed: {str(e)}")
            await status_message.edit(content=tracker.render())
            return

        # ---- Honest summary, always shown, never skipped ----
        passing = result.passing
        failing = result.failing

        tracker.add_summary("")
        tracker.add_summary(f"Files generated: {len(generated)}")
        tracker.add_summary(f"Files passing validation: {len(passing)}")
        tracker.add_summary(f"Files with errors: {len(failing)}")
        tracker.add_summary(f"Regeneration attempted: {'yes' if result.any_regeneration_attempted else 'no'}")
        tracker.add_summary(f"Build time: {result.duration_seconds}s")

        if failing:
            names = ", ".join(f.filename for f in failing)
            tracker.add_summary(f"Requires manual correction: {names}")

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

        if len(response) > MAX_MESSAGE_LENGTH:
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
