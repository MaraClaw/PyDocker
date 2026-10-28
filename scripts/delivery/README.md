# Tested Delivery Chain

These helpers are contributor CI tools, not installed PyWhales APIs. They use
the existing locked Python/Pydantic environment and runner `curl`. Local tests
replace every external service with inert fixtures. Do not run publication,
release or deployment operations locally against the production repository.

## Handoffs

1. `python-package.yml` keeps every original required job. It builds one wheel
   and sdist, inspects metadata and typed-package contents, and separately
   installs both exact artifacts using isolated Python outside the checkout.
   The rendered site and distributions are uploaded under run/attempt-qualified
   names. `Seal tested delivery` depends on every check, including each matrix
   result. PR runs still test/build but cannot seal deliverable material.
2. The seal records schema version 1, repository name/numeric ID, workflow
   numeric ID/path, run ID/attempt, source commit, exact job IDs, project version,
   artifact IDs/names, ZIP SHA256 and every extracted file SHA256. Dist/site IDs
   and ZIP digests must match the uploader action outputs passed through job
   outputs, not merely a matching name or creation timestamp.
3. `python-publish.yml` runs from the trusted default-branch workflow revision.
   It revalidates the successful same-repository `push` to `main`, workflow ID,
   attempt endpoint and full job set, and queries authoritative associated PRs
   and pull details for the exact merged-main SHA. It downloads by artifact ID,
   explicitly verifies each ZIP digest before inspecting data, and checks every
   file against the seal. It never checks out producer code or installs/builds
   downloaded distributions in the privileged consumer.
4. The publisher queries `https://pypi.org/pypi/pywhales/{version}/json`.
   Existing wheel/sdist files must have the exact filenames, package types and
   SHA256 hashes. Only missing files are offered to the uploader. A failed,
   skipped or concurrent upload can yield a receipt only if a fresh destination
   inventory confirms both exact distributions. Unexpected/conflicting files
   fail closed. A matching import or version string is not publication evidence.
5. The receipt embeds the complete seal, seal-artifact identity/content hashes,
   publisher identity and explicit destination confirmation. A successful
   publisher run with no receipt, or a skipped publishing job, cannot release.
   The receipt is uploaded under the publisher run/attempt-qualified name.
6. `release-on-merge.yml` consumes only a successful publisher receipt. It
   independently repeats producer and destination validation. Tag creation,
   release creation and `422` races are accepted only after resolving the
   existing lightweight or annotated tag to the receipt's exact source commit.
   New releases are created as drafts and the tag is resolved again before
   publication, so a tag creation/mutation race cannot be accepted by name.
   Draft and prerelease records are not accepted as the published release.
7. Release completion explicitly calls `gh-pages.yml` using `workflow_call`.
   It does not depend on a token-created release event triggering anything.
   Pages validates the same receipt, destination and release, then extracts the
   tested site without regeneration. Pinned upload-pages-artifact v5 preserves
   hidden files and repackages the validated regular files into the current
   deploying run, as required by deploy-pages. The actual generated Pages tar is
   compared against every tested site file before deployment is permitted.
   Manual `workflow_dispatch` requires
   `publisher_run_id` and invokes precisely the same checks. Non-main dispatch
   refs cannot enter the privileged job.

## Fail-Closed Reruns And Retention

GitHub artifacts are run-scoped, not attempt-scoped. Attempt-qualified names,
uploader IDs/digests and the complete attempt-specific jobs endpoint are all
required. Artifacts must also have matching authoritative run/repository/source
linkage and creation/update times inside their producing job interval. GitHub
does not expose an artifact job ID; timestamps alone are not treated as proof.
Job `run_attempt`, when returned by GitHub, must match the scoped endpoint.

Missing, expired, duplicate, unsafe, altered or mismatched evidence is an error.
Every pagination endpoint is exhausted before selecting a candidate. A newer
producer/publisher attempt invalidates an older receipt unless that entire
latest chain is complete and matching. Rerunning only failed jobs cannot borrow
checks or material from a previous attempt. Use a full workflow rerun/fresh run
when that proof is missing. Artifact retention is GitHub's repository default;
expired artifacts require a new complete tested chain, not a rebuild downstream.

The publisher serializes publication, and Pages serializes deployments, without
cancelling an in-progress delivery. Retry an incomplete publication after PyPI's
inventory exposes successfully uploaded files. No credentials are passed to
downloaded artifacts, and privileged checkouts always use `github.workflow_sha`.

## Executable Local Checks

```bash
uv run --locked pytest tests/pywhales/test_delivery_workflows.py
uv run --locked ruff check scripts/delivery tests/pywhales/test_delivery_workflows.py
uv run --locked ruff format --check scripts/delivery tests/pywhales/test_delivery_workflows.py
uv run --locked ty check --config 'src.include = ["scripts/delivery", "tests/pywhales/test_delivery_workflows.py"]' scripts/delivery tests/pywhales/test_delivery_workflows.py
uv run --locked python -m scripts.delivery --help
```

The tests drive the real helper decisions and CLI against stubbed REST,
artifact-download, PyPI and release boundaries. They do not pin YAML prose or
publish production packages. The parent CI still owns full SDK integration,
documentation, package builds, and destructive disposable-daemon coverage.

## API Contracts

- [Attempt-specific jobs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
- [Run-scoped artifact inventory and ZIP download](https://docs.github.com/en/rest/actions/artifacts)
- [PyPI release JSON file inventory](https://docs.pypi.org/api/json/)
- [Token-trigger restrictions](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow)
