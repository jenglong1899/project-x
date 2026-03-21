I want to build a multi-agent mechanism based on Steer conversation.

Agents communicate through user messages, meaning through steer conversation. This allows asynchronous communication: a model can choose to reply immediately or later. The reason to use user messages instead of tool calls is that once you send a tool call, you must attach a tool result in the next turn, which makes asynchronous communication impossible.

There should be a tool called `Create subagent`, whose parameters include the agent's name. There should also be a `first msg` parameter, which is the first thing said to that subagent, i.e. the task being delegated to it.

There should be another tool called `send message to agent`, with parameters `agent_name` and `msg`. Under the hood, this should work by enqueueing a user message.

The system should wrap messages in XML, like this: `<msg from="agent_name">some content</msg>`.

Another thing I want to build on top of this multi-agent mechanism is a memory system that differs from the common memory mechanisms on the market today.

The principle should be similar to the human brain. The human brain has a fast system and a slow system. One of the functions of the fast system is handling memory. It autonomously decides what to forget and what to remember. Humans do not retain the exact sentences they said a few days ago, but they do retain the rough idea, an overall impression.

You could tell the agent in the instructions to record its memory frequently, but that would distract it. So I want two agents: one agent is responsible for doing the work and interacting with the user, while the other agent records memory for the working agent. It can create summaries, decide whether certain content should be summarized, or determine that it can simply be forgotten. This memory agent should also be able to search previous full chat logs stored in json files.

I think this system needs a tool to monitor the conversation log of that slow-system agent. If new content is appended to the conversation log file, it should automatically send a user message to notify the memory agent, letting it decide whether to summarize the new content and then write the result into a document.
