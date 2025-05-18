from adrf.serializers import ModelSerializer
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from .models import *
# from .models import EmployeeTransfer, TransferHistory, RoleAssignment 

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

class RoleAssignmentSerializer(ModelSerializer):
    initiated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    
    class Meta:
        model = RoleAssignment
        fields = ['id', 'initiated_by', 'target_user', 'target_email', 'role', 'type', 'company', 'country', 'restaurant', 'branch']
        extra_kwargs = {
            'target_user': {'required': False},
            'target_email': {'required': False},
            'company': {'required': False},
            'country': {'required': False},
            'restaurant': {'required': False},
            'branch': {'required': False}
        }

    def validate(self, data):
        # Ensure target_user or target_email is provided
        if not data.get('target_user') and not data.get('target_email'):
            raise serializers.ValidationError(_("Either target_user or target_email is required."))
    
        mapped_fields = ['company', 'country', 'restaurant', 'branch']
        if not any(data.get(field) for field in mapped_fields):
            raise serializers.ValidationError(_("At least one of company, country, restaurant, or branch must be provided."))
        
        return data
    
    async def acreate(self, validated_data):
        """Custom async create method"""
        instance = RoleAssignment(**validated_data)
        await instance.asave()
        return instance

    async def asave(self, **kwargs):
        """Full async save replacement"""
        validated_data = {**self.validated_data, **kwargs}
        if self.instance:
            self.instance = await self.aupdate(self.instance, validated_data)
        else:
            self.instance = await self.acreate(validated_data)
        return self.instance
    
class ShiftAssignmentLogSerializer(ModelSerializer):
    class Meta:
        model = ShiftAssignmentLog
        fields = '__all__'