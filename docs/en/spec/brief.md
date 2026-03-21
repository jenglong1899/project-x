# Background
This product is aimed at ordinary users.
LLMs have jagged intelligence, so the product should make it easy to supervise how the LLM works. If the user decides not to supervise, the user bears that risk.

Possible direction:
- Expose advanced features to technical users without adding burden to ordinary users.

# Core Capabilities
There should be a web client, and it should support streaming only.
Support resetting memory.
Support steering the conversation.
Support scheduled tasks.
Integrate with IM platforms.
Support multi-agent workflows.

# Basic Tools

## bash
Start with the simplest possible version.

Later:
1. Support running in the background. Use steer conversation for notifications. If a run exceeds 5 seconds, automatically move it to the background.
2. Persist background state? For example, persist the working path via environment variables set by the terminal, so the agent does not need to `cd` every time. This may affect the sandbox and still needs research.

## read
filepath:str # absolute or relative path. Supports plain text files and image files.
context_percentage_limit:float=5 # by default, read at most 5% of the context in one call. If the file would exceed that, return empty content with an error that reports what percentage the file would occupy.
line_display:bool # whether to show line numbers, using a `sed`-style format

->str
For example:
```
some_filepath:1-200
```

Why return empty content instead of returning the first 5% when the limit is exceeded? I have an intuitive reason for this, but I cannot recall it clearly right now. One likely case is that a partial answer can sometimes be worse than no answer.
We have already been burned by `line_display` before: even relatively capable models such as DS 3.2 or Minimax can still misunderstand custom line-number separators and treat them as part of the file content.

## replace
```python
(
    file_path: str = Field(description="Absolute or relative path")
    needle: str = Field(
        description="The string or regex pattern to search for. "
                    "If mode is \"literal\", this string is matched exactly. "
                    "If mode is \"regex\", this string is treated as a regular expression (using Python's re syntax, "
                    "with DOTALL and MULTILINE enabled).")
    repl: str = Field(
        description="The replacement string. "
                    "If mode is \"regex\", this string may contain backreferences to capture groups in needle, "
                    "using syntax such as $!1, $!2, etc. for groups 1, 2, and so on.")
    mode: Literal["literal", "regex"] = Field(description="Specifies how to interpret needle.")
    allow_multiple_occurrences: bool = (
        Field(False, description=
        "If True, the regex may match multiple occurrences in the file and all of them will be replaced. "
        "If False and the regex matches multiple occurrences, return an error instead (the caller can retry with a revised, more specific regex)."))
)
```
Using a regex in the form `beginning.*?end-of-text-to-be-replaced` lets you reference a large block of text without typing it out in full.
If you need to edit a JSON file, it may be more convenient to write a small Python script on the spot and use the `json` library to edit it.

Although the example above uses Pydantic `description`, we should avoid presenting tools that way in practice.
The overall purpose of a function and the meaning of each parameter should stay together. Pydantic tends to split them apart.
There should be a dedicated field for the tool description shown to the AI. The function's own docstring is for developers, so sometimes we need an additional developer-facing note beyond the AI-facing description.

With `replace`, there is no real need for separate insert-before / insert-after tools.

Because regex replacement is error-prone, the return value should automatically show the edited area with nearby context.

## undo
Regex is easy to get wrong, so an undo tool is necessary. Human users also rely on undo in editors.
Use git for this.
Reference a specific edit by the earlier `replace` tool call ID.

## write
filepath:Path
text:str
mode:Literal['w','a']
)
If the file does not exist, create it automatically, even if multiple parent directories are also missing. This is not something the plain command line can do by itself.
Sometimes you simply want to append; this tool should support that directly.
If you try to append via `replace`, you first need to read the end of the file.

# Session Store
Store sessions in json under `~/.bionic-claw/memories/originals/`.
The file should have two major parts: `meta` metadata and a `messages` array.
Use `coolname + timestamp` for the filename (`coolname` should come from a third-party library; do not hand-roll it).

`meta` includes:
- `display-name`: the user's first message. This is for frontend display; the frontend should show this instead of the json filename.

Within `messages`, each message should also have its own `meta` field, including:
- `timestamp`

# Memory
If `messages` shows that this is the first time `reset context` has been called, then on that first call it should not actually run. Instead, return a prompt such as: "Please check whether your memory document is in good shape first."

In the future, memory should be written directly into `AGENTS.md` and automatically loaded into context by the system, so the agent does not need to read it manually each time.

# Automatic Reminders
Do not remind the AI at fixed context thresholds such as 85%, 70%, or 65% to check whether it should reset context.
Instead, remind it after a certain number of messages to check whether it should reset context.
Reason: each message (AI message with tool calls, or tool message) basically represents one thing the AI is doing. Humans record things continuously while working.
The reminder can also mention how much context remains.

# Continue After Interrupt
Suppose the AI is running and gets interrupted accidentally. Technically, we could call `stream(messages)` again, and add a feature where if the user message is `/resume_break`, then we trigger `stream` once more.
But explaining this feature has a cost, and users would likely find it confusing.
In practice, the user can simply say, "I accidentally interrupted you just now, please continue."
So the current decision is not to build this feature.

# Pause
In theory, once steer conversation is implemented, a user message like "stop for a moment" should also work.

# Interrupt
Previously, Meta's alignment lead told OpenClaw to stop, but OpenClaw kept going. Based on that case, an interrupt button is indeed necessary.
That report did not say which model was used.

# Integrating QQ and Other IM Platforms
If you @-mention a real person in a work group, normally what you want is only their final result. If they narrate their whole process in the group, it takes up too much space.
So the implementation should work like this:

tool: send_msg_to_im_platform(platform_name:str,msg:str)

After the user @-mentions the AI in a group, the orchestrator sends a steer message:
```text
(...all messages since the previous @-mention...)
@bot please go do xxx
```
Edge case: "...all messages since the previous @-mention..." could exceed the context window. That is unlikely, so we will not handle it for now.

After the AI finishes the work, it calls `send_msg_to_im_platform` to send a message back to the user.

If the user wants to see the working process, they should check it in the web client.

# Context-Percentage-Related Tooling
Originally the idea was to build a tool called `count_token_percentage`.
Later the idea changed to this:
when `readfile` exceeds the default context-percentage threshold, the failure response should also tell you what percentage the file would take.
This is better than "first count the percentage, then decide whether to read it."
