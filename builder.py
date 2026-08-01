import discord
import io
from typing import List, Tuple
from groq import plan_project, generate_file, ask_general
from analyzer import RequestAnalyzer


class ProgressTracker:
    """Tracks and manages progress updates for a build operation."""

    def __init__(self):
        self.steps = []
        self.completed = []

    def add_step(self, step: str) -> None:
        """Add a step to track."""
        self.steps.append(step)

    def complete_step(self, step_index: int) -> None:
        """Mark a step as completed."""
        if step_index < len(self.steps):
            self.completed.append(self.steps[step_index])

    def get_message(self) -> str:
        """Get formatted progress message."""
        lines = ["**Developer Note**"]
        
        for i, step in enumerate(self.steps):
            if i < len(self.completed):
                lines.append(f"✓ {self.completed[i]}")
            else:
                lines.append(f"• {step}")
                break

        return "\n".join(lines)

    def is_complete(self) -> bool:
        """Check if all steps are done."""
        return len(self.completed) == len(self.steps)


class ProjectBuilder:
    """Orchestrates the multi-file project building process."""

    @staticmethod
    async def build_project(
        ctx,
        prompt: str,
        status_message: discord.Message
    ) -> None:
        """
        Build a multi-file project with progress tracking.
        
        Args:
            ctx: Discord context
            prompt: User's build request
            status_message: Message to edit with progress
        """
        tracker = ProgressTracker()
        tracker.add_step("Reading request...")
        tracker.add_step("Planning project structure...")
        tracker.add_step("Generating files...")
        tracker.add_step("Preparing uploads...")

        # Step 1: Read request
        await status_message.edit(content=tracker.get_message())
        tracker.complete_step(0)
        await status_message.edit(content=tracker.get_message())

        # Step 2: Plan project
        filenames = plan_project(prompt)
        
        if not filenames:
            await status_message.edit(
                content="Failed to plan project structure. Please try again."
            )
            return

        tracker.complete_step(1)
        await status_message.edit(content=tracker.get_message())

        # Step 3: Generate files
        files = []
        tracker.steps[2] = f"Generating {len(filenames)} files..."
        
        for i, filename in enumerate(filenames):
            # Update progress
            tracker.steps[2] = f"Generating files... ({i+1}/{len(filenames)})"
            await status_message.edit(content=tracker.get_message())

            # Generate file
            content = generate_file(prompt, filename)

            if content and not content.startswith("API"):
                files.append((filename, content))

        tracker.complete_step(2)
        await status_message.edit(content=tracker.get_message())

        # Step 4: Upload files
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
        Upload generated files to Discord.
        
        Args:
            ctx: Discord context
            status_message: Message to edit
            tracker: Progress tracker
            files: List of (filename, content) tuples
        """
        if not files:
            await status_message.edit(
                content="No files were generated. Please try again."
            )
            return

        try:
            discord_files = []

            for filename, content in files:
                # Convert to bytes
                file_bytes = content.encode('utf-8')
                
                # Create Discord file object
                discord_file = discord.File(
                    io.BytesIO(file_bytes),
                    filename=filename
                )
                discord_files.append(discord_file)

            tracker.complete_step(3)
            await status_message.edit(content=tracker.get_message())

            # Send files
            await ctx.send(
                f"Generated {len(files)} file(s):",
                files=discord_files
            )

            # Final message
            await status_message.edit(
                content=tracker.get_message() + "\n\n✅ Upload complete."
            )

        except Exception as e:
            await status_message.edit(
                content=f"Failed to upload files: {str(e)}"
            )


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
