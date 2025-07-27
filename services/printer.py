from django.utils import timezone
from django.conf import settings
from escpos.printer import Usb, Network, Serial
from escpos.exceptions import BarcodeTypeError, BarcodeSizeError, DeviceNotFoundError, USBNotFoundError
from usb.core import USBError
import redis.asyncio as redis
from asgiref.sync import sync_to_async
import json

from CRE.models import Order
import logging
logger = logging.getLogger(__name__)

class ReceiptPrinter:
    """Handles printing receipts for orders using ESC/POS thermal printers."""
    def __init__(self, connection_type='usb', vendor_id=0x04b8, product_id=0x0202, ip_address=None, serial_port=None):
        """Initialize printer based on connection type."""
        self.connection_type = connection_type
        self.printer = None
        if connection_type == 'usb':
            try:
                self.printer = Usb(vendor_id, product_id, profile="TM-T88III")
            except USBError as e:
                logger.error(f"Failed to initialize USB printer: {str(e)}")
                raise
        elif connection_type == 'network':
            try:
                self.printer = Network(ip_address, profile="TM-T88III")
            except DeviceNotFoundError as e:
                logger.error(f"Failed to initialize network printer: {str(e)}")
                raise
        elif connection_type == 'serial':
            try:
                self.printer = Serial(
                    devfile=serial_port,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=1.00,
                    dsrdtr=True,
                    profile="TM-T88III"
                )
            except DeviceNotFoundError as e:
                logger.error(f"Failed to initialize serial printer: {str(e)}")
                raise

    async def print_receipt(self, order_id):
        """Print receipt for the given order."""
        try:
            # Fetch order asynchronously
            order = await sync_to_async(Order.objects.get)(pk=order_id)
            # Format receipt
            self.printer.text(f"{'*' * 20}\n")
            self.printer.text(f"Receipt for Order {order.order_number}\n")
            self.printer.text(f"Date: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.printer.text(f"Branch: {order.branch.name}\n")
            self.printer.text(f"{'-' * 20}\n")
            # Fetch order items
            order_items = await sync_to_async(list)(
                order.orderitem_set.all().select_related('menu_item')
            )
            for item in order_items:
                self.printer.text(f"{item.menu_item.name}: {item.quantity} x ${item.price:.2f}\n")
            self.printer.text(f"{'-' * 20}\n")
            self.printer.text(f"Total: ${order.total:.2f}\n")
            # Print barcode for order_id
            self.printer.barcode(f"{order.order_number}", "CODE39", height=64, width=2, pos="BELOW", align_ct=True)
            self.printer.text(f"{'*' * 20}\n")
            self.printer.cut()
            # Log success to Redis
            client = redis.Redis.from_url(settings.REDIS_URL)
            try:
                await client.publish(
                    f"events:{order.branch.id}",
                    json.dumps({
                        'type': 'receipt_printed',
                        'data': {'order_id': order_id, 'order_number': order.order_number}
                    })
                )
            finally:
                await client.close()
        except (Order.DoesNotExist, BarcodeTypeError, BarcodeSizeError) as e:
            logger.error(f"Order {order_id} not found for printing")
            raise
        except Exception as e:
            logger.error(f"Failed to print receipt for order {order_id}: {str(e)}")
            raise
        finally:
            if self.printer:
                self.printer.close()

    async def close(self):
        """Close the printer connection."""
        if self.printer:
            self.printer.close()