from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from .models import Subscription

class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ['id', 'user', 'company', 'plan', 'start_date', 'end_date']

    def create(self, validated_data):
        subscription = Subscription.objects.create(**validated_data)
        return subscription