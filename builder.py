import discord
import io
import os
import time
import asyncio
import tempfile
import shutil
import zipfile
import random
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
    generate_wrapup_thought,
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
        """Add a regular (non-subtext) summary/result line."""
        self.lines.append((text, False))

    def add_thought(self, text: str) -> None:
        """
        Add a simulated 'inner thought' line - fictional UI flavor text
        written as if Yen is thinking through the task, rendered in
        italics via Discord's *text* markdown. This is NOT the model's
        actual reasoning, just narration generated for display.

        Empty/error text is silently skipped so a failed narration call
        never shows up as an empty *…* line or a raw error string.
        """
        if not text or text.startswith("API") or text.startswith("Failed"):
            return
        self.lines.append((f"*{text}*", False))

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
    build_id: str
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
        build_id = f"{random.randint(10000, 99999)}"
        result = BuildResult(build_id=build_id)
        tracker = ProgressTracker()

        # Real temp workspace on disk for this build - files are actually
        # written here as they're generated, and the whole directory is
        # removed in the finally block regardless of how the build ends.
        workspace_dir = tempfile.mkdtemp(prefix=f"build_{build_id}_")

        try:
            await ProjectBuilder._run_build(ctx, prompt, status_message, tracker, result, workspace_dir)
        finally:
            shutil.rmtree(workspace_dir, ignore_errors=True)

    @staticmethod
    async def _run_build(
        ctx,
        prompt: str,
        status_message: discord.Message,
        tracker: "ProgressTracker",
        result: "BuildResult",
        workspace_dir: str
    ) -> None:
        """
        The actual build workflow, separated from build_project so the
        temp workspace cleanup in the caller reliably runs via `finally`
        even if something here raises.
        """
        tracker.add_progress(f"Build #{result.build_id} starting...")
        tracker.complete_last(f"Build #{result.build_id} started.")
        await status_message.edit(content=tracker.render())

        # ---- Simulated inner thoughts: sizing up the request (italic) ----
        observations = await asyncio.to_thread(generate_observations, prompt)
        for obs in observations:
            tracker.add_thought(obs)
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
        tracker.add_thought(structure_note)
        tracker.add_summary("")
        await status_message.edit(content=tracker.render())

        # ---- Generate + validate each file, tracking real outcomes ----
        # Each file is written to the real temp workspace and uploaded
        # to Discord the moment it's done, rather than all at the end.
        total_bytes = 0

        for filename in filenames:
            file_result = await ProjectBuilder._generate_and_validate_file(
                status_message, tracker, prompt, filename
            )
            result.files.append(file_result)

            if file_result.content:
                total_bytes += len(file_result.content.encode("utf-8"))

                # Write the real file to the real workspace on disk
                try:
                    file_path = os.path.join(workspace_dir, filename)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(file_result.content)
                except OSError:
                    pass  # workspace write is best-effort, upload still happens below

                # Upload this file immediately, right after it's ready
                await ProjectBuilder._upload_single_file(ctx, file_result)

            # Enforce total-size limit as we go, not after the fact
            if total_bytes > MAX_TOTAL_BUILD_BYTES:
                tracker.add_summary(
                    f"Build stopped: total output exceeded "
                    f"{MAX_TOTAL_BUILD_BYTES // 1000}KB limit."
                )
                await status_message.edit(content=tracker.render())
                break

        # ---- Honest readiness summary + optional ZIP ----
        await ProjectBuilder._finalize_build(ctx, status_message, tracker, result, workspace_dir)

    @staticmethod
    async def _upload_single_file(ctx, file_result: "FileResult") -> None:
        """
        Upload one generated file as its own Discord message, immediately
        after it's ready, rather than batching all files into one message
        at the very end. Files that still failed validation are labeled
        in the message text so they're never mistaken for clean output.

        Args:
            ctx: Discord context
            file_result: The real result for this specific file
        """
        if not file_result.content:
            return

        try:
            discord_file = discord.File(
                io.BytesIO(file_result.content.encode("utf-8")),
                filename=file_result.filename
            )

            if file_result.passed_validation:
                await ctx.send(f"Generated `{file_result.filename}`", file=discord_file)
            else:
                await ctx.send(
                    f"Generated `{file_result.filename}` (needs review - "
                    f"unresolved syntax issue)",
                    file=discord_file
                )
        except discord.HTTPException:
            pass  # A failed individual upload doesn't abort the rest of the build

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
            tracker.complete_last(f"Couldn't generate {filename}.")
            await status_message.edit(content=tracker.render())
            return FileResult(filename, None, False, False, final_error="generation failed")

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
        tracker.add_thought(file_obs)
        await status_message.edit(content=tracker.render())

        if not can_validate_syntax(filename):
            return FileResult(filename, content, passed_validation=True, was_checked=False)

        tracker.add_progress("Checking generated code...")
        await status_message.edit(content=tracker.render())

        is_valid, error_message, line_number = validate_file(filename, content)

        if is_valid:
            tracker.complete_last("Code check passed.")

            check_note = await asyncio.to_thread(generate_check_note, filename, True)
            tracker.add_thought(check_note)

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
        tracker.add_thought(check_note)
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
    async def _finalize_build(
        ctx,
        status_message: discord.Message,
        tracker: ProgressTracker,
        result: BuildResult,
        workspace_dir: str
    ) -> None:
        """
        Produce an honest end-of-run summary and, if more than one file
        was generated, bundle the real workspace directory into a ZIP
        as a convenience alongside the individual files already sent.

        Individual files were already uploaded one-by-one as they were
        generated (see _upload_single_file), so this only handles the
        optional ZIP and the final summary text.

        Args:
            ctx: Discord context
            status_message: Message to edit
            tracker: Progress tracker
            result: Aggregate build result
            workspace_dir: Real temp directory holding the generated files
        """
        generated = [f for f in result.files if f.content is not None]

        if not generated:
            tracker.add_summary("No files were generated. Build failed.")
            await status_message.edit(content=tracker.render())
            return

        all_passed = all(f.passed_validation for f in generated)
        wrapup = await asyncio.to_thread(
            generate_wrapup_thought, [f.filename for f in generated], all_passed
        )
        tracker.add_thought(wrapup)
        await status_message.edit(content=tracker.render())

        # ---- Optional ZIP of the whole project, only worth it for 2+ files ----
        if len(generated) > 1:
            tracker.add_progress("Packaging project as a zip...")
            await status_message.edit(content=tracker.render())

            zip_path = os.path.join(workspace_dir, f"build_{result.build_id}.zip")
            try:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in generated:
                        file_path = os.path.join(workspace_dir, f.filename)
                        if os.path.exists(file_path):
                            zf.write(file_path, arcname=f.filename)

                with open(zip_path, "rb") as zf:
                    zip_bytes = zf.read()

                zip_file = discord.File(
                    io.BytesIO(zip_bytes),
                    filename=f"build_{result.build_id}.zip"
                )
                await asyncio.wait_for(
                    ctx.send(f"Full project as a zip:", file=zip_file),
                    timeout=TIMEOUT_UPLOAD
                )
                tracker.complete_last("Zip packaged and sent.")

            except (OSError, asyncio.TimeoutError, discord.HTTPException) as e:
                tracker.complete_last(f"Couldn't package the zip: {type(e).__name__}.")

            await status_message.edit(content=tracker.render())

        # ---- Honest summary, always shown, never skipped ----
        passing = result.passing
        failing = result.failing

        tracker.add_summary("")
        tracker.add_summary(f"Build #{result.build_id}")
        tracker.add_summary("")
        tracker.add_summary(f"Files: {len(generated)}")
        tracker.add_summary(f"Generated: {len(generated)}")
        regen_count = sum(1 for f in generated if f.regeneration_attempted)
        tracker.add_summary(f"Regenerated: {regen_count}")
        tracker.add_summary(f"Passing validation: {len(passing)}")
        tracker.add_summary(f"With errors: {len(failing)}")
        tracker.add_summary(f"Build time: {result.duration_seconds}s")

        if failing:
            names = ", ".join(f.filename for f in failing)
            tracker.add_summary(f"Requires manual correction: {names}")

        await status_message.edit(content=tracker.render())


class GeneralResponder:
    """Handles general (non-project) requests, triggered by `yen ask`."""

    # Typewriter tuning
    _MIN_EDIT_INTERVAL = 0.35      # never edit more often than this, seconds
    _CHARS_PER_BATCH_MIN = 8
    _CHARS_PER_BATCH_MAX = 40
    _MAX_ANIMATION_SECONDS = 8     # if reveal would take longer than this, skip to full text
    _TARGET_EDITS = 18             # batch size is chosen to land near this many edits

    @staticmethod
    async def respond(
        ctx,
        prompt: str,
        status_message: discord.Message
    ) -> None:
        """
        Answer a general question with a buffered typewriter reveal.

        The full response is generated first (one real API call), then
        revealed via a small, bounded number of batched message edits -
        never one edit per character. Batch size scales with response
        length so longer answers don't multiply the edit count, and if
        the estimated animation time is too long the effect is skipped
        entirely in favor of showing the final text immediately.

        Args:
            ctx: Discord context
            prompt: User's question
            status_message: Message to edit with the response
        """
        from config import MAX_MESSAGE_LENGTH

        response = await asyncio.to_thread(ask_general, prompt)

        if not response or response.startswith("API") or response.startswith("Failed"):
            await status_message.edit(content="Couldn't get a response, try again.")
            return

        if len(response) > MAX_MESSAGE_LENGTH:
            # Too long for a single message - no point animating, send as file
            file = discord.File(
                io.BytesIO(response.encode('utf-8')),
                filename="response.txt"
            )
            await status_message.edit(content="That answer was long, sending it as a file:")
            await ctx.send(file=file)
            return

        await GeneralResponder._typewriter_reveal(status_message, response)

    @staticmethod
    async def _typewriter_reveal(status_message: discord.Message, full_text: str) -> None:
        """
        Reveal full_text via batched, rate-limit-safe message edits.

        Batch size is scaled so the total edit count stays roughly
        constant regardless of response length, and a minimum interval
        between edits prevents hammering Discord. If a 429 is hit, the
        animation is abandoned in favor of immediately showing the
        final text - no aggressive retry loop.

        Args:
            status_message: The message to progressively edit
            full_text: The complete response to reveal
        """
        text_length = len(full_text)

        if text_length == 0:
            await status_message.edit(content=full_text)
            return

        # Scale batch size so total edits land near _TARGET_EDITS
        batch_size = max(
            GeneralResponder._CHARS_PER_BATCH_MIN,
            min(
                GeneralResponder._CHARS_PER_BATCH_MAX,
                text_length // GeneralResponder._TARGET_EDITS or GeneralResponder._CHARS_PER_BATCH_MIN
            )
        )

        estimated_edits = text_length / batch_size
        estimated_seconds = estimated_edits * GeneralResponder._MIN_EDIT_INTERVAL

        if estimated_seconds > GeneralResponder._MAX_ANIMATION_SECONDS:
            # Would take too long to animate readably - just show it
            await status_message.edit(content=full_text)
            return

        revealed = ""
        last_edit_time = 0.0

        for i in range(0, text_length, batch_size):
            revealed = full_text[:i + batch_size]

            elapsed_since_last = time.time() - last_edit_time
            if elapsed_since_last < GeneralResponder._MIN_EDIT_INTERVAL:
                await asyncio.sleep(GeneralResponder._MIN_EDIT_INTERVAL - elapsed_since_last)

            try:
                await status_message.edit(content=revealed)
                last_edit_time = time.time()
            except discord.HTTPException as e:
                if e.status == 429:
                    # Rate limited - stop animating, don't retry aggressively
                    break
                # Any other HTTP issue - also just stop animating gracefully
                break

        # Always land on the complete, correct text regardless of how
        # the animation loop above ended
        try:
            await status_message.edit(content=full_text)
        except discord.HTTPException:
            pass
