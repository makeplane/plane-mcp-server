# Plane MCP Server - Pages API Setup Guide

## Quick Start

### 1. Install
```bash
npm install -g @makeplane/plane-mcp-server
```

### 2. Configure Your MCP Client

#### For Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["-y", "@makeplane/plane-mcp-server"],
      "env": {
        "PLANE_API_KEY": "your-api-key-here",
        "PLANE_WORKSPACE_SLUG": "your-workspace-slug",
        "PLANE_API_HOST_URL": "https://api.plane.so/",
        "PLANE_EMAIL": "your-email@example.com",
        "PLANE_PASSWORD": "your-password"
      }
    }
  }
}
```

#### For VSCode

Edit `~/.vscode/mcp.json`:

```json
{
  "servers": {
    "plane": {
      "command": "npx",
      "args": ["-y", "@makeplane/plane-mcp-server"],
      "env": {
        "PLANE_API_KEY": "your-api-key-here",
        "PLANE_WORKSPACE_SLUG": "your-workspace-slug",
        "PLANE_API_HOST_URL": "https://api.plane.so/",
        "PLANE_EMAIL": "your-email@example.com",
        "PLANE_PASSWORD": "your-password"
      }
    }
  }
}
```

### 3. Environment Variables

#### Required for All Features
- `PLANE_API_KEY` - Get from Workspace Settings > API Tokens in Plane
- `PLANE_WORKSPACE_SLUG` - Found in your Plane workspace URL

#### Optional
- `PLANE_API_HOST_URL` - Defaults to `https://api.plane.so/` (set for self-hosted)

#### For Pages API Only
- `PLANE_EMAIL` - Your Plane account email
- `PLANE_PASSWORD` - Your Plane account password

**Note:** If you don't set email/password, you can still use Pages tools by calling `plane_login` manually in your conversation.

## Authentication Methods

### API Key Authentication
Used for most tools:
- Projects, Issues, Modules, Cycles
- Labels, States, Issue Types
- Work Logs

### Session Authentication
Required for Pages API tools:
- All 18 Pages API tools
- Use `plane_login` tool or set `PLANE_EMAIL`/`PLANE_PASSWORD` env vars

## Testing Your Setup

### Test API Key Authentication
Ask your AI assistant:
```
"List my Plane projects"
```

### Test Pages Authentication
Ask your AI assistant:
```
"Login to Plane with my credentials and list pages in project <project-id>"
```

## Troubleshooting

### Pages API Not Working
1. Verify you've called `plane_login` or set `PLANE_EMAIL`/`PLANE_PASSWORD`
2. Check your password is correct (no SSO - must be email/password account)
3. For cloud instances, ensure you're using `https://api.plane.so/`

### API Key Authentication Failing
1. Verify your API key is valid in Plane settings
2. Check `PLANE_WORKSPACE_SLUG` matches your workspace URL
3. For self-hosted, verify `PLANE_API_HOST_URL` is correct

## Self-Hosted Plane

Set `PLANE_API_HOST_URL` to your instance:
```json
{
  "env": {
    "PLANE_API_HOST_URL": "http://your-plane-instance.com/",
    ...
  }
}
```

## Security Notes

- Store credentials securely in your MCP client config
- Don't share your API keys or passwords
- Use environment-specific credentials (dev vs prod)
- Consider using separate API keys for different tools

## Getting Help

- [Plane Documentation](https://docs.plane.so)
- [MCP Documentation](https://modelcontextprotocol.io)
- [GitHub Issues](https://github.com/makeplane/plane-mcp-server/issues)
