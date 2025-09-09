import json
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

channel_layer = get_channel_layer()

async def handle_printer_discovered(scan_id, config, receiver):
    from .models import Printer
    
    print("scan_id: ", scan_id)
    print("config: ", config)
    print("receiver: ", receiver)
    fingerprint = config.get('fingerprint')
    # printer = await sync_to_async(Printer.objects.filter(fingerprint=fingerprint).first)()
    # if not printer:
    #     printer = await sync_to_async(Printer.objects.create)(

    #         fingerprint=fingerprint,
    #         connection_type=config.get('connection_type'),
    #         vendor_id=config.get('vendor_id', ''),
    #         product_id=config.get('product_id', ''),
    #         ip_address=config.get('ip_address', ''),
    #         serial_port=config.get('serial_port', ''),
    #         profile=config.get('profile', 'TM-T88III'),
    #         is_default=await sync_to_async(Printer.objects.filter(branch_id=data['branch_id'], is_default=True).exists)() == False
    #     )
    await channel_layer.group_send(
        f"user_{receiver}",
        {
            'signal': 'scan',
            'type': 'stakeholder.notification',
            'message': json.dumps({'scan_id': scan_id, 'printer': config, 'found': True})

            # 'printer_id': str(printer.id),
            # 'config': {
            #     'connection_type': printer.connection_type,
            #     'vendor_id': printer.vendor_id,
            #     'product_id': printer.product_id,
            #     'ip_address': printer.ip_address,
            #     'serial_port': printer.serial_port,
            #     'profile': printer.profile,
            #     'is_default': printer.is_default
            # }
        }
    )

async def handle_scan_complete(scan_id, count, receiver):
    pass

async def handle_ack(data):
    pass