from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    # @sync_to_async
    def validate(self, attrs):
        data = super().validate(attrs)
  
        return data