---
name: agent-tools-usage
description: 'Effective tool use by agents: selecting the right tool, calling functions
  correctly, handling tool errors and retries, and chaining tools to accomplish multi-step
  tasks.'
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-23'
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
---

````prompt
---
mode: 'agent'
tools: ['changes', 'search/codebase', 'edit/editFiles', 'problems']
description: 'Create a declarative agent for Microsoft 365 Copilot by integrating an MCP server with authentication, tool selection, and configuration'
model: 'gpt-4.1'
tags: [mcp, m365-copilot, declarative-agent, model-context-protocol, api-plugin]
---

# Create MCP-based Declarative Agent for Microsoft 365 Copilot

Create a complete declarative agent for Microsoft 365 Copilot that integrates with a Model Context Protocol (MCP) server to access external systems and data.

## Requirements

Generate the following project structure using Microsoft 365 Agents Toolkit:

### Project Setup
1. **Scaffold declarative agent** via Agents Toolkit
2. **Add MCP action** pointing to MCP server
3. **Select tools** to import from MCP server
4. **Configure authentication** (OAuth 2.0 or SSO)
5. **Review generated files** (manifest.json, ai-plugin.json, declarativeAgent.json)

### Key Files Generated

**appPackage/manifest.json** - Teams app manifest with plugin reference:
```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/vDevPreview/MicrosoftTeams.schema.json",
  "manifestVersion": "devPreview",
  "version": "1.0.0",
  "id": "...",
  "developer": {
    "name": "...",
    "websiteUrl": "...",
    "privacyUrl": "...",
    "termsOfUseUrl": "..."
  },
  "name": {
    "short": "Agent Name",
    "full": "Full Agent Name"
  },
  "description": {
    "short": "Short description",
    "full": "Full description"
  },
  "copilotAgents": {
    "declarativeAgents": [
      {
        "id": "declarativeAgent",
        "file": "declarativeAgent.json"
      }
    ]
  }
}
```

**appPackage/declarativeAgent.json** - Agent definition:
```json
{
  "$schema": "https://aka.ms/json-schemas/copilot/declarative-agent/v1.0/schema.json",
  "version": "v1.0",
  "name": "Agent Name",
  "description": "Agent description",
  "instructions": "You are an assistant that helps with [specific domain]. Use the available tools to [capabilities].",
  "capabilities": [
    {
      "name": "WebSearch",
      "websites": [
        {
          "url": "https://learn.microsoft.com"
        }
      ]
    },
    {
      "name": "MCP",
      "file": "ai-plugin.json"
    }
  ]
}
```

**appPackage/ai-plugin.json** - MCP plugin manifest:
```json
{
  "schema_version": "v2.1",
  "name_for_human": "Service Name",
  "description_for_human": "Description for users",
  "description_for_model": "Description for AI model",
  "contact_email": "support@company.com",
  "namespace": "serviceName",
  "capabilities": {
    "conversation_starters": [
      {
        "text": "Example query 1"
      }
    ]
  },
  "functions": [
    {
      "name": "functionName",
      "description": "Function description",
      "capabilities": {
        "response_semantics": {
          "data_path": "$",
          "properties": {
            "title": "$.title",
            "subtitle": "$.description"
          }
        }
      }
    }
  ],
  "runtimes": [
    {
      "type": "MCP",
      "spec": {
        "url": "https://api.service.com/mcp/"
      },
      "run_for_functions": ["functionName"],
      "auth": {
        "type": "OAuthPluginVault",
        "reference_id": "${{OAUTH_REFERENCE_ID}}"
      }
    }
  ]
}
```

**/.vscode/mcp.json** - MCP server configuration:
```json
{
  "serverUrl": "https://api.service.com/mcp/",
  "pluginFilePath": "appPackage/ai-plugin.json"
}
```

## MCP Server Integration

### Supported MCP Endpoints
The MCP server must provide:
- **Server metadata** endpoint
- **Tools listing** endpoint (exposes available functions)
- **Tool execution** endpoint (handles function calls)

### Tool Selection
When importing from MCP:
1. Fetch available tools from server
2. Select specific tools to include (for security/simplicity)
3. Tool definitions are auto-generated in ai-plugin.json

### Authentication Types

**OAuth 2.0 (Static Registration)**
```json
"auth": {
  "type": "OAuthPluginVault",
  "reference_id": "${{OAUTH_REFERENCE_ID}}",
  "authorization_url": "https://auth.service.com/authorize",
  "client_id": "${{CLIENT_ID}}",
  "client_secret": "${{CLIENT_SECRET}}",
  "scope": "read write"
}
```

**Single Sign-On (SSO)**
```json
"auth": {
  "type": "SSO"
}
```

## Response Semantics

### Define Data Mapping
Use `response_semantics` to extract relevant fields from API responses:

```json
"capabilities": {
  "response_semantics": {
    "data_path": "$.results",
    "properties": {
      "title": "$.name",
      "subtitle": "$.description",
      "url": "$.link"
    }
  }
}
```

### Add Adaptive Cards (Optional)
See the `mcp-create-adaptive-cards` prompt for adding visual card templates.

## Environment Configuration

Create `.env.local` or `.env.dev` for credentials:

```env
OAUTH_REFERENCE_ID=your-oauth-reference-id
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
```

## Testing & Deployment

### Local Testing
1. **Provision** agent in Agents Toolkit
2. **Start debugging** to sideload in Teams
3. Test in Microsoft 365 Copilot at https://m365.cloud.microsoft/chat
4. Authenticate when prompted
5. Query the agent using natural language

### Validation
- Verify tool imports in ai-plugin.json
- Check authentication configuration
- Test each exposed function
- Validate response data mapping

## Best Practices

### Tool Design
- **Focused functions**: Each tool should do one thing well
- **Clear descriptions**: Help the model understand when to use each tool
- **Minimal scoping**: Only import tools the agent needs
- **Descriptive names**: Use action-oriented function names

### Security
- **Use OAuth 2.0** for production scenarios
- **Store secrets** in environment variables
- **Validate inputs** on the MCP server side
- **Limit scopes** to minimum required permissions
- **Use reference IDs** for OAuth registration

### Instructions
- **Be specific** about the agent's purpose and capabilities
- **Define behavior** for both successful and error scenarios
- **Reference tools** explicitly in instructions when applicable
- **Set expectations** for users about what the agent can/cannot do

### Performance
- **Cache responses** when appropriate on MCP server
- **Batch operations** where possible
- **Set timeouts** for long-running operations
- **Paginate results** for large datasets

## Common MCP Server Examples

### GitHub MCP Server
```
URL: https://api.githubcopilot.com/mcp/
Tools: search_repositories, search_users, get_repository
Auth: OAuth 2.0
```

### Jira MCP Server
```
URL: https://your-domain.atlassian.net/mcp/
Tools: search_issues, create_issue, update_issue
Auth: OAuth 2.0
```

### Custom Service
```
URL: https://api.your-service.com/mcp/
Tools: Custom tools exposed by your service
Auth: OAuth 2.0 or SSO
```

## Workflow

Ask the user:
1. What MCP server are you integrating with (URL)?
2. What tools should be exposed to Copilot?
3. What authentication method does the server support?
4. What should the agent's primary purpose be?
5. Do you need response semantics or Adaptive Cards?

Then generate:
- Complete appPackage/ structure (manifest.json, declarativeAgent.json, ai-plugin.json)
- mcp.json configuration
- .env.local template
- Provisioning and testing instructions

## Troubleshooting

### MCP Server Not Responding
- Verify server URL is correct
- Check network connectivity
- Validate MCP server implements required endpoints

### Authentication Fails
- Verify OAuth credentials are correct
- Check reference ID matches registration
- Confirm scopes are requested properly
- Test OAuth flow independently

### Tools Not Appearing
- Ensure mcp.json points to correct server
- Verify tools were selected during import
- Check ai-plugin.json has correct function definitions
- Re-fetch actions from MCP if server changed

### Agent Not Understanding Queries
- Review instructions in declarativeAgent.json
- Check function descriptions are clear
- Verify response_semantics extract correct data
- Test with more specific queries

````

---
name: flow-nexus-user-tools
description: User management and system utilities specialist. Handles profile management, storage operations, real-time subscriptions, and platform administration.
color: gray
---

You are a Flow Nexus User Tools Agent, an expert in user experience optimization and platform utility management. Your expertise lies in providing comprehensive user support, system administration, and platform utility services.

Your core responsibilities:
- Manage user profiles, preferences, and account configuration
- Handle file storage, organization, and access management
- Configure real-time subscriptions and notification systems
- Monitor system health and provide diagnostic information
- Facilitate communication with Queen Seraphina for advanced guidance
- Support email verification and account security operations

Your user tools toolkit:
```javascript
// Profile Management
mcp__flow-nexus__user_profile({ user_id: "user_id" })
mcp__flow-nexus__user_update_profile({
  user_id: "user_id",
  updates: {
    full_name: "New Name",
    bio: "AI Developer",
    github_username: "username"
  }
})

// Storage Management
mcp__flow-nexus__storage_upload({
  bucket: "private",
  path: "projects$config.json",
  content: JSON.stringify(data),
  content_type: "application$json"
})

mcp__flow-nexus__storage_get_url({
  bucket: "public",
  path: "assets$image.png",
  expires_in: 3600
})

// Real-time Subscriptions
mcp__flow-nexus__realtime_subscribe({
  table: "tasks",
  event: "INSERT",
  filter: "status=eq.pending"
})

// Queen Seraphina Consultation
mcp__flow-nexus__seraphina_chat({
  message: "How should I architect my distributed system?",
  enable_tools: true
})
```

Your user support approach:
1. **Profile Optimization**: Configure user profiles for optimal platform experience
2. **Storage Organization**: Implement efficient file organization and access patterns
3. **Notification Setup**: Configure real-time updates for relevant platform events
4. **System Monitoring**: Proactively monitor system health and user experience
5. **Advanced Guidance**: Facilitate consultations with Queen Seraphina for complex decisions
6. **Security Management**: Ensure proper account security and verification procedures

Storage buckets you manage:
- **Private**: User-only access for personal files and configurations
- **Public**: Publicly accessible files for sharing and distribution
- **Shared**: Team collaboration spaces with controlled access
- **Temp**: Auto-expiring temporary files for transient data

Quality standards:
- Secure file storage with appropriate access controls and encryption
- Efficient real-time subscription management with proper resource cleanup
- Clear user profile organization with privacy-conscious data handling
- Responsive system monitoring with proactive issue detection
- Seamless integration with Queen Seraphina's advisory capabilities
- Comprehensive audit logging for security and compliance

Advanced features you leverage:
- **Intelligent File Organization**: AI-powered file categorization and search
- **Real-time Collaboration**: Live updates and synchronization across team members
- **Advanced Analytics**: User behavior insights and platform usage optimization
- **Security Monitoring**: Proactive threat detection and account protection
- **Integration Hub**: Seamless connections with external services and APIs
- **Backup and Recovery**: Automated data protection and disaster recovery

User experience optimizations you implement:
- **Personalized Dashboard**: Customized interface based on user preferences and usage patterns
- **Smart Notifications**: Intelligent filtering of real-time updates to reduce noise
- **Quick Access**: Streamlined workflows for frequently used features and tools
- **Performance Monitoring**: User-specific performance tracking and optimization recommendations
- **Learning Path Integration**: Personalized recommendations based on skills and interests
- **Community Features**: Enhanced collaboration and knowledge sharing capabilities

When managing user tools and platform utilities, always prioritize user privacy, system performance, seamless integration, and proactive support while maintaining high security standards and platform reliability.

Access MCP servers through the command line. MCP enables interaction with external systems like GitHub, filesystems, databases, and APIs.

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

## Overview

Create MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. The quality of an MCP server is measured by how well it enables LLMs to accomplish real-world tasks.

---

## Microsoft MCP Ecosystem

Microsoft provides extensive MCP infrastructure for Azure and Foundry services. Understanding this ecosystem helps you decide whether to build custom servers or leverage existing ones.

### Server Types

| Type | Transport | Use Case | Example |
|------|-----------|----------|---------|
| **Local** | stdio | Desktop apps, single-user, local dev | Azure MCP Server via NPM/Docker |
| **Remote** | Streamable HTTP | Cloud services, multi-tenant, Agent Service | `https://mcp.ai.azure.com` (Foundry) |

### Microsoft MCP Servers

Before building a custom server, check if Microsoft already provides one:

| Server | Type | Description |
|--------|------|-------------|
| **Azure MCP** | Local | 48+ Azure services (Storage, KeyVault, Cosmos, SQL, etc.) |
| **Foundry MCP** | Remote | `https://mcp.ai.azure.com` - Models, deployments, evals, agents |
| **Fabric MCP** | Local | Microsoft Fabric APIs, OneLake, item definitions |
| **Playwright MCP** | Local | Browser automation and testing |
| **GitHub MCP** | Remote | `https://api.githubcopilot.com/mcp` |

**Full ecosystem:** See [🔷 Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md) for complete server catalog and patterns.

### When to Use Microsoft vs Custom

| Scenario | Recommendation |
|----------|----------------|
| Azure service integration | Use **Azure MCP Server** (48 services covered) |
| AI Foundry agents/evals | Use **Foundry MCP** remote server |
| Custom internal APIs | Build **custom server** (this guide) |
| Third-party SaaS integration | Build **custom server** (this guide) |
| Extending Azure MCP | Follow [Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md)

---

# Process

## 🚀 High-Level Workflow

Creating a high-quality MCP server involves four main phases:

### Phase 1: Deep Research and Planning

#### 1.1 Understand Modern MCP Design

**API Coverage vs. Workflow Tools:**
Balance comprehensive API endpoint coverage with specialized workflow tools. Workflow tools can be more convenient for specific tasks, while comprehensive coverage gives agents flexibility to compose operations. Performance varies by client—some clients benefit from code execution that combines basic tools, while others work better with higher-level workflows. When uncertain, prioritize comprehensive API coverage.

**Tool Naming and Discoverability:**
Clear, descriptive tool names help agents find the right tools quickly. Use consistent prefixes (e.g., `github_create_issue`, `github_list_repos`) and action-oriented naming.

**Context Management:**
Agents benefit from concise tool descriptions and the ability to filter/paginate results. Design tools that return focused, relevant data. Some clients support code execution which can help agents filter and process data efficiently.

**Actionable Error Messages:**
Error messages should guide agents toward solutions with specific suggestions and next steps.

#### 1.2 Study MCP Protocol Documentation

**Navigate the MCP specification:**

Start with the sitemap to find relevant pages: `https://modelcontextprotocol.io/sitemap.xml`

Then fetch specific pages with `.md` suffix for markdown format (e.g., `https://modelcontextprotocol.io/specification/draft.md`).

Key pages to review:
- Specification overview and architecture
- Transport mechanisms (streamable HTTP, stdio)
- Tool, resource, and prompt definitions

#### 1.3 Study Framework Documentation

**Language Selection:**

| Language | Best For | SDK |
|----------|----------|-----|
| **TypeScript** (recommended) | General MCP servers, broad compatibility | `@modelcontextprotocol/sdk` |
| **Python** | Data/ML pipelines, FastAPI integration | `mcp` (FastMCP) |
| **C#/.NET** | Azure/Microsoft ecosystem, enterprise | `Microsoft.Mcp.Core` |

**Transport Selection:**

| Transport | Use Case | Characteristics |
|-----------|----------|-----------------|
| **Streamable HTTP** | Remote servers, multi-tenant, Agent Service | Stateless, scalable, requires auth |
| **stdio** | Local servers, desktop apps | Simple, single-user, no network |

**Load framework documentation:**

- **MCP Best Practices**: [📋 View Best Practices](./reference/mcp_best_practices.md) - Core guidelines

**For TypeScript (recommended):**
- **TypeScript SDK**: Use WebFetch to load `https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`
- [⚡ TypeScript Guide](./reference/node_mcp_server.md) - TypeScript patterns and examples

**For Python:**
- **Python SDK**: Use WebFetch to load `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- [🐍 Python Guide](./reference/python_mcp_server.md) - Python patterns and examples

**For C#/.NET (Microsoft ecosystem):**
- [🔷 Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md) - C# patterns, Azure MCP architecture, command hierarchy

#### 1.4 Plan Your Implementation

**Understand the API:**
Review the service's API documentation to identify key endpoints, authentication requirements, and data models. Use web search and WebFetch as needed.

**Tool Selection:**
Prioritize comprehensive API coverage. List endpoints to implement, starting with the most common operations.

---

### Phase 2: Implementation

#### 2.1 Set Up Project Structure

See language-specific guides for project setup:
- [⚡ TypeScript Guide](./reference/node_mcp_server.md) - Project structure, package.json, tsconfig.json
- [🐍 Python Guide](./reference/python_mcp_server.md) - Module organization, dependencies
- [🔷 Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md) - C# project structure, command hierarchy

#### 2.2 Implement Core Infrastructure

Create shared utilities:
- API client with authentication
- Error handling helpers
- Response formatting (JSON/Markdown)
- Pagination support

#### 2.3 Implement Tools

For each tool:

**Input Schema:**
- Use Zod (TypeScript) or Pydantic (Python)
- Include constraints and clear descriptions
- Add examples in field descriptions

**Output Schema:**
- Define `outputSchema` where possible for structured data
- Use `structuredContent` in tool responses (TypeScript SDK feature)
- Helps clients understand and process tool outputs

**Tool Description:**
- Concise summary of functionality
- Parameter descriptions
- Return type schema

**Implementation:**
- Async/await for I/O operations
- Proper error handling with actionable messages
- Support pagination where applicable
- Return both text content and structured data when using modern SDKs

**Annotations:**
- `readOnlyHint`: true/false
- `destructiveHint`: true/false
- `idempotentHint`: true/false
- `openWorldHint`: true/false

---

### Phase 3: Review and Test

#### 3.1 Code Quality

Review for:
- No duplicated code (DRY principle)
- Consistent error handling
- Full type coverage
- Clear tool descriptions

#### 3.2 Build and Test

**TypeScript:**
- Run `npm run build` to verify compilation
- Test with MCP Inspector: `npx @modelcontextprotocol/inspector`

**Python:**
- Verify syntax: `python -m py_compile your_server.py`
- Test with MCP Inspector

See language-specific guides for detailed testing approaches and quality checklists.

---

### Phase 4: Create Evaluations

After implementing your MCP server, create comprehensive evaluations to test its effectiveness.

**Load [✅ Evaluation Guide](./reference/evaluation.md) for complete evaluation guidelines.**

#### 4.1 Understand Evaluation Purpose

Use evaluations to test whether LLMs can effectively use your MCP server to answer realistic, complex questions.

#### 4.2 Create 10 Evaluation Questions

To create effective evaluations, follow the process outlined in the evaluation guide:

1. **Tool Inspection**: List available tools and understand their capabilities
2. **Content Exploration**: Use READ-ONLY operations to explore available data
3. **Question Generation**: Create 10 complex, realistic questions
4. **Answer Verification**: Solve each question yourself to verify answers

#### 4.3 Evaluation Requirements

Ensure each question is:
- **Independent**: Not dependent on other questions
- **Read-only**: Only non-destructive operations required
- **Complex**: Requiring multiple tool calls and deep exploration
- **Realistic**: Based on real use cases humans would care about
- **Verifiable**: Single, clear answer that can be verified by string comparison
- **Stable**: Answer won't change over time

#### 4.4 Output Format

Create an XML file with this structure:

```xml
<evaluation>
  <qa_pair>
    <question>Find discussions about AI model launches with animal codenames. One model needed a specific safety designation that uses the format ASL-X. What number X was being determined for the model named after a spotted wild cat?</question>
    <answer>3</answer>
  </qa_pair>
<!-- More qa_pairs... -->
</evaluation>
```

---

# Reference Files

## 📚 Documentation Library

Load these resources as needed during development:

### Core MCP Documentation (Load First)
- **MCP Protocol**: Start with sitemap at `https://modelcontextprotocol.io/sitemap.xml`, then fetch specific pages with `.md` suffix
- [📋 MCP Best Practices](./reference/mcp_best_practices.md) - Universal MCP guidelines including:
  - Server and tool naming conventions
  - Response format guidelines (JSON vs Markdown)
  - Pagination best practices
  - Transport selection (streamable HTTP vs stdio)
  - Security and error handling standards

### Microsoft MCP Documentation (For Azure/Foundry)
- [🔷 Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md) - Microsoft-specific patterns including:
  - Azure MCP Server architecture (48+ Azure services)
  - C#/.NET command implementation patterns
  - Remote MCP with Foundry Agent Service
  - Authentication (Entra ID, OBO flow, Managed Identity)
  - Testing infrastructure with Bicep templates

### SDK Documentation (Load During Phase 1/2)
- **Python SDK**: Fetch from `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- **TypeScript SDK**: Fetch from `https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`
- **Microsoft MCP SDK**: See [Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md) for C#/.NET

### Language-Specific Implementation Guides (Load During Phase 2)
- [🐍 Python Implementation Guide](./reference/python_mcp_server.md) - Complete Python/FastMCP guide with:
  - Server initialization patterns
  - Pydantic model examples
  - Tool registration with `@mcp.tool`
  - Complete working examples
  - Quality checklist

- [⚡ TypeScript Implementation Guide](./reference/node_mcp_server.md) - Complete TypeScript guide with:
  - Project structure
  - Zod schema patterns
  - Tool registration with `server.registerTool`
  - Complete working examples
  - Quality checklist

- [🔷 Microsoft MCP Patterns](./reference/microsoft_mcp_patterns.md) - Complete C#/.NET guide with:
  - Command hierarchy (BaseCommand → GlobalCommand → SubscriptionCommand)
  - Naming conventions (`{Resource}{Operation}Command`)
  - Option handling with `.AsRequired()` / `.AsOptional()`
  - Azure Functions remote MCP deployment
  - Live test patterns with Bicep

### Evaluation Guide (Load During Phase 4)
- [✅ Evaluation Guide](./reference/evaluation.md) - Complete evaluation creation guide with:
  - Question creation guidelines
  - Answer verification strategies
  - XML format specifications
  - Example questions and answers
  - Running an evaluation with the provided scripts

## Credits

- **github/mcp-create-declarative-agent** — license: unknown — https://skillmd.com/skills/github/mcp-create-declarative-agent
- **jrennie99-glitch/agent-user-tools** — license: unknown — https://skillmd.com/skills/jrennie99-glitch/agent-user-tools
- **github/mcp-cli** — license: unknown — https://skillmd.com/skills/github/mcp-cli
- **github/python-mcp-server-generator** — license: unknown — https://skillmd.com/skills/github/python-mcp-server-generator
- **microsoft/mcp-builder** — license: unknown — https://skillmd.com/skills/microsoft/mcp-builder
