import json
from redis.asyncio import Redis
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from .utils import get_branch_printers

import logging

logger = logging.getLogger(__name__)
channel_layer = get_channel_layer()
redis_client = Redis.from_url(settings.REDIS_URL)

async def handle_printer_discovered(data):
    scan_id = data['scan_id'] 
    config = data['config'] 
    receiver = data.pop('sender', None)
    print("scan_id: ", scan_id)
    print("config: ", config)
    print("receiver: ", receiver)

    await channel_layer.group_send(
        f"user_{receiver}",
        {
            'signal': 'scan',
            'type': 'stakeholder.notification',
            'message': json.dumps(data)
        }
    )

async def handle_scan_complete(data):
    from .models import Printer

    scan_id = data['scan_id'] 
    branch_id = data['branch_id'] 
    found_devices = data['printers'] 
    receiver = data.pop('sender', None)
    print("handle_scan_complete: ", scan_id, branch_id, receiver)

    if receiver:
        await channel_layer.group_send(
            f"user_{receiver}",
            {
                'signal': 'scan',
                'type': 'stakeholder.notification',
                'message': json.dumps(data)
            }
        )
    cached = await get_branch_printers(int(branch_id))
    new_printers = []
    new_entries = []

    # Load existing cache or start fresh
    printers_data = json.loads(cached) if cached else []
    seen_fingerprints = {p['fingerprint'] for p in printers_data}

    for p in found_devices:
        if p['fingerprint'] in seen_fingerprints:
            # Skip if already in cache
            continue

        # Prepare Printer instance for DB
        new_printers.append(
            Printer(
                branch_id=branch_id,
                name=p.get('name'),
                fingerprint=p['fingerprint'],
                is_default=p.get('is_default', False),
            )
        )

        # Add to new entries for cache update
        new_entries.append(p)
        seen_fingerprints.add(p['fingerprint'])
    
    # Persist and update cache only if we have new ones
    if new_printers:
        # Bulk save in a thread-safe way
        await sync_to_async(Printer.objects.bulk_create)(
            new_printers, ignore_conflicts=True
        )

        printers_data.extend(new_entries)
        redis_client.set(f'devices:{branch_id}', json.dumps(printers_data))

async def handle_ack(data):
    try:
        receiver = data.pop('sender', None)
        signal = data.get('command') 
        print("in handle ack: ", data)
        await channel_layer.group_send(
            f"user_{receiver}",
            {
                'signal': signal,
                'type': 'stakeholder.notification',
                'message': json.dumps(data)
            }
        )
    except Exception as e:
        logger.error(f"Message failed for user {receiver}: {str(e)}")

async def handle_token_refresh(self, data):
    from .models import Device, generate_device_token, default_expiry
    device_id = data.get('device_id')
    try:
        device = await sync_to_async(Device.objects.get)(device_id=device_id)
        new_token = generate_device_token()
        expiry_date = default_expiry()
        device.device_token = new_token
        device.expiry_date = expiry_date
        await sync_to_async(device.save)()
        await self.send(text_data=json.dumps({
            'type': 'token.refresh',
            'device_token': new_token,
            'expiry_date': expiry_date.isoformat(),
            'payload': {}
        }))
        logger.info(f"Refreshed token for device {device_id}")
    except Device.DoesNotExist:
        logger.error(f"Device {device_id} not found for token refresh")
        await self.send(text_data=json.dumps({
            'type': 'error',
            'message': 'Device not found'
        }))