# Coding Agent --- Full Project Documentation

## 1. Overview

Coding Agent is a developer-focused terminal AI coding assistant built
around two deliberately separated components:

-   **Python + LangGraph** --- agent runtime, state, reasoning loop, and
    tools.
-   **TypeScript + Bun + OpenTUI** --- interactive terminal user
    interface.

The initial architecture is intentionally incremental. The project
starts as a minimal working agent and grows through filesystem tools,
shell execution, streaming, approval workflows, Git, MCP, memory,
testing, and production hardening.

### Architecture

``` text
                         ┌───────────────────────┐
                         │       Developer       │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │     OpenTUI CLI       │
                         │   TypeScript + Bun    │
                         └───────────┬───────────┘
                                     │
                              HTTP / SSE
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │   Python Agent API    │
                         │                       │
                         │      LangGraph        │
                         └───────────┬───────────┘
                                     │
                  ┌──────────────────┼──────────────────┐
                  ▼                  ▼                  ▼
                 LLM              Tools               State
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
                 Filesystem       Shell            Git
                                    │
                                    ▼
                              Local Repository
```

The project deliberately keeps the UI independent from the agent
runtime. This allows the Python agent to later be reused through an API,
tests, another UI, or automation.

------------------------------------------------------------------------

# 2. Goals

## Primary goals

1.  Build a practical coding agent in Python.
2.  Use LangGraph for explicit agent orchestration and state
    transitions.
3.  Provide a polished terminal UI with OpenTUI.
4.  Let the agent inspect and modify a local repository.
5.  Execute tests and development commands with controlled permissions.
6.  Stream agent/tool activity to the CLI.
7.  Require human approval for potentially destructive actions.
8.  Support Git-aware workflows.
9.  Add MCP integration after the core agent is stable.
10. Build toward measurable evaluation and production deployment.

## Non-goals for the first version

The first version should not immediately include:

-   multi-agent orchestration
-   Redis
-   PostgreSQL
-   distributed workers
-   long-term memory
-   RAG
-   MCP
-   complex plugin systems
-   autonomous unrestricted shell access

These are later milestones and should only be introduced when they solve
a concrete requirement.

------------------------------------------------------------------------

# 3. Technology Stack

  Layer                    Technology
  ------------------------ -----------------------------------------
  Agent language           Python 3.12+
  Python package manager   uv
  Agent orchestration      LangGraph
  LLM abstraction          LangChain
  Initial LLM provider     OpenAI-compatible LangChain integration
  CLI language             TypeScript
  CLI runtime              Bun
  Terminal UI              OpenTUI
  Configuration            Environment variables / `.env`
  Testing                  pytest
  Linting                  Ruff
  Version control          Git
  API/transport            HTTP initially; SSE for streaming
  Future protocol          MCP

The Python engineering baseline favors Python 3.12+, type hints, async
support where useful, Pydantic, environment variables, structured
logging, exception handling, and unit/integration tests.

------------------------------------------------------------------------

# 4. Repository Structure

Recommended final structure:

``` text
coding-agent/
│
├── agent/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── .env
│   ├── .env.example
│   │
│   └── src/
│       └── coding_agent/
│           ├── __init__.py
│           ├── main.py
│           │
│           ├── agent/
│           │   ├── __init__.py
│           │   ├── graph.py
│           │   ├── state.py
│           │   └── nodes.py
│           │
│           ├── tools/
│           │   ├── __init__.py
│           │   ├── filesystem.py
│           │   ├── shell.py
│           │   ├── git.py
│           │   └── search.py
│           │
│           ├── server/
│           │   ├── __init__.py
│           │   ├── api.py
│           │   └── events.py
│           │
│           ├── prompts/
│           │   └── coding_agent.py
│           │
│           ├── config/
│           │   └── settings.py
│           │
│           ├── services/
│           │   ├── workspace.py
│           │   └── approvals.py
│           │
│           └── utils/
│               ├── logging.py
│               └── security.py
│
├── cli/
│   ├── package.json
│   ├── bun.lock
│   ├── tsconfig.json
│   │
│   └── src/
│       ├── main.ts
│       ├── app.ts
│       │
│       ├── components/
│       │   ├── header.ts
│       │   ├── chat.ts
│       │   ├── input.ts
│       │   ├── tool.ts
│       │   ├── diff.ts
│       │   ├── approval.ts
│       │   └── status.ts
│       │
│       ├── api/
│       │   ├── agent.ts
│       │   └── events.ts
│       │
│       └── state/
│           └── app-state.ts
│
├── tests/
│   ├── agent/
│   ├── tools/
│   ├── api/
│   └── e2e/
│
├── scripts/
│
├── README.md
├── .gitignore
└── LICENSE
```

The architecture may be simplified during early milestones. Do not
create every directory before it is needed.

------------------------------------------------------------------------

# 5. Initial Setup

## Prerequisites

Install:

-   Python 3.12+
-   uv
-   Bun
-   Git

Verify:

``` bash
python3 --version
uv --version
bun --version
git --version
```

## Create the repository

``` bash
mkdir coding-agent
cd coding-agent
git init

mkdir agent cli
```

## Initialize Python

``` bash
cd agent

uv init
uv python pin 3.12

uv add langgraph langchain langchain-openai python-dotenv
uv add --dev pytest ruff

cd ..
```

## Initialize OpenTUI

``` bash
cd cli

bun init -y
bun add @opentui/core

cd ..
```

------------------------------------------------------------------------

# 6. Environment Configuration

Create:

``` text
agent/.env
```

Example:

``` env
OPENAI_API_KEY=your_api_key_here
```

Create:

``` text
agent/.env.example
```

``` env
OPENAI_API_KEY=
```

Never commit `.env`.

Recommended `.gitignore`:

``` gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.env
*.pyc
node_modules/
dist/
```

------------------------------------------------------------------------

# 7. Agent Responsibilities

The Python agent is responsible for:

1.  receiving a user request
2.  maintaining agent state
3.  deciding whether a tool is required
4.  calling tools
5.  observing tool results
6.  continuing or stopping the agent loop
7.  returning a final response
8.  emitting structured events for the UI

The UI is not responsible for reasoning. It displays and collects
information.

------------------------------------------------------------------------

# 8. LangGraph State

Initial state:

``` python
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

Later state can evolve into:

``` python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    working_directory: str
    current_task: str
    files_changed: list[str]
    command_results: list[str]
    pending_approval: dict | None
```

Do not add fields without a real requirement.

------------------------------------------------------------------------

# 9. Agent Graph

The fundamental graph is:

``` text
START
  │
  ▼
agent
  │
  ├── tool call ──► tools
  │                  │
  │                  └──────► agent
  │
  └── final answer ────────► END
```

Conceptually:

``` text
User
 │
 ▼
Agent
 │
 ▼
Decision
 │
 ├── no tool ───────────────► Final Response
 │
 └── tool required
          │
          ▼
       Tool
          │
          ▼
      Observation
          │
          ▼
        Agent
```

This loop is the core of the coding agent.

------------------------------------------------------------------------

# 10. Tools

Tools are first-class components.

Every important tool should define:

``` text
Tool name
Purpose
Input schema
Output schema
Side effects
Failure modes
Permissions
Authentication
```

## 10.1 `list_files`

Purpose:

``` text
Inspect repository structure.
```

Input:

``` text
path: string
```

Output:

``` text
Human-readable file listing
```

Side effects:

``` text
None
```

Permission:

``` text
Read-only
```

------------------------------------------------------------------------

## 10.2 `read_file`

Purpose:

``` text
Read source/configuration/documentation files.
```

Input:

``` text
path: string
```

Output:

``` text
File contents
```

Side effects:

``` text
None
```

Security requirements:

-   resolve paths
-   prevent access outside the allowed workspace
-   avoid reading secrets unnecessarily
-   handle binary files
-   enforce file-size limits

------------------------------------------------------------------------

## 10.3 `write_file`

Purpose:

``` text
Create or modify a repository file.
```

Input:

``` text
path: string
content: string
```

Side effects:

``` text
Filesystem mutation
```

Permission:

``` text
Requires workspace permission.
```

For the first safe implementation, the UI should display the proposed
change before applying it.

------------------------------------------------------------------------

## 10.4 `run_command`

Purpose:

``` text
Execute development commands.
```

Examples:

``` bash
pytest
npm test
bun test
ruff check .
git diff
```

This is a high-risk tool.

The implementation must eventually include:

-   command allowlist/denylist strategy
-   workspace restriction
-   timeout
-   output limit
-   environment filtering
-   approval for dangerous commands
-   process cleanup
-   exit-code reporting

Never provide unrestricted shell access simply because the LLM requests
it.

------------------------------------------------------------------------

## 10.5 `git`

Future Git operations:

``` text
git status
git diff
git log
git branch
git add
git commit
```

Destructive operations such as reset, force operations, or history
rewriting should require explicit approval.

------------------------------------------------------------------------

# 11. Tool Execution Security

Coding agents have unusually high privilege because they can potentially
modify files and execute commands.

Security model:

``` text
User
 │
 ▼
LLM
 │
 ▼
Tool Request
 │
 ▼
Policy Check
 │
 ├── Safe ───────────────► Execute
 │
 └── Sensitive
          │
          ▼
       Approval
          │
      ┌───┴───┐
      ▼       ▼
    Allow    Reject
      │
      ▼
   Execute
```

Minimum security rules:

1.  Restrict filesystem access to the workspace.
2.  Never expose `.env` contents to the model unless explicitly
    required.
3.  Never hard-code secrets.
4.  Validate tool arguments.
5.  Treat tool output as untrusted data.
6.  Use command timeouts.
7.  Limit tool output size.
8.  Require approval for destructive operations.
9.  Keep least privilege as the default.
10. Log security-relevant actions.

------------------------------------------------------------------------

# 12. OpenTUI CLI

The OpenTUI application owns the interactive terminal experience.

Initial component tree:

``` text
App
├── Header
├── Conversation
│   ├── UserMessage
│   ├── AgentMessage
│   ├── ToolCall
│   ├── ToolResult
│   └── Diff
├── Input
└── StatusBar
```

Target experience:

``` text
╭────────────────────────────────────────────────────────────╮
│ ⚡ Coding Agent                              ~/project      │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ You                                                        │
│ > Fix the authentication bug                               │
│                                                            │
│ Agent                                                      │
│ → Inspecting repository...                                 │
│                                                            │
│ Tool                                                      │
│ → read_file("src/auth/login.ts")                           │
│                                                            │
│ Agent                                                      │
│ I found the issue.                                         │
│                                                            │
│ Proposed change                                            │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ - old condition                                        │ │
│ │ + new condition                                        │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ > Type a message...                         Ctrl+C Exit   │
╰────────────────────────────────────────────────────────────╯
```

------------------------------------------------------------------------

# 13. CLI State

The UI should maintain UI state separately from LangGraph state.

Example:

``` text
CLI State
├── connectionStatus
├── messages
├── activeTool
├── streamingText
├── pendingApproval
├── currentDiff
└── status
```

The server remains the source of truth for agent execution.

------------------------------------------------------------------------

# 14. Python ↔ OpenTUI Communication

The recommended boundary is:

``` text
OpenTUI
   │
   │ HTTP request
   ▼
Python API
   │
   ▼
LangGraph
```

For streaming:

``` text
LangGraph
   │
   │ agent events
   ▼
SSE endpoint
   │
   ▼
OpenTUI
```

Example event model:

``` json
{
  "type": "agent_message",
  "content": "I am inspecting the repository."
}
```

Tool event:

``` json
{
  "type": "tool_start",
  "tool": "read_file",
  "args": {
    "path": "src/auth/login.ts"
  }
}
```

Tool completion:

``` json
{
  "type": "tool_end",
  "tool": "read_file",
  "result": "..."
}
```

Approval request:

``` json
{
  "type": "approval_required",
  "action": "write_file",
  "path": "src/auth/login.ts"
}
```

Final response:

``` json
{
  "type": "final",
  "content": "The authentication bug has been fixed."
}
```

------------------------------------------------------------------------

# 15. Streaming

Streaming is important for a coding agent because tool execution can
take time.

Target event flow:

``` text
User
 │
 ▼
Agent
 │
 ├── thinking/event
 │
 ├── tool_start
 │
 ├── tool_output
 │
 ├── agent_token
 │
 ├── tool_start
 │
 ├── tool_output
 │
 └── final
```

The UI should render these events incrementally instead of waiting for
the complete graph execution.

------------------------------------------------------------------------

# 16. Approval System

The approval system is a central safety boundary.

Example:

``` text
Agent wants to execute:

rm -rf build/

╭─────────────────────────────────────╮
│         Approval Required           │
├─────────────────────────────────────┤
│ Command: rm -rf build/              │
│                                     │
│ Allow this command?                 │
│                                     │
│ [Y] Allow   [N] Reject              │
╰─────────────────────────────────────╯
```

Approval should be represented explicitly in state:

``` python
pending_approval = {
    "action": "run_command",
    "arguments": {
        "command": "..."
    }
}
```

The graph should pause until the user responds.

------------------------------------------------------------------------

# 17. File Modification Workflow

Never make file modification opaque.

Preferred flow:

``` text
User request
     │
     ▼
Inspect files
     │
     ▼
Generate modification
     │
     ▼
Generate diff
     │
     ▼
Show diff in OpenTUI
     │
     ▼
User approval
     │
  ┌──┴──┐
  ▼     ▼
Apply  Reject
  │
  ▼
Run tests
  │
  ▼
Report result
```

This makes the agent much easier to trust and debug.

------------------------------------------------------------------------

# 18. Coding Workflow

A typical task should follow:

``` text
1. Understand request
2. Inspect repository
3. Locate relevant files
4. Read relevant code
5. Form implementation plan
6. Modify files
7. Inspect diff
8. Run focused tests
9. Fix failures
10. Run broader tests
11. Summarize changes
```

The agent should not blindly modify files before understanding the
repository.

------------------------------------------------------------------------

# 19. Example Task

User:

``` text
Fix the login bug.
```

Expected agent behavior:

``` text
Agent
→ Inspect repository

Tool
→ list_files()

Agent
→ Search authentication code

Tool
→ search_files("login")

Agent
→ Read login implementation

Tool
→ read_file("src/auth/login.ts")

Agent
→ Identify bug

Agent
→ Propose patch

UI
→ Show diff

User
→ Approve

Tool
→ write_file()

Agent
→ Run tests

Tool
→ run_command("pytest ...")

Agent
→ Report result
```

------------------------------------------------------------------------

# 20. Configuration

Recommended settings:

``` python
class Settings:
    model: str
    workspace: str
    max_tool_output: int
    command_timeout: int
    require_approval: bool
```

Environment example:

``` env
OPENAI_API_KEY=
MODEL=
WORKSPACE=
MAX_TOOL_OUTPUT=20000
COMMAND_TIMEOUT=30
REQUIRE_APPROVAL=true
```

The exact model name should remain configurable because model/provider
APIs change over time.

------------------------------------------------------------------------

# 21. Testing Strategy

Testing should exist at multiple levels.

``` text
Tests
├── Unit
│   ├── tools
│   ├── state
│   └── security
│
├── Integration
│   ├── LangGraph
│   ├── API
│   └── tool execution
│
├── E2E
│   └── CLI → API → Agent → Tool
│
└── Evaluation
    ├── task success
    ├── tool selection
    ├── tool arguments
    ├── hallucination
    ├── reliability
    └── regression
```

## Tool unit test

Example:

``` python
def test_read_file(tmp_path):
    file = tmp_path / "hello.py"
    file.write_text("print('hello')")

    result = read_file.invoke({
        "path": str(file)
    })

    assert "hello" in result
```

## Security test

Test that:

``` text
workspace/
  allowed.py

/etc/passwd
```

cannot be accessed when the workspace is:

``` text
workspace/
```

------------------------------------------------------------------------

# 22. Evaluation

For serious development, success should be measurable.

Create benchmark tasks such as:

``` text
Task 001
Add a missing function.

Task 002
Fix a failing test.

Task 003
Refactor a module.

Task 004
Find a bug without modifying files.

Task 005
Implement a feature and pass tests.
```

Record:

``` text
Task success
Tool selection
Tool arguments
Number of iterations
Token usage
Latency
Test success
Failure recovery
Safety violations
```

A useful evaluation dataset should contain both normal tasks and
adversarial tasks.

------------------------------------------------------------------------

# 23. Logging

Use structured logs in the Python runtime.

Important fields:

``` text
timestamp
request_id
session_id
agent_node
tool_name
tool_arguments
tool_duration
tool_result_status
model
token_usage
error
```

Never log:

``` text
API keys
passwords
private tokens
secret environment values
```

------------------------------------------------------------------------

# 24. Error Handling

Classify failures:

``` text
Syntax Error
     ↓
Runtime Error
     ↓
Dependency Error
     ↓
Configuration Error
     ↓
API Error
     ↓
Tool Error
     ↓
Architecture Error
     ↓
Logic Error
```

Agent behavior should distinguish:

``` text
recoverable error
      │
      ▼
retry / inspect / fix

non-recoverable error
      │
      ▼
stop + explain
```

Avoid infinite tool loops.

Set maximum iterations for an agent run.

------------------------------------------------------------------------

# 25. Git Workflow

Recommended Git workflow:

``` bash
git status
git diff
git add <files>
git commit
```

The agent should initially be read-only with Git.

Later:

``` text
Read operations
├── status
├── diff
├── log
└── show

Write operations
├── add
├── commit
└── branch

High-risk operations
├── reset
├── clean
├── push --force
└── history rewrite
```

High-risk operations require explicit human approval.

------------------------------------------------------------------------

# 26. MCP Integration

MCP should be introduced after the basic agent/tool system works.

Architecture:

``` text
Coding Agent
     │
     ▼
MCP Client
     │
     ▼
MCP Server
     │
     ├── Tools
     ├── Resources
     └── Prompts
           │
           ▼
      External System
```

Potential future MCP servers:

``` text
GitHub
PostgreSQL
Filesystem
Documentation
Issue tracker
Cloud infrastructure
Browser automation
```

Do not make MCP a prerequisite for the first working coding agent.

------------------------------------------------------------------------

# 27. Memory

Memory should be added only after the core workflow is stable.

Distinguish:

### Short-term memory

Current LangGraph execution:

``` text
current conversation
current task
current tool results
current plan
```

### Long-term memory

Persistent information:

``` text
developer preferences
repository conventions
previous decisions
project instructions
```

### External knowledge

``` text
documentation
database
repository
APIs
MCP resources
```

RAG and memory are different concerns.

------------------------------------------------------------------------

# 28. Production Architecture

A future production deployment can become:

``` text
                   ┌───────────────┐
                   │    OpenTUI    │
                   └───────┬───────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Gateway   │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ Agent API   │
                    │  FastAPI    │
                    └──────┬──────┘
                           │
                  ┌────────┼────────┐
                  ▼        ▼        ▼
               LangGraph  Tools   Storage
                  │
                  ▼
                 LLM
```

Only introduce infrastructure that solves a real scaling or reliability
requirement.

Potential production components:

``` text
FastAPI
PostgreSQL
Redis
background workers
Docker
Nginx
Cloudflare
observability
CI/CD
```

------------------------------------------------------------------------

# 29. Development Milestones

## Milestone 1 --- Foundation

### Goal

Create independent Python and OpenTUI projects.

### Files

``` text
agent/
cli/
```

### Commands

``` bash
uv init
uv python pin 3.12
uv add langgraph langchain langchain-openai python-dotenv
```

``` bash
bun init -y
bun add @opentui/core
```

### Expected result

Both runtimes start successfully.

------------------------------------------------------------------------

## Milestone 2 --- LangGraph Agent

### Goal

Create a minimal agent loop.

``` text
START
 ↓
Agent
 ↓
END
```

Then add:

``` text
Agent
 ↓
Tool
 ↓
Agent
```

### Expected result

The Python process can receive a prompt and produce an LLM response.

------------------------------------------------------------------------

## Milestone 3 --- OpenTUI Chat

### Goal

Build:

``` text
Header
Conversation
Input
Status
```

### Expected result

The terminal feels like a real coding assistant rather than a simple
`input()` loop.

------------------------------------------------------------------------

## Milestone 4 --- Connect CLI and Agent

### Goal

Connect:

``` text
OpenTUI
   ↓
Python API
   ↓
LangGraph
```

### Expected result

Typing a prompt into OpenTUI causes a LangGraph execution.

------------------------------------------------------------------------

## Milestone 5 --- Filesystem Tools

Implement:

``` text
list_files
read_file
search_files
write_file
```

### Expected result

The agent can understand and modify a repository.

------------------------------------------------------------------------

## Milestone 6 --- Shell

Implement:

``` text
run_command
```

with:

-   timeout
-   output limit
-   workspace restriction
-   approval
-   exit code

### Expected result

The agent can run tests and development commands safely.

------------------------------------------------------------------------

## Milestone 7 --- Streaming

Implement:

``` text
LangGraph
 ↓
events
 ↓
SSE
 ↓
OpenTUI
```

### Expected result

Users see agent/tool activity as it happens.

------------------------------------------------------------------------

## Milestone 8 --- Diff + Approval

Add:

``` text
proposed change
 ↓
diff
 ↓
approval
 ↓
write
```

### Expected result

File changes are transparent and user-controlled.

------------------------------------------------------------------------

## Milestone 9 --- Git

Add:

``` text
status
diff
log
branch
commit
```

### Expected result

The agent can operate Git-aware workflows.

------------------------------------------------------------------------

## Milestone 10 --- Checkpointing

Add LangGraph persistence.

Goals:

-   resume sessions
-   recover failures
-   preserve execution state
-   support human-in-the-loop pauses

------------------------------------------------------------------------

## Milestone 11 --- MCP

Add MCP client capabilities.

Goals:

-   external tools
-   resources
-   integrations
-   standardized tool access

------------------------------------------------------------------------

## Milestone 12 --- Evaluation

Build a benchmark suite.

Measure:

``` text
success rate
tool accuracy
failure recovery
latency
token cost
safety
regressions
```

------------------------------------------------------------------------

## Milestone 13 --- Production

Only after the previous milestones are stable:

``` text
Docker
FastAPI
PostgreSQL
Redis
observability
CI/CD
Nginx
Cloudflare
```

------------------------------------------------------------------------

# 30. Developer Commands

## Python

Run agent:

``` bash
cd agent
uv run python -m coding_agent.main
```

Run tests:

``` bash
uv run pytest
```

Lint:

``` bash
uv run ruff check .
```

Format:

``` bash
uv run ruff format .
```

## CLI

Run OpenTUI:

``` bash
cd cli
bun run src/main.ts
```

------------------------------------------------------------------------

# 31. Development Principles

### Start small

Do not begin with a distributed architecture.

### Inspect before modifying

A coding agent should understand the repository before changing it.

### Make tools explicit

Every side effect should happen through a defined tool.

### Keep permissions narrow

The agent should have only the permissions required for the current
task.

### Keep UI and agent separate

OpenTUI should render state and collect user interaction. LangGraph
should own agent execution.

### Prefer deterministic tooling

File and shell operations should be validated and observable.

### Test the tools

A broken tool can make a good model look like a bad agent.

### Evaluate the whole system

LLM quality alone is not enough. Evaluate:

``` text
model
+
prompt
+
state
+
tools
+
control flow
+
security
+
UI
```

------------------------------------------------------------------------

# 32. Definition of Done

The coding agent is considered production-useful when it can:

-   [ ] start from a repository
-   [ ] understand a coding request
-   [ ] inspect files
-   [ ] search the codebase
-   [ ] read relevant code
-   [ ] create a plan
-   [ ] propose modifications
-   [ ] display a diff
-   [ ] request approval
-   [ ] modify files
-   [ ] run tests
-   [ ] inspect failures
-   [ ] repair failures
-   [ ] show tool activity
-   [ ] stream progress
-   [ ] preserve execution state
-   [ ] operate with bounded permissions
-   [ ] interact with Git
-   [ ] integrate external tools through MCP
-   [ ] pass automated evaluations
-   [ ] provide useful error messages
-   [ ] avoid leaking secrets

------------------------------------------------------------------------

# 33. Recommended First Build

Do not implement the entire specification at once.

The first working target should be exactly:

``` text
             OpenTUI
                │
                │
                ▼
         Python LangGraph
                │
                ▼
               LLM
                │
                ▼
          list_files()
          read_file()
```

Then:

``` text
read
 ↓
reason
 ↓
write
 ↓
test
 ↓
fix
```

That is the smallest implementation that starts behaving like a real
coding agent.

------------------------------------------------------------------------

# 34. Final Architecture

The intended mature architecture is:

``` text
┌──────────────────────────────────────────────────────────────┐
│                         OpenTUI CLI                          │
│                                                              │
│  Chat │ Tool Events │ Diff │ Approval │ Status │ Input      │
└────────────────────────────┬─────────────────────────────────┘
                             │
                         HTTP / SSE
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                       Python Runtime                         │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                    LangGraph                           │  │
│  │                                                        │  │
│  │  Router → Agent → Tool → Observation → Agent → END    │  │
│  └───────────────────────────┬────────────────────────────┘  │
│                              │                               │
│              ┌───────────────┼───────────────┐               │
│              ▼               ▼               ▼               │
│         Filesystem         Shell            Git              │
│              │               │               │               │
│              └───────────────┼───────────────┘               │
│                              │                               │
│                         Policy Layer                         │
│                              │                               │
│                         Approval                             │
│                              │                               │
│                         Workspace                            │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
                         External Systems
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
                   LLM        MCP       GitHub
```

This architecture keeps the project understandable while leaving a clean
path toward a serious developer agent.
