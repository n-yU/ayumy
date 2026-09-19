"""Tests for the assets report.summarizer.resources loads and fills in."""

import jsonschema
import pytest

from report.summarizer import resources, tags


class TestFill:
    def test_replaces_placeholder_inside_text(self):
        assert resources._fill("tool: $tool_name") == f"tool: {resources.TOOL_NAME}"

    def test_placeholder_standing_alone_keeps_value_type(self):
        assert resources._fill("$allowed_tags") == list(tags.ALLOWED_NAMES)

    def test_walks_nested_containers(self):
        node = {"outer": [{"inner": "$tool_name"}]}
        assert resources._fill(node) == {"outer": [{"inner": resources.TOOL_NAME}]}

    def test_leaves_non_text_values_untouched(self):
        assert resources._fill({"minItems": 2, "flag": True}) == {
            "minItems": 2,
            "flag": True,
        }

    def test_raises_when_placeholder_has_no_value(self):
        with pytest.raises(KeyError):
            resources._fill("$unknown_value")


class TestToolDefinition:
    def _repo_properties(self) -> dict:
        return resources.TOOL_DEFINITION["input_schema"]["properties"]["repositories"][
            "items"
        ]["properties"]

    def test_names_tool_the_prompt_refers_to(self):
        assert resources.TOOL_DEFINITION["name"] == resources.TOOL_NAME

    def test_enforces_allowed_tags_via_enum(self):
        assert self._repo_properties()["tags"]["items"]["enum"] == list(
            tags.ALLOWED_NAMES
        )

    def test_tags_field_carries_description_per_tag(self):
        description = self._repo_properties()["tags"]["description"]
        for tag in tags.DEFINITIONS:
            assert tag.name in description
            assert tag.description in description

    def test_bounds_summary_item_count(self):
        summary_field = self._repo_properties()["summary"]
        assert summary_field["minItems"] == 2
        assert summary_field["maxItems"] == 5


class TestResponseShape:
    def _repo_properties(self) -> dict:
        return resources.RESPONSE_SHAPE["properties"]["repositories"]["items"][
            "properties"
        ]

    def test_keeps_structure_of_tool_definition(self):
        assert resources.RESPONSE_SHAPE["required"] == ["repositories"]
        items = resources.RESPONSE_SHAPE["properties"]["repositories"]["items"]
        assert items["required"] == ["name", "summary", "tags"]
        assert sorted(self._repo_properties()) == ["name", "summary", "tags"]

    def test_drops_item_count_bounds(self):
        summary_field = self._repo_properties()["summary"]
        assert "minItems" not in summary_field
        assert "maxItems" not in summary_field

    def test_drops_allowed_tags_enum(self):
        assert "enum" not in self._repo_properties()["tags"]["items"]

    def test_drops_constraint_keyword_it_has_never_seen(self):
        schema = {"type": "string", "pattern": "^x$"}
        assert resources._shape_only(schema) == {"type": "string"}

    def test_accepts_tag_outside_allowlist(self):
        report = {
            "repositories": [
                {"name": "repo", "summary": ["s1", "s2"], "tags": ["InvalidTag"]}
            ]
        }
        jsonschema.validate(report, resources.RESPONSE_SHAPE)

    def test_accepts_summary_outside_item_count_bounds(self):
        report = {
            "repositories": [
                {
                    "name": "repo",
                    "summary": ["only one"],
                    "tags": [tags.ALLOWED_NAMES[0]],
                }
            ]
        }
        jsonschema.validate(report, resources.RESPONSE_SHAPE)


class TestSystemPrompt:
    def test_names_tool_the_model_must_call(self):
        assert resources.TOOL_NAME in resources.SYSTEM_PROMPT

    def test_lists_each_tag_with_description(self):
        for tag in tags.DEFINITIONS:
            assert tag.name in resources.SYSTEM_PROMPT
            assert tag.description in resources.SYSTEM_PROMPT

    @pytest.mark.parametrize(
        "rule",
        [
            pytest.param("種別を置くことは", id="no_kind_prefix"),
            pytest.param("カッコで囲まず", id="no_parenthesized_number"),
            pytest.param("claude-config#12", id="cross_repository_number"),
            pytest.param("PR-3 / Phase 2 / Commit 1", id="plan_identifier"),
            pytest.param("(#180: PR-2)", id="unopened_pull_binding"),
            pytest.param("**...**", id="allowed_decoration"),
        ],
    )
    def test_states_each_notation_rule(self, rule):
        assert rule in resources.SYSTEM_PROMPT
