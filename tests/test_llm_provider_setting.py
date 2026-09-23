"""The admin's choice of who pays for the AI, shared by both calculators."""
import pytest

from app.core import llm_settings


def test_the_default_is_aws(db):
    """Shipped default: the usage draws on AWS (user, 2026-09-24)."""
    assert llm_settings.stored() is None
    assert llm_settings.provider() == llm_settings.AWS
    assert llm_settings.DEFAULT == llm_settings.AWS


def test_an_admin_choice_is_stored_and_wins(db):
    llm_settings.set_provider("openai", "admin")
    assert llm_settings.stored() == "openai" and llm_settings.provider() == "openai"
    llm_settings.set_provider("aws", "admin")
    assert llm_settings.provider() == "aws"


def test_an_unknown_provider_is_refused(db):
    from app.core.errors import UserError
    for bad in ("azure", "", "AWS ", None):
        if bad == "AWS ":
            assert llm_settings.set_provider(bad, "a") == "aws"   # trimmed and lowercased
            continue
        with pytest.raises(UserError):
            llm_settings.set_provider(bad, "a")


def test_aws_without_credentials_falls_back_rather_than_failing(db, monkeypatch):
    """A server with no AWS credentials must keep rating, not fail every call."""
    monkeypatch.setattr(llm_settings, "aws_credentials_available", lambda: False)
    llm_settings.set_provider("aws", "admin")
    assert llm_settings.provider() == "aws"        # what the admin chose
    assert llm_settings.resolve() == "openai"      # what is actually used


def test_the_endpoint_reads_and_sets_it(admin_client, db, monkeypatch):
    monkeypatch.setattr(llm_settings, "aws_credentials_available", lambda: True)
    body = admin_client.get("/api/admin/settings/llm-provider").json()
    assert body["provider"] == "aws" and body["chosen_by_admin"] is False
    assert [o["value"] for o in body["options"]] == ["aws", "openai"]

    out = admin_client.put("/api/admin/settings/llm-provider", json={"provider": "openai"})
    assert out.status_code == 200 and out.json()["provider"] == "openai"
    assert out.json()["chosen_by_admin"] is True
    assert admin_client.get("/api/admin/settings/llm-provider").json()["provider"] == "openai"

    assert admin_client.put("/api/admin/settings/llm-provider",
                            json={"provider": "azure"}).status_code == 422


def test_the_endpoint_needs_an_admin(client, db):
    assert client.get("/api/admin/settings/llm-provider").status_code == 401
    assert client.put("/api/admin/settings/llm-provider", json={"provider": "aws"}).status_code == 401
