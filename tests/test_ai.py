"""The assistant's resilience: provider chain, failover, the shared daily
budget, and the answer path that runs when no model does.

The rule under test is that /ask never dead-ends. Either a provider answers, or
the next one does, or the ledger does — and the token budget that guards the
model is counted once for all three processes that serve questions, not once
per process.
"""

import asyncio
import io
import urllib.error

import pytest

from bot import ai, ai_data, i18n

ALICE = 4001
USDC = 10**6

def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_providers(monkeypatch):
    """No test inherits the machine's real .env keys, a local model server or the
    public fallback. The chain tests below are about order and parking; the
    auto-discovered members are covered by their own tests, which switch them on."""
    monkeypatch.setattr(ai.config, "AI_PROVIDER_CHAIN", "")
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("AI_API_URL", "https://primary.invalid/v1")
    monkeypatch.setattr(ai.config, "AI_API_URL", "https://primary.invalid/v1")
    _off(monkeypatch)
    yield


def _off(monkeypatch):
    """Every automatic source of providers, switched off."""
    monkeypatch.setattr(ai.config, "AI_AUTODISCOVER_KEYS", False)
    monkeypatch.setattr(ai.config, "AI_FREE_ENDPOINT", False)
    monkeypatch.setattr(ai.config, "AI_LOCAL_SERVERS", "")
    ai._LOCAL.update(at=0.0, providers=[])


def _no_model(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr(ai.config, "AI_PROVIDER_CHAIN", "")
    _off(monkeypatch)


# ---------- the chain ----------


def test_primary_is_always_first_and_malformed_entries_are_skipped(monkeypatch):
    monkeypatch.setattr(
        ai.config, "AI_PROVIDER_CHAIN",
        "backup|https://backup.invalid/v1|BACKUP_KEY|m2 , oops|a|b ,nameonly",
    )
    monkeypatch.setenv("AI_API_KEY", "k1")
    monkeypatch.setenv("BACKUP_KEY", "k2")
    assert [p.name for p in ai.chain()] == ["primary", "backup"]
    assert [(p.name, p.key) for p in ai.configured()] == [("primary", "k1"), ("backup", "k2")]


def test_a_typo_in_the_chain_does_not_take_the_working_provider_with_it(monkeypatch):
    monkeypatch.setattr(ai.config, "AI_PROVIDER_CHAIN", "|https://x.invalid/v1|K|m")
    monkeypatch.setenv("AI_API_KEY", "k1")
    assert [p.name for p in ai.configured()] == ["primary"]


def test_the_key_is_never_part_of_what_gets_stored():
    provider = ai._entry("x", "https://e.invalid/v1", "SOME_KEY", "m")
    assert set(provider.persist()) == {"pid", "name", "url", "model", "key_env"}
    assert provider.key == ""


def test_both_endpoint_spellings_reach_the_same_provider(monkeypatch):
    monkeypatch.setattr(
        ai.config, "AI_PROVIDER_CHAIN",
        "full|https://e.invalid/v1/chat/completions|FULL_KEY|m",
    )
    full = next(p for p in ai.chain() if p.name == "full")
    assert full.url == "https://e.invalid/v1"


# ---------- what the chain finds by itself ----------


def test_a_key_already_in_the_environment_becomes_a_provider(monkeypatch):
    """Nobody edited AI_PROVIDERS here. The point of the map in bot/ai.py is that
    an operator who holds a key for a known service gets it used, verified against
    the real endpoint, and parked by the same rules as a hand-written entry."""
    monkeypatch.setattr(ai.config, "AI_AUTODISCOVER_KEYS", True)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake_for_test")
    found = {p.name: p for p in ai.chain()}
    assert "groq" in found
    assert found["groq"].key == "gsk_fake_for_test"
    assert found["groq"].kind == "discovered"
    assert "groq" in [p.name for p in ai.configured()]


def test_an_empty_environment_variable_is_not_a_provider(monkeypatch):
    monkeypatch.setattr(ai.config, "AI_AUTODISCOVER_KEYS", True)
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert "openai" not in [p.name for p in ai.chain()]


def test_a_local_model_server_joins_the_chain_without_a_key(monkeypatch):
    """Ollama, LM Studio, vLLM and llama.cpp all answer GET /v1/models, so starting
    one on this host is enough — the assistant finds it and needs no credential."""
    monkeypatch.setattr(ai.config, "AI_LOCAL_SERVERS", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(ai, "_probe_local", lambda base: "qwen2.5:1.5b")
    local = [p for p in ai.chain() if p.kind == "local"]
    assert [(p.url, p.model, p.keyless) for p in local] == [
        ("http://127.0.0.1:11434/v1", "qwen2.5:1.5b", True)
    ]
    assert local[0].pid in {p.pid for p in ai.configured()}


def test_a_server_that_is_not_there_offers_nothing(monkeypatch):
    monkeypatch.setattr(ai.config, "AI_LOCAL_SERVERS", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(ai, "_probe_local", lambda base: "")
    assert [p for p in ai.chain() if p.kind == "local"] == []
    assert ai.configured() == []


def test_the_probe_is_repeated_only_after_its_ttl(monkeypatch):
    """Probing happens while a user waits for an answer, so it is cached — but a
    server started later has to be noticed without a restart."""
    calls = []
    monkeypatch.setattr(ai.config, "AI_LOCAL_SERVERS", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(ai.config, "AI_LOCAL_REPROBE_SECONDS", 120)
    monkeypatch.setattr(ai, "_probe_local", lambda base: calls.append(base) or "m1")
    ai.chain()
    ai.chain()
    ai.chain()
    assert len(calls) == 1
    monkeypatch.setattr(ai.config, "AI_LOCAL_REPROBE_SECONDS", 0)
    ai.chain()
    assert len(calls) == 2


def test_the_first_probe_happens_on_a_freshly_booted_host(monkeypatch):
    """time.monotonic() counts from boot, so on a just-started runner it is smaller
    than the TTL. The empty cache then looks fresh and the probe never runs — the
    assistant misses a model server that was already up when it started."""
    monkeypatch.setattr(ai.time, "monotonic", lambda: 5.0)
    monkeypatch.setattr(ai.config, "AI_LOCAL_SERVERS", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(ai.config, "AI_LOCAL_REPROBE_SECONDS", 120)
    monkeypatch.setattr(ai, "_probe_local", lambda base: "m1")
    assert [p.model for p in ai.chain() if p.kind == "local"] == ["m1"]


def test_the_public_fallback_is_last_and_needs_no_key(monkeypatch):
    monkeypatch.setattr(ai.config, "AI_PROVIDER_CHAIN", "backup|https://b.invalid/v1|BK|m2")
    monkeypatch.setenv("BK", "k2")
    monkeypatch.setattr(ai.config, "AI_FREE_ENDPOINT", True)
    monkeypatch.setattr(ai.config, "AI_FREE_URL", "https://free.invalid/v1")
    members = ai.chain()
    assert members[-1].kind == "free"
    assert members[-1].key == "" and members[-1].keyless is True
    # A configured provider is still tried before the anonymous one.
    assert [p.name for p in members] == ["primary", "backup", "public"]


def test_a_keyless_request_carries_no_authorization_header(monkeypatch):
    """`Bearer ` with an empty token is how a local server and an anonymous
    endpoint answer 401, which then parks a provider that was never broken."""
    captured = {}

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return FakeResponse(b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}')

    monkeypatch.setattr(ai.config, "AI_FREE_ENDPOINT", True)
    provider = next(p for p in ai.chain() if p.kind == "free")
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    ai._post(provider, {"model": "m", "messages": []})
    assert not any("uthorization" in k for k in captured["headers"]), captured["headers"]


def test_status_says_why_a_member_cannot_be_used(monkeypatch):
    monkeypatch.setattr(ai.config, "AI_PROVIDER_CHAIN", "backup|https://b.invalid/v1|BK|m2")
    monkeypatch.delenv("BK", raising=False)
    monkeypatch.setattr(ai.config, "AI_FREE_ENDPOINT", True)
    state = ai.status()
    by_name = {p["name"]: p for p in state["providers"]}
    assert by_name["backup"]["usable"] is False
    assert by_name["backup"]["key_present"] is False
    assert by_name["public"]["usable"] is True and by_name["public"]["keyless"] is True
    assert state["mode"] == "llm"


def test_the_probe_survives_every_shape_a_local_server_may_answer(monkeypatch):
    """/v1/models is a convention, not a contract: Ollama and vLLM return
    {"data":[{...}]}, some llama.cpp builds return a bare list, and a malformed
    answer must cost the assistant nothing — not a 500 on the question."""
    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def serve(body: bytes):
        monkeypatch.setattr(ai.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(body))

    serve(b'{"data":[{"id":"qwen2.5:3b"}]}')
    assert ai._probe_local("http://127.0.0.1:11434/v1") == "qwen2.5:3b"
    serve(b'["llama-3.2-1b"]')
    assert ai._probe_local("http://127.0.0.1:11434/v1") == "llama-3.2-1b"
    serve(b'{"error":"no models loaded"}')
    assert ai._probe_local("http://127.0.0.1:11434/v1") == ""
    serve(b"<html>not json</html>")
    assert ai._probe_local("http://127.0.0.1:11434/v1") == ""


def test_a_bad_credential_is_parked_longer_than_a_rate_limit():
    assert ai._park_seconds(401) > ai._park_seconds(429)
    assert ai._park_seconds(403) == ai._park_seconds(401)
    # 400 is our payload's fault, not the endpoint's: parking would hide a bug.
    assert ai._park_seconds(400) == 0


# ---------- failover ----------


def test_failover_parks_the_rate_limited_provider_and_uses_the_next(ledger, monkeypatch):
    monkeypatch.setattr(
        ai.config, "AI_PROVIDER_CHAIN", "backup|https://b.invalid/v1|BACKUP_KEY|m2"
    )
    monkeypatch.setenv("AI_API_KEY", "k1")
    monkeypatch.setenv("BACKUP_KEY", "k2")
    seen = []

    def fake_post(provider, payload):
        seen.append(provider.name)
        if provider.name == "primary":
            raise ai.ProviderError("primary HTTP 429: slow down", park_seconds=120)
        return {"choices": [{"message": {"content": "from backup"}}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    message = ai.call([{"role": "user", "content": "hi"}])

    assert message["content"] == "from backup"
    assert seen == ["primary", "backup"]
    rows = {r["name"]: r for r in ledger.ai_provider_rows()}
    assert rows["primary"]["parked"] is True
    assert rows["primary"]["fail_count"] == 1
    assert rows["backup"]["parked"] is False


def test_a_parked_provider_is_not_retried_until_its_park_expires(ledger, monkeypatch):
    """Waiting out a 45 s timeout per question is not a degradation, it is a
    hang. The park expires on its own, so nothing is lost by skipping it."""
    monkeypatch.setenv("AI_API_KEY", "k1")
    primary = ai.chain()[0]
    ledger.ai_provider_fail(error="HTTP 500: boom", park_seconds=600, **primary.persist())
    assert ai.candidates() == []
    with pytest.raises(RuntimeError) as exc:
        ai.call([{"role": "user", "content": "hi"}])
    assert "parked" in str(exc.value)


def test_success_clears_an_earlier_failure(ledger, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "k1")
    primary = ai.chain()[0]
    ledger.ai_provider_fail(error="HTTP 500", park_seconds=600, **primary.persist())
    monkeypatch.setattr(
        ai, "_post", lambda p, payload: {"choices": [{"message": {"content": "ok"}}]}
    )
    # candidates() hides it, so the failover retry path is what answers here.
    monkeypatch.setattr(ai, "candidates", lambda: [primary])
    ai.call([{"role": "user", "content": "hi"}])
    row = next(r for r in ledger.ai_provider_rows() if r["id"] == primary.pid)
    assert row["parked"] is False and row["fail_count"] == 0 and row["last_error"] is None


def test_every_provider_failing_raises_one_message_the_user_can_read(ledger, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "k1")
    monkeypatch.setattr(
        ai.config, "AI_PROVIDER_CHAIN", "backup|https://b.invalid/v1|BACKUP_KEY|m2"
    )
    monkeypatch.setenv("BACKUP_KEY", "k2")

    def down(provider, payload):
        raise ai.ProviderError(f"{provider.name} unreachable: TimeoutError", park_seconds=60)

    monkeypatch.setattr(ai, "_post", down)
    with pytest.raises(RuntimeError) as exc:
        ai.call([{"role": "user", "content": "hi"}])
    assert "unreachable" in str(exc.value)
    assert len([r for r in ledger.ai_provider_rows() if r["parked"]]) == 2


def test_a_provider_error_body_never_reaches_the_user(ledger, monkeypatch):
    """Endpoints echo the Authorization header in their 401/429 bodies. The
    status is safe to show; the body is only safe to store redacted."""
    monkeypatch.setenv("AI_API_KEY", "k1")
    body = b'{"error":{"message":"invalid key sk-supersecret-value"}}'

    err = urllib.error.HTTPError(
        "https://primary.invalid/v1/chat/completions", 401, "Unauthorized", {},
        io.BytesIO(body),
    )

    def raise_401(*args, **kwargs):
        raise err

    # The real _post, so the translation of an HTTPError into a user-facing
    # message is what is under test.
    monkeypatch.setattr(ai.urllib.request, "urlopen", raise_401)
    with pytest.raises(RuntimeError) as exc:
        ai.call([{"role": "user", "content": "hi"}])
    assert "sk-supersecret-value" not in str(exc.value)
    assert str(exc.value) == "primary HTTP 401"
    row = ledger.ai_provider_rows()[0]
    assert row["parked"] is True
    assert "sk-supersecret-value" not in row["last_error"]
    assert "invalid key" in row["last_error"]


# ---------- the shared daily budget ----------


def test_no_key_means_no_spending_and_a_raise(ledger, monkeypatch):
    _no_model(monkeypatch)
    with pytest.raises(RuntimeError):
        run(ai.ask_about_markets("какие рынки?"))
    assert ledger.ai_usage_today() == 0


def test_the_budget_is_counted_once_per_answer(ledger, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "k1")
    monkeypatch.setattr(ai.config, "AI_DAILY_ANSWERS", 2)
    async def answered(q, rounds):
        return "ok"

    monkeypatch.setattr(ai, "_ask_llm", answered)

    assert run(ai.ask_about_markets("a")) == "ok"
    assert run(ai.ask_about_markets("b")) == "ok"
    with pytest.raises(RuntimeError):
        run(ai.ask_about_markets("c"))
    assert ledger.ai_usage_today() == 2


def test_a_failed_question_does_not_eat_the_budget(ledger, monkeypatch):
    """The point of the guard is token spend. A provider that never answered
    spent none, so it must not be paid for out of the user's daily allowance."""
    monkeypatch.setenv("AI_API_KEY", "k1")
    monkeypatch.setattr(ai.config, "AI_DAILY_ANSWERS", 5)

    def down(provider, payload):
        raise ai.ProviderError("primary HTTP 503: down", park_seconds=60)

    monkeypatch.setattr(ai, "_post", down)
    with pytest.raises(RuntimeError):
        run(ai.ask_about_markets("a"))
    assert ledger.ai_usage_today() == 0


def test_the_counter_is_the_same_row_for_a_second_process(ledger, monkeypatch):
    """ai_usage_day is per UTC day with an atomic increment, so a second
    Ledger connection (what uvicorn and the bot really are) sees the same
    number rather than starting its own at zero."""
    from conftest import TEST_DB_URL

    from bot.ledger import Ledger

    peer = Ledger(TEST_DB_URL)  # what a second process would be
    try:
        assert ledger.ai_reserve_answer(1) is True
        assert ledger.ai_reserve_answer(1) is False
        assert peer.ai_usage_today() == 1
    finally:
        peer._conn.close()


# ---------- answering without a model ----------


def _market(ledger, question="Перепишет ли Tippy 1000 чаевых до конца месяца?", options=("Да", "Нет")):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    mid = ledger.create_market(ALICE, question, list(options), 10 * USDC)
    assert isinstance(mid, int), mid
    return mid


def test_lists_open_markets_with_real_odds(ledger, monkeypatch):
    _no_model(monkeypatch)
    mid = _market(ledger)
    text = run(ai_data.answer("какие рынки сейчас открыты?", "ru"))
    assert f"#{mid}" in text
    assert i18n.t("ru", "ai_data_markets_head", n=1) in text
    assert "%" in text and "USDC" in text
    assert text.endswith(i18n.t("ru", "ai_data_footer"))


def test_resolves_a_market_named_by_id(ledger, monkeypatch):
    _no_model(monkeypatch)
    mid = _market(ledger, question="Будет ли ETH выше 4000 USD?")
    text = run(ai_data.answer(f"шансы рынка {mid}", "ru"))
    assert f"#{mid}" in text and "ETH" in text
    assert i18n.t("ru", "ai_data_markets_head", n=1) not in text


def test_a_market_id_is_not_resolved_as_a_bet(ledger, monkeypatch):
    """#7 is a market, "ставка 7" is a pool bet: the same integer means two
    different things in this bot and the wording decides."""
    _no_model(monkeypatch)
    bid = ledger.create_bet(ALICE, "Выиграет ли Аргентина ЧМ-2026?", ["Да", "Нет"])
    market_text = run(ai_data.answer(f"рынок {bid}", "ru"))
    assert i18n.t("ru", "ai_data_not_found") in market_text
    bet_text = run(ai_data.answer(f"ставка {bid}", "ru"))
    assert "Аргентина" in bet_text
    assert i18n.t("ru", "ai_data_bet_meta", amount="0.00") in bet_text


def test_lists_pool_bets(ledger, monkeypatch):
    _no_model(monkeypatch)
    ledger.create_bet(ALICE, "Сколько транзакций в сутки будет в Base?", ["< 5M", "> 10M"])
    text = run(ai_data.answer("дай мне список ставок", "ru"))
    assert i18n.t("ru", "ai_data_bets_head", n=1) in text
    assert "Base" in text


def test_an_ordinary_greeting_is_not_mistaken_for_a_bet_question(ledger, monkeypatch):
    """The pool intent matches the stem "став", which also sits inside
    "здравствуй" — the word boundary is what keeps them apart."""
    _no_model(monkeypatch)
    # An empty database makes the pool branch answer "no bets yet", which
    # proves the greeting reached it; with a pool present the branch answers
    # with its header instead.
    ledger.create_bet(ALICE, "Выиграет ли Аргентина ЧМ-2026?", ["Да", "Нет"])
    text = run(ai_data.answer("здравствуй, как дела?", "ru"))
    assert text.startswith(i18n.t("ru", "ai_data_none"))
    assert "Открытые ставки" not in text


def test_the_help_answer_carries_no_telegram_markup_in_any_language(ledger, monkeypatch):
    _no_model(monkeypatch)
    for lang in ("ru", "en", "zh"):
        text = run(ai_data.answer("что ты умеешь?", lang))
        assert "<b>" not in text and "</code>" not in text
        # help_full escapes the angle brackets of its own argument placeholders;
        # a plain-text answer has to give them back as characters.
        assert "&lt;" not in text and "&gt;" not in text
        assert "/tip" in text


def test_a_named_command_is_echoed_before_the_help_card(ledger, monkeypatch):
    _no_model(monkeypatch)
    text = run(ai_data.answer("как отправить чаевые? /tip", "ru"))
    assert text.startswith(i18n.t("ru", "ai_data_cmd_head"))
    assert "/tip" in text.splitlines()[1]


def test_an_empty_market_list_says_so(ledger, monkeypatch):
    _no_model(monkeypatch)
    text = run(ai_data.answer("какие рынки открыты?", "ru"))
    assert "Открытых рынков нет" in text


@pytest.mark.parametrize("lang", ["ru", "en", "zh"])
def test_every_no_model_answer_is_labelled_as_such(ledger, monkeypatch, lang):
    """The user is told a model did not write this, whichever branch answered —
    otherwise a lookup reads as generated prose."""
    _no_model(monkeypatch)
    _market(ledger)
    for question in ("какие рынки открыты?", "рынок 999", "что ты умеешь", "привет"):
        text = run(ai_data.answer(question, lang))
        assert text.rstrip().endswith(i18n.t(lang, "ai_data_footer").rstrip())


def test_a_broken_ledger_is_reported_as_text_not_as_an_error(ledger, monkeypatch):
    _no_model(monkeypatch)

    class Dead:
        async def open_markets(self, *args, **kwargs):
            raise RuntimeError("connection lost")

    monkeypatch.setattr(ai_data, "ledger", Dead())
    text = run(ai_data.answer("какие рынки открыты?", "ru"))
    assert i18n.t("ru", "ai_data_unavailable") in text


def test_the_unknown_id_answer_points_at_the_real_lists(ledger, monkeypatch):
    _no_model(monkeypatch)
    text = run(ai_data.answer("рынок 424242", "ru"))
    assert "/markets" in text and "/bets" in text
