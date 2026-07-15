import subprocess
import os

def create_file(path, content):
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)
    return f"Created {path} with length of {len(content)} bytes"

TOOLS_SCHEMA = {
        "name": "create_file",
        "description": "Tool to create file",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "We need you to come up with a path that is limited to what is needed only",
                },
                "content": {
                    "type": "string",
                },
            },
            "required": ["path", "content"]
        }
    }
TOOL_DISPATCH = {
    "create_file": lambda args: create_file(path=args["path"], content=args["content"]),
}
def execute_tool(arguments: dict, name: str) -> str:
    if name not in TOOL_DISPATCH:
        return f"Unknown tool: {name}"
    return TOOL_DISPATCH[name](arguments)

def chat(user_message: str) -> str:
    import ollama
    messages = [{"role": "user", "content": user_message}]
    response = ollama.chat(
        model="llama3.2:3b",
        messages= messages,
        tools=TOOLS_SCHEMA
    )
    message = response["message"]
    tool_calls = message.get("tool_calls") or []

    if not tool_calls:
        return message.get("content", "")

    messages.append(message)

