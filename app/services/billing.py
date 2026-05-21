"""Stripe billing integration for subscription management.

Handles:
- Subscription creation and management
- Usage tracking and limits
- Webhook processing for payment events
- Plan upgrades/downgrades
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import stripe
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.db import User, Subscription, SubscriptionPlan, SubscriptionStatus

logger = logging.getLogger(__name__)
settings = get_settings()

stripe.api_key = settings.stripe_secret_key if hasattr(settings, 'stripe_secret_key') else None


# ── Plan Definitions ─────────────────────────────────────────────────────────


class PlanLimits(Enum):
    """Render limits per plan per month."""
    STARTER = 20
    CREATOR_PRO = 100
    AGENCY = 500


PLAN_PRICES = {
    SubscriptionPlan.starter: 0,  # Free
    SubscriptionPlan.creator_pro: 99_00,  # $99/month in cents
    SubscriptionPlan.agency: 399_00,  # $399/month in cents
}


# ── Subscription Management ──────────────────────────────────────────────────


async def create_stripe_customer(user: User) -> str:
    """Create a Stripe customer for a user."""
    try:
        customer = stripe.Customer.create(
            email=user.email,
            name=user.name,
            metadata={"user_id": str(user.id)},
        )
        return customer.id
    except stripe.error.StripeError as exc:
        logger.error(f"Failed to create Stripe customer: {exc}")
        raise HTTPException(status_code=500, detail="Payment system error") from exc


async def create_checkout_session(
    user: User,
    plan: SubscriptionPlan,
    success_url: str,
    cancel_url: str,
    db: AsyncSession,
) -> str:
    """Create a Stripe Checkout session for subscription."""
    # Get or create Stripe customer
    subscription = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    sub = subscription.scalar_one_or_none()
    
    if not sub or not sub.stripe_customer_id:
        customer_id = await create_stripe_customer(user)
        if not sub:
            sub = Subscription(
                user_id=user.id,
                stripe_customer_id=customer_id,
                plan=SubscriptionPlan.starter,
                renders_limit=PlanLimits.STARTER.value,
            )
            db.add(sub)
        else:
            sub.stripe_customer_id = customer_id
        await db.commit()
    else:
        customer_id = sub.stripe_customer_id
    
    # Get price ID from environment or create on-the-fly
    price_id = settings.stripe_price_ids.get(plan.value) if hasattr(settings, 'stripe_price_ids') else None
    
    if not price_id:
        # Create price on-the-fly (for testing)
        price = stripe.Price.create(
            unit_amount=PLAN_PRICES[plan],
            currency="usd",
            recurring={"interval": "month"},
            product_data={"name": f"TikTok Engine - {plan.value}"},
        )
        price_id = price.id
    
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(user.id),
                "plan": plan.value,
            },
        )
        
        return session.url
    except stripe.error.StripeError as exc:
        logger.error(f"Failed to create checkout session: {exc}")
        raise HTTPException(status_code=500, detail="Payment system error") from exc


async def handle_subscription_created(event_data: dict, db: AsyncSession):
    """Handle subscription.created webhook event."""
    subscription_data = event_data["data"]["object"]
    customer_id = subscription_data["customer"]
    
    # Find user by Stripe customer ID
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_customer_id == customer_id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        subscription.stripe_subscription_id = subscription_data["id"]
        subscription.status = SubscriptionStatus.active
        subscription.current_period_end = datetime.fromtimestamp(
            subscription_data["current_period_end"],
            tz=timezone.utc,
        )
        await db.commit()


async def handle_subscription_updated(event_data: dict, db: AsyncSession):
    """Handle subscription.updated webhook event."""
    subscription_data = event_data["data"]["object"]
    stripe_sub_id = subscription_data["id"]
    
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        # Update status
        stripe_status = subscription_data["status"]
        if stripe_status == "active":
            subscription.status = SubscriptionStatus.active
        elif stripe_status == "past_due":
            subscription.status = SubscriptionStatus.past_due
        elif stripe_status == "canceled":
            subscription.status = SubscriptionStatus.cancelled
        
        subscription.current_period_end = datetime.fromtimestamp(
            subscription_data["current_period_end"],
            tz=timezone.utc,
        )
        await db.commit()


async def handle_subscription_deleted(event_data: dict, db: AsyncSession):
    """Handle subscription.deleted webhook event."""
    subscription_data = event_data["data"]["object"]
    stripe_sub_id = subscription_data["id"]
    
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        subscription.status = SubscriptionStatus.cancelled
        subscription.plan = SubscriptionPlan.starter
        subscription.renders_limit = PlanLimits.STARTER.value
        await db.commit()


# ── Usage Tracking ───────────────────────────────────────────────────────────


async def check_render_limit(user: User, db: AsyncSession) -> bool:
    """Check if user has renders remaining this month."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        # Create default subscription
        subscription = Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.starter,
            renders_limit=PlanLimits.STARTER.value,
        )
        db.add(subscription)
        await db.commit()
    
    return subscription.renders_used_this_month < subscription.renders_limit


async def increment_render_usage(user: User, db: AsyncSession):
    """Increment the user's render count for this month."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        subscription.renders_used_this_month += 1
        await db.commit()


async def reset_monthly_usage(db: AsyncSession):
    """Reset render usage for all users (run monthly via cron)."""
    from sqlalchemy import update
    
    await db.execute(
        update(Subscription).values(renders_used_this_month=0)
    )
    await db.commit()
    logger.info("Reset monthly render usage for all subscriptions")


# ── Portal & Management ──────────────────────────────────────────────────────


async def create_billing_portal_session(user: User, return_url: str, db: AsyncSession) -> str:
    """Create a Stripe billing portal session for subscription management."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription or not subscription.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No subscription found")
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=subscription.stripe_customer_id,
            return_url=return_url,
        )
        return session.url
    except stripe.error.StripeError as exc:
        logger.error(f"Failed to create billing portal session: {exc}")
        raise HTTPException(status_code=500, detail="Payment system error") from exc
