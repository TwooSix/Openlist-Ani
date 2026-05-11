from types import SimpleNamespace

import openlist_ani.bootstrap.backend as backend


def test_regex_parser_mode_does_not_create_validator_llm_client(monkeypatch):
    monkeypatch.setattr(
        backend,
        "config",
        SimpleNamespace(
            metadata_parser=SimpleNamespace(provider="regex"),
            llm=SimpleNamespace(
                openai_api_key="configured",
                provider_type="openai",
                openai_base_url="https://example.invalid/v1",
                openai_model="unused",
            ),
        ),
    )

    def fail_if_called(_settings):
        raise AssertionError("regex+tmdb validation must not create an LLM client")

    monkeypatch.setattr(backend, "create_llm_client", fail_if_called)

    assert backend._create_validator_llm_client() is None
