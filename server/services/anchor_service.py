from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any


_ALLOWED_ANCHOR_TYPES = {"file", "commit", "pr", "issue"}
_ALLOWED_PROVENANCE_MODES = {"verified", "derived"}
_ANCHOR_INDEX_KEYS = {
    "anchor_type",
    "anchor_repo",
    "anchor_locator",
    "anchor_ref",
    "anchor_commit_sha",
    "anchor_url",
    "anchor_title",
    "anchor_created_at",
    "anchor_observed_at",
    "anchor_is_stale",
    "anchor_provenance_mode",
    "anchor_provenance_source",
    "anchor_commit_pinned",
    "anchor_is_verified",
    "anchor_is_derived",
}


class AnchorService:
    def prepare_metadata(
        self,
        metadata: dict[str, Any] | None,
        *,
        write_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if metadata is None:
            return None

        prepared_metadata = deepcopy(metadata)
        anchor_context = self._pop_anchor_context(prepared_metadata)
        if "anchor" not in prepared_metadata:
            derived_anchor = self._build_anchor_from_context(
                anchor_context, write_context=write_context
            )
            if derived_anchor is not None:
                prepared_metadata["anchor"] = derived_anchor
                self._apply_anchor_index(prepared_metadata, derived_anchor)
            elif anchor_context is not None:
                prepared_metadata["anchor"] = None
                self._apply_anchor_index(prepared_metadata, None)
            return prepared_metadata

        anchor_value = prepared_metadata.get("anchor")
        if anchor_value is None:
            self._apply_anchor_index(prepared_metadata, None)
            return prepared_metadata
        if not isinstance(anchor_value, dict):
            return prepared_metadata

        normalized_anchor = self._normalize_anchor(anchor_value, strict=True)
        prepared_metadata["anchor"] = normalized_anchor
        self._apply_anchor_index(prepared_metadata, normalized_anchor)
        return prepared_metadata

    def prepare_filters(self, filters: dict[str, Any] | None) -> dict[str, Any] | None:
        if filters is None:
            return None

        prepared_filters = deepcopy(filters)
        if "anchor" not in prepared_filters:
            return prepared_filters

        anchor_value = prepared_filters.get("anchor")
        if anchor_value is None:
            self._apply_partial_anchor_index(prepared_filters, None)
            return prepared_filters
        if not isinstance(anchor_value, dict):
            return prepared_filters

        normalized_anchor = self._normalize_anchor_filter(anchor_value)
        prepared_filters["anchor"] = normalized_anchor
        self._apply_partial_anchor_index(prepared_filters, normalized_anchor)
        return prepared_filters

    def prepare_update_data(self, updated_memory: dict[str, Any]) -> dict[str, Any]:
        prepared_update = deepcopy(updated_memory)
        if "metadata" in prepared_update:
            metadata = prepared_update.get("metadata")
            prepared_update["metadata"] = self.prepare_metadata(metadata)
        return prepared_update

    def _build_anchor_from_context(
        self,
        anchor_context: Any,
        *,
        write_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(anchor_context, dict):
            return None

        try:
            context = deepcopy(anchor_context)
            anchor_type = self._infer_anchor_type(context)
            if anchor_type is None or anchor_type not in _ALLOWED_ANCHOR_TYPES:
                return None

            repo = self._optional_string(context.get("repo"))
            if repo is None:
                return None

            locator = self._derive_locator(anchor_type, context)
            if locator is None:
                return None

            ref = self._optional_string(context.get("ref"))
            commit_sha = self._optional_string(context.get("commit_sha"))
            is_observed = self._can_verify_anchor(
                anchor_type, locator=locator, commit_sha=commit_sha
            )
            created_at = (
                self._optional_string(context.get("created_at"))
                or self._timestamp_now()
            )
            observed_at = None
            if is_observed:
                observed_at = (
                    self._optional_string(context.get("observed_at")) or created_at
                )

            provenance_source = "observed" if is_observed else "inferred-from-context"
            if not is_observed and write_context:
                provenance_source = "inferred-from-context"

            anchor = {
                "type": anchor_type,
                "repo": repo,
                "locator": locator,
                "ref": ref,
                "commit_sha": commit_sha if is_observed else None,
                "url": self._derive_url(
                    repo=repo,
                    anchor_type=anchor_type,
                    locator=locator,
                    commit_sha=commit_sha if is_observed else None,
                    explicit_url=self._optional_string(context.get("url")),
                ),
                "title": self._derive_title(
                    anchor_type=anchor_type,
                    locator=locator,
                    explicit_title=self._optional_string(context.get("title")),
                ),
                "created_at": created_at,
                "observed_at": observed_at,
                "is_stale": self._derive_is_stale(context),
                "provenance": {
                    "mode": "verified" if is_observed else "derived",
                    "source": provenance_source,
                    "commit_pinned": is_observed,
                },
            }

            return self._normalize_anchor(anchor, strict=True)
        except ValueError:
            return None

    def normalize_payload(self, payload: Any) -> Any:
        if isinstance(payload, list):
            return [self.normalize_payload(item) for item in payload]

        if not isinstance(payload, dict):
            return payload

        normalized_payload = dict(payload)
        if "results" in normalized_payload and isinstance(
            normalized_payload["results"], list
        ):
            normalized_payload["results"] = [
                self.normalize_payload(item) for item in normalized_payload["results"]
            ]
        if "items" in normalized_payload and isinstance(
            normalized_payload["items"], list
        ):
            normalized_payload["items"] = [
                self.normalize_payload(item) for item in normalized_payload["items"]
            ]
        if self._looks_like_memory_record(normalized_payload):
            return self.normalize_record(normalized_payload)
        return normalized_payload

    def normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized_record = dict(record)
        metadata = normalized_record.get("metadata")
        normalized_metadata = metadata
        if isinstance(metadata, dict):
            normalized_metadata = self._normalize_record_metadata(metadata)
            normalized_record["metadata"] = normalized_metadata

        normalized_record["anchor"] = self._extract_anchor(
            normalized_record, normalized_metadata
        )
        return normalized_record

    def capability_snapshot(self) -> dict[str, Any]:
        return {
            "anchor_ownership": "reserved-for-backend",
            "status": "schema-normalized",
            "supported_anchor_types": sorted(_ALLOWED_ANCHOR_TYPES),
            "nullable_anchor": True,
        }

    def _normalize_record_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        normalized_metadata = deepcopy(metadata)
        anchor_value = normalized_metadata.get("anchor")
        if not isinstance(anchor_value, dict):
            return normalized_metadata

        normalized_anchor = self._normalize_anchor(anchor_value, strict=False)
        normalized_metadata["anchor"] = normalized_anchor
        self._apply_anchor_index(normalized_metadata, normalized_anchor)
        return normalized_metadata

    def _extract_anchor(
        self,
        record: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        for candidate in (
            record.get("anchor"),
            None if metadata is None else metadata.get("anchor"),
        ):
            if candidate is None:
                continue
            if not isinstance(candidate, dict):
                continue
            normalized_candidate = self._normalize_anchor(candidate, strict=False)
            if normalized_candidate is not None:
                return normalized_candidate
        return None

    def _normalize_anchor(
        self,
        anchor: dict[str, Any] | None,
        *,
        strict: bool,
    ) -> dict[str, Any] | None:
        if anchor is None:
            return None

        normalized_anchor = deepcopy(anchor)
        anchor_type = self._required_string(
            normalized_anchor.get("type"), "anchor.type", strict=strict
        )
        if anchor_type is None:
            return None
        if anchor_type not in _ALLOWED_ANCHOR_TYPES:
            raise ValueError("anchor.type must be one of: file, commit, pr, issue")

        repo = self._required_string(
            normalized_anchor.get("repo"), "anchor.repo", strict=strict
        )
        if repo is None:
            return None

        locator = self._normalize_locator(
            anchor_type,
            normalized_anchor.get("locator"),
            field_name="anchor.locator",
            strict=strict,
        )
        if locator is None:
            return None

        provenance = normalized_anchor.get("provenance")
        if provenance is None and not strict:
            return None
        if not isinstance(provenance, dict):
            raise ValueError("anchor.provenance must be an object")
        normalized_provenance = self._normalize_provenance(provenance, strict=strict)
        if normalized_provenance is None:
            return None

        created_at = self._required_string(
            normalized_anchor.get("created_at"), "anchor.created_at", strict=strict
        )
        if created_at is None:
            return None

        if "is_stale" not in normalized_anchor:
            if strict:
                raise ValueError("anchor.is_stale is required")
            return None

        is_stale = normalized_anchor.get("is_stale")
        if not isinstance(is_stale, bool):
            raise ValueError("anchor.is_stale must be a boolean")

        normalized_anchor["type"] = anchor_type
        normalized_anchor["repo"] = repo
        normalized_anchor["locator"] = locator
        normalized_anchor["ref"] = self._optional_string(normalized_anchor.get("ref"))
        normalized_anchor["commit_sha"] = self._optional_string(
            normalized_anchor.get("commit_sha")
        )
        normalized_anchor["url"] = self._optional_string(normalized_anchor.get("url"))
        normalized_anchor["title"] = self._optional_string(
            normalized_anchor.get("title")
        )
        normalized_anchor["created_at"] = created_at
        normalized_anchor["observed_at"] = self._optional_string(
            normalized_anchor.get("observed_at")
        )
        normalized_anchor["is_stale"] = is_stale
        normalized_anchor["provenance"] = normalized_provenance
        return normalized_anchor

    def _normalize_provenance(
        self,
        provenance: dict[str, Any],
        *,
        strict: bool,
    ) -> dict[str, Any] | None:
        normalized_provenance = deepcopy(provenance)
        mode = self._required_string(
            normalized_provenance.get("mode"),
            "anchor.provenance.mode",
            strict=strict,
        )
        if mode is None:
            return None
        if mode not in _ALLOWED_PROVENANCE_MODES:
            raise ValueError("anchor.provenance.mode must be verified or derived")

        source = self._required_string(
            normalized_provenance.get("source"),
            "anchor.provenance.source",
            strict=strict,
        )
        if source is None:
            return None

        if "commit_pinned" not in normalized_provenance:
            if strict:
                raise ValueError("anchor.provenance.commit_pinned is required")
            return None

        commit_pinned = normalized_provenance.get("commit_pinned")
        if not isinstance(commit_pinned, bool):
            raise ValueError("anchor.provenance.commit_pinned must be a boolean")

        normalized_provenance["mode"] = mode
        normalized_provenance["source"] = source
        normalized_provenance["commit_pinned"] = commit_pinned
        return normalized_provenance

    def _normalize_anchor_filter(self, anchor_filter: dict[str, Any]) -> dict[str, Any]:
        normalized_filter = deepcopy(anchor_filter)
        anchor_type = None
        if "type" in normalized_filter:
            anchor_type = self._required_string(
                normalized_filter.get("type"),
                "filters.anchor.type",
                strict=True,
            )
            if anchor_type not in _ALLOWED_ANCHOR_TYPES:
                raise ValueError(
                    "filters.anchor.type must be one of: file, commit, pr, issue"
                )
            normalized_filter["type"] = anchor_type

        for key in (
            "repo",
            "ref",
            "commit_sha",
            "url",
            "title",
            "created_at",
            "observed_at",
        ):
            if key in normalized_filter:
                normalized_filter[key] = self._optional_string(
                    normalized_filter.get(key)
                )

        if "locator" in normalized_filter:
            locator_value = normalized_filter.get("locator")
            if anchor_type is None:
                normalized_filter["locator"] = self._required_string(
                    locator_value,
                    "filters.anchor.locator",
                    strict=True,
                )
            else:
                normalized_filter["locator"] = self._normalize_locator(
                    anchor_type,
                    locator_value,
                    field_name="filters.anchor.locator",
                    strict=True,
                )

        if "is_stale" in normalized_filter and not isinstance(
            normalized_filter.get("is_stale"), bool
        ):
            raise ValueError("filters.anchor.is_stale must be a boolean")

        provenance = normalized_filter.get("provenance")
        if provenance is not None:
            if not isinstance(provenance, dict):
                raise ValueError("filters.anchor.provenance must be an object")
            normalized_filter["provenance"] = self._normalize_provenance_filter(
                provenance
            )

        return normalized_filter

    def _pop_anchor_context(
        self,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        for key in ("anchor_context", "provenance_context"):
            if key not in metadata:
                continue
            raw_context = metadata.pop(key)
            if isinstance(raw_context, dict):
                return raw_context
            return None
        return None

    def _infer_anchor_type(self, context: dict[str, Any]) -> str | None:
        explicit_type = self._optional_string(context.get("type"))
        if explicit_type is not None:
            return explicit_type

        if any(
            self._optional_string(context.get(key))
            for key in ("locator", "path", "file_path")
        ):
            return "file"
        if self._optional_string(context.get("commit_sha")) is not None:
            return "commit"
        if self._optional_string(context.get("pr_number")) is not None:
            return "pr"
        if self._optional_string(context.get("issue_number")) is not None:
            return "issue"
        return None

    def _derive_locator(self, anchor_type: str, context: dict[str, Any]) -> str | None:
        locator_value = self._optional_string(
            context.get("locator") or context.get("path") or context.get("file_path")
        )
        if anchor_type == "commit":
            locator_value = locator_value or self._optional_string(
                context.get("commit_sha")
            )
        if anchor_type == "pr":
            locator_value = locator_value or self._optional_string(
                context.get("pr_number")
            )
        if anchor_type == "issue":
            locator_value = locator_value or self._optional_string(
                context.get("issue_number")
            )
        if locator_value is None:
            return None
        return self._normalize_locator(
            anchor_type,
            locator_value,
            field_name="anchor.locator",
            strict=False,
        )

    @staticmethod
    def _can_verify_anchor(
        anchor_type: str,
        *,
        locator: str,
        commit_sha: str | None,
    ) -> bool:
        if commit_sha is None:
            return False
        if anchor_type == "commit":
            return True
        return bool(locator)

    @staticmethod
    def _derive_is_stale(context: dict[str, Any]) -> bool:
        is_stale = context.get("is_stale")
        if isinstance(is_stale, bool):
            return is_stale
        return False

    def _derive_url(
        self,
        *,
        repo: str,
        anchor_type: str,
        locator: str,
        commit_sha: str | None,
        explicit_url: str | None,
    ) -> str | None:
        if explicit_url is not None:
            return explicit_url
        if not repo or not commit_sha:
            return None
        if anchor_type == "file":
            return f"https://{repo}/blob/{commit_sha}/{locator}"
        if anchor_type == "commit":
            return f"https://{repo}/commit/{commit_sha}"
        if anchor_type == "pr":
            pr_number = locator.removeprefix("pr/")
            return f"https://{repo}/pull/{pr_number}"
        if anchor_type == "issue":
            issue_number = locator.removeprefix("issue/")
            return f"https://{repo}/issues/{issue_number}"
        return None

    @staticmethod
    def _derive_title(
        *,
        anchor_type: str,
        locator: str,
        explicit_title: str | None,
    ) -> str | None:
        if explicit_title is not None:
            return explicit_title
        if anchor_type == "file":
            return PurePosixPath(locator).name
        if anchor_type == "commit":
            return locator[:12]
        if anchor_type == "pr":
            return f"PR #{locator.removeprefix('pr/')}"
        if anchor_type == "issue":
            return f"Issue #{locator.removeprefix('issue/')}"
        return None

    @staticmethod
    def _timestamp_now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    def _normalize_provenance_filter(
        self, provenance_filter: dict[str, Any]
    ) -> dict[str, Any]:
        normalized_filter = deepcopy(provenance_filter)
        if "mode" in normalized_filter:
            mode = self._required_string(
                normalized_filter.get("mode"),
                "filters.anchor.provenance.mode",
                strict=True,
            )
            if mode not in _ALLOWED_PROVENANCE_MODES:
                raise ValueError(
                    "filters.anchor.provenance.mode must be verified or derived"
                )
            normalized_filter["mode"] = mode

        if "source" in normalized_filter:
            normalized_filter["source"] = self._required_string(
                normalized_filter.get("source"),
                "filters.anchor.provenance.source",
                strict=True,
            )

        if "commit_pinned" in normalized_filter and not isinstance(
            normalized_filter.get("commit_pinned"), bool
        ):
            raise ValueError(
                "filters.anchor.provenance.commit_pinned must be a boolean"
            )

        return normalized_filter

    def _normalize_locator(
        self,
        anchor_type: str,
        locator: Any,
        *,
        field_name: str,
        strict: bool,
    ) -> str | None:
        raw_locator = self._required_string(locator, field_name, strict=strict)
        if raw_locator is None:
            return None

        if anchor_type == "file":
            normalized_locator = raw_locator.replace("\\", "/")
            while normalized_locator.startswith("./"):
                normalized_locator = normalized_locator[2:]
            normalized_locator = str(PurePosixPath(normalized_locator))
            if normalized_locator in {".", ""}:
                raise ValueError("anchor.locator must be a repo-relative file path")
            return normalized_locator

        if anchor_type == "commit":
            return raw_locator.lower()

        prefix = f"{anchor_type}/"
        suffix = raw_locator
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix) :]
        if not suffix.isdigit():
            raise ValueError(
                f"anchor.locator for {anchor_type} anchors must use the form {prefix}<number>"
            )
        return f"{prefix}{suffix}"

    def _apply_anchor_index(
        self,
        container: dict[str, Any],
        anchor: dict[str, Any] | None,
    ) -> None:
        for key in _ANCHOR_INDEX_KEYS:
            container.pop(key, None)

        if anchor is None:
            return

        provenance = anchor["provenance"]
        container.update(
            {
                "anchor_type": anchor["type"],
                "anchor_repo": anchor["repo"],
                "anchor_locator": anchor["locator"],
                "anchor_ref": anchor["ref"],
                "anchor_commit_sha": anchor["commit_sha"],
                "anchor_url": anchor["url"],
                "anchor_title": anchor["title"],
                "anchor_created_at": anchor["created_at"],
                "anchor_observed_at": anchor["observed_at"],
                "anchor_is_stale": anchor["is_stale"],
                "anchor_provenance_mode": provenance["mode"],
                "anchor_provenance_source": provenance["source"],
                "anchor_commit_pinned": provenance["commit_pinned"],
                "anchor_is_verified": provenance["mode"] == "verified",
                "anchor_is_derived": provenance["mode"] == "derived",
            }
        )

    def _apply_partial_anchor_index(
        self,
        container: dict[str, Any],
        anchor_filter: dict[str, Any] | None,
    ) -> None:
        for key in _ANCHOR_INDEX_KEYS:
            container.pop(key, None)

        if anchor_filter is None:
            return

        if "type" in anchor_filter:
            container["anchor_type"] = anchor_filter["type"]
        if "repo" in anchor_filter:
            container["anchor_repo"] = anchor_filter["repo"]
        if "locator" in anchor_filter:
            container["anchor_locator"] = anchor_filter["locator"]
        if "ref" in anchor_filter:
            container["anchor_ref"] = anchor_filter["ref"]
        if "commit_sha" in anchor_filter:
            container["anchor_commit_sha"] = anchor_filter["commit_sha"]
        if "url" in anchor_filter:
            container["anchor_url"] = anchor_filter["url"]
        if "title" in anchor_filter:
            container["anchor_title"] = anchor_filter["title"]
        if "created_at" in anchor_filter:
            container["anchor_created_at"] = anchor_filter["created_at"]
        if "observed_at" in anchor_filter:
            container["anchor_observed_at"] = anchor_filter["observed_at"]
        if "is_stale" in anchor_filter:
            container["anchor_is_stale"] = anchor_filter["is_stale"]

        provenance = anchor_filter.get("provenance")
        if not isinstance(provenance, dict):
            return

        if "mode" in provenance:
            mode = provenance["mode"]
            container["anchor_provenance_mode"] = mode
            container["anchor_is_verified"] = mode == "verified"
            container["anchor_is_derived"] = mode == "derived"
        if "source" in provenance:
            container["anchor_provenance_source"] = provenance["source"]
        if "commit_pinned" in provenance:
            container["anchor_commit_pinned"] = provenance["commit_pinned"]

    @staticmethod
    def _looks_like_memory_record(payload: dict[str, Any]) -> bool:
        return any(
            key in payload for key in ("id", "memory", "messages", "metadata", "anchor")
        )

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("anchor fields must be strings when provided")
        stripped_value = value.strip()
        return stripped_value or None

    def _required_string(
        self,
        value: Any,
        field_name: str,
        *,
        strict: bool,
    ) -> str | None:
        normalized_value = self._optional_string(value)
        if normalized_value is None and strict:
            raise ValueError(f"{field_name} is required")
        return normalized_value
