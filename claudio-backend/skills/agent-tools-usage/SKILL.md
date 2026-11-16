---
name: agent-tools-usage
description: 'Effective tool use by agents and MCP integration: selecting the right
  tool, calling functions correctly, handling errors and retries, chaining tools for
  multi-step tasks, building MCP servers, interactive MCP apps and .mcpb bundles,
  wiring MCP servers into plugins via .mcp.json, and discovering existing connectors,
  tools and agents before building.'
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: github
    name: mcp-create-declarative-agent
    license: unknown
    url: https://skillmd.com/skills/github/mcp-create-declarative-agent
  - owner: jrennie99-glitch
    name: agent-user-tools
    license: unknown
    url: https://skillmd.com/skills/jrennie99-glitch/agent-user-tools
  - owner: github
    name: mcp-cli
    license: unknown
    url: https://skillmd.com/skills/github/mcp-cli
  - owner: github
    name: python-mcp-server-generator
    license: unknown
    url: https://skillmd.com/skills/github/python-mcp-server-generator
  - owner: microsoft
    name: mcp-builder
    license: unknown
    url: https://skillmd.com/skills/microsoft/mcp-builder
  - owner: mcp-server-dev
    name: build-mcp-server
    license: unknown
  - owner: mcp-server-dev
    name: build-mcp-app
    license: unknown
  - owner: mcp-server-dev
    name: build-mcpb
    license: unknown
  - owner: plugin-dev
    name: mcp-integration
    license: unknown
  - owner: GitHub app
    name: connector-finder
    license: unknown
  - owner: GitHub app
    name: agentfinder
    license: unknown
  - owner: GitHub app
    name: af
    license: unknown
---



## Example Tool Types to Consider
- Data processing and transformation
- File system operations (read, analyze, search)
- External API integrations
- Database queries
- Text analysis or generation (with sampling)
- System information retrieval
- Math or scientific calculations

## Configuration Options
- **For stdio Servers**:
  - Simple direct execution
  - Test with `uv run mcp dev server.py`
  - Install to Claude: `uv run mcp install server.py`
  
- **For HTTP Servers**:
  - Port configuration via environment variables
  - Stateless mode for scalability: `stateless_http=True`
  - JSON response mode: `json_response=True`
  - CORS configuration for browser clients
  - Mounting to existing ASGI servers (Starlette/FastAPI)

## Testing Guidance
- Explain how to run the server:
  - stdio: `python server.py` or `uv run server.py`
  - HTTP: `python server.py` then connect to `http://localhost:PORT/mcp`
- Test with MCP Inspector: `uv run mcp dev server.py`
- Install to Claude Desktop: `uv run mcp install server.py`
- Include example tool invocations
- Add troubleshooting tips

## Additional Features to Consider
- Context usage for logging, progress, and notifications
- LLM sampling for AI-powered tools
- User input elicitation for interactive workflows
- Lifespan management for shared resources (databases, connections)
- Structured output with Pydantic models
- Icons for UI display
- Image handling with Image class
- Completion support for better UX

## Best Practices
- Use type hints everywhere - they're not optional
- Return structured data when possible
- Log to stderr (or use Context logging) to avoid stdout pollution
- Clean up resources properly
- Validate inputs early
- Provide clear error messages
- Test tools independently before LLM integration

Generate a complete, production-ready MCP server with type safety, proper error handling, and comprehensive documentation.

## Credits

- **github/mcp-create-declarative-agent** — license: unknown — https://skillmd.com/skills/github/mcp-create-declarative-agent
- **jrennie99-glitch/agent-user-tools** — license: unknown — https://skillmd.com/skills/jrennie99-glitch/agent-user-tools
- **github/mcp-cli** — license: unknown — https://skillmd.com/skills/github/mcp-cli
- **github/python-mcp-server-generator** — license: unknown — https://skillmd.com/skills/github/python-mcp-server-generator
- **microsoft/mcp-builder** — license: unknown — https://skillmd.com/skills/microsoft/mcp-builder

---

## Mcp Integration (merged from `temp/mcp-integration`, 2026-10-25)

# MCP Server & App Development

Three distinct deliverables get confused constantly: an **MCP server** (tools),
an **MCP app** (interactive UI rendered in chat) and an **MCP bundle**
(`.mcpb`, distribution format with a runtime). Pick the shape before writing code.

## Constituent skills (7)

| Skill | Origin | What it contributes |
|---|---|---|
| build-mcp-server | mcp-server-dev | Entry point: interrogate the use case, pick deployment model (remote HTTP / MCPB / local stdio), pick a tool-design pattern |
| build-mcp-app | mcp-server-dev | Interactive UI/widgets in chat: components, forms, pickers |
| build-mcpb | mcp-server-dev | Package a server as `.mcpb` incl. bundled Node or Python runtime |
| mcp-integration | plugin-dev | Adding/integrating MCP servers in a plugin via `.mcp.json` |
| connector-finder | GitHub app | Locating an existing connector instead of building a new one |
| agentfinder | GitHub app | Discover installable MCP servers, tools, skills and agents for a task via the ARD Agent Finder |
| af | GitHub app | Shorthand alias that immediately invokes `agentfinder` |

## Consolidated guidance

1. **Discovery before code.** Determine the use case, the data volume, who
   authenticates, and where the data lives *before* scaffolding. The deployment
   model chosen first is very hard to change later.
2. **Pick the deployment model deliberately:**
   - *Remote HTTP* — shared, multi-user, needs OAuth, scales.
   - *Local stdio* — single user, local files, zero infra, fastest to ship.
   - *MCPB* — must ship to others and needs a pinned runtime.
3. **Search before you build.** `connector-finder`: an existing MCP server for
   the target system is almost always better than a new one.
4. **Tools are verbs with narrow contracts.** Each tool: one action, explicit
   typed inputs, a description that says exactly when to call it and when not to.
5. **Apps need a reason to exist.** Only add interactive UI (form, picker,
   preview) when the user's input cannot be captured cleanly in one message.
6. **Integration is a config file.** In a plugin, MCP servers are declared in
   `.mcp.json`; verify the server actually starts and lists its tools before
   claiming the integration works.
7. **Check the live spec.** The MCP spec and Claude's auth/review criteria move
   faster than local knowledge — consult the current Claude connector docs
   before finalizing auth and review-sensitive decisions.

## When to use this skill

"Build an MCP server for…", "wrap this API so Claude can use it", "make a
widget/form in chat", "package this as .mcpb", "add this MCP server to my plugin".

## References (load on demand)

The detailed sections below are **not** in this body to keep the loaded skill small. When a task needs one, load it with `read_skill_resource("<skill-name>", "references/<file>.md")` (substitute this skill's name). Read only the reference the current task calls for.

- **`references/mcp-server-integration.md`** — building or wiring an MCP server: commands, workflow, examples, options, exit codes, requirements and implementation details
- **`references/m365-ecosystem.md`** — the Microsoft 365 Copilot MCP ecosystem: overview, high-level workflow and documentation library
- **`references/m365-declarative-agent.md`** — the full Microsoft 365 Copilot declarative-agent generation prompt (MCP action, tool selection, auth)
