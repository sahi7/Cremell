from rest_framework import serializers
from adrf.serializers import Serializer
from django.utils.translation import gettext_lazy as _



class BranchPermissionAssignmentSerializer(Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    role_ids = serializers.ListField(child=serializers.CharField(max_length=30), required=False)
    branch_id = serializers.IntegerField(required=True)
    permission_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    start_time = serializers.DateTimeField(required=False, allow_null=True)
    end_time = serializers.DateTimeField(required=False, allow_null=True)
    conditions = serializers.JSONField(required=False, default=dict)

    def validate(self, data):
        # Ensure either user_ids or role_ids is provided, not both
        if not data.get('user_ids') and not data.get('role_ids'):
            raise serializers.ValidationError(_("Either user_ids or role_ids must be provided."))
        if data.get('user_ids') and data.get('role_ids'):
            raise serializers.ValidationError(_("Cannot provide both user_ids and role_ids."))

        # Validate time-based constraints
        if data.get('start_time') and data.get('end_time'):
            if data['end_time'] <= data['start_time']:
                raise serializers.ValidationError(_("end_time must be after start_time."))

        return data

class BranchPermissionPoolSerializer(Serializer):
    branch_id = serializers.IntegerField(required=True)
    permission_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)

    def validate(self, data):
        # No I/O-bound operations, just basic validation
        if not data.get('permission_ids'):
            raise serializers.ValidationError(_("At least one permission_id must be provided."))
        return data