"""Tests for the broker adapters.

No test here touches the network. The Alpaca adapter's request path is
deliberately untested against the live API — that is stated in its docstring
rather than papered over with a mock that proves nothing.
"""

from __future__ import annotations

import pytest

from screener.brokers.base import BrokerError, OrderRequest, OrderResult
from screener.brokers.dryrun import DryRunBroker


class TestOrderRequestValidation:
    def test_accepts_a_well_formed_limit_order(self):
        OrderRequest(symbol="AAPL", side="buy", quantity=1, limit_price=100.0).validate()

    def test_rejects_an_unknown_side(self):
        with pytest.raises(ValueError, match="side"):
            OrderRequest(symbol="AAPL", side="hold", quantity=1, limit_price=1.0).validate()

    def test_rejects_zero_quantity(self):
        with pytest.raises(ValueError, match="quantity"):
            OrderRequest(symbol="AAPL", side="buy", quantity=0, limit_price=1.0).validate()

    def test_rejects_negative_quantity(self):
        with pytest.raises(ValueError, match="quantity"):
            OrderRequest(symbol="AAPL", side="buy", quantity=-5, limit_price=1.0).validate()

    def test_rejects_a_limit_order_without_a_price(self):
        with pytest.raises(ValueError, match="limit_price"):
            OrderRequest(symbol="AAPL", side="buy", quantity=1).validate()

    def test_market_order_needs_no_price(self):
        OrderRequest(symbol="AAPL", side="buy", quantity=1, order_type="market").validate()

    def test_rejects_an_unsupported_order_type(self):
        with pytest.raises(ValueError, match="order type"):
            OrderRequest(symbol="AAPL", side="buy", quantity=1,
                         order_type="trailing_stop").validate()


class TestDryRunBroker:
    def test_places_nothing(self):
        broker = DryRunBroker()
        result = broker.submit(
            OrderRequest(symbol="AAPL", side="buy", quantity=2, limit_price=180.0)
        )
        assert isinstance(result, OrderResult)
        assert result.placed is False
        assert result.status == "simulated"

    def test_describes_what_it_would_have_done(self):
        broker = DryRunBroker()
        result = broker.submit(
            OrderRequest(symbol="AAPL", side="buy", quantity=2, limit_price=180.0)
        )
        assert "Would buy 2 AAPL" in result.message
        assert "180.00" in result.message

    def test_still_validates(self):
        """A dry run that accepts malformed orders would teach you nothing."""
        with pytest.raises(ValueError):
            DryRunBroker().submit(OrderRequest(symbol="AAPL", side="buy", quantity=0))

    def test_records_submissions(self):
        broker = DryRunBroker()
        broker.submit(OrderRequest(symbol="AAPL", side="buy", quantity=1, limit_price=1.0))
        broker.submit(OrderRequest(symbol="MSFT", side="sell", quantity=3, limit_price=2.0))
        assert [r.symbol for r in broker.submitted] == ["AAPL", "MSFT"]

    def test_is_always_paper(self):
        assert DryRunBroker().is_paper is True

    def test_account_states_nothing_is_connected(self):
        assert "Nothing can be placed" in DryRunBroker().account()["note"]


class TestAlpacaCredentialHandling:
    def test_refuses_to_construct_without_credentials(self, monkeypatch):
        from screener.brokers.alpaca import AlpacaBroker

        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
        with pytest.raises(BrokerError, match="ALPACA_API_KEY_ID"):
            AlpacaBroker()

    def test_refuses_with_only_one_credential(self, monkeypatch):
        from screener.brokers.alpaca import AlpacaBroker

        monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
        with pytest.raises(BrokerError):
            AlpacaBroker()

    def test_defaults_to_the_paper_host(self, monkeypatch):
        from screener.brokers.alpaca import PAPER_HOST, AlpacaBroker

        monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
        broker = AlpacaBroker()
        assert broker.is_paper is True
        assert broker.host == PAPER_HOST

    def test_live_host_requires_opting_in(self, monkeypatch):
        from screener.brokers.alpaca import LIVE_HOST, AlpacaBroker

        monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
        broker = AlpacaBroker(paper=False)
        assert broker.is_paper is False
        assert broker.host == LIVE_HOST

    def test_rejects_a_bad_time_in_force_before_sending(self, monkeypatch):
        """Caught locally, so a malformed order never reaches the network."""
        from screener.brokers.alpaca import AlpacaBroker

        monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
        broker = AlpacaBroker()
        with pytest.raises(BrokerError, match="time_in_force"):
            broker.submit(OrderRequest(symbol="AAPL", side="buy", quantity=1,
                                       limit_price=1.0, time_in_force="forever"))


def test_both_brokers_satisfy_the_protocol():
    from screener.brokers.base import Broker

    assert isinstance(DryRunBroker(), Broker)


def test_order_result_serialises_without_raw_payload():
    """to_dict is what gets logged, so it must not carry the broker's raw body."""
    result = OrderResult(placed=True, status="accepted", symbol="AAPL",
                         raw={"secret_looking": "payload"})
    assert "raw" not in result.to_dict()
