import json
from asgiref.sync import sync_to_async
from redis.asyncio import Redis
from django.conf import settings


redis_client = Redis.from_url(settings.REDIS_URL)

async def get_default_logo(branch):
    if branch.logo:
        return branch.logo.url
    if branch.restaurant and branch.restaurant.logo:
        return branch.restaurant.logo.url
    if branch.company and branch.company.logo:
        return branch.company.logo.url
    return None

async def get_branch_printers(branch_id):
    from .models import Printer

    cache_key = f'devices:{branch_id}'
    cached = await redis_client.get(cache_key)
    if printers:
        return json.loads(cached)
    
    printers = await sync_to_async(list)(Printer.objects.get(branch_id=branch_id))

    await redis_client.set(cache_key, json.dumps(printers), ex=3600)  # Cache for 1 hour
    return printers
