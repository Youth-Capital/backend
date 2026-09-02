"""Payment provider abstraction.

Business logic never imports a gateway. It calls `get_provider()` and works
against this interface, so swapping Stripe for Payme, Click or Uzum is a
settings change plus one new subclass — not a rewrite of the subscription
flow (prompt §31).

Credentials are read from the environment. Nothing here has a key baked in.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class CheckoutSession:
    """What the client needs to complete a purchase.

    `redirect_url` is None for providers that settle server-side without
    sending the user anywhere — the manual provider used in demo and on-prem
    deployments does exactly that.
    """

    reference: str
    provider: str
    redirect_url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentOutcome:
    reference: str
    succeeded: bool
    amount_minor: int
    currency: str
    failure_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(ABC):
    """The whole surface the rest of the app is allowed to depend on."""

    name: str = "abstract"

    @abstractmethod
    def create_checkout(
        self, *, user, plan, success_url: str = "", cancel_url: str = ""
    ) -> CheckoutSession:
        """Begin a purchase and return where to send the user, if anywhere."""

    @abstractmethod
    def verify(self, reference: str) -> PaymentOutcome:
        """Ask the provider what actually happened to a charge.

        Called from the confirm endpoint and, later, from a webhook. Trusting
        a client-supplied "payment succeeded" flag would let anyone upgrade
        themselves for free, so settlement is always re-read from the source.
        """

    def cancel(self, subscription) -> None:
        """Stop future charges. No-op for providers that do not hold a mandate."""
        return None


class ManualPaymentProvider(PaymentProvider):
    """Settles in-process. The default, and the only one that needs no keys.

    Two legitimate uses: free-plan activation, where no money moves at all,
    and bank-transfer / offline flows where an administrator confirms receipt.
    It is also what makes the demo work without a gateway account.

    It is deliberately NOT a silent auto-approve for paid plans in production:
    `BILLING_MANUAL_AUTO_CONFIRM` must be switched on explicitly, and the
    default is off outside DEBUG.
    """

    name = "manual"

    @property
    def _auto_confirm(self) -> bool:
        return bool(getattr(settings, "BILLING_MANUAL_AUTO_CONFIRM", settings.DEBUG))

    def create_checkout(self, *, user, plan, success_url="", cancel_url="") -> CheckoutSession:
        return CheckoutSession(
            reference=f"manual_{uuid.uuid4().hex[:24]}",
            provider=self.name,
            redirect_url=None,
            payload={
                "plan": plan.code,
                "amount_minor": plan.price_minor,
                "currency": plan.currency,
                "auto_confirm": self._auto_confirm or plan.is_free,
                "created_at": timezone.now().isoformat(),
            },
        )

    def verify(self, reference: str) -> PaymentOutcome:
        from .models import Payment

        payment = Payment.objects.filter(provider_ref=reference).first()
        if payment is None:
            return PaymentOutcome(reference, False, 0, "", "unknown_reference")

        # Free plans always settle; paid ones only when explicitly allowed.
        settled = payment.amount_minor == 0 or self._auto_confirm
        return PaymentOutcome(
            reference=reference,
            succeeded=settled,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            failure_reason="" if settled else "awaiting_manual_confirmation",
        )


class StripePaymentProvider(PaymentProvider):
    """Scaffold. Deliberately unimplemented rather than half-implemented.

    Everything it needs from the rest of the system already exists — plan
    codes, minor-unit amounts, a reference column, `verify()` as the single
    settlement path. Wiring it is adding the SDK calls below; nothing else in
    the codebase has to change.
    """

    name = "stripe"

    def __init__(self) -> None:
        self.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
        if not self.api_key:
            raise ImproperlyConfigured(
                "STRIPE_SECRET_KEY is not set. Configure it in the environment "
                "or keep BILLING_PROVIDER=manual."
            )

    def create_checkout(self, *, user, plan, success_url="", cancel_url="") -> CheckoutSession:
        raise NotImplementedError(
            "Stripe checkout is not wired yet. Create a Checkout Session with "
            "line item amount=plan.price_minor, currency=plan.currency, and "
            "return its id as `reference` plus its url as `redirect_url`."
        )

    def verify(self, reference: str) -> PaymentOutcome:
        raise NotImplementedError(
            "Retrieve the Checkout Session by id and map payment_status == "
            "'paid' to PaymentOutcome.succeeded."
        )


_PROVIDERS = {
    "manual": ManualPaymentProvider,
    "stripe": StripePaymentProvider,
}

_cached: PaymentProvider | None = None


def get_provider() -> PaymentProvider:
    """Resolve the configured provider once per process.

    Accepts either a short name from `_PROVIDERS` or a dotted path, so a
    deployment can drop in a local gateway without editing this file.
    """
    global _cached
    if _cached is not None:
        return _cached

    configured = getattr(settings, "BILLING_PROVIDER", "manual")
    if configured in _PROVIDERS:
        _cached = _PROVIDERS[configured]()
    else:
        _cached = import_string(configured)()
    return _cached


def reset_provider_cache() -> None:
    """Test seam — settings overrides otherwise fight the module-level cache."""
    global _cached
    _cached = None
