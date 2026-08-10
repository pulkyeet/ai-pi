from __future__ import annotations

from api.models.entity import EntityScheme
from api.tasks.discover import NON_CANDIDATE_DOMAINS, _is_github_list_repo, candidate_from_url


class TestCandidateFromUrl:
    def test_ordinary_domain_becomes_web_candidate(self) -> None:
        result = candidate_from_url("https://expensify.com/pricing", "Expensify")
        assert result == (EntityScheme.WEB, "expensify.com", "Expensify")

    def test_www_prefix_is_stripped(self) -> None:
        result = candidate_from_url("https://www.expensify.com/", "Expensify")
        assert result == (EntityScheme.WEB, "expensify.com", "Expensify")

    def test_missing_title_falls_back_to_host(self) -> None:
        result = candidate_from_url("https://foo.com/x", "")
        assert result == (EntityScheme.WEB, "foo.com", "foo.com")

    def test_github_repo_url_becomes_gh_candidate(self) -> None:
        result = candidate_from_url("https://github.com/acme/widget", "acme/widget")
        assert result == (EntityScheme.GH, "acme/widget", "acme/widget")

    def test_github_repo_url_with_subpath_still_resolves(self) -> None:
        result = candidate_from_url("https://github.com/acme/widget/issues/12", "some issue")
        assert result == (EntityScheme.GH, "acme/widget", "some issue")

    def test_bare_github_host_is_not_a_candidate(self) -> None:
        assert candidate_from_url("https://github.com/", "GitHub") is None

    def test_github_org_page_is_not_a_repo_candidate(self) -> None:
        assert candidate_from_url("https://github.com/orgs/acme", "acme org") is None

    def test_every_declared_non_candidate_domain_is_rejected(self) -> None:
        for domain in NON_CANDIDATE_DOMAINS:
            if domain == "github.com":
                continue  # covered by its own repo-path-aware tests above
            assert candidate_from_url(f"https://{domain}/whatever", "x") is None

    def test_subdomain_of_a_non_candidate_domain_is_rejected(self) -> None:
        assert candidate_from_url("https://reviews.g2.com/foo", "x") is None

    def test_unparseable_url_returns_none(self) -> None:
        assert candidate_from_url("not a url", "x") is None


class TestGithubListRepoFilter:
    """Phase 14 follow-up: GitHub seeding must never profile `awesome-*`
    curated lists — a list of a hundred projects is not a product."""

    def test_awesome_prefixed_repo_is_a_list(self) -> None:
        assert _is_github_list_repo("sindresorhus/awesome", "A curated list of awesome things")

    def test_curated_list_description_is_a_list(self) -> None:
        assert _is_github_list_repo("acme/tools", "A curated list of awesome software tools")

    def test_list_of_best_description_is_a_list(self) -> None:
        assert _is_github_list_repo("acme/things", "A list of the best useful libraries")

    def test_awesome_in_description_is_a_list(self) -> None:
        assert _is_github_list_repo("acme/x", "An awesome list of resources")

    def test_real_oss_product_repo_is_not_a_list(self) -> None:
        assert not _is_github_list_repo(
            "facebook/docusaurus", "Docusaurus is a static site generator"
        )

    def test_none_description_is_not_a_list(self) -> None:
        assert not _is_github_list_repo("acme/widget", None)
