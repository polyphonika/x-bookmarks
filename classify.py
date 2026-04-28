"""Classify cached bookmarks with Claude.

Two phases:
  python classify.py discover   # sample bookmarks, propose 6-10 topics, write topics.json
  python classify.py run        # classify every untagged bookmark into the topics

Edit topics.json by hand between the two phases — that's the point.
"""
import json
import sqlite3
import sys
from pathlib import Path
from typing import List

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

DB_PATH = Path("bookmarks.db")
TOPICS_PATH = Path("topics.json")
MODEL = "claude-opus-4-7"
SAMPLE_SIZE = 50

client = anthropic.Anthropic()


class Topic(BaseModel):
    name: str = Field(description="Short topic name, 1-3 words.")
    description: str = Field(description="One-sentence description of what fits in this bucket.")


class TopicProposal(BaseModel):
    topics: List[Topic]


class Classification(BaseModel):
    topic: str = Field(description="Exact topic name from the provided taxonomy.")
    summary: str = Field(description="One sentence on why this bookmark is worth keeping.")


def discover() -> None:
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT text, author_username FROM bookmarks "
        "ORDER BY RANDOM() LIMIT ?",
        (SAMPLE_SIZE,),
    ).fetchall()
    if not rows:
        print("No bookmarks in DB — run `python pull.py` first.")
        return

    sample = "\n\n---\n\n".join(f"@{u}: {t}" for t, u in rows)
    msg = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        output_format=TopicProposal,
        messages=[
            {
                "role": "user",
                "content": (
                    "Below is a sample of X (Twitter) bookmarks I've saved over "
                    "the last several months. Propose a small taxonomy of 6-10 "
                    "broad topic buckets that covers them. Keep names short "
                    "(1-3 words). Include an 'Other' bucket for things that "
                    "don't fit cleanly. Topics should reflect what's actually "
                    "in the sample, not what's typical of Twitter generally.\n\n"
                    f"{sample}"
                ),
            }
        ],
    )
    proposal = msg.parsed_output
    if proposal is None:
        print("Failed to parse topic proposal. Raw response:")
        print(msg.content)
        return
    TOPICS_PATH.write_text(proposal.model_dump_json(indent=2))
    print(f"Wrote {len(proposal.topics)} topics to {TOPICS_PATH}:")
    for t in proposal.topics:
        print(f"  - {t.name}: {t.description}")
    print("\nReview / edit topics.json, then run: python classify.py run")


def run() -> None:
    if not TOPICS_PATH.exists():
        print("No topics.json — run `python classify.py discover` first.")
        return
    proposal = TopicProposal.model_validate_json(TOPICS_PATH.read_text())
    valid = {t.name for t in proposal.topics}
    topic_list = "\n".join(
        f"- {t.name}: {t.description}" for t in proposal.topics
    )

    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT id, text, author_username FROM bookmarks "
        "WHERE topic IS NULL ORDER BY created_at DESC"
    ).fetchall()
    print(f"Classifying {len(rows)} bookmarks…")

    system = (
        "You classify X (Twitter) bookmarks into a fixed taxonomy of topics. "
        "Pick the SINGLE best-fitting topic name from the list (use 'Other' if "
        "nothing fits well). Also write one sentence on why this bookmark is "
        "worth keeping — what makes it useful or interesting.\n\n"
        f"Topics:\n{topic_list}"
    )

    for i, (bid, text, handle) in enumerate(rows, 1):
        try:
            msg = client.messages.parse(
                model=MODEL,
                max_tokens=400,
                system=system,
                output_format=Classification,
                messages=[{"role": "user", "content": f"@{handle}: {text}"}],
            )
        except anthropic.APIError as e:
            print(f"  [{i}/{len(rows)}] {bid}: API error — {e}")
            continue

        result = msg.parsed_output
        if result is None:
            print(f"  [{i}/{len(rows)}] {bid}: parse failed, skipping")
            continue
        topic = result.topic if result.topic in valid else "Other"
        db.execute(
            "UPDATE bookmarks SET topic = ?, summary = ? WHERE id = ?",
            (topic, result.summary, bid),
        )
        db.commit()
        if i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] done")

    print("Classification complete.")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "discover":
        discover()
    elif cmd == "run":
        run()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
