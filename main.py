import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os

from config import TOKEN, PREFIX
from permissions import PermissionManager
from analyzer import RequestAnalyzer
from builder import ProjectBuilder, GeneralResponder

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


# ================= EVENTS =================
@bot.event
async def on_ready():
    """Bot startup event."""
    print(f" {bot.user} is online")


# ================= COMMANDS =================
@bot.command(name="grant")
@commands.has_permissions(administrator=True)
async def grant_access(ctx, member: discord.Member):
    """
    Grant Yen Max access to a user.
    
    Usage: yen grant @user
    """
    if perm_manager.grant(member.id):
        await ctx.send(
            f" Granted Yen Max access to {member.mention}"
        )
    else:
        await ctx.send(
            f" {member.mention} already has access"
        )


@bot.command(name="revoke")
@commands.has_permissions(administrator=True)
async def revoke_access(ctx, member: discord.Member):
    """
    Revoke Yen Max access from a user.
    
    Usage: yen revoke @user
    """
    if perm_manager.revoke(member.id):
        await ctx.send(
            f" Revoked Yen Max access from {member.mention}"
        )
    else:
        await ctx.send(
            f" {member.mention} doesn't have access"
        )


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
    # ================= PERMISSION CHECK =================
    if not perm_manager.is_allowed(ctx.author.id):
        await ctx.send(
            " You don't have permission to use Yen Max.\n"
            "Ask a server admin to grant you access."
        )
        return

    # ================= ANALYZE REQUEST =================
    is_project = RequestAnalyzer.should_use_project_planner(prompt)

    # ================= SEND INITIAL STATUS =================
    status_message = await ctx.send(
        "**Developer Note**\n"
        "-# Reading request..."
    )

    try:
        if is_project:
            # ================= PROJECT WORKFLOW =================
            await ProjectBuilder.build_project(ctx, prompt, status_message)
        else:
            # ================= GENERAL WORKFLOW =================
            await GeneralResponder.respond(ctx, prompt, status_message)

    except Exception as e:
        await status_message.edit(
            content=f" An error occurred: {str(e)}"
        )
        print(f"Error in build command: {e}")


# ================= RUN BOT =================
if __name__ == "__main__":
    bot.run(TOKEN)
