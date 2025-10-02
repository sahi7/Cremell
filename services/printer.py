from django.utils import timezone
from django.conf import settings
from escpos.printer import Usb, Network, Serial
from escpos.exceptions import BarcodeTypeError, BarcodeSizeError, DeviceNotFoundError
from asgiref.sync import sync_to_async
from zeroconf import Zeroconf, ServiceBrowser  # For network detection
import redis.asyncio as redis
import usb.core
import socket  # For network connection test
import json
import asyncio

from cre.models import Order
import logging
logger = logging.getLogger('web')

class PrinterListener:
    """Listener for zeroconf network printer discovery."""
    def __init__(self):
        self.printers = []

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            ip = socket.inet_ntoa(info.addresses[0])
            self.printers.append({'name': name, 'ip_address': ip, 'port': info.port})

class ReceiptPrinter:
    """Handles printing and printer management."""

    def __init__(self, printer_config=None):
        self.connection_type = printer_config.connection_type if printer_config else None
        self.printer = None
        self.printer_config = printer_config

    async def connect(self):
        """Asynchronously connect to the printer based on config."""
        if not self.printer_config:
            raise ValueError("Printer config required for connection.")
        try:
            if self.connection_type == 'usb':
                self.printer = Usb(
                    int(self.printer_config.vendor_id, 16),
                    int(self.printer_config.product_id, 16),
                    profile=self.printer_config.profile
                )
            elif self.connection_type == 'network':
                self.printer = Network(
                    self.printer_config.ip_address,
                    profile=self.printer_config.profile
                )
            elif self.connection_type == 'serial':
                self.printer = Serial(
                    devfile=self.printer_config.serial_port,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=1.00,
                    dsrdtr=True,
                    profile=self.printer_config.profile
                )
            # Update status asynchronously
            await sync_to_async(self._update_status)(True)
            logger.info(f"Connected to printer {self.printer_config.name}")
        except (usb.core.USBError, DeviceNotFoundError) as e:
            await sync_to_async(self._update_status)(False)
            logger.error(f"Failed to connect to {self.printer_config.name}: {str(e)}")
            raise

    async def disconnect(self):
        """Asynchronously disconnect from the printer."""
        if self.printer:
            self.printer.close()
            self.printer = None
            logger.info(f"Disconnected from printer {self.printer_config.name}")

    async def test_connection(self):
        """Asynchronously test printer connection by sending a status check."""
        if not self.printer:
            await self.connect()
        try:
            # Simple test: Send a text and cut (ESC/POS status check)
            self.printer.text("Connection test\n")
            self.printer.cut()
            logger.info(f"Connection test successful for {self.printer_config.name}")
            return True
        except Exception as e:
            logger.error(f"Connection test failed for {self.printer_config.name}: {str(e)}")
            return False
        finally:
            await self.disconnect()

    @staticmethod
    async def detect_printers(connection_type='all', branch_id=None):
        """Asynchronously detect available printers and update Printer model."""
        detected = []
        if connection_type in ['all', 'usb']:
            devices = usb.core.find(find_all=True)
            for dev in devices:
                if dev.bDeviceClass == 7:  # Printer class
                    detected.append({
                        'connection_type': 'usb',
                        'vendor_id': hex(dev.idVendor),
                        'product_id': hex(dev.idProduct),
                        'profile': 'TM-T88III'  # Default; customize based on IDs
                    })

        if connection_type in ['all', 'network']:
            zeroconf = Zeroconf()
            listener = PrinterListener()
            browser = ServiceBrowser(zeroconf, "_printer._tcp.local.", listener)
            await asyncio.sleep(5)  # Allow time for discovery
            browser.cancel()
            zeroconf.close()
            detected.extend([{
                'connection_type': 'network',
                'ip_address': p['ip_address'],
                'name': p['name'],
                'profile': 'TM-T88III'
            } for p in listener.printers])

        if connection_type in ['all', 'serial']:
            ports = serial.tools.list_ports.comports()
            detected.extend([{
                'connection_type': 'serial',
                'serial_port': port.device,
                'profile': 'TM-T88III'
            } for port in ports if 'printer' in port.description.lower()])  # Filter heuristically

        # Update Django models asynchronously
        for d in detected:
            await sync_to_async(Printer.objects.update_or_create)(
                branch_id=branch_id,
                connection_type=d['connection_type'],
                defaults=d
            )
        logger.info(f"Detected {len(detected)} printers")
        return detected

    def _update_status(self, is_active):
        """Sync helper to update Printer model."""
        self.printer_config.is_active = is_active
        self.printer_config.last_connected = timezone.now() if is_active else None
        self.printer_config.save()

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