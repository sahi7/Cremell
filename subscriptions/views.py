import json
from asgiref.sync import sync_to_async
from rest_framework.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Plan, Feature, Coupon, Subscription, EventType, EntityType, CouponEffectType, CouponRedemption
from .serializers import PlanSerializer, FeatureSerializer, CouponSerializer, SubscriptionSerializer, CouponRedemptionRequestSerializer
from .permissions import IsSuperUser
from .tasks import log_subscription_event
from notifications.tasks import invalidate_cache_keys

client = settings.ASYNC_REDIS

class FeatureViewSet(ModelViewSet):
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer
    permission_classes = [IsSuperUser]

    async def perform_create(self, serializer):
        feature = await sync_to_async(serializer.save)()
        active_features = await client.get('active_features')
        active_features = json.loads(active_features) if active_features else []
        active_features.append({
            'feature_id': str(feature.feature_id),
            'price': float(feature.price)
        })
        await client.set('active_features', json.dumps(active_features), ex=3600)
        log_subscription_event.delay(
            subscription_id=None,
            event_type=EventType.FEATURE_CREATED,
            new_feature_id=str(feature.feature_id),
            notes=f"Created feature {feature.name}"
        )

    async def perform_update(self, serializer):
        old_feature = await sync_to_async(self.get_object)()
        feature = await sync_to_async(serializer.save)()
        invalidate_cache_keys.delay(['active_features'])
        log_subscription_event.delay(
            subscription_id=None,
            event_type=EventType.FEATURE_UPDATED,
            old_feature_id=str(old_feature.feature_id),
            new_feature_id=str(feature.feature_id),
            notes=f"Updated feature {feature.name}"
        )

    async def perform_destroy(self, instance):
        feature_id = str(instance.feature_id)
        feature_name = instance.name
        await sync_to_async(instance.delete)()
        invalidate_cache_keys.delay(['active_features'])
        log_subscription_event.delay(
            subscription_id=None,
            event_type=EventType.FEATURE_DELETED,
            old_feature_id=feature_id,
            notes=f"Deleted feature {feature_name}"
        )

class CouponViewSet(ModelViewSet):
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer
    permission_classes = [IsSuperUser]

    async def perform_create(self, serializer):
        coupon = await sync_to_async(serializer.save)()
        await client.set(
            f'coupon:{coupon.code}',
            json.dumps({
                'coupon_id': str(coupon.coupon_id),
                'effect_type': coupon.effect_type,
                'coupon_metadata': coupon.coupon_metadata,
                'applies_to_plan_id': str(coupon.applies_to_plan_id) if coupon.applies_to_plan_id else None,
                'applies_to_feature_id': str(coupon.applies_to_feature_id) if coupon.applies_to_feature_id else None,
                'applies_to_entity_type': coupon.applies_to_entity_type,
                'start_date': coupon.start_date.isoformat(),
                'end_date': coupon.end_date.isoformat() if coupon.end_date else None,
                'max_uses': coupon.max_uses,
                'max_uses_per_entity': coupon.max_uses_per_entity,
                'discount_type': coupon.discount_type,
                'discount_value': float(coupon.discount_value) if coupon.discount_value is not None else None
            }),
            ex=3600
        )
        log_subscription_event.delay(
            subscription_id=None,
            event_type=EventType.COUPON_CREATED,
            notes=f"Created coupon {coupon.code}"
        )

    async def perform_update(self, serializer):
        coupon = await sync_to_async(serializer.save)()
        invalidate_cache_keys.delay([f'coupon:{coupon.code}'])
        log_subscription_event.delay(
            subscription_id=None,
            event_type=EventType.COUPON_UPDATED,
            notes=f"Updated coupon {coupon.code}"
        )

    async def perform_destroy(self, instance):
        coupon_code = instance.code
        await sync_to_async(instance.delete)()
        invalidate_cache_keys.delay([f'coupon:{coupon_code}'])
        log_subscription_event.delay(
            subscription_id=None,
            event_type=EventType.COUPON_DELETED,
            notes=f"Deleted coupon {coupon_code}"
        )

class CouponRedemptionView(ModelViewSet):
    serializer_class = CouponRedemptionRequestSerializer
    permission_classes = []

    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        coupon_code = serializer.validated_data['coupon_code']
        entities = serializer.validated_data['entities']
        plan_id = serializer.validated_data.get('plan_id')
        feature_ids = serializer.validated_data.get('feature_ids', [])
        subscription_id = serializer.validated_data.get('subscription_id')

        # Select entity based on precedence: company > restaurant > branch
        entity_type = None
        entity_id = None
        for et in [EntityType.COMPANY, EntityType.RESTAURANT, EntityType.BRANCH]:
            if et in entities and entities[et]:
                entity_type = et
                entity_id = entities[et][0]
                break
        if not entity_type or not entity_id:
            return Response(
                {"error": _("At least one valid entity (company, restaurant, or branch) must be provided.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch coupon and redemption data atomically using Redis Lua script
        lua_script = """
        local coupon = redis.call('GET', KEYS[1])
        local redemptions = redis.call('GET', KEYS[2])
        return {coupon, redemptions}
        """
        coupon_key = f'coupon:{coupon_code}'
        redemption_key = f'redemptions:{entity_id}'
        keys = [coupon_key, redemption_key]
        coupon_data, redemptions = await client.eval(lua_script, len(keys), *keys)
        coupon_data = json.loads(coupon_data) if coupon_data else None
        redemptions = json.loads(redemptions) if redemptions else []

        # Fetch from database on cache miss
        if not coupon_data:
            try:
                coupon = await sync_to_async(Coupon.objects.select_related('applies_to_plan', 'applies_to_feature').get)(
                    code=coupon_code, is_active=True
                )
                coupon_data = {
                    'coupon_id': str(coupon.coupon_id),
                    'effect_type': coupon.effect_type,
                    'coupon_metadata': coupon.coupon_metadata,
                    'applies_to_plan_id': str(coupon.applies_to_plan_id) if coupon.applies_to_plan_id else None,
                    'applies_to_feature_id': str(coupon.applies_to_feature_id) if coupon.applies_to_feature_id else None,
                    'applies_to_entity_type': coupon.applies_to_entity_type,
                    'start_date': coupon.start_date,
                    'end_date': coupon.end_date,
                    'max_uses': coupon.max_uses,
                    'max_uses_per_entity': coupon.max_uses_per_entity,
                    'discount_type': coupon.discount_type,
                    'discount_value': float(coupon.discount_value) if coupon.discount_value else None
                }
                await client.set(coupon_key, json.dumps(coupon_data), ex=3600)
            except Coupon.DoesNotExist:
                return Response({"error": _("Invalid or inactive coupon code.")}, status=status.HTTP_400_BAD_REQUEST)

        if not redemptions:
            redemptions = await sync_to_async(list)(
                CouponRedemption.objects.filter(coupon__code=coupon_code, entity_id=entity_id, entity_type=entity_type)
                .values('redemption_id', 'effect_applied', 'redeemed_at')
            )
            await client.set(redemption_key, json.dumps(redemptions), ex=300)

        # Validate coupon applicability
        now = timezone.now()
        start_date = timezone.datetime.fromisoformat(coupon_data['start_date'])
        end_date = timezone.datetime.fromisoformat(coupon_data['end_date']) if coupon_data['end_date'] else None
        if start_date > now or (end_date and end_date < now):
            return Response({"error": _("Coupon is not valid at this time.")}, status=status.HTTP_400_BAD_REQUEST)
        if coupon_data['applies_to_entity_type'] != 'any' and coupon_data['applies_to_entity_type'] != entity_type:
            return Response({"error": _("Coupon does not apply to this entity type.")}, status=status.HTTP_400_BAD_REQUEST)

        # Validate redemption limits
        if coupon_data['max_uses']:
            total_redemptions = await sync_to_async(CouponRedemption.objects.filter(coupon__code=coupon_code).count)()
            if total_redemptions >= coupon_data['max_uses']:
                return Response({"error": _("Coupon has reached its maximum usage limit.")}, status=status.HTTP_400_BAD_REQUEST)

        if coupon_data['max_uses_per_entity']:
            entity_redemptions = len([r for r in redemptions if r['coupon__code'] == coupon_code])
            if entity_redemptions >= coupon_data['max_uses_per_entity']:
                return Response({"error": _("Coupon has reached its per-entity usage limit.")}, status=status.HTTP_400_BAD_REQUEST)

        # Fetch plan and feature data
        plan_data = None
        if plan_id:
            active_plans = await client.get('active_plans')
            active_plans = json.loads(active_plans) if active_plans else []
            if not active_plans:
                active_plans = await sync_to_async(list)(Plan.objects.filter(is_active=True).values('plan_id', 'monthly_price'))
                await client.set('active_plans', json.dumps(active_plans), ex=3600)
            plan_data = next((plan for plan in active_plans if plan['plan_id'] == str(plan_id)), None)
            if not plan_data:
                return Response({"error": _("Invalid or inactive plan ID.")}, status=status.HTTP_400_BAD_REQUEST)
            if coupon_data['applies_to_plan_id'] and coupon_data['applies_to_plan_id'] != str(plan_id):
                return Response({"error": _("Coupon does not apply to the selected plan.")}, status=status.HTTP_400_BAD_REQUEST)

        subscription = None
        subscription_features = []
        if subscription_id:
            subscription = await sync_to_async(Subscription.objects.select_related('plan').prefetch_related('features').get)(pk=subscription_id)
            subscription_features = await sync_to_async(list)(subscription.features.values_list('feature_id', flat=True))
            if coupon_data['applies_to_plan_id'] and coupon_data['applies_to_plan_id'] != str(subscription.plan_id):
                return Response({"error": _("Coupon does not apply to the subscription's plan.")}, status=status.HTTP_400_BAD_REQUEST)

        if feature_ids:
            active_features = await client.get('active_features')
            active_features = json.loads(active_features) if active_features else []
            if not active_features:
                active_features = await sync_to_async(list)(Feature.objects.filter(is_active=True).values('feature_id', 'price'))
                await client.set('active_features', json.dumps(active_features), ex=3600)
            invalid_features = [f for f in feature_ids if not any(feat['feature_id'] == str(f) for feat in active_features)]
            if invalid_features:
                return Response(
                    {"error": _("Invalid or inactive feature IDs: {invalid_features}").format(invalid_features=invalid_features)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if coupon_data['applies_to_feature_id'] and coupon_data['applies_to_feature_id'] not in [str(f) for f in feature_ids]:
                return Response({"error": _("Coupon does not apply to any selected features.")}, status=status.HTTP_400_BAD_REQUEST)

        # Apply coupon effect
        effect_applied = {}
        discount_applied = 0
        if subscription:
            if coupon_data['effect_type'] == CouponEffectType.CREDIT_ADDITION:
                subscription.balance += coupon_data['coupon_metadata'].get('credits', 0)
                effect_applied = {'credits_added': coupon_data['coupon_metadata'].get('credits', 0)}
            elif coupon_data['effect_type'] == CouponEffectType.TRIAL_EXTENSION:
                if subscription.trial_end_date:
                    subscription.trial_end_date += timezone.timedelta(days=coupon_data['coupon_metadata'].get('days', 0))
                    effect_applied = {'days_extended': coupon_data['coupon_metadata'].get('days', 0)}
            elif coupon_data['effect_type'] == CouponEffectType.FREE_FEATURE:
                effect_applied = {
                    'feature_id': coupon_data['coupon_metadata'].get('feature_id'),
                    'duration_days': coupon_data['coupon_metadata'].get('duration_days', 0)
                }
                feature_id = coupon_data['coupon_metadata'].get('feature_id')
                if feature_id and feature_id not in subscription_features:
                    await sync_to_async(subscription.features.add)(feature_id)
            elif coupon_data['effect_type'] in [CouponEffectType.DISCOUNT_PERCENTAGE, CouponEffectType.DISCOUNT_FIXED]:
                plan_price = subscription.plan.monthly_price or 0
                feature_prices = sum(feat['price'] for feat in active_features if feat['feature_id'] in subscription_features)
                total_price = plan_price + feature_prices
                discount_applied = total_price * (coupon_data['discount_value'] / 100) if coupon_data['effect_type'] == CouponEffectType.DISCOUNT_PERCENTAGE else coupon_data['discount_value']
                effect_applied = {'discount_applied': discount_applied}
            elif coupon_data['effect_type'] == CouponEffectType.BUNDLE_DISCOUNT:
                if coupon_data['coupon_metadata'].get('plan_id') == str(subscription.plan_id) and set(coupon_data['coupon_metadata'].get('feature_ids', [])) <= set(subscription_features):
                    discount_applied = (subscription.plan.monthly_price or 0) * (coupon_data['coupon_metadata'].get('percentage', 0) / 100)
                    effect_applied = {'discount_applied': discount_applied}
            await sync_to_async(subscription.save)()
        else:
            if coupon_data['effect_type'] == CouponEffectType.CREDIT_ADDITION:
                effect_applied = {'credits_added': coupon_data['coupon_metadata'].get('credits', 0)}
            elif coupon_data['effect_type'] == CouponEffectType.TRIAL_EXTENSION:
                effect_applied = {'days_extended': coupon_data['coupon_metadata'].get('days', 0)}
            elif coupon_data['effect_type'] == CouponEffectType.FREE_FEATURE:
                effect_applied = {
                    'feature_id': coupon_data['coupon_metadata'].get('feature_id'),
                    'duration_days': coupon_data['coupon_metadata'].get('duration_days', 0)
                }
            elif coupon_data['effect_type'] in [CouponEffectType.DISCOUNT_PERCENTAGE, CouponEffectType.DISCOUNT_FIXED]:
                plan_price = plan_data['monthly_price'] if plan_data else 0
                feature_prices = sum(feat['price'] for feat in active_features if feat['feature_id'] in [str(f) for f in feature_ids])
                total_price = plan_price + feature_prices
                discount_applied = total_price * (coupon_data['discount_value'] / 100) if coupon_data['effect_type'] == CouponEffectType.DISCOUNT_PERCENTAGE else coupon_data['discount_value']
                effect_applied = {'discount_applied': discount_applied}
            elif coupon_data['effect_type'] == CouponEffectType.BUNDLE_DISCOUNT:
                if plan_id and coupon_data['coupon_metadata'].get('plan_id') == str(plan_id) and set(coupon_data['coupon_metadata'].get('feature_ids', [])) <= set([str(f) for f in feature_ids]):
                    discount_applied = (plan_data['monthly_price'] or 0) * (coupon_data['coupon_metadata'].get('percentage', 0) / 100)
                    effect_applied = {'discount_applied': discount_applied}

        # Record redemption
        redemption = await sync_to_async(CouponRedemption.objects.create)(
            coupon_id=coupon_data['coupon_id'],
            subscription=subscription,
            entity_type=entity_type,
            entity_id=entity_id,
            effect_applied=effect_applied,
            discount_applied=discount_applied
        )

        # Update redemption cache
        redemptions.append({
            'redemption_id': str(redemption.redemption_id),
            'coupon__code': coupon_code,
            'effect_applied': effect_applied,
            'redeemed_at': redemption.redeemed_at.isoformat()
        })
        await client.set(redemption_key, json.dumps(redemptions), ex=300)

        log_subscription_event.delay(
            subscription_id=str(subscription.subscription_id) if subscription else None,
            event_type=EventType.COUPON_REDEEMED,
            notes=f"Redeemed coupon {coupon_code} with effect {effect_applied} for {entity_type}:{entity_id}"
        )

        response_data = {
            "redemption_id": str(redemption.redemption_id),
            "coupon_code": coupon_code,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "effect_applied": effect_applied,
            "discount_applied": float(discount_applied),
            "subscription_id": str(subscription.subscription_id) if subscription else None
        }
        return Response(response_data, status=status.HTTP_201_CREATED)