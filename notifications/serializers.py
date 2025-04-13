from adrf.serializers import ModelSerializer
from rest_framework import serializers
from .models import EmployeeTransfer, TransferHistory

class TransferSerializer(ModelSerializer):
    initiated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = EmployeeTransfer
        fields = ['id', 'user', 'from_branch', 'to_branch', 'from_restaurant', 'to_restaurant', 'transfer_type', 'end_date', 'initiated_by', 'status']
        extra_kwargs = {
            'status': {'default': 'pending'},
            'initiated_by': {'read_only': True},
        }

        async def acreate(self, validated_data):
            """Custom async create method"""
            return await EmployeeTransfer.objects.acreate(**validated_data)

        async def asave(self, **kwargs):
            """Full async save replacement"""
            validated_data = {**self.validated_data, **kwargs}
            if self.instance:
                self.instance = await self.aupdate(self.instance, validated_data)
            else:
                self.instance = await self.acreate(validated_data)
            return self.instance

class TransferHistorySerializer(ModelSerializer):
    class Meta:
        model = TransferHistory
        fields = ['id', 'user', 'branch', 'restaurant', 'transfer_type', 'from_entity', 'to_entity', 'initiated_by', 'timestamp', 'status', 'end_date']