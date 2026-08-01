import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os
import time

from config import TOKEN, PREFIX, BUILD_COOLDOWN_SECONDS
from permissions import PermissionManager
from analyzer import RequestAnalyzer
from builder import ProjectBuilder, GeneralResponder
from groq import plan_project, detect_language_and_framework

# ================= FLASK KEEP-ALIVE =================
app = Flask(__name__)


@app.route("/")
def home():
    return "Yen Max is online!"


def run_web():
    """Run Flask app for keep-alive."""
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


Thread(target=run_web, daemon=True).start()

# ================= DISCORD BOT SETUP =================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)

# ================= PERMISSION MANAGER =================
perm_manager = PermissionManager()

# ================= BUILD STATE TRACKING =================
# Tracks last build time per user for cooldowns, and which users
# currently have an active build running so a second `yen build`
# can't be started while the first is still in progress.
_last_build_time: dict = {}
_active_builds: set = set()

# Last successful build's file list per user, so `yen retry <filename>`
# knows what was actually generated without re-running the planner.
_last_build_files: dict = {}


def _check_cooldown(user_id: int) -> float:
    """
    Return seconds remaining on cooldown, or 0 if the user can build now.
    """
    last = _last_build_time.get(user_id, 0)
    elapsed = time.time() - last
    remaining = BUILD_COOLDOWN_SECONDS - elapsed
    return max(0, remaining)


# ================= EVENTS =================
@bot.event
async def on_ready():
    """Bot startup event. Registers the first person to see this as owner if none is set."""
    print(f"{bot.user} is online")


# ================= COMMANDS =================
@bot.command(name="grant")
@commands.has_permissions(administrator=True)
async def grant_access(ctx, member: discord.Member):
    """
    Grant Yen Max access to a user, globally (all servers).

    Usage: yen grant @user
    """
    if perm_manager.grant(member.id):
        await ctx.send(f"Granted Yen Max access to {member.mention}")
    else:
        await ctx.send(f"{member.mention} already has access")


@bot.command(name="grantserver")
@commands.has_permissions(administrator=True)
async def grant_server_access(ctx, member: discord.Member):
    """
    Grant Yen Max access to a user, scoped to this server only.

    Usage: yen grantserver @user
    """
    if not ctx.guild:
        await ctx.send("This command only works in a server.")
        return

    if perm_manager.grant_server(member.id, ctx.guild.id):
        await ctx.send(f"Granted {member.mention} access in this server.")
    else:
        await ctx.send(f"{member.mention} already has access in this server.")


@bot.command(name="revoke")
@commands.has_permissions(administrator=True)
async def revoke_access(ctx, member: discord.Member):
    """
    Revoke Yen Max global access from a user.

    Usage: yen revoke @user
    """
    if perm_manager.revoke(member.id):
        await ctx.send(f"Revoked Yen Max access from {member.mention}")
    else:
        await ctx.send(f"{member.mention} doesn't have global access")


@bot.command(name="setowner")
async def set_owner(ctx):
    """
    Register the first person to run this as the bot owner.
    Owner-only afterward - once an owner exists, this command
    silently refuses to change it (use direct file edit if needed).

    Usage: yen setowner
    """
    if perm_manager.data.get("owner_id") is not None:
        await ctx.send("An owner is already registered.")
        return

    perm_manager.set_owner(ctx.author.id)
    await ctx.send(f"{ctx.author.mention} is now the registered bot owner.")


@bot.command(name="preview")
async def preview(ctx, *, prompt: str):
    """
    Show the planned file tree for a request WITHOUT generating any
    code or making generation API calls. Lets you sanity-check what
    Yen Max is about to build before committing tokens to it.

    Usage: yen preview <description>
    """
    if not perm_manager.is_allowed(ctx.author.id, ctx.guild.id if ctx.guild else None):
        await ctx.send("You don't have permission to use Yen Max.")
        return

    if not RequestAnalyzer.should_use_project_planner(prompt):
        await ctx.send("This doesn't look like a multi-file project request - nothing to preview.")
        return

    status = await ctx.send("-# Planning project structure...")

    filenames = plan_project(prompt)

    if not filenames:
        await status.edit(content="Failed to plan project structure.")
        return

    language, framework = detect_language_and_framework(prompt)

    lines = ["**Project Preview**", ""]
    if language:
        lines.append(f"Language: {language}")
    else:
        lines.append("Language: not specified, Python will be used")
    if framework:
        lines.append(f"Framework: {framework}")

    lines.append("")
    lines.append("Planned files:")
    for f in filenames:
        lines.append(f"-# {f}")

    lines.append("")
    lines.append(f"Run `yen build {prompt}` to generate these files.")

    await status.edit(content="\n".join(lines))


@bot.command(name="build")
async def build(ctx, *, prompt: str):
    """
    Build a project or generate code.
    
    Usage: yen build <description>
    Examples:
        yen build make a discord bot
        yen build create a weather app
        yen build write a python calculator
    """
    guild_id = ctx.guild.id if ctx.guild else None

    # ================= PERMISSION CHECK =================
    if not perm_manager.is_allowed(ctx.author.id, guild_id):
        await ctx.send(
            "You don't have permission to use Yen Max.\n"
            "Ask a server admin to grant you access."
        )
        return

    # ================= COOLDOWN CHECK =================
    remaining = _check_cooldown(ctx.author.id)
    if remaining > 0:
        await ctx.send(f"Please wait {remaining:.0f}s before starting another build.")
        return

    # ================= CONCURRENT BUILD CHECK =================
    if ctx.author.id in _active_builds:
        await ctx.send("You already have a build in progress. Please wait for it to finish.")
        return

    _active_builds.add(ctx.author.id)
    _last_build_time[ctx.author.id] = time.time()

    try:
        is_project = RequestAnalyzer.should_use_project_planner(prompt)

        status_message = await ctx.send(
            "**Developer Note**\n"
            "-# Reading request..."
        )

        if is_project:
            await ProjectBuilder.build_project(ctx, prompt, status_message)
        else:
            await GeneralResponder.respond(ctx, prompt, status_message)

    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {str(e)}")
        print(f"Error in build command: {e}")

    finally:
        # Always release the lock, even if something above raised
        _active_builds.discard(ctx.author.id)


# ================= RUN BOT =================
if __name__ == "__main__":
    bot.run(TOKEN)
