# middleware.py
from channels.auth import AuthMiddlewareStack
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.authentication import TokenAuthentication
from django.contrib.auth.models import AnonymousUser
from channels.db import database_sync_to_async
from zMisc.utils import get_scopes_and_groups


@database_sync_to_async
def get_user_from_token(token, auth_class):
    try:
        if auth_class == JWTAuthentication:
            # Validate JWT token
            jwt_auth = JWTAuthentication()
            validated_token = jwt_auth.get_validated_token(token)
            user = jwt_auth.get_user(validated_token)
            return user
        elif auth_class == TokenAuthentication:
            # Validate DRF Token
            token_auth = TokenAuthentication()
            user, _ = token_auth.authenticate_credentials(token)
            return user
    except Exception:
        return AnonymousUser()


class TokenAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        auth_header = headers.get(b'authorization', b'').decode('utf-8')

        user = AnonymousUser()
        if auth_header:
            # Try JWT first
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
                user = await get_user_from_token(token, JWTAuthentication)
            # Then try DRF Token
            elif auth_header.startswith('Token '):
                token = auth_header.split(' ')[1]
                user = await get_user_from_token(token, TokenAuthentication)

        scope['user'] = user
        _scopes = await get_scopes_and_groups(user.id)
        scope['branches'] = _scopes.get('branch', [])
        scope['role'] = _scopes.get('role', [])
        return await self.inner(scope, receive, send)


# Stack the middleware
TokenAuthMiddlewareStack = lambda inner: TokenAuthMiddleware(AuthMiddlewareStack(inner))