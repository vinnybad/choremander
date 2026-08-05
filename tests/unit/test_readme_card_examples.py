"""Guard that the README's card examples name the entity each card reads.

Every card declares the entity it is built against in its `getStubConfig()` -
that is what the visual editor drops in when a card is added from the picker.
A README example that names a different entity sends anyone copying YAML to a
sensor without the attributes the card reads, and the card has no way to tell
that apart from "nothing to show": it renders an empty state and the
misconfiguration is invisible.

That is exactly how the approvals card ended up documented against
`sensor.choremander_overview`, which carries no `chore_completions`, so it
reported "All caught up!" while approvals were pending
([issue #5](https://github.com/vinnybad/choremander/issues/5)).
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
WWW = REPO_ROOT / "custom_components" / "choremander" / "www"

_YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_CARD_TYPE = re.compile(r"^type:\s*custom:(choremander-[\w-]+)\s*$", re.MULTILINE)
_ENTITY = re.compile(r"^entity:\s*(\S+)", re.MULTILINE)
_STUB_ENTITY = re.compile(
    r"getStubConfig\(\)\s*{\s*return\s*{.*?entity:\s*[\"']([^\"']+)[\"']",
    re.DOTALL,
)


def _documented_examples() -> list[tuple[str, str]]:
    """Return every (card, entity) pair the README hands out as copyable YAML."""
    examples = []
    for block in _YAML_BLOCK.findall(README.read_text()):
        card = _CARD_TYPE.search(block)
        entity = _ENTITY.search(block)
        if card and entity:
            examples.append((card.group(1), entity.group(1)))
    return examples


def _stub_entity(card: str) -> str:
    """Return the entity `card`'s own `getStubConfig()` defaults to."""
    source = (WWW / f"{card}.js").read_text()
    match = _STUB_ENTITY.search(source)
    assert match, f"{card}.js has no entity in getStubConfig(), so the README cannot be checked"
    return match.group(1)


def test_the_readme_documents_at_least_one_example_per_card() -> None:
    """A parsing regression here would make the entity test vacuously pass."""
    documented = {card for card, _ in _documented_examples()}
    shipped = {path.stem for path in WWW.glob("choremander-*-card.js")}

    assert documented == shipped, (
        f"README examples cover {sorted(documented)} but the integration ships "
        f"{sorted(shipped)}. Either a card is undocumented or the README's YAML "
        "blocks stopped parsing."
    )


@pytest.mark.parametrize(("card", "entity"), _documented_examples())
def test_readme_examples_name_the_entity_the_card_reads(card: str, entity: str) -> None:
    """Copying a README example must produce a working card."""
    expected = _stub_entity(card)

    assert entity == expected, (
        f"The README documents {card} against `{entity}`, but the card defaults "
        f"to `{expected}`. A card pointed at the wrong sensor finds none of the "
        "attributes it reads and renders its empty state, so the example looks "
        "like it works while showing nothing."
    )
