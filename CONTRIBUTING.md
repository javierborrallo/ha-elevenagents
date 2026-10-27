# Contributing

For a large change, open an issue first, so we can agree on it before you write the code.

## Set up

```bash
scripts/setup      # install the development dependencies
scripts/develop    # start Home Assistant against config/ with the integration loaded
scripts/lint       # format and fix with ruff
```

To run the tests, install the test dependencies too:

```bash
python3 -m pip install -r requirements_common.txt -r requirements_test.txt
```

## What CI checks

Run the same checks before you open a pull request:

```bash
python3 -m ruff check .
python3 -m ruff format . --check
python3 -m pytest tests/
python3 -m mypy custom_components/ tests/
node --test tests/js/*.test.mjs
```

The JavaScript tests cover the panel and the dashboard card.

## Home Assistant versions

The tests run on two Home Assistant releases: the minimum a user needs, and the newest.

- **The minimum, 2026.9.0**, is what `hacs.json` declares. It is the first release with
  `async_get_effective_area_id`, which tool synchronisation calls. Its own ElevenLabs integration
  pins `elevenlabs==2.51.0`, the floor this one needs; an older Home Assistant would pin an older
  SDK, and the two integrations would fight over one package. `requirements_test_minimum.txt` pins
  it.
- **The newest** is what `requirements_dev.txt` and `requirements_test.txt` pin.

Type checks run on the minimum only. Home Assistant 2026.10 replaced voluptuous with probatio and
aliases one to the other at run time, which mypy cannot see.

`pytest-homeassistant-custom-component` pins one exact Home Assistant version, so each test file
pins its own pair. `tests/test_requirements.py` fails when the minimum file disagrees with
`hacs.json`, when the newest disagrees with `requirements_dev.txt`, or when `elevenlabs` differs
from the version Home Assistant pins.

## Dependency updates

Dependabot proposes updates for pip packages, GitHub Actions and devcontainer features. It leaves
alone `homeassistant`, `pytest-homeassistant-custom-component`, `elevenlabs` and the other packages
the test files pin to match Home Assistant.

## The prompt block

`docs/prompt-block.md` and `custom_components/elevenagents/panel/block.js` carry the same block.
`tests/test_prompt_block.py` fails when they differ, so change both together.

## Conventions

English everywhere: code, comments, commit messages and documentation. Keep commit messages short:
a subject line, and a body only when the diff does not explain itself.
