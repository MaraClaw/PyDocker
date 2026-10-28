# Changelog

## 0.6.0

### Breaking Changes

- `stack.deploy()` no longer accepts `orchestrator`; omit the argument.
- Use `client_call` instead of `client_binary`.
- Use `compose_env_files` instead of `compose_env_file`.
- Container, image, network, volume, service, and secret filters now accept an
  iterable of `(key, value)` tuples instead of a mapping.
- Set `PYWHALES_DEBUG=1` to enable execution diagnostics.
