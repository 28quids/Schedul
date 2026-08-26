"""Document numbers and filenames: scoped tokens resolved into one string.

v1 kept a flat ``document_number`` dict, which is wrong: tokens vary at
different levels and the model has to say so. A project has one project number
but several buildings; an AHU is always ventilation whatever project it is on.

=============  ==================================================  ===============================================
scope          tokens                                              why
=============  ==================================================  ===============================================
``company``    ``originator``                                      constant for the firm
``project``    ``project_number``, ``doc_type``, ``discipline``,    one value per job
               ``classification``, ``level``, ``location``
``building``   ``building``                                        a project has several blocks
``type``       ``volume``                                          follows the equipment type, not the project
``schedule``   ``number``                                           per document
=============  ==================================================  ===============================================

Resolution order, most specific wins:
**schedule override -> building -> type default -> project -> company default.**

``level`` and ``location`` are project-scoped and effectively always ``XX``:
ISO 19650 uses them for drawings, not schedules. They stay in the pattern and
stay editable, but they do not belong in the per-schedule UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Mapping

__all__ = [
    "SCOPES",
    "TokenSpec",
    "NamingScheme",
    "ResolutionContext",
    "ResolvedToken",
    "NamingError",
    "slug",
    "filename_safe",
]

#: Most general first. Resolution walks this in reverse.
SCOPES: tuple[str, ...] = ("company", "project", "type", "building", "schedule")

_TOKEN_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class NamingError(Exception):
    """A pattern or token set that cannot produce a document number."""


def slug(text: str) -> str:
    """``'Fan Coil Unit Schedule'`` -> ``'Fan_Coil_Unit_Schedule'``.

    Vendored from v1 unchanged: the sample filenames were generated with these
    exact rules and must keep matching.
    """
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", text)).strip("_")


def filename_safe(value: str) -> str:
    """Make a token value safe to sit in a filename.

    Applied where a token has no explicit ``filename_value``, so nobody has to
    remember that ``5.7`` is written ``5_7`` on disk.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")


@dataclass(slots=True)
class TokenSpec:
    """One token in the pattern: where its value comes from, and its default."""

    scope: str = "project"
    value: str = ""
    filename_value: str | None = None
    width: int | None = None
    start: int | None = None

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise NamingError(f"unknown token scope {self.scope!r}; expected one of {SCOPES}")

    def render(self, raw: Any) -> str:
        """Format a resolved raw value for the document number.

        Numeric tokens are zero-filled to ``width``. Otherwise the filename-safe
        form is used: the document number is embedded in the filename, and every
        v1 sample stores ``5_6`` rather than ``5.6`` in ``Config!$B$4``.
        """
        if raw is None or raw == "":
            return ""
        if self.width is not None and isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return str(int(raw)).zfill(self.width)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return str(int(raw))
        return str(raw)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"scope": self.scope, "value": self.value}
        if self.filename_value is not None:
            out["filename_value"] = self.filename_value
        if self.width is not None:
            out["width"] = self.width
        if self.start is not None:
            out["start"] = self.start
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TokenSpec":
        return cls(
            scope=data.get("scope", "project"),
            value=data.get("value", ""),
            filename_value=data.get("filename_value"),
            width=data.get("width"),
            start=data.get("start"),
        )


@dataclass(slots=True)
class ResolutionContext:
    """Token values supplied at each scope for one particular schedule.

    Every layer is a plain ``{token_name: value}`` mapping. Missing keys fall
    through to the next-general layer, ending at the token's own default.
    """

    company: dict[str, Any] = _field(default_factory=dict)
    project: dict[str, Any] = _field(default_factory=dict)
    type: dict[str, Any] = _field(default_factory=dict)
    building: dict[str, Any] = _field(default_factory=dict)
    schedule: dict[str, Any] = _field(default_factory=dict)

    def layer(self, scope: str) -> dict[str, Any]:
        return getattr(self, scope)


@dataclass(frozen=True, slots=True)
class ResolvedToken:
    """A token's value and where it came from, for the numbering tab's readout."""

    name: str
    value: str
    source: str
    scope: str


@dataclass(slots=True)
class NamingScheme:
    """A pattern plus its tokens. Owned by the house standard, overridable per project."""

    pattern: str
    tokens: dict[str, TokenSpec] = _field(default_factory=dict)
    separator: str = "-"
    suffix: str = "_-_{title_slug}"

    # -- construction -----------------------------------------------------
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NamingScheme":
        return cls(
            pattern=data["pattern"],
            tokens={k: TokenSpec.from_dict(v) for k, v in (data.get("tokens") or {}).items()},
            separator=data.get("separator", "-"),
            suffix=data.get("suffix", "_-_{title_slug}"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "separator": self.separator,
            "suffix": self.suffix,
            "tokens": {k: v.to_dict() for k, v in self.tokens.items()},
        }

    # -- inspection -------------------------------------------------------
    @property
    def pattern_tokens(self) -> list[str]:
        """Token names appearing in the pattern, in order."""
        return _TOKEN_RE.findall(self.pattern)

    def validate(self) -> list[str]:
        """Problems that would stop this scheme producing a document number."""
        problems: list[str] = []
        used = self.pattern_tokens
        if not used:
            problems.append("pattern contains no {tokens}")
        for name in used:
            if name not in self.tokens:
                problems.append(f"pattern uses {{{name}}} but no such token is defined")
        for name in self.tokens:
            if name not in used:
                problems.append(f"token {name!r} is defined but not used in the pattern")
        if "number" in self.tokens and self.tokens["number"].scope != "schedule":
            problems.append("the 'number' token must be schedule-scoped")
        return problems

    # -- resolution -------------------------------------------------------
    def resolve_token(self, name: str, context: ResolutionContext) -> ResolvedToken:
        """Resolve one token, most specific scope first.

        Walks schedule -> building -> type -> project -> company, then falls back
        to the token's own default value.
        """
        spec = self.tokens.get(name)
        if spec is None:
            raise NamingError(f"pattern uses {{{name}}} but no such token is defined")

        for scope in reversed(SCOPES):
            layer = context.layer(scope)
            if name in layer and layer[name] not in (None, ""):
                return ResolvedToken(name, spec.render(layer[name]), scope, spec.scope)

        default = spec.filename_value if spec.filename_value is not None else spec.value
        return ResolvedToken(name, spec.render(default), "default", spec.scope)

    def resolve_all(self, context: ResolutionContext) -> dict[str, ResolvedToken]:
        """Resolve every token in the pattern."""
        return {name: self.resolve_token(name, context) for name in self.pattern_tokens}

    def document_number(self, context: ResolutionContext) -> str:
        """Render the full document number.

        This is the value written to ``Config!$B$4`` and embedded in the
        filename. It is the schedule's identity: renaming a schedule is exactly
        two writes, this cell and the filename, and everything else in the
        workbook follows by formula.
        """
        resolved = self.resolve_all(context)
        missing = [n for n, t in resolved.items() if not t.value]
        if missing:
            raise NamingError(
                "cannot build a document number, these tokens have no value: "
                + ", ".join(sorted(missing))
            )

        def replace(m: re.Match[str]) -> str:
            return resolved[m.group(1)].value

        return _TOKEN_RE.sub(replace, self.pattern)

    def filename(self, context: ResolutionContext, title: str) -> str:
        """``<document number><suffix>.xlsx``, matching the v1 sample files."""
        docnum = self.document_number(context)
        suffix = self.suffix.replace("{title_slug}", slug(title))
        return f"{docnum}{suffix}.xlsx"

    def preview(self, context: ResolutionContext, title: str) -> dict[str, Any]:
        """Everything the numbering tab shows: tokens, sources, and the results.

        Never raises -- an incomplete token set is what the user is looking at
        the preview to discover.
        """
        try:
            resolved = self.resolve_all(context)
        except NamingError as exc:
            return {"error": str(exc), "tokens": [], "document_number": "", "filename": ""}

        try:
            docnum = self.document_number(context)
            fname = self.filename(context, title)
            error = None
        except NamingError as exc:
            docnum, fname, error = "", "", str(exc)

        return {
            "error": error,
            "tokens": [
                {
                    "name": name,
                    "value": tok.value,
                    "source": tok.source,
                    "scope": tok.scope,
                }
                for name, tok in resolved.items()
            ],
            "document_number": docnum,
            "filename": fname,
        }


def volume_context(volume: str, scheme: NamingScheme) -> dict[str, Any]:
    """Type-scope layer for a schedule type's volume.

    Converts ``5.7`` to the filename-safe ``5_7`` the document number carries,
    unless the scheme's token defines an explicit ``filename_value``.
    """
    if not volume:
        return {}
    spec = scheme.tokens.get("volume")
    if spec is not None and spec.filename_value is not None and spec.value == volume:
        return {"volume": spec.filename_value}
    return {"volume": filename_safe(volume)}
