# MCP server integration & construction



## Commands

| Command                            | Output                          |
| ---------------------------------- | ------------------------------- |
| `mcp-cli`                          | List all servers and tool names |
| `mcp-cli <server>`                 | Show tools with parameters      |
| `mcp-cli <server>/<tool>`          | Get tool JSON schema            |
| `mcp-cli <server>/<tool> '<json>'` | Call tool with arguments        |
| `mcp-cli grep "<glob>"`            | Search tools by name            |

**Add `-d` to include descriptions** (e.g., `mcp-cli filesystem -d`)

## Workflow

1. **Discover**: `mcp-cli` → see available servers and tools
2. **Explore**: `mcp-cli <server>` → see tools with parameters
3. **Inspect**: `mcp-cli <server>/<tool>` → get full JSON input schema
4. **Execute**: `mcp-cli <server>/<tool> '<json>'` → run with arguments

## Examples

```bash
# List all servers and tool names
mcp-cli

# See all tools with parameters
mcp-cli filesystem

# With descriptions (more verbose)
mcp-cli filesystem -d

# Get JSON schema for specific tool
mcp-cli filesystem/read_file

# Call the tool
mcp-cli filesystem/read_file '{"path": "./README.md"}'

# Search for tools
mcp-cli grep "*file*"

# JSON output for parsing
mcp-cli filesystem/read_file '{"path": "./README.md"}' --json

# Complex JSON with quotes (use heredoc or stdin)
mcp-cli server/tool <<EOF
{"content": "Text with 'quotes' inside"}
EOF

# Or pipe from a file/command
cat args.json | mcp-cli server/tool

# Find all TypeScript files and read the first one
mcp-cli filesystem/search_files '{"path": "src/", "pattern": "*.ts"}' --json | jq -r '.content[0].text' | head -1 | xargs -I {} sh -c 'mcp-cli filesystem/read_file "{\"path\": \"{}\"}"'
```

## Options

| Flag         | Purpose                   |
| ------------ | ------------------------- |
| `-j, --json` | JSON output for scripting |
| `-r, --raw`  | Raw text content          |
| `-d`         | Include descriptions      |

## Exit Codes

- `0`: Success
- `1`: Client error (bad args, missing config)
- `2`: Server error (tool failed)
- `3`: Network error

Create a complete Model Context Protocol (MCP) server in Python with the following specifications:

## Requirements

1. **Project Structure**: Create a new Python project with proper structure using uv
2. **Dependencies**: Include mcp[cli] package with uv
3. **Transport Type**: Choose between stdio (for local) or streamable-http (for remote)
4. **Tools**: Create at least one useful tool with proper type hints
5. **Error Handling**: Include comprehensive error handling and validation

## Implementation Details

### Project Setup
- Initialize with `uv init project-name`
- Add MCP SDK: `uv add "mcp[cli]"`
- Create main server file (e.g., `server.py`)
- Add `.gitignore` for Python projects
- Configure for direct execution with `if __name__ == "__main__"`

### Server Configuration
- Use `FastMCP` class from `mcp.server.fastmcp`
- Set server name and optional instructions
- Choose transport: stdio (default) or streamable-http
- For HTTP: optionally configure host, port, and stateless mode

### Tool Implementation
- Use `@mcp.tool()` decorator on functions
- Always include type hints - they generate schemas automatically
- Write clear docstrings - they become tool descriptions
- Use Pydantic models or TypedDicts for structured outputs
- Support async operations for I/O-bound tasks
- Include proper error handling

### Resource/Prompt Setup (Optional)
- Add resources with `@mcp.resource()` decorator
- Use URI templates for dynamic resources: `"resource://{param}"`
- Add prompts with `@mcp.prompt()` decorator
- Return strings or Message lists from prompts

### Code Quality
- Use type hints for all function parameters and returns
- Write docstrings for tools, resources, and prompts
- Follow PEP 8 style guidelines
- Use async/await for asynchronous operations
- Implement context managers for resource cleanup
- Add inline comments for complex logic
