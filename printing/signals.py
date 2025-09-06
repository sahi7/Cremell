from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from redis.asyncio import Redis
from django.conf import settings

from .models import Device
redis_client = Redis.from_url(settings.REDIS_URL)

@receiver(post_save, sender=Device)
async def invalidate_cache(sender, instance, **kwargs):
    cache_key = f"devices:{instance.branch_id}"
    await redis_client.delete(cache_key)