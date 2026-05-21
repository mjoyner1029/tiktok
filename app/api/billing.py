"""Billing and subscription API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import stripe

from app.auth import require_user
from app.database import get_db
from app.models.db import User, Subscription, SubscriptionPlan
from app.services.billing import (
    check_render_limit,
    create_billing_portal_session,
    create_checkout_session,
    handle_subscription_created,
    handle_subscription_deleted,
    handle_subscription_updated,
)
from app.config import get_settings

router = APIRouter(prefix="/billing", tags=["billing"])
settings = get_settings()


# ── Schemas ──────────────────────────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    plan: str
    success_url: HttpUrl
    cancel_url: HttpUrl


class CheckoutResponse(BaseModel):
    checkout_url: str


class BillingPortalRequest(BaseModel):
    return_url: HttpUrl


class BillingPortalResponse(BaseModel):
    portal_url: str


class SubscriptionOut(BaseModel):
    plan: str
    status: str
    renders_used_this_month: int
    renders_limit: int
    current_period_end: str | None

    class Config:
        from_attributes = True


class UsageResponse(BaseModel):
    renders_used: int
    renders_limit: int
    can_render: bool


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Checkout session for subscription."""
    try:
        plan = SubscriptionPlan(body.plan)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan: {body.plan}",
        )
    
    checkout_url = await create_checkout_session(
        user=user,
        plan=plan,
        success_url=str(body.success_url),
        cancel_url=str(body.cancel_url),
        db=db,
    )
    
    return CheckoutResponse(checkout_url=checkout_url)


@router.post("/portal", response_model=BillingPortalResponse)
async def get_billing_portal(
    body: BillingPortalRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Get Stripe billing portal URL for subscription management."""
    portal_url = await create_billing_portal_session(
        user=user,
        return_url=str(body.return_url),
        db=db,
    )
    
    return BillingPortalResponse(portal_url=portal_url)


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's subscription details."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        # Return default starter plan
        return SubscriptionOut(
            plan="starter",
            status="active",
            renders_used_this_month=0,
            renders_limit=20,
            current_period_end=None,
        )
    
    return SubscriptionOut(
        plan=subscription.plan.value,
        status=subscription.status.value,
        renders_used_this_month=subscription.renders_used_this_month,
        renders_limit=subscription.renders_limit,
        current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
    )


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current usage and limits."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        return UsageResponse(
            renders_used=0,
            renders_limit=20,
            can_render=True,
        )
    
    can_render = await check_render_limit(user, db)
    
    return UsageResponse(
        renders_used=subscription.renders_used_this_month,
        renders_limit=subscription.renders_limit,
        can_render=can_render,
    )


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhooks for subscription events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not hasattr(settings, 'stripe_webhook_secret') or not settings.stripe_webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle different event types
    event_type = event["type"]
    
    if event_type == "checkout.session.completed":
        # Payment succeeded, subscription created
        await handle_subscription_created(event, db)
    
    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(event, db)
    
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(event, db)
    
    elif event_type == "invoice.payment_failed":
        # Handle failed payment
        pass
    
    return {"status": "success"}
