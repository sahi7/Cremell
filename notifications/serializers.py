from rest_framework import serializers
from .models import EmployeeTransfer, TransferHistory

class TransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeTransfer
        fields = ['id', 'user', 'from_branch', 'to_branch', 'from_restaurant', 'to_restaurant', 'transfer_type', 'end_date', 'status']
        extra_kwargs = {
            'status': {'default': 'pending'},
            'initiated_by': {'read_only': True},
        }

class TransferHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TransferHistory
        fields = ['id', 'user', 'branch', 'restaurant', 'transfer_type', 'from_entity', 'to_entity', 'initiated_by', 'timestamp', 'status', 'end_date']