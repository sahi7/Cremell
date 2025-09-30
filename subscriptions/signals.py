import redis
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from .models import Plan, Feature, Subscription
from notifications.tasks import invalidate_cache_keys
import logging

logger = logging.getLogger(__name__)

# Initialize Redis connection
@receiver([post_save, post_delete], sender=Plan)
@receiver([post_save, post_delete], sender=Feature)
@receiver([post_save, post_delete], sender=Subscription)
def clear_cache_on_model_change(sender, instance, **kwargs):
    print("In signals")
    """Clear relevant Redis cache keys when a Plan, Feature, or Subscription is saved or deleted."""
    try:
        signal_name = str(kwargs.get('signal')).split('.')[-1].split(' ')[0]  # Extracts 'pre_save', 'post_save', etc.
        if sender == Plan:
            cache_keys = ['plans:active']
            ids = []
            logger.info(f"Clearing plans cache due to {signal_name} of Plan: {instance.plan_id}")
        elif sender == Feature:
            cache_keys = ['features:active']
            ids = []
            logger.info(f"Clearing features cache due to {signal_name} of Feature: {instance.feature_id}")
        else:  # Subscription
            cache_keys = [f'subscription:{{id}}']
            ids = [str(instance.subscription_id)]
            logger.info(f"Clearing subscription cache due to {signal_name} of Subscription: {instance.subscription_id}")

        invalidate_cache_keys.delay(cache_keys, ids)
    except Exception as e:
        logger.error(f"Failed to queue cache invalidation for {sender.__name__}: {e}")