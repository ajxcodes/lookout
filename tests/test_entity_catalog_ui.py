"""Source-level assertions for the entity catalog editor in the settings modal."""

from pathlib import Path

_JS_DIR = Path(__file__).parents[1] / "src/lookout/adapters/web/static/js"


def test_settings_modal_entity_edit_and_emoji() -> None:
    """TR-8 (AC3/AC5): the settings modal renders stored emoji and offers inline Edit."""
    js = (_JS_DIR / "components" / "settings-modal.js").read_text(encoding="utf-8")

    # Stored emoji is rendered, with a type-default fallback map.
    assert "ent.emoji" in js
    assert "defaultEmoji" in js

    # Inline edit controls for both name and emoji, plus an Edit action.
    assert "Edit" in js
    assert "entity-edit" in js
    assert "entity-emoji-input" in js
    assert "entity-name-input" in js

    # Saving POSTs the full payload (including emoji) to the entity upsert endpoint.
    assert 'resolveUrl("/api/entities")' in js
    assert "emoji: emoji" in js

    # The list refreshes after a successful save.
    assert "await this.loadEntities()" in js
