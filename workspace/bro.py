def create_file(path, content):
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)
    return f"Created {path} with length of {len(content)} bytes"

TOOLS_SCHEMA = [{
    "type": "function",
    "function": {   # This is a bare dict describing one tool. Ollama's tools= argument expects a list of tools, each shaped as {"type": "function", "function": {name, description, parameters}}. Why it matters: this isn't cosmetic — it's the actual contract Ollama's API parses. Pass the wrong shape and either the model never sees the tool as available at all, or the call errors outright. Fix:
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
}]
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
    for call in tool_calls:
        name = call["function"]["name"]
        args = call["function"]["arguments"]
        if isinstance(args, str):
            import json
            args = json.loads(args)
        result = execute_tool(args, name)
        messages.append({"role": "tool", "content": result})
    follow_up = ollama.chat(model="llama3.2:3b", messages=messages, tools=TOOLS_SCHEMA)
    return follow_up["message"].get("content", "")

