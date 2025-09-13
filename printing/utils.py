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
    if cached:
        return json.loads(cached)
    
    printers = await sync_to_async(list)(Printer.objects.filter(branch_id=branch_id))

    await redis_client.set(cache_key, json.dumps(printers), ex=3600)  # Cache for 1 hour
    return printers

async def generate_device_id():
    """Generate a 6-character unique device ID."""
    import uuid
    counter = await get_next_id('device')
    uuid_part = uuid.uuid4().hex[:4].upper()
    # print("c-v:v", counter, uuid_part)
    result = ""
    for c, u in zip(counter[:4], uuid_part):
        result += c + u
    result += counter[4:]
    # print("res: ", result)
    return result


async def get_next_id(counter_name: str) -> str:
    numeric_key = f"counter:{counter_name}:numeric"
    prefix_key = f"counter:{counter_name}:prefix"
    
    # Try incrementing numeric counter
    next_id = await redis_client.incr(numeric_key)
    
    if next_id > 99999999:
        # Reset numeric counter and manage prefix
        await redis_client.set(numeric_key, 1)
        next_id = 1
        
        # Get or initialize prefix
        current_prefix = await redis_client.get(prefix_key)
        if current_prefix is None:
            await redis_client.set(prefix_key, b'A')
            current_prefix = b'A'
        
        # Decode prefix and check if we need to reset
        prefix_str = current_prefix.decode('utf-8')
        if prefix_str == 'Z':
            await redis_client.set(prefix_key, b'A')  # Reset to A
            prefix_str = 'A'
        else:
            # Increment prefix (A -> B, etc.)
            next_prefix = chr(ord(prefix_str) + 1)
            await redis_client.set(prefix_key, next_prefix.encode('utf-8'))
            prefix_str = next_prefix
    
        # Format with prefix
        return f"{prefix_str}{next_id:07d}"
    
    # Numeric mode: pad to 6 digits
    return f"{next_id:08d}"

