from asgiref.sync import sync_to_async
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import OutstandingToken
from django.utils import timezone
from django.utils.translation import gettext as _
from django.conf import settings

import logging

logger = logging.getLogger(__name__)

class CustomJWTAuthentication(JWTAuthentication):
    async def get_validated_token(self, raw_token):
        validated_token = await sync_to_async(super().get_validated_token)(raw_token)
        jti = validated_token['jti']
        logger.info(f"Validated token with jti: {jti}")
        # try:
        #     outstanding_token = await OutstandingToken.objects.filter(jti=jti).afirst()
        #     if not outstanding_token:
        #         logger.error(f"No OutstandingToken found for jti: {jti}")
        #         raise AuthenticationFailed(_("Token not found"))
        #     if outstanding_token.expires_at <= timezone.now():
        #         logger.warning(f"Token expired for jti: {jti}, expires_at: {outstanding_token.expires_at}")
        #         raise AuthenticationFailed(_("Token has expired"))
        # except Exception as e:
        #     raise AuthenticationFailed(str(_(f"Token validation failed: {str(e)}")))
        return validated_token
    
    async def authenticate(self, request):
        # Log request headers and cookies for debugging
        logger.info(f"Request headers: {request.headers}")
        logger.info(f"Request cookies: {request.COOKIES}")

        # Extract raw token from header or cookie
        header = self.get_header(request)
        print("header: ", header)
        if header is None:
            access_cookie = settings.SIMPLE_JWT.get('AUTH_COOKIE')
            raw_token = request.COOKIES.get(access_cookie) or None
            logger.info(f"No Authorization header, trying cookie: {raw_token}")
        else:
            raw_token = self.get_raw_token(header)
            logger.info(f"Raw token from header: {raw_token}")
        if raw_token is None:
            logger.warning("No token provided in request")
            return None

        try:
            validated_token = await self.get_validated_token(raw_token)
            user = await sync_to_async(self.get_user)(validated_token)
            logger.info(f"Authenticated user: {user}, token jti: {validated_token.get('jti')}")
            return user, validated_token
        except AuthenticationFailed:
            return None