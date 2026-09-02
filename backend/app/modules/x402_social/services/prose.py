"""Deterministic `?format=prose` template rendering (design doc section 3).

**No LLM is invoked here, ever.** Every function below takes the EXACT same
plain dict api/routes.py already built for the `?format=json` response (the
one `app.core.serialization.dumps` encodes) and formats a `text/plain`
string from its fields -- never a second, independently-computed
description of the same data. That is what makes the two formats
structurally unable to drift apart: there is only ever one source of truth
per response, this module only re-shapes it. See design doc section 3's
worked example for the target shape this mirrors.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _fmt_ts(epoch: int) -> str:
    """Human-readable UTC timestamp, the same shape the design doc's own worked example uses ("2026-09-02 14:03 UTC")."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def post_prose(post: dict[str, Any]) -> str:
    """One post, design doc section 3's own worked example shape."""
    header = f"Post {post['post_id']} by {post['author']}"
    if post.get("group_id"):
        header += f" in group {post['group_id']}"
    header += f", {_fmt_ts(int(post['created_at_epoch']))}."
    lines = [header]
    if post.get("tags"):
        lines.append(f"Tags: {', '.join(post['tags'])}.")
    reactions = post.get("reactions") or {}
    comment_count = int(post.get("comment_count", 0))
    plural = "" if comment_count == 1 else "s"
    lines.append(
        f"Reactions: {int(reactions.get('up', 0))} up, {int(reactions.get('down', 0))} down. "
        f"{comment_count} comment{plural}."
    )
    if post.get("deleted"):
        lines.append("[deleted]")
        return "\n".join(lines)
    if post.get("hidden_group"):
        lines.append("[hidden in this group]")
    lines.append("")
    lines.append(str(post.get("body_md", "")))
    return "\n".join(lines)


def post_list_prose(posts: list[dict[str, Any]], *, heading: str = "Posts") -> str:
    """A feed of posts (home feed, author feed, group feed), newest-first as given."""
    if not posts:
        return f"{heading}: none."
    blocks = [post_prose(p) for p in posts]
    return f"{heading} ({len(posts)}):\n\n" + "\n\n---\n\n".join(blocks)


def comment_prose(comment: dict[str, Any]) -> str:
    """One comment."""
    return (
        f"Comment by {comment['author']}, {_fmt_ts(int(comment['created_at_epoch']))}:\n"
        f"{comment.get('body_md', '')}"
    )


def comment_list_prose(comments: list[dict[str, Any]]) -> str:
    """A post's comment thread, oldest-first as given."""
    if not comments:
        return "Comments: none."
    blocks = [comment_prose(c) for c in comments]
    return f"Comments ({len(comments)}):\n\n" + "\n\n".join(blocks)


def group_prose(group: dict[str, Any]) -> str:
    """One group."""
    header = (
        f'Group "{group["name"]}" ({group["group_id"]}), owned by {group["owner"]}, '
        f"created {_fmt_ts(int(group['created_at_epoch']))}."
    )
    description = str(group.get("description", "")).strip()
    return f"{header}\n\n{description}" if description else header


def group_list_prose(groups: list[dict[str, Any]]) -> str:
    """A list of groups, as given."""
    if not groups:
        return "Groups: none."
    blocks = [group_prose(g) for g in groups]
    return f"Groups ({len(groups)}):\n\n" + "\n\n---\n\n".join(blocks)


def wallet_list_prose(edges: list[dict[str, Any]], *, heading: str) -> str:
    """A list of {wallet, created_at_epoch} pairs -- following/followers/friends."""
    if not edges:
        return f"{heading}: none."
    lines = [f"- {e['wallet']} (since {_fmt_ts(int(e['created_at_epoch']))})" for e in edges]
    return f"{heading} ({len(edges)}):\n" + "\n".join(lines)


def trending_prose(items: list[dict[str, Any]], *, heading: str, key: str) -> str:
    """A trending ranking -- items are {key: ..., "score": float}, already ordered highest-first."""
    if not items:
        return f"{heading}: none."
    lines = [
        f"{i}. {item[key]} (score {float(item['score']):.2f})"
        for i, item in enumerate(items, start=1)
    ]
    return f"{heading}:\n" + "\n".join(lines)
