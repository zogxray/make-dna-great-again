"""Story helpers without live LLM."""

from __future__ import annotations

from ydna.core.chat import Message, Thread
from ydna.core.lineage_query import commons_search_url, media_from_node
from ydna.core.story_brief import (
    collect_media_slots, find_last_oracle, story_prompt,
)


def test_commons_search_url():
    url = commons_search_url("Çatalhöyük")
    assert "commons.wikimedia.org" in url
    assert "MediaSearch" in url


def test_media_from_node_discover():
    node = {
        "name": "J-X",
        "ancient": [
            {"name": "Sample A", "image_url": "https://example.com/a.jpg"},
            {"name": "No image"},
        ],
    }
    media = media_from_node(node)
    assert media[0]["url"] == "https://example.com/a.jpg"
    assert media[0]["source"] == "discover_ancient"


def test_collect_media_slots_from_compact():
    facts = {
        "y_milestones": {
            "milestones": [{
                "clade": "J-X",
                "lore": {
                    "epoch": "Bronze",
                    "media": [{
                        "source": "discover_ancient",
                        "url": "https://example.com/b.jpg",
                        "caption": "B",
                    }],
                    "aadr_lines": ["Sarakenos 37 (Greece)"],
                },
            }],
        },
    }
    slots = collect_media_slots(facts)
    urls = {s["url"] for s in slots}
    assert "https://example.com/b.jpg" in urls
    assert any("commons.wikimedia.org" in u for u in urls)


def test_find_last_oracle_skips_story():
    t = Thread(id="t", title="t", messages=[
        Message("user", "q"),
        Message("model", "### Oracle · m\n\n## Oracle\n\n" + ("x" * 300)),
        Message("model", "### Story · m\n\n## Story\n\nhuman"),
    ])
    assert find_last_oracle(t) is not None
    assert "x" * 50 in find_last_oracle(t)


def test_story_prompt_has_media_and_timeline():
    p = story_prompt(
        "родословная от Адама",
        oracle="## Oracle\n" + ("body " * 40),
        facts={"y_path": {"tip": "J-X"}},
        media=[{"url": "https://example.com/z.jpg", "caption": "Z", "source": "discover_ancient"}],
        language="Russian",
    )
    assert "Russian" in p
    assert "Timeline" in p
    assert "https://example.com/z.jpg" in p
