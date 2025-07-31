from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import exceptions
from django.utils.translation import gettext as _

import logging

logger = logging.getLogger(__name__)

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    # @sync_to_async
    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user

        if not user.is_active:
            logger.warning(f"Inactive user attempt: {user.username}")
            raise exceptions.AuthenticationFailed(_('User account is inactive.'))
        
        if user.status not in ['active', 'on_leave']:
            logger.warning(f"Unauthorized user status: {user.username} - {user.status}")
            raise exceptions.AuthenticationFailed(_('User not assigned.'))
        
        return data