from celery import shared_task
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
from django.db import transaction
from subscriptions.models import Subscription, History, EventType, BillingType
import logging

logger = logging.getLogger(__name__)

from .models import History
from notifications.tasks import send_batch_notifications

@shared_task
def log_subscription_event(subscription_id, event_type, old_status=None, new_status=None, old_plan_id=None, new_plan_id=None, old_feature_id=None, new_feature_id=None, notes=''):
    # Create history record directly to database
    history = History(
        subscription_id=subscription_id,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        old_plan_id=old_plan_id,
        new_plan_id=new_plan_id,
        old_feature_id=old_feature_id,
        new_feature_id=new_feature_id,
        notes=notes,
        created_at=timezone.now()
    )
    history.save()



@shared_task(bind=True, max_retries=3)
def manage_subscription_status(self):
    """
    Daily task to manage pay_per_order trial subscriptions and send notifications
    for trial expirations and monthly/yearly renewals.
    """
    try:
        today = timezone.now().date()
        # Fetch pay_per_order subscriptions on trial
        trial_subscriptions = Subscription.objects.filter(
            plan__billing_type=BillingType.PAY_PER_ORDER,
            status='trial',
            trial_end_date__isnull=False
        ).select_related('plan')

        # Process trial subscriptions in batches
        batch_size = 1000
        for i in range(0, trial_subscriptions.count(), batch_size):
            batch = trial_subscriptions[i:i + batch_size]
            with transaction.atomic():
                for sub in batch:
                    trial_end_date = sub.trial_end_date.date()
                    days_until_expiry = (trial_end_date - today).days
                    print("trial_end_date: ", trial_end_date, days_until_expiry)

                    # Switch to active if trial ended
                    if days_until_expiry <= 0:
                        sub.status = 'active'
                        sub.updated_at = timezone.now()
                        sub.save()
                        History.objects.create(
                            subscription=sub,
                            event_type=EventType.STATUS_CHANGED,
                            old_status='trial',
                            new_status='active',
                            notes='Trial ended, switched to active'
                        )
                        # Send activation notification
                        send_activation_notification(sub)
                    # Send trial expiration notifications
                    elif days_until_expiry in (1, 2, 3):
                        send_trial_expiration_notification(sub, days_until_expiry)

        # Fetch monthly/yearly subscriptions for renewal reminders
        active_subscriptions = Subscription.objects.filter(
            Q(plan__billing_type=BillingType.MONTHLY_FIXED) | 
            Q(plan__billing_type=BillingType.YEARLY_FIXED),
            status='active',
            auto_renew=False,
            current_period_end__isnull=False
        ).select_related('plan')

        for i in range(0, active_subscriptions.count(), batch_size):
            batch = active_subscriptions[i:i + batch_size]
            for sub in batch:
                period_end_date = sub.current_period_end.date()
                days_until_end = (period_end_date - today).days
                # Backoff: notify at 7, 3, or 1 day before expiry
                if days_until_end in (1, 3, 7):
                    send_renewal_notification(sub, days_until_end)

        logger.info(f"Processed {trial_subscriptions.count()} trial and {active_subscriptions.count()} active subscriptions")
    except Exception as e:
        logger.error(f"Error in manage_subscription_status: {e}")
        raise self.retry(countdown=60)

def send_trial_expiration_notification(subscription, days_remaining):
    """Send notification for trial expiration."""
    entity_kwargs = resolve_entity(subscription.entity_type, subscription.entity_id)
    print("entity_kwargs: ", entity_kwargs)
    extra_context = {
        'plan_name': subscription.plan_name,
        'days_remaining': days_remaining,
        'subscription_type': 'pay_per_order',
        'trial_end_date': subscription.trial_end_date.date()
    }
    send_batch_notifications.delay(
        **entity_kwargs,
        message=f"Your {subscription.plan_name} trial expires in {days_remaining} day(s).",
        subject=f"Trial Ending Soon: {subscription.plan_name}",
        extra_context=extra_context,
        template_name='emails/subscription_notification.html'
    )
    History.objects.create(
        subscription=subscription,
        event_type=EventType.NOTIFICATION_SENT,
        notes=f"Trial expiration notification sent ({days_remaining} days remaining)"
    )

def send_renewal_notification(subscription, days_remaining):
    """Send notification for monthly/yearly renewal."""
    entity_kwargs = resolve_entity(subscription.entity_type, subscription.entity_id)
    billing_type = 'monthly' if subscription.plan.billing_type == BillingType.MONTHLY_FIXED else 'yearly'
    extra_context = {
        'plan_name': subscription.plan_name,
        'days_remaining': days_remaining,
        'subscription_type': billing_type,
        'current_period_end': subscription.current_period_end.date()
    }
    send_batch_notifications.delay(
        **entity_kwargs,
        message=f"Your {subscription.plan_name} {billing_type} subscription renews in {days_remaining} day(s).",
        subject=f"Renewal Reminder: {subscription.plan_name}",
        extra_context=extra_context,
        template_name='emails/subscription_notification.html'
    )
    History.objects.create(
        subscription=subscription,
        event_type=EventType.NOTIFICATION_SENT,
        notes=f"Renewal notification sent ({days_remaining} days remaining)"
    )

def send_activation_notification(subscription):
    """Send notification when subscription becomes active."""
    entity_kwargs = resolve_entity(subscription.entity_type, subscription.entity_id)
    extra_context = {
        'plan_name': subscription.plan_name,
        'subscription_type': 'pay_per_order',
        'activation_date': timezone.now().date()
    }
    send_batch_notifications.delay(
        **entity_kwargs,
        message=f"Your {subscription.plan_name} subscription is now active.",
        subject=f"Subscription Activated: {subscription.plan_name}",
        extra_context=extra_context,
        template_name='emails/subscription_notification.html'
    )
    History.objects.create(
        subscription=subscription,
        event_type=EventType.NOTIFICATION_SENT,
        notes="Activation notification sent"
    )

def resolve_entity(entity_type, entity_id):
    """Map entity_type and entity_id to company_id, restaurant_id, or branch_id."""
    kwargs = {
        'company_id': None,
        'restaurant_id': None,
        'branch_id': None
    }
    if entity_type == 'company':
        kwargs['company_id'] = entity_id
    elif entity_type == 'restaurant':
        kwargs['restaurant_id'] = entity_id
    elif entity_type == 'branch':
        kwargs['branch_id'] = entity_id
    return kwargs