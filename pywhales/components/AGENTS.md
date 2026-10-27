# Command Groups

## Local Navigation

The 18 groups are `buildx`, `compose`, `config`, `container`, `context`, `image`,
`manifest`, `network`, `node`, `plugin`, `secret`, `service`, `stack`, `swarm`,
`system`, `task`, `trust`, and `volume`.

- Each group's `cli_wrapper.py` holds its CLI class and resource wrappers where present.
  Its `models.py`, when present, defines typed Docker wire data.
- Empty `__init__.py` files aren't public API entry points.
- For group-specific instructions, continue in `buildx/AGENTS.md`, `compose/AGENTS.md`,
  `container/AGENTS.md`, or `image/AGENTS.md` when working in those directories.

## Wrapper Changes

- Extend the group's existing `Command` and configured command prefix; reuse shared
  argument helpers instead of adding a separate command-building path.
- Keep resource-or-string inputs in the existing `Valid*` aliases and shared CLI
  conversion path. Don't replace resource conversion with ad hoc name/ID extraction.
- Forward modern options directly to the CLI. Don't add version probes or fallback
  invocations that silently drop options or change behavior.
- Model changes need representative wire JSON fixtures and static type contracts.
  Preserve existing wire aliases when adding fields or changing their types.

## Shared Contracts

- Use `_cleanup.py::resource_cleanup` in resource `__exit__` methods. It catches
  `BaseException` from cleanup and, when the body also failed, preserves both errors
  in a `BaseExceptionGroup`. Don't swallow either failure or narrow the catch.
- Config, Node, Secret, and Service inspect use `_swarm_inspect.py::parse_swarm_inspect_results`.
  Resolve reordered responses by exact ID, then exact name (hostname for nodes), then
  unique ID prefix. Validate identities, ambiguity, snapshot consistency, and requested
  multiplicity before associating any snapshot with a resource cache.
- `top.py` shares table parsing, but Docker top takes `ps_args` and Compose top selects
  services with grouped-container or flat-service output. Keep those grammars separate.
  Parsed tables support scalar columns and at most one final free-text field.
  Unsupported layouts raise `TopParseError`; use explicit `raw=True` instead of guessing.

## Test Navigation

- Group behavior: `tests/pywhales/components/test_<group>.py`; Buildx has its own subtree.
- Shared top parsing is covered through `tests/pywhales/components/test_container.py`
  and `tests/pywhales/components/test_compose.py`; root-alias coverage is in
  `tests/pywhales/test_docker_client.py`.
- Wire fixtures live in `tests/pywhales/components/jsons/`; static resource and top
  contracts live in `tests/pywhales/test_type_contracts.py`, with group-specific contracts
  alongside it for Buildx and Compose.
