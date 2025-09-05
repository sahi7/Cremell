from rest_framework import serializers
from adrf.serializers import ModelSerializer
from .models import Device
from cre.models import Branch

class DeviceSerializer(ModelSerializer):
    branch_id = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.all(), source='branch'
    )

    class Meta:
        model = Device
        fields = ['id', 'device_id', 'branch_id', 'name', 'is_active', 'added_by', 'last_seen']
        read_only_fields = ['device_id', 'device_token', 'last_seen']

    def validate(self, data):
        """Ensure branch exists and user has permission."""
        if not data['branch']:
            raise serializers.ValidationError("Branch is required")
        return data