"""One owner for the daemons that are NOT work in a seat's worktree.

fleet regression taught the dispatcher not to count MCP servers as agent work; fleet regression taught `fleet-seat-live` the same for a
seat's own language server. Both now read the list from here, so the next daemon is added once.

A daemon is recognised by its COMMAND LINE, never by being `node`: `fleet-seat-live` still refuses a real `node build.js`,
`npm run build`, `npx jest`, `tsx script.mts`, `vite build` or `playwright test` with its cwd in the tree.
"""
import re
import sys

# MCP servers and CLI integrations that idle in a worktree (fleet regression).
MCP_PATTERN = r"\bmcp(?:\s+(?:start|serve)|$)|codegraph|mcp-server|-mcp\b|mcp[-_]|server-(github|sequential)"

# Language servers an editor/plugin keeps running for a seat (fleet regression).
LSP_PATTERN = (
    r"typescript-language-server|tsserver|typingsInstaller"
    r"|language-server|langserver|pyright|pylsp|jedi-language-server"
    r"|rust-analyzer|gopls|clangd|intelephense|lua-language-server"
    r"|bash-language-server|yaml-language-server|json-language-server"
    r"|vscode-[a-z0-9-]+|eslint_d|stylelint-language-server|emmet-ls"
)

DAEMON_PATTERN = f"(?:{MCP_PATTERN})|(?:{LSP_PATTERN})"
DAEMON = re.compile(DAEMON_PATTERN, re.IGNORECASE)


def is_daemon(command: str) -> bool:
    """True when the command line names a language server or an MCP daemon."""
    return bool(DAEMON.search(command or ''))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--match':
        raise SystemExit(0 if is_daemon(sys.argv[2]) else 1)
    print(__doc__.strip())
    raise SystemExit(2)
