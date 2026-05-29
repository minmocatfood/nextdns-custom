"""Tests for delta-aware list operations."""

import requests_mock as rm

from nextdnsctl.api import API_BASE
from nextdnsctl.nextdnsctl import (
    _plan_domain_additions,
    _plan_domain_removals,
    cli,
)


class TestDeltaPlanning:
    """Tests for pure delta planning helpers."""

    def test_add_plan_skips_existing_and_duplicates(self):
        plan = _plan_domain_additions(
            ["new.com", "existing.com", "inactive.com", "new.com"],
            [
                {"id": "existing.com", "active": True},
                {"id": "inactive.com", "active": False},
            ],
            desired_active=True,
            update_existing=False,
        )

        assert plan.to_add == ["new.com"]
        assert plan.to_update == []
        assert plan.already_present == ["existing.com"]
        assert plan.skipped_mismatched == ["inactive.com"]
        assert plan.duplicate_input == ["new.com"]

    def test_add_plan_updates_state_mismatches_when_requested(self):
        plan = _plan_domain_additions(
            ["inactive.com"],
            [{"id": "inactive.com", "active": False}],
            desired_active=True,
            update_existing=True,
        )

        assert plan.to_add == []
        assert plan.to_update == ["inactive.com"]
        assert plan.skipped_mismatched == []

    def test_remove_plan_skips_missing_and_duplicates(self):
        plan = _plan_domain_removals(
            ["present.com", "missing.com", "present.com"],
            [{"id": "present.com", "active": True}],
        )

        assert plan.to_remove == ["present.com"]
        assert plan.missing == ["missing.com"]
        assert plan.duplicate_input == ["present.com"]


class TestDeltaAwareCli:
    """CLI integration tests for delta-aware behavior."""

    def test_import_only_posts_missing_domains(self, runner, mock_api_key, mock_profiles_response, tmp_path):
        domains_file = tmp_path / "domains.txt"
        domains_file.write_text("new.com\nexisting.com\nnew.com\n")

        with rm.Mocker() as m:
            m.get(f"{API_BASE}profiles", json=mock_profiles_response)
            m.get(
                f"{API_BASE}profiles/abc1234/denylist",
                json={"data": [{"id": "existing.com", "active": True}]},
            )
            post = m.post(f"{API_BASE}profiles/abc1234/denylist", status_code=204)

            result = runner.invoke(
                cli,
                ["--concurrency", "1", "denylist", "import", "abc1234", str(domains_file)],
            )

            assert result.exit_code == 0
            assert post.call_count == 1
            assert post.last_request.json() == {"id": "new.com", "active": True}
            assert "Already present: 1" in result.output
            assert "Duplicate input skipped: 1" in result.output

    def test_import_updates_existing_state_when_requested(self, runner, mock_api_key, mock_profiles_response, tmp_path):
        domains_file = tmp_path / "domains.txt"
        domains_file.write_text("inactive.com\nnew.com\n")

        with rm.Mocker() as m:
            m.get(f"{API_BASE}profiles", json=mock_profiles_response)
            m.get(
                f"{API_BASE}profiles/abc1234/denylist",
                json={"data": [{"id": "inactive.com", "active": False}]},
            )
            post = m.post(f"{API_BASE}profiles/abc1234/denylist", status_code=204)
            patch = m.patch(f"{API_BASE}profiles/abc1234/denylist/inactive.com", status_code=204)

            result = runner.invoke(
                cli,
                [
                    "--concurrency",
                    "1",
                    "denylist",
                    "import",
                    "abc1234",
                    str(domains_file),
                    "--update-existing",
                ],
            )

            assert result.exit_code == 0
            assert post.call_count == 1
            assert post.last_request.json() == {"id": "new.com", "active": True}
            assert patch.call_count == 1
            assert patch.last_request.json() == {"active": True}

    def test_import_skips_state_mismatch_without_update_existing(
        self, runner, mock_api_key, mock_profiles_response, tmp_path
    ):
        domains_file = tmp_path / "domains.txt"
        domains_file.write_text("inactive.com\n")

        with rm.Mocker() as m:
            m.get(f"{API_BASE}profiles", json=mock_profiles_response)
            m.get(
                f"{API_BASE}profiles/abc1234/denylist",
                json={"data": [{"id": "inactive.com", "active": False}]},
            )
            patch = m.patch(f"{API_BASE}profiles/abc1234/denylist/inactive.com", status_code=204)

            result = runner.invoke(cli, ["denylist", "import", "abc1234", str(domains_file)])

            assert result.exit_code == 0
            assert patch.call_count == 0
            assert "State mismatches skipped: 1" in result.output
            assert "Use --update-existing" in result.output

    def test_remove_only_deletes_present_domains(self, runner, mock_api_key, mock_profiles_response):
        with rm.Mocker() as m:
            m.get(f"{API_BASE}profiles", json=mock_profiles_response)
            m.get(
                f"{API_BASE}profiles/abc1234/denylist",
                json={"data": [{"id": "present.com", "active": True}]},
            )
            delete = m.delete(f"{API_BASE}profiles/abc1234/denylist/present.com", status_code=204)

            result = runner.invoke(
                cli,
                ["--concurrency", "1", "denylist", "remove", "abc1234", "present.com", "missing.com"],
            )

            assert result.exit_code == 0
            assert delete.call_count == 1
            assert "Missing domains skipped: 1" in result.output
