# Hippocampus OpenCode plugin template

`mem0.ts` is the maintained source template for the Hippocampus OpenCode
plugin. Copy it into an OpenCode configuration directory to add durable memory
retrieval, explicit memory tools, and compaction context to OpenCode sessions.
The file in this repository is not installed automatically.

For implementation details and environment-variable behavior, see
[`functional.md`](./functional.md).

## Prerequisite: configure a memory server

The plugin needs a running Hippocampus-compatible REST server. Set
`MEM0_SERVER_URL` in the environment that starts OpenCode:

```sh
export MEM0_SERVER_URL="http://localhost:8000"
```

Use the URL for your deployment. Although the source currently has a fallback
URL (`http://100.75.83.103:18000`), that address is environment-specific and is
not a portable local default. Configure `MEM0_SERVER_URL` explicitly.

Set `MEM0_BACKEND_MODE=backend` when the server supports `POST /retrieve`.
Otherwise, omit it or set it to `legacy` to use `POST /search`.

## Project installation

Copy the template into the project's OpenCode plugin directory:

```sh
mkdir -p .opencode/plugins
cp plugin-defs/opencode/mem0.ts .opencode/plugins/mem0.ts
```

Add the plugin's direct dependencies to `.opencode/package.json`, merging them
with any existing fields:

```json
{
  "dependencies": {
    "@opencode-ai/plugin": "1.17.20",
    "@opencode-ai/sdk": "1.17.20"
  }
}
```

## Global installation

Copy the template into the global OpenCode plugin directory:

```sh
mkdir -p ~/.config/opencode/plugins
cp plugin-defs/opencode/mem0.ts ~/.config/opencode/plugins/mem0.ts
```

Add the same pinned dependencies to
`~/.config/opencode/package.json`, again merging with existing fields:

```json
{
  "dependencies": {
    "@opencode-ai/plugin": "1.17.20",
    "@opencode-ai/sdk": "1.17.20"
  }
}
```

OpenCode runs `bun install` at startup to install local-plugin dependencies
from the matching configuration directory. The `package.json` and `bun.lock`
beside this README are only the repository's Bun test harness. Do not copy that
lockfile into an OpenCode configuration directory as the installation lock.

You must **restart the OpenCode process/session after copying or changing the
plugin or its target-directory dependencies**. A running session keeps the
plugin version that it loaded at startup.

See the official OpenCode documentation for
[plugins](https://opencode.ai/docs/plugins/) and
[configuration](https://opencode.ai/docs/config/).

## Memory tool modes

The plugin exposes one `mem0` tool with five modes:

- `add`: stores high-signal content after privacy, quality, metadata, and
  optional anchor processing.
- `search`: queries one memory scope through `/search` in legacy mode or
  `/retrieve` in backend mode.
- `list`: lists memories for the selected identity scope.
- `forget`: deletes one memory by ID.
- `help`: reports the available modes, scopes, backend mode, and search
  endpoint without changing memory.

The plugin also retrieves memory automatically for selected chat turns and
during session compaction. It supports only the operations above; other server
administration and record-management endpoints are not exposed as tool modes.
