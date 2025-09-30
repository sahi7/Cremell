from celery import shared_task
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import redis.asyncio as redis
import json
from .models import History


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