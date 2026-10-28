# Changelog

## Unreleased

- `stack.deploy()` deprecates the `orchestrator` argument. Passing a non-`None`
  value emits one `DeprecationWarning` at the caller's location before the CLI
  runs and forwards the value unchanged, including an empty string. Omitting
  the argument or passing `None` emits no warning and omits the flag. Omit the
  argument in new and updated calls. CLI errors propagate without fallback.
  This change retains compatibility forwarding; the warning window and removal
  release remain undecided by the maintainer. Any removal is a separate future
  change.
