import json
import io

import discord
from discord.ext import commands

from config import TOKEN, PREFIX
from groq import ask_ai

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

    response = ask_ai(prompt)

    code_keywords = [
        "code",
        "python",
        "discord",
        "bot",
        "script",
        "program",
        "function",
        "class",
        "command",
        "main.py",
        "html",
        "css",
        "javascript",
        "java",
        "c++",
        "c#",
        "cpp",
        "sql",
        "php",
        "lua",
        "go",
        "rust"
    ]

    is_code_request = any(
        word in prompt.lower()
        for word in code_keywords
    )

    if is_code_request:

        file = discord.File(
            io.BytesIO(response.encode()),
            filename="generated_code.txt"
        )

        await status.edit(
            content="Finished.\nYour generated code is attached below.",
            attachments=[file]
        )

    elif len(response) < 1500 and response.count("\n") < 40:

        await status.edit(content=response)

    else:

        file = discord.File(
            io.BytesIO(response.encode()),
            filename="output.txt"
        )

        await status.edit(
            content="Finished.\nThe output was too large, so I've attached it as a file.",
            attachments=[file]
        )


bot.run(TOKEN)