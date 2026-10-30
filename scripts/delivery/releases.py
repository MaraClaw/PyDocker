"""Race-safe releases whose tag resolves to the receipt's exact tested commit."""

from .api import GitHub, json_body, require
from .models import GitRef, Receipt, Release
from .notes import matches


def tag_commit(api: GitHub, tag: str) -> str | None:
    """Dereference lightweight and annotated tags with cycle detection."""
    response = api.response(f"git/ref/tags/{tag}")
    if response.status == 404:
        return None
    require(response.status == 200, "tag lookup failed")
    target = GitRef.model_validate_json(response.body).object
    seen: set[str] = set()
    while target.type == "tag":
        require(target.sha not in seen, "cyclic annotated tag")
        seen.add(target.sha)
        target = api.get(f"git/tags/{target.sha}", GitRef).object
    require(target.type == "commit", "tag is not a commit")
    return target.sha


def released(api: GitHub, document: Receipt) -> None:
    """Verify a published release and its fully resolved tag before Pages use."""
    tag = f"v{document.seal.version}"
    release = api.get(f"releases/tags/{tag}", Release)
    require(
        release.tag_name == tag and not release.draft and not release.prerelease,
        "release state",
    )
    require(
        tag_commit(api, tag) == document.seal.producer.sha,
        "release tag commit mismatch",
    )


def release_record(api: GitHub, tag: str) -> Release | None:
    """Find a published release or an authenticated draft without ambiguity."""
    response = api.response(f"releases/tags/{tag}")
    require(response.status in {200, 404}, "release lookup failed")
    if response.status == 200:
        return Release.model_validate_json(response.body)
    matches = [
        record for record in api.pages("releases", Release) if record.tag_name == tag
    ]
    require(len(matches) <= 1, "ambiguous draft releases")
    return matches[0] if matches else None


def release(api: GitHub, document: Receipt) -> None:
    """Create refs before releases, and verify both after create/conflict races.

    The sealed notes are the only body ever sent. A draft receives them and its
    publication in one PATCH, after the tag and state checks. A published release
    with other text is rejected untouched, and the final body is read back.
    """
    tag = f"v{document.seal.version}"
    sha = document.seal.producer.sha
    notes = document.seal.notes
    current = tag_commit(api, tag)
    if current is None:
        response = api.response(
            "git/refs", json_body({"ref": f"refs/tags/{tag}", "sha": sha})
        )
        require(response.status in {201, 422}, "tag creation failed")
    require(tag_commit(api, tag) == sha, "release tag commit mismatch")
    record = release_record(api, tag)
    if record is None:
        response = api.response(
            "releases",
            json_body(
                {
                    "tag_name": tag,
                    "name": tag,
                    "target_commitish": sha,
                    "body": notes,
                    "draft": True,
                    "prerelease": False,
                }
            ),
        )
        require(response.status in {201, 422}, "release creation failed")
        record = (
            Release.model_validate_json(response.body)
            if response.status == 201
            else release_record(api, tag)
        )
    require(record is not None, "missing created release")
    assert record is not None
    require(tag_commit(api, tag) == sha, "release tag commit mismatch")
    require(record.tag_name == tag and not record.prerelease, "release state")
    if record.draft:
        response = api.response(
            f"releases/{record.id}",
            json_body({"body": notes, "draft": False}),
            method="PATCH",
        )
        require(response.status == 200, "release publication failed")
    else:
        require(matches(record.body, notes), "release body mismatch")
    released(api, document)
    require(
        matches(api.get(f"releases/tags/{tag}", Release).body, notes),
        "release body mismatch",
    )
