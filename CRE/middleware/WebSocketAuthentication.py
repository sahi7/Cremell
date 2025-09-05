# middleware.py
from channels.db import database_sync_to_async
from channels.auth import AuthMiddlewareStack
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

from printing.models import Device
from zMisc.utils import get_scopes_and_groups


@database_sync_to_async
def get_user_from_token(token):
    try:
        # Validate JWT token
        jwt_auth = JWTAuthentication()  
        validated_token = jwt_auth.get_validated_token(token)
        user = jwt_auth.get_user(validated_token)
        return user
    except Exception:
        return AnonymousUser()
    
@database_sync_to_async
def get_device_from_token(token):
    """Validate device_token and return Device instance or None."""
    try:
        device = Device.objects.get(device_token=token, is_active=True)
        if device.expiry_date and timezone.now() > device.expiry_date:
            return None  # expired
        device_dets = {
            "device_id": device.device_id,
            "branch_id": device.branch_id,
            "device_token": device.device_token,
            "expiry_date": device.expiry_date.isoformat(),
            "is_active": device.is_active
        }
        return device_dets
    except ObjectDoesNotExist:
        logger.error(f"Token not valid")
        return None
    except Exception as e:
        logger.error(f"Error building device data: {str(e)}")
        return None


class TokenAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    # async def __call__(self, scope, receive, send):
    #     headers = dict(scope['headers'])
    #     auth_header = headers.get(b'authorization', b'').decode('utf-8')

    #     user = AnonymousUser()
    #     device = None
    #     if auth_header:
    #         # Try JWT first
    #         if auth_header.startswith('Bearer '):
    #             token = auth_header.split(' ')[1]
    #             user = await get_user_from_token(token)

                
    #         # Then try DRF Token
    #         elif auth_header.startswith('Token '):
    #             token = auth_header.split(' ')[1]
    #             device = await get_device_from_token(token)
    #     try:
    #         scope['user'] = user
    #         _scopes = await get_scopes_and_groups(user)
    #         scope['branches'] = _scopes.get('branch', [])
    #         scope['role'] = _scopes.get('role', [])
    #     except Exception:
    #         return AnonymousUser()
    #     return await self.inner(scope, receive, send)

    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        auth_header = headers.get(b'authorization', b'').decode('utf-8')

        # Defaults
        user = AnonymousUser()
        device = None
        branches, roles = [], []

        if auth_header.startswith('Bearer '):
            # User JWT auth
            token = auth_header[7:]  # strip "Bearer "
            user = await get_user_from_token(token)

            if user and not isinstance(user, AnonymousUser):
                try:
                    _scopes = await get_scopes_and_groups(user)
                    branches = _scopes.get('branches', [])
                    roles = _scopes.get('role', [])
                except Exception:
                    pass

        elif auth_header.startswith('Token '):
            # Device DRF token auth
            token = auth_header[6:]  # strip "Token "
            device = await get_device_from_token(token)
            # print("in 4323423: ", device)

        # Attach final scope
        scope.update({
            'user': user,
            'device': device,
            'branches': branches,
            'role': roles,
        })
        # print("scope: ", scope)

        return await self.inner(scope, receive, send)


# Stack the middleware
TokenAuthMiddlewareStack = lambda inner: TokenAuthMiddleware(AuthMiddlewareStack(inner))