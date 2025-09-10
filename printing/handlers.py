import json
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

import logging

logger = logging.getLogger(__name__)
channel_layer = get_channel_layer()

async def handle_printer_discovered(data):
    from .models import Printer
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
    scan_id = data['scan_id'] 
    count = data['count'] 
    receiver = data.pop('sender', None)
    print("handle_scan_complete: ", scan_id, count, receiver)

    await channel_layer.group_send(
        f"user_{receiver}",
        {
            'signal': 'scan',
            'type': 'stakeholder.notification',
            'message': json.dumps(data)
        }
    )

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