"""Chainlit chat app — wired to MindsDB agent."""

import asyncio
import os

import chainlit as cl
from agent_client import query_agent, query_agent_stream

STREAM_THINKING = os.getenv("STREAM_THINKING", "").lower() in ("1", "true", "yes")

_DONE = object()


@cl.on_chat_start
async def on_start():
    cl.user_session.set("history", [])


@cl.set_starters
async def starters():
    return [
        cl.Starter(
            label="Top populated states",
            message="What are the top 10 most populated states?",
        ),
        cl.Starter(
            label="Highest poverty rates",
            message="Which counties have the highest poverty rates?",
        ),
        cl.Starter(
            label="Education comparison",
            message="Compare education levels in Texas vs California counties",
        ),
        cl.Starter(
            label="Income trends",
            message="How has median household income changed from 2019 to 2023 for the top 5 states?",
        ),
    ]


async def _handle_blocking(msg, question, history):
    answer, exports = await cl.make_async(query_agent)(question, history)
    msg.content = answer
    if exports:
        msg.elements = [
            cl.File(name=e["filename"], path=e["path"], display="inline")
            for e in exports
        ]
    await msg.update()
    return answer


async def _handle_streaming(question, history):
    loop = asyncio.get_running_loop()
    gen = await loop.run_in_executor(
        None, lambda: query_agent_stream(question, history)
    )

    final_answer = ""
    all_exports = []

    while True:
        chunk = await loop.run_in_executor(
            None, lambda: next(gen, _DONE)
        )
        if chunk is _DONE:
            break

        if not isinstance(chunk, dict):
            final_answer += str(chunk)
            continue

        # Agent tool call + result — show as collapsible step
        if "steps" in chunk:
            for s in chunk["steps"]:
                action = s.get("action", {})
                async with cl.Step(
                    name=action.get("tool", "Tool"),
                    type="tool",
                    show_input="sql",
                ) as step:
                    step.input = action.get("tool_input", "")
                    step.output = s.get("observation", "")

        # Collect file exports
        if "exports" in chunk:
            all_exports.extend(chunk["exports"])

        if "output" in chunk:
            final_answer = chunk["output"]

    # Send final answer AFTER all steps so it appears at the bottom
    final_answer = final_answer or "No response received."
    elements = [
        cl.File(name=e["filename"], path=e["path"], display="inline")
        for e in all_exports
    ]
    msg = cl.Message(content=final_answer, elements=elements)
    await msg.send()
    return final_answer


@cl.on_message
async def on_message(message: cl.Message):
    history = cl.user_session.get("history")
    try:
        if STREAM_THINKING:
            answer = await _handle_streaming(message.content, history)
        else:
            msg = cl.Message(content="")
            await msg.send()
            answer = await _handle_blocking(msg, message.content, history)
        history.append({"question": message.content, "answer": answer})
        cl.user_session.set("history", history)
    except Exception as e:
        err = cl.Message(content=f"Sorry, I encountered an error: {str(e)}")
        await err.send()
