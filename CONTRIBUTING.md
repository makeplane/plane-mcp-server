# Contributing to Plane MCP Server

Thank you for showing an interest in contributing to Plane MCP Server! All kinds of contributions are valuable to us. In this guide, we will cover how you can quickly onboard and make your first contribution.

## Submitting an issue

Before submitting a new issue, please search the [issues](https://github.com/makeplane/plane-mcp-server/issues) tab. Maybe an issue or discussion already exists and might inform you of workarounds. Otherwise, you can give new information.

While we want to fix all the [issues](https://github.com/makeplane/plane-mcp-server/issues), before fixing a bug we need to be able to reproduce and confirm it. Please provide us with a minimal reproduction scenario. Having a live, reproducible scenario gives us the information without asking questions back & forth with additional questions like:

- Python version and OS
- MCP client being used (Claude Desktop, etc.)
- Transport method (stdio, HTTP, SSE)
- A use-case that fails

Without said minimal reproduction, we won't be able to investigate all [issues](https://github.com/makeplane/plane-mcp-server/issues), and the issue might not be resolved.

You can open a new issue with this [issue form](https://github.com/makeplane/plane-mcp-server/issues/new).

### Naming conventions for issues

When opening a new issue, please use a clear and concise title that follows this format:

- For bugs: `Bug: [short description]`
- For features: `Feature: [short description]`
- For improvements: `Improvement: [short description]`
- For documentation: `Docs: [short description]`

**Examples:**

- `Bug: OAuth token refresh fails with Redis backend`
- `Feature: Add support for custom fields in work items`
- `Improvement: Better error messages for missing environment variables`
- `Docs: Clarify PAT token setup for HTTP transport`

This helps us triage and manage issues more efficiently.

## Project setup

### Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (fast Python package installer)
- A Plane instance (cloud or self-hosted) with an API key for testing

### Setup the project

1. Clone the repo

```bash
git clone https://github.com/makeplane/plane-mcp-server.git
cd plane-mcp-server
```

2. Install dependencies

```bash
uv pip install -e ".[dev]"
```

3. Configure environment variables

```bash
cp .env.test .env.test.local
# Edit .env.test.local with your Plane API credentials
```

4. Run the server locally (stdio mode)

```bash
PLANE_API_KEY="your-api-key" PLANE_WORKSPACE_SLUG="your-workspace" python -m plane_mcp stdio
```

5. Run the tests

```bash
export $(cat .env.test.local | xargs) && pytest tests/ -v
```

## Missing a Feature?

If a feature is missing, you can directly _request_ a new one [here](https://github.com/makeplane/plane-mcp-server/issues/new). If you would like to _implement_ it, an issue with your proposal must be submitted first, to be sure that we can use it. Please consider the guidelines given below.

## Coding guidelines

To ensure consistency throughout the source code, please keep these rules in mind as you are working:

- All features or bug fixes must be tested by one or more specs (unit-tests).
- We format with [Black](https://black.readthedocs.io/) (line length: 100) and lint with [Ruff](https://docs.astral.sh/ruff/) (rules: E, F, I, UP, B; line length: 100).
- Use Python 3.10+ union syntax (`str | None` instead of `Optional[str]`).
- Tool functions must follow the existing pattern: use `@mcp.tool()` decorator, accept typed parameters, and return Pydantic models from `plane-sdk`.
- Include docstrings with `Args` and `Returns` sections for all new tools.

### Adding a new tool

1. Create or update a module in `plane_mcp/tools/` following the existing domain organization.
2. Implement a `register_*_tools(mcp: FastMCP)` function.
3. Register it in `plane_mcp/tools/__init__.py`.
4. Add tests covering the new tool.

### Running checks

```bash
# Format
black plane_mcp/

# Lint
ruff check plane_mcp/

# Test
pytest
```

## Ways to contribute

- Try the Plane MCP Server with different MCP clients and give feedback
- Help with open [issues](https://github.com/makeplane/plane-mcp-server/issues) or [create your own](https://github.com/makeplane/plane-mcp-server/issues/new)
- Add new tools for Plane API endpoints
- Improve existing tool documentation and descriptions
- Report bugs
- Propose features

## Need help? Questions and suggestions

Questions, suggestions, and thoughts are most welcome. We can also be reached in our [Discord Server](https://discord.com/invite/A92xrEGCge).
