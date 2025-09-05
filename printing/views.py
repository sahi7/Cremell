from adrf.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async

from .models import Device
from .serializers import DeviceSerializer
from .permissions import DevicePermission
from zMisc.policies import ScopeAccessPolicy
from zMisc.permissions import StaffAccessPolicy
from zMisc.utils import clean_request_data

class DeviceViewSet(ModelViewSet):
    queryset = Device.objects.filter(is_active=True)
    serializer_class = DeviceSerializer
    # permission_classes = (ScopeAccessPolicy, )
    # permission_classes = (ScopeAccessPolicy, DevicePermission, )
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
            'branches'
        }
        """
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch_id'] = request.data.get('branches', [None])[0]
        serializer = self.serializer_class(data=data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        await self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    async def partial_update(self, request, *args, **kwargs):
        # Get PK from URL kwargs
        pk = kwargs.get('pk')
        device = await sync_to_async(Device.objects.get)(pk=pk)

        # Only allow specific fields to be updated
        allowed_fields = ["name", "user"]
        data = {k: v for k, v in request.data.items() if k in allowed_fields}

        # Update the object
        for field, value in data.items():
            setattr(device, field, value)

        # Save asynchronously, Serialize and return
        await sync_to_async(device.save)()
        serializer = DeviceSerializer(device)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='register-device')
    async def register_device(self, request):
        """Async return device details for given device_id."""
        device_id = request.data.get('device_id')
        if not device_id:
            return Response({'error': 'device_id required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            device = await database_sync_to_async(Device.objects.get)(device_id=device_id, is_active=True)
            return Response({
                'device_id': device.device_id,
                'device_token': device.device_token,
                'branch_id': device.branch_id,
                'name': device.name
            }, status=status.HTTP_200_OK)
        except Device.DoesNotExist:
            return Response({'error': 'Invalid or inactive device_id'}, status=status.HTTP_404_NOT_FOUND)