#!/usr/bin/env python3
"""AgentM — 本地 CLI 对话工具。

用法:
    agentm                     # 新会话（默认，省 Token）
    agentm -c                  # 继承当前目录的上次会话
    agentm --resume <uuid>      # 恢复指定 UUID 会话
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

API_URL = os.getenv("AGENTM_API", "http://127.0.0.1:3000/api/chat")
SESSION_FILE = ".agentm_session"

console = Console()

# prompt_toolkit session — 支持方向键光标移动、UTF-8 安全输入
_prompt_style = Style.from_dict({"prompt": "bold cyan"})
_session = PromptSession(style=_prompt_style)


# ═══════════════════════════════════════════════════════════════
# Session 管理
# ═══════════════════════════════════════════════════════════════


def new_session() -> str:
    sid = str(uuid.uuid4())
    Path(SESSION_FILE).write_text(sid)
    return sid


def load_session() -> str | None:
    path = Path(SESSION_FILE)
    if path.exists():
        sid = path.read_text().strip()
        if sid:
            return sid
    return None


# ═══════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════


async def send_message(session_id: str, query: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            API_URL,
            json={"query": query, "session_id": session_id},
        )
        resp.raise_for_status()
        return resp.json().get("answer", "")


async def fetch_history(session_id: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"http://127.0.0.1:3000/api/chat/history/{session_id}"
            )
            resp.raise_for_status()
            return resp.json().get("messages", [])
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# REPL
# ═══════════════════════════════════════════════════════════════


async def repl(session_id: str, is_resume: bool = False) -> None:
    # ── 欢迎界面 ──
    welcome_text = f"""
[bold cyan]AgentM[/bold cyan] — 本地 AI 对话助手

Session: {session_id}
Commands: /exit /new /clear
""".strip()
    console.print(Panel(welcome_text, border_style="cyan"))

    # ── 加载历史 ──
    if is_resume:
        history = await fetch_history(session_id)
        if history:
            console.print(Rule("History", style="dim"))
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "human":
                    console.print(Rule(f"[dim]You[/dim]", style="dim", align="left"))
                elif role == "ai":
                    console.print(Markdown(content))
            console.print(Rule("End of history", style="dim"))
        else:
            console.print("[dim](no history found)[/dim]")
    console.print()

    # ── 主循环 ──
    while True:
        console.print(Rule(style="cyan"))
        try:
            user_input = (await _session.prompt_async([("class:prompt", "❯ ")])).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\nGoodbye!")
            break
        console.print(Rule(style="cyan"))

        if not user_input:
            continue

        if user_input in ("/exit", "quit", "exit", "/quit"):
            console.print("[bright_black]Goodbye![/bright_black]")
            break

        if user_input == "/new":
            sid = new_session()
            console.print(f"[bright_black]New session: {sid}[/bright_black]")
            continue

        if user_input == "/clear":
            console.clear()
            continue

        try:
            with console.status("[bold cyan]Agent 思考中...[/bold cyan]", spinner="dots"):
                answer = await send_message(session_id, user_input)
        except httpx.ConnectError:
            console.print("[red]连接后端失败 — 127.0.0.1:3000 不可达[/red]")
            continue
        except Exception as e:
            console.print(f"[red]请求异常: {e}[/red]")
            continue

        console.print()
        console.print(Markdown(answer))
        console.print()


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════


async def main() -> None:
    p = argparse.ArgumentParser(description="AgentM CLI")
    group = p.add_mutually_exclusive_group()
    group.add_argument("-c", "--continue-session", action="store_true",
                       help="继承当前目录 .agentm_session 的上次会话")
    group.add_argument("--resume", default="", metavar="UUID",
                       help="恢复指定 UUID 会话")
    group.add_argument("--session-id", default="", metavar="UUID",
                       help=argparse.SUPPRESS)  # 向后兼容，隐藏
    args = p.parse_args()

    if args.continue_session:
        sid = load_session()
        if sid is None:
            console.print("[yellow]No .agentm_session found, creating new one.[/yellow]")
            sid = new_session()
        is_resume = True
    elif args.resume:
        sid = args.resume
        Path(SESSION_FILE).write_text(sid)
        is_resume = True
    elif args.session_id:
        sid = args.session_id
        Path(SESSION_FILE).write_text(sid)
        is_resume = False
    else:
        # 默认：生成全新 session，节省 Token
        sid = new_session()
        is_resume = False

    await repl(sid, is_resume=is_resume)


def entry() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry()
