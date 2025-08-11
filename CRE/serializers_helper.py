import time
from redis.asyncio import Redis
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers
from adrf.views import APIView
from rest_framework import exceptions
from asgiref.sync import sync_to_async
from django.contrib.auth import authenticate, get_user_model
from django.conf import settings
import hashlib
from django.contrib.auth.hashers import make_password

import logging

logger = logging.getLogger(__name__)
redis = Redis.from_url(settings.REDIS_URL)
CustomUser = get_user_model()

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    async def validate(self, attrs):
        start = time.perf_counter()
        identifier = attrs.get("email") or attrs.get("username") or attrs.get("phone_number")
        password = attrs.get("password")
        field = next((f for f in ["email", "username", "phone_number"] if attrs.get(f)), None)

        if not identifier or not password:
            raise exceptions.AuthenticationFailed("Identifier and password are required.")
        print(f"A user get took {(time.perf_counter() - start) * 1000:.3f} ms")

        start = time.perf_counter()
        try:
            if not field:
                raise exceptions.AuthenticationFailed("No valid identifier provided.")
            user = await CustomUser.objects.aget(**{field: identifier})
        except CustomUser.DoesNotExist:
            raise exceptions.AuthenticationFailed("No user found with provided identifier.")
        print(f"A-1 Get user took {(time.perf_counter() - start) * 1000:.3f} ms")
        
        # Async password check
        start = time.perf_counter()
        if not await sync_to_async(user.check_password)(password):
            raise exceptions.AuthenticationFailed("Invalid credentials.")
        print(f"B user validation took {(time.perf_counter() - start) * 1000:.3f} ms")

        start = time.perf_counter()
        # Generate JWT tokens async
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = await sync_to_async(RefreshToken.for_user)(user)
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user_id': user.id
        }
        self.user = user
        print(f"C validation took {(time.perf_counter() - start) * 1000:.3f} ms")

        return data