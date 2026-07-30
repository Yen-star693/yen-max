import json
import io

import discord
from discord.ext import commands

from config import TOKEN, PREFIX
from claude import ask_claude

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)


def load_allowed():

    try:
        with open("allowed_users.json", "r") as f:
            return json.load(f)

    except:
        return []


def save_allowed(users):

    with open("allowed_users.json", "w") as f:
        json.dump(users, f, indent=4)


@bot.event
async def on_ready():

    print(f"{bot.user} is online.")


@bot.command()
@commands.has_permissions(administrator=True)
async def grant(ctx, member: discord.Member):

    users = load_allowed()

    if member.id not in users:
        users.append(member.id)
        save_allowed(users)

    await ctx.send(
        f"Granted Maximum Output access to {member.mention}."
    )


@bot.command()
async def build(ctx, *, prompt):

    users = load_allowed()

    if ctx.author.id not in users:
        return await ctx.send(
            "You don't have permission to use Yen Max."
        )

    status = await ctx.send(
        "**Developer Note**\n"
        "-# Reading request..."
    )

    response = ask_claude(prompt)

    await status.edit(
        content=response
    )


bot.run(TOKEN)