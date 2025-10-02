import json
import time
from adrf.views import APIView
from adrf.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from .models import Device
from .utils import generate_device_id
from .serializers import DeviceSerializer
from .permissions import DevicePermission
from zMisc.policies import ScopeAccessPolicy
from zMisc.permissions import StaffAccessPolicy
from zMisc.utils import clean_request_data, get_branch_device

import logging

logger = logging.getLogger('web')
channel_layer = get_channel_layer()

class DeviceViewSet(ModelViewSet):
    queryset = Device.objects.filter(is_active=True)
    serializer_class = DeviceSerializer
    lookup_field = "device_id"

    def get_permissions(self):
        role_value = self.request.user.r_val
        self._access_policy = ScopeAccessPolicy if role_value <= 5 else StaffAccessPolicy
        return [self._access_policy(), DevicePermission()]
    
    async def get_queryset(self):
        user = self.request.user
        scope_filter = await self._access_policy().get_queryset_scope(user, view=self)
        return self.queryset.filter(scope_filter)
    
    async def list(self, request, *args, **kwargs):
        queryset = await self.get_queryset()
        serializer = self.serializer_class(queryset, many=True)
        data = await sync_to_async(lambda: serializer.data)()
        return Response(data)

    @database_sync_to_async
    def perform_create(self, serializer):
        """Set added_by to current user."""
        serializer.save(added_by=self.request.user)

    async def create(self, request, *args, **kwargs):
        """
        request data
        {
            'name': 'My Printer',
            "user_id": 27,
            'branches'
        }
        """
        start = time.perf_counter()
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch_id'] = request.data.get('branches', [None])[0]
        serializer = self.serializer_class(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        await self.perform_create(serializer)
        print(f"1st device create took {(time.perf_counter() - start) * 1000:.3f} ms")
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    async def partial_update(self, request, *args, **kwargs):
        # Get device_id from URL kwargs
        start = time.perf_counter()
        device_id = kwargs.get(self.lookup_field)
        device = await sync_to_async(Device.objects.get)(device_id=device_id)

        # Only allow specific fields to be updated
        allowed_fields = ["name", "user_id", "is_default"]
        data = {k: v for k, v in request.data.items() if k in allowed_fields}

        if data.get('is_default'):
            await sync_to_async(Device.objects.filter(branch_id=device.branch_id, is_default=True).exclude(device_id=device_id).update)(is_default=False)
        
        # Update the object
        for field, value in data.items():
            setattr(device, field, value)

        # Save asynchronously, Serialize and return
        await sync_to_async(device.save)()
        serializer = DeviceSerializer(device)
        print(f"1st device partial_update took {(time.perf_counter() - start) * 1000:.3f} ms")
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    async def reset(self, request, device_id=None):
        try:
            start = time.perf_counter()
            user_id = request.user.id
            device = await sync_to_async(Device.objects.get)(device_id=device_id)
            device.is_active = False  # Mark as inactive to block connections
            await sync_to_async(device.save)()

            payload = {
                'device_id': device.device_id,
                'sender': user_id
            }
            await channel_layer.group_send(
                f"device_{device.device_id}", {
                'type': 'print.job',
                'signal': 'reset',
                'message': payload
            }) 

            logger.info(f"Reset initiated for device {device.device_id}")
            print(f"1st device reset took {(time.perf_counter() - start) * 1000:.3f} ms")
            return Response({'status': _('Device reset in progress')})
        except Device.DoesNotExist:
            logger.error(f"Device {device_id} not found")
            return Response({'error': _('Device not found')}, status=404)
        except Exception as e:
            logger.error(f"Reset failed for device {device_id}: {str(e)}")
            return Response({'error': str(e)}, status=500)
        
    @action(detail=True, methods=['post'], url_path='remove-printer')
    async def remove_printer(self, request, device_id=None):
        try:
            start = time.perf_counter()
            data = request.data
            user_id = request.user.id
            use_device = data.get('use_device')
            branch_id = int(request.data['branches'][0])
            printer_id = data.get('printer_id')
            device_id = device_id if use_device else await get_branch_device(branch_id, user_id)
            payload = {
                'printer_id': printer_id,
                'device_id': device_id,
                'sender': user_id
            }
            
            # Send WebSocket message
            await channel_layer.group_send(
                f"device_{device_id}",
                {
                    'signal': 'remove',
                    'type': 'print.job',
                    'message': payload
                }
            )
            print(f"1st printer remove took {(time.perf_counter() - start) * 1000:.3f} ms")
            return Response({'status': _('Removal initiated')}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': f'Internal server error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='set-default-printer')
    async def set_default_printer(self, request, device_id=None):
        try:
            start = time.perf_counter()
            data = request.data
            user_id = request.user.id
            printer_id = data.get('printer_id')
            use_device = data.get('use_device')
            branch_id = int(request.data['branches'][0])
            device_id = device_id if use_device else await get_branch_device(branch_id, user_id)
            payload = {
                'printer_id': printer_id,
                'device_id': device_id,
                'sender': user_id
            }
            
            # Send WebSocket message
            await channel_layer.group_send(
                f"device_{device_id}",
                {
                    'signal': 'default',
                    'type': 'print.job',
                    'message': payload
                }
            )
            print(f"1st set default took {(time.perf_counter() - start) * 1000:.3f} ms")
            return Response({'status': _('Setting default printer')}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': f'Internal server error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='test-printer')
    async def test_printer(self, request, device_id=None):
        try:
            start = time.perf_counter()
            data = request.data
            user_id = request.user.id
            printer_id = data.get('printer_id')
            branch_id = int(request.data['branches'][0])
            use_device = data.get('use_device')
            device_id = device_id if use_device else await get_branch_device(branch_id, user_id)
            payload = {
                'printer_id': printer_id,
                'device_id': device_id,
                'sender': user_id
            }
            
            # Send WebSocket message
            await channel_layer.group_send(
                f"device_{device_id}",
                {
                    'signal': 'test',
                    'type': 'print.job',
                    'message': payload
                }
            )
            print(f"1st test printer took {(time.perf_counter() - start) * 1000:.3f} ms")
            return Response({'status': 'Test initiated'}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': f'Internal server error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='list-printers')
    async def list_printers(self, request, device_id=None):
        try:
            start = time.perf_counter()
            data = request.data
            user_id = request.user.id
            use_device = data.get('use_device')
            branch_id = int(request.data['branches'][0])
            device_id = device_id if use_device else await get_branch_device(branch_id, user_id)
            payload = {
                'device_id': device_id,
                'sender': user_id
            }
            
            # # Verify device exists
            # await Device.objects.aget(device_id=device_id, branch_id_id=branch_id)
            
            # Send WebSocket message
            await channel_layer.group_send(
                f"device_{device_id}",
                {
                    'signal': 'list',
                    'type': 'print.job',
                    'message': payload
                }
            )
            print(f"1st list printers took {(time.perf_counter() - start) * 1000:.3f} ms")
            return Response({'status': 'List request initiated'}, status=status.HTTP_200_OK)
            
        except Device.DoesNotExist:
            return Response({'error': 'Device not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': f'Internal server error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='scan-printers')
    async def scan_printers(self, request, device_id=None):
        import uuid
        try:
            start = time.perf_counter()
            # Get the device instance (device_id is the device ID from URL)
            user = request.user
            device = await Device.objects.aget(device_id=device_id, is_active=True)
            scan_id = str(uuid.uuid4())
            print("device-scan: ", device, scan_id)

            await channel_layer.group_send(
                f"device_{device.device_id}",
                {
                    'signal': 'scan',
                    'type': 'print.job',
                    'message': json.dumps({'scan_id': scan_id, 'sender': user.id})
                }
            )
            print(f"1st scan printers took {(time.perf_counter() - start) * 1000:.3f} ms")
            return Response({'detail': _('Scan initiated'), 'scan_id': scan_id}, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                'error': f'Internal server error: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

from django.http import JsonResponse
from rest_framework.permissions import AllowAny

class RegisterDeviceView(APIView):
    permission_classes = [AllowAny]

    async def post(self, request):
        from .utils import get_default_logo
        try:
            start = time.perf_counter()
            data = json.loads(request.body)
            device_id = data.get('device_id')
            print("device_id: ", device_id)
            
            if not device_id:
                logger.error("Missing device_id in request")
                return JsonResponse({'error': _('Device ID is required')}, status=400)
            
            try:
                # print("now in view: ", timezone.now())
                device = await Device.objects.select_related('branch__restaurant', 'branch__company').aget(device_id=device_id, is_active=True, expiry_date__gte=timezone.now())
                # print("dev in view: ", device.added_by_id, device.device_id)
                logo_url = await get_default_logo(device.branch)
            except Device.DoesNotExist:
                logger.error(f"Device with ID {device_id} not found or inactive")
                return JsonResponse({'error': _('Device Not Found')}, status=404)
            print("logo_url: ", logo_url)
            
            branch_dets = {
                'branch_id': device.branch_id,
                'name': device.branch.name,
                'address': device.branch.address,
                'currency': device.branch.currency,
                'timezone': device.branch.timezone,
                "logo_url": logo_url,
            }
            print("branch_dets: ", branch_dets)
            
            new_device_id = await generate_device_id()
            device.device_id = new_device_id
            await device.asave()
            
            response_data = {
                'branch': branch_dets,
                'device': {
                    'name': device.name or f"Device-{device.device_id}",
                    "device_id": device.device_id,
                    'device_token': device.device_token,
                    'expiry_date': device.expiry_date
                }
            }
            logger.info(f"Device {device_id} registered successfully for branch {device.branch_id}")
            print(f"1st reg device took {(time.perf_counter() - start) * 1000:.3f} ms")
            return JsonResponse(response_data, status=200)
        
        except json.JSONDecodeError:
            logger.error("Invalid JSON in request body")
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.error(f"Error registering device: {str(e)}")
            return JsonResponse({'error': 'Internal server error'}, status=500)