# Plane plugin for Grok Build

Connects Grok Build to [Plane](https://plane.so) through the hosted
[Plane MCP server](https://github.com/makeplane/plane-mcp-server).

Ask for work in the terms you already use — "what's blocking the current cycle",
"file a bug in Web for the login redirect", "move everything unstarted to next
sprint" — and Grok reads and writes it in Plane directly.

## What it provides

One MCP server, `plane`, advertising **30 tools** covering **204 operations** —
one tool per Plane resource, each taking an `action` that selects the operation.

| Area | Resources |
|---|---|
| Work | work items, comments, links, attachments, relations, properties, activity, work logs |
| Planning | projects, cycles, modules, milestones, initiatives, states, labels, estimates |
| Releases | releases, release tags, release labels |
| Customers | customers, customer requests, customer properties |
| Workspace | workspace, members, pages, templates, intake, collections |

Work item lists, counts and searches accept **PQL**, Plane's query language:

```
workitem(action="list", project_id=..., pql='state__group = "started" AND priority = "urgent"')
```

The `get_pql_reference` tool serves the full syntax on demand, so Grok can look
up operators and worked examples itself rather than guessing at filters.

## Setup

Install the plugin and connect. On first use Grok opens Plane's OAuth flow in a
browser; approve it and pick your workspace. There is nothing to configure and no
API key to paste — the plugin ships only an endpoint URL.

## Network endpoints

| Host | Purpose |
|---|---|
| `mcp.plane.so` | The MCP server itself, and the OAuth authorization, token and registration endpoints |
| `api.plane.so` | Reached by the MCP server, server-side, to serve tool calls. The plugin never contacts it directly |

The plugin reads no files, no environment variables and no credentials from the
machine it runs on. Authorization lives entirely in the OAuth token the client
obtains and sends; the token is scoped `read` and `write` against the single
workspace approved during sign-in, and can be revoked from Plane at any time.

## Self-hosted Plane

The hosted endpoint serves Plane Cloud. For a self-hosted instance, run the
server locally over stdio against your own API URL, as described in
[Quick start](../README.md#quick-start) and
[Transports](../README.md#transports) in the root README.

## License

MIT — see [LICENSE](../LICENSE).
