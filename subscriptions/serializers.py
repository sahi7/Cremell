from adrf.serializers import Serializer, ModelSerializer
from rest_framework import serializers
from .models import Subscription, Plan, Feature, Coupon, CouponRedemption, History, EventType, EntityType, DiscountType, CouponEffectType
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import uuid

class PlanSerializer(ModelSerializer):
    class Meta:
        model = Plan
        fields = ['plan_id', 'name', 'billing_type', 'monthly_price', 'included_credits', 'grace_credits', 'grace_days', 'is_active', 'created_at']
        read_only_fields = ['plan_id', 'created_at']

    def validate(self, data):
        billing_type = data.get('billing_type')
        monthly_price = data.get('monthly_price')
        included_credits = data.get('included_credits')
        if billing_type == 'monthly_fixed' and monthly_price is None:
            raise serializers.ValidationError(_('Monthly price is required for monthly fixed plans.'))
        if billing_type == 'pay_per_request' and included_credits is None:
            raise serializers.ValidationError(_('Included credits are required for pay-per-request plans.'))
        return data

class FeatureSerializer(ModelSerializer):
    class Meta:
        model = Feature
        fields = ['feature_id', 'name', 'description', 'price', 'is_active', 'created_at']
        read_only_fields = ['feature_id', 'created_at']

class CouponSerializer(ModelSerializer):
    class Meta:
        model = Coupon
        fields = ['coupon_id', 'code', 'effect_type', 'coupon_metadata', 'discount_type', 'discount_value', 'max_uses', 'max_uses_per_entity', 'start_date', 'end_date', 'applies_to_plan', 'applies_to_feature', 'applies_to_entity_type', 'is_active', 'created_at']
        read_only_fields = ['coupon_id', 'created_at']

    def validate(self, data):
        effect_type = data.get('effect_type')
        coupon_metadata = data.get('coupon_metadata', {})
        discount_type = data.get('discount_type')
        discount_value = data.get('discount_value')

        # Validate discount_type and discount_value
        if effect_type in ['discount_percentage', 'discount_fixed']:
            if discount_type is None or discount_value is None:
                raise serializers.ValidationError(_('Discount type and value are required for discount effects.'))
            if discount_type == 'percentage' and discount_value > 100:
                raise serializers.ValidationError(_('Percentage discount cannot exceed 100.'))

        # Validate effect-specific metadata
        if effect_type == 'credit_addition' and 'credits' not in coupon_metadata:
            raise serializers.ValidationError(_('Credits must be specified in coupon_metadata for credit_addition effect.'))
        if effect_type == 'trial_extension' and 'days' not in coupon_metadata:
            raise serializers.ValidationError(_('Days must be specified in coupon_metadata for trial_extension effect.'))
        if effect_type == 'free_feature' and 'feature_id' not in coupon_metadata:
            raise serializers.ValidationError(_('Feature ID must be specified in coupon_metadata for free_feature effect.'))
        if effect_type == 'bundle_discount' and ('plan_id' not in coupon_metadata or 'feature_ids' not in coupon_metadata):
            raise serializers.ValidationError(_('Plan ID and feature IDs must be specified in coupon_metadata for bundle_discount effect.'))

        # Ensure start_date is before end_date
        if data.get('end_date') and data.get('start_date') > data.get('end_date'):
            raise serializers.ValidationError(_('Start date must be before end date.'))

        return data

class CouponRedemptionSerializer(ModelSerializer):
    class Meta:
        model = CouponRedemption
        fields = ['redemption_id', 'coupon', 'subscription', 'entity_type', 'entity_id', 'effect_applied', 'redeemed_at', 'discount_applied']
        read_only_fields = ['redemption_id', 'redeemed_at']

    def validate(self, data):
        if not data.get('subscription') and not (data.get('entity_type') and data.get('entity_id')):
            raise serializers.ValidationError(_('Either subscription or entity_type and entity_id must be provided.'))
        return data

class SubscriptionSerializer(Serializer):
    plan_id = serializers.UUIDField(required=True)
    entity_type = serializers.ChoiceField(choices=EntityType.choices, required=True)
    entity_id = serializers.IntegerField(required=True)
    features = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)
    coupon_code = serializers.CharField(max_length=50, required=False, allow_null=True)

    def validate_plan_id(self, value):
        try:
            uuid.UUID(str(value))
        except ValueError:
            raise serializers.ValidationError(_('Invalid plan ID format.'))
        return value

    def validate_features(self, value):
        if not value:
            return value
        for feature_id in value:
            try:
                uuid.UUID(str(feature_id))
            except ValueError:
                raise serializers.ValidationError(_('Invalid feature ID format: {feature_id}').format(feature_id=feature_id))
        return value

    def validate_coupon_code(self, value):
        if not value:
            return value
        if not isinstance(value, str) or len(value) > 50:
            raise serializers.ValidationError(_('Coupon code must be a string with maximum length of 50 characters.'))
        return value

    def validate(self, data):
        return data

class CouponRedemptionRequestSerializer(Serializer):
    coupon_code = serializers.CharField(max_length=50, required=True)
    entities = serializers.DictField(
        child=serializers.ListField(child=serializers.UUIDField()),
        required=True,
        help_text=_('Dictionary with entity types (company, restaurants, branches) mapping to lists of entity IDs.')
    )
    plan_id = serializers.UUIDField(required=False)
    feature_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)
    subscription_id = serializers.UUIDField(required=False)

    def validate_coupon_code(self, value):
        if not isinstance(value, str) or len(value) > 50:
            raise serializers.ValidationError(_('Coupon code must be a string with maximum length of 50 characters.'))
        return value

    def validate_entities(self, value):
        valid_entity_types = [EntityType.COMPANY, EntityType.RESTAURANT, EntityType.BRANCH]
        for entity_type in value.keys():
            if entity_type not in valid_entity_types:
                raise serializers.ValidationError(_('Invalid entity type: {entity_type}').format(entity_type=entity_type))
            if not isinstance(value[entity_type], list) or not all(isinstance(eid, str) for eid in value[entity_type]):
                raise serializers.ValidationError(_('Entity IDs for {entity_type} must be a list of UUIDs.').format(entity_type=entity_type))
        return value

    def validate(self, data):
        entities = data['entities']
        if not any(entities.get(et) for et in [EntityType.COMPANY, EntityType.RESTAURANT, EntityType.BRANCH, EntityType.ANY]):
            raise serializers.ValidationError(_('At least one valid entity (company, restaurant, or branch) must be provided.'))
        return data