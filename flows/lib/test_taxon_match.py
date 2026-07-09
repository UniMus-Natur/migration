"""Unit tests for taxon name matching helpers (no database)."""

from __future__ import annotations

import types

from flows.lib.musit_taxon_match import (
    binomial_prefix_from_valid_classterm,
    norm_taxon_label,
    taxon_label_candidates,
    taxon_matches_valid_classterm,
)


def _taxon(**kwargs: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kwargs)


def test_binomial_prefix_with_author_suffix() -> None:
    assert binomial_prefix_from_valid_classterm("Podocarpus lawrencei Hook.f.") == "Podocarpus lawrencei"


def test_binomial_prefix_absent_for_binomial_only() -> None:
    assert binomial_prefix_from_valid_classterm("Podocarpus lawrencei") is None


def test_label_candidates_include_name_plus_author() -> None:
    labels = taxon_label_candidates(_taxon(name="Podocarpus lawrencei", author="Hook. f."))
    assert "podocarpus lawrencei hook f" in labels


def test_matches_valid_classterm_when_author_is_separate_field() -> None:
    taxon = _taxon(name="Podocarpus lawrencei", author="Hook. f.")
    assert taxon_matches_valid_classterm(taxon, "Podocarpus lawrencei Hook.f.")


def test_matches_valid_classterm_via_fullname_when_name_is_epithet() -> None:
    taxon = _taxon(name="annua", author="L.", fullname="Poa annua")
    assert taxon_matches_valid_classterm(taxon, "Poa annua L.")


def test_matches_valid_classterm_via_fullname_binomial_prefix() -> None:
    taxon = _taxon(name="lawrencei", fullname="Podocarpus lawrencei")
    assert taxon_matches_valid_classterm(taxon, "Podocarpus lawrencei Hook.f.")


def test_norm_strips_punctuation_in_authorship() -> None:
    assert norm_taxon_label("Hook. f.") == "hook f"
