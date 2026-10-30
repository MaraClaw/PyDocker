# Image Component Guide

## Local Navigation

- `cli_wrapper.py`: `Image`, `ImageCLI`, batch inspection, transfers, copy helpers, and archives.
- `models.py`: inspect snapshots; `config` and `container_config` reuse `ContainerConfig`,
  while `ImageGraphDriver.data` accepts arbitrary JSON values, including scalars and null.
- `streams.py`: `MultiImageStream`, plus the `ImageStream` and `EmptyImageStream` type aliases.

## Pull And Push Contracts

- Deduplicate iterable references in first-seen order before choosing single or multiple transfers.
  An iterable pull still returns a list without streaming, even with one unique reference.
- `quiet=True` with `stream_logs=True` raises `ValueError` for both pull and push.
- Nonstreamed transfers are eager; multiple unique references use a four-worker `ThreadPool`
  through `streams.py::map_in_contexts`. Each task enters its own `copy_context()` made in the
  caller's thread; never share one `Context` across workers. Inside `observation()`, `quiet=False`
  raises the same `ValueError` for one reference or many.
  Pull returns `Image` for a string or an ordered list for an iterable; push returns `None`.
- `pull([])` returns `[]`, or an empty generator with `stream_logs=True`; no child CLI starts.
  `push([])` returns `None` even with streaming enabled. Preserve this asymmetry.
- Push batch-inspects deduplicated references before starting any transfer, including streamed ones.
- At public `pull()`/`push()` entry, select non-`None` `image_stream_buffer_bytes`, otherwise
  `stream_buffer_bytes`, once before consuming references or running push preflight. Keep the
  independent override unchanged; don't write the fallback into config.

## Multi-Image Streams

- `MultiImageStream` starts workers on first consumption, not construction or context entry.
  It owns active child streams and admits at most four concurrent transfers. It snapshots the
  creating context and runs each worker in a fresh copy, so children keep the creator's
  observation scope even if iteration starts after its `with` block.
- A single unique reference uses an eager `MappedProcessStream`, including duplicate-only input.
- Records are `(image_reference, bytes)`; don't promise global ordering across images or pipes.
- The first worker error stops sibling work and queued transfers without waiting for consumption.
  Deliver that original error once through iteration, including after explicit `close()`.
- Snapshot the selected budget, including `None`, for the group queue and every eager, lazy, or queued
  child stream. Later budget mutations affect new public operations only. Pass `None` explicitly at
  the shared boundary so it stays unbounded. A record exceeding a configured budget raises `BufferError`.

## Inspection, Copy, And Archives

- Batch inspect must verify each snapshot's ID or normalized tag/digest aliases against its request.
  Positional pairing alone isn't identity validation; mismatches raise `ParsingError`.
- Normalization handles Docker Hub/library prefixes, implicit `:latest`, and tagged digest references.
  Keep typed inspect snapshots on returned images instead of fetching them again immediately.
- `copy_from()` copies through a temporary container; `copy_to()` copies into one and commits it,
  returning a new `Image` with optional `new_tag`. Both forward `pull`, defaulting to `"missing"`.
- `save()` rejects empty input and inspects references before saving. An output path returns `None`;
  omitted output returns an iterator of archive bytes. Don't treat it as a progress-log stream.
- Direct archive load/save launches go through `_prepare_archive_launch`, which snapshots the
  observation active at launch (first iteration for a lazy save) and applies its preflight; the
  scope reaches `ArchiveDeadline` only when one is active. `save()` payloads aren't response-charged.
- Path-based `load()` uses the general stream budget, not the pull/push override.
  Direct archive load/save subprocesses snapshot `raw_diagnostics` before launch for failure rendering;
  preserve archive payloads, exception classes, and raw attributes. Don't promise archive memory bounds.
- Automatic missing-image reference diagnostics require `raw_diagnostics=True`; fallback behavior is unchanged.

## Test Navigation

- `tests/pywhales/test_container_image_enum_json.py`: nested config and graph-driver JSON contracts.
- `tests/pywhales/components/test_image.py` contains mocked CLI and real child-process stream tests:
  `test_empty_image_operations_keep_existing_result_shapes`,
  `test_multiple_image_stream_has_lazy_owned_lifecycle`,
  `test_multiple_image_stream_keeps_first_original_error`,
  `test_batch_inspect_image_aliases_and_typed_snapshot`.
- The same file covers fallback/override snapshots before preflight, lazy queued children,
  backpressure, path-load budgets, and safe/raw direct-archive failures.
- The same component file covers copy policy forwarding with `test_copy_from_pull` and
  `test_copy_to_pull`; archive integration coverage includes `test_save_load` and
  `test_save_iterator_bytes`. Read fixtures before choosing daemon-free selections.
