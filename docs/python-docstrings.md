# Python docstring standard

Follow this standard for new or changed module, class, function, and method
docstrings. Leave unrelated existing docstrings alone. Use Google style, matching
`docstring_style: google` in `docs/mkdocs.yml`.
Simple contracts can use one-line docstrings. Document nontrivial private contracts
too, especially ownership, ordering, or lifecycle rules that callers must know.

## Layout and summaries

- Use triple-double quotes (`"""`) and place the docstring as the first statement.
- End the summary with a period. Functions and methods use an imperative verb,
  such as "Return the selected items." Modules and classes use a concise purpose
  description, such as "Helpers for formatting labels."
- For multiline docstrings, put a blank line after the summary and between
  paragraphs or sections. Put the closing quotes on their own line.
- Align section headings with the summary's indentation inside the docstring.
  Indent section entries four spaces and wrapped entry text four spaces further.
- Use plain Google headings: `Args:`, `Returns:`, `Yields:`, `Raises:`,
  `Examples:`, and, for classes, `Attributes:`. Omit irrelevant or empty sections.
  Existing `Parameters:` and `# Raises` forms are not the canonical syntax.

## Modules

Place the module docstring after any optional shebang and encoding declaration,
before `from __future__` statements, other imports, or executable statements.
Describe the module's purpose, main responsibilities, and relevant boundaries.
Explain actual import-time work when relevant; do not promise side-effect-free
imports without verifying that behavior. Avoid a catalog of every symbol.

## Classes and constructors

Describe the class's responsibility and the contract shared by its instances.
Include lifecycle or ownership constraints when they affect how instances are used.
Use `Attributes:` only for useful public instance or class state. Describe its
meaning and relevant mutability; do not list every implementation field.
Keep attribute types in annotations rather than repeating them in prose.
Document constructor parameters and failures on `__init__`, following the function
rules below: `merge_init_into_class: false` keeps its documentation separate.
Do not duplicate the constructor's `Args:` prose in the class overview.

## Function and method arguments

Use `Args:` entries with exact signature names and in signature order; omit
`self` and `cls`. Preserve `*args` and `**kwargs` spelling when documenting them.
Keep types in annotations; entries explain semantics, constraints, and interactions
instead of repeating the annotation or merely restating the parameter name.
Explain meaningful defaults, what `None` means, and units such as seconds or bytes.
Distinguish an omitted option from an explicit empty or zero value when they differ.
For overloaded APIs, keep shared behavior in the implementation docstring and
explain input-dependent behavior in prose without copying every overload signature.

## Results and failures

- Use `Returns:` for a returned value, describing its meaning and relevant shape,
  ordering, or ownership. Types belong in the return annotation.
- Use `Yields:` for values produced by a generator, including asynchronous
  generators. A function that returns an iterator without yielding uses `Returns:`;
  describe the iterator's elements and consumption contract there.
- Omit `Returns:` for functions that only return `None`, including `__init__`.
  If `None` is one possible result, explain exactly when it occurs.
- Describe each conditional result and its controlling arguments, including modes
  that return captured output, a resource, or a stream. Match the overloads.
- Use `Raises:` entries as `ExceptionName: Condition that triggers the exception.`
  Include intentional failures and relevant propagated exceptions callers can act
  on. State specific conditions, not "If an error occurs."
- Do not enumerate incidental implementation exceptions or invent guarantees about
  exception types. Omit `Raises:` when there is no relevant failure contract.

## Execution and lifecycle contracts

When relevant, explain in prose after the summary:

- Whether work starts at the call, on iteration, or on entering a context manager;
  whether calls or iteration block, and when work is considered complete.
- Timeout scope and units, including what `None` means, when timing starts,
  and what happens to outstanding work when the deadline expires.
- Who owns returned streams or resources, how to close or release them, and what
  exhaustion, early exit, cancellation, or context-manager exit actually cleans up.
- External side effects, resource mutation or removal, and prerequisites callers
  need, including Docker CLI, daemon, or plugin availability when applicable.

PyWhales wraps the Docker CLI. Distinguish the local CLI subprocess from resources
or jobs owned by the selected Docker daemon, which may be remote. Do not imply
that closing a stream or timing out a CLI process stops daemon-side work unless
the implementation guarantees it. Check the actual execution and cleanup paths.

## Examples

`Examples:` is optional. Prefer short, deterministic examples with stable output
and no credentials or confidential payloads. Make prerequisites and side effects
explicit for examples that need Docker, a daemon, or plugins; use a disposable
daemon for destructive examples and explain cleanup. Do not imply they run offline.
Keep examples executable and consistent with annotations and documented failures.

## Complete example

This small module shows placement and all four docstring scopes without Docker.

```python
"""Helpers for formatting labels.

Keep label formatting separate from external resource operations.
"""

from __future__ import annotations


def format_label(value: str, *, prefix: str = "") -> str:
    """Return a prefixed label with surrounding value whitespace removed.

    Args:
        value: Label text; must contain a non-whitespace character.
        prefix: Text prepended unchanged. Defaults to no prefix.

    Returns:
        The prefix followed by the stripped label text.

    Raises:
        ValueError: If value is empty or contains only whitespace.

    Examples:
        >>> format_label(" sample ", prefix="job:")
        'job:sample'
        >>> format_label("sample")
        'sample'
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be blank")
    return prefix + stripped


class LabelFormatter:
    """Reusable label formatting settings.

    Attributes:
        prefix: Text prepended unchanged to formatted labels.
    """

    prefix: str

    def __init__(self, prefix: str = "") -> None:
        """Store the prefix for subsequent formatting.

        Args:
            prefix: Initial prefix; the default empty string adds nothing.
        """
        self.prefix = prefix

    def format(self, value: str) -> str:
        """Return the label using format_label with the stored prefix."""
        return format_label(value, prefix=self.prefix)
```
