from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
import uuid

class BillingType(models.TextChoices):
    MONTHLY_FIXED = 'monthly_fixed', 'Monthly Fixed'
    PAY_PER_ORDER = 'pay_per_order', 'Pay Per Order'

class SubscriptionStatus(models.TextChoices):
    TRIAL = 'trial', 'Trial'
    ACTIVE = 'active', 'Active'
    GRACE_PERIOD = 'grace_period', 'Grace Period'
    SUSPENDED = 'suspended', 'Suspended'
    CANCELED = 'canceled', 'Canceled'

class EventType(models.TextChoices):
    CREATED = 'created', 'Created'
    RENEWED = 'renewed', 'Renewed'
    CANCELED = 'canceled', 'Canceled'
    PLAN_CHANGED = 'plan_changed', 'Plan Changed'
    STATUS_CHANGED = 'status_changed', 'Status Changed'
    FEATURE_ADDED = 'feature_added', 'Feature Added'
    FEATURE_REMOVED = 'feature_removed', 'Feature Removed'

class DiscountType(models.TextChoices):
    PERCENTAGE = 'percentage', 'Percentage'
    FIXED_AMOUNT = 'fixed_amount', 'Fixed Amount'

class EntityType(models.TextChoices):
    COMPANY = 'company', 'Company'
    BRANCH = 'branch', 'Branch'
    RESTAURANT = 'restaurant', 'Restaurant'

class Plan(models.Model):
    plan_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    billing_type = models.CharField(max_length=20, choices=BillingType.choices)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    included_credits = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(0)])
    grace_credits = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    grace_days = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['is_active'], name='idx_plan_is_active'),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(billing_type='monthly_fixed', monthly_price__isnull=False, included_credits__isnull=True) |
                    models.Q(billing_type='pay_per_order', monthly_price__isnull=True, included_credits__isnull=False)
                ),
                name='valid_pricing'
            ),
        ]

    def __str__(self):
        return self.name

class Feature(models.Model):
    feature_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['is_active'], name='idx_feature_is_active'),
        ]

    def __str__(self):
        return self.name

class Subscription(models.Model):
    subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    entity_id = models.PositiveIntegerField()
    plan = models.ForeignKey(Plan, on_delete=models.RESTRICT)
    features = models.ManyToManyField(Feature, related_name='subscriptions')
    status = models.CharField(max_length=20, choices=SubscriptionStatus.choices)
    start_date = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    cancel_at_period_end = models.BooleanField(default=False)
    balance = models.IntegerField(default=0)  # Can go negative up to grace_credits
    plan_name = models.CharField(max_length=100)  # Denormalized for read efficiency
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['entity_type', 'entity_id'], name='idx_subscription_entity'),
            models.Index(fields=['status'], name='idx_subscription_status'),
            models.Index(fields=['plan'], name='idx_subscription_plan_id'),
            models.Index(fields=['current_period_start', 'current_period_end'], name='idx_subscription_period'),
        ]

    def __str__(self):
        return f"{self.plan_name} ({self.entity_type}:{self.entity_id})"

class History(models.Model):
    history_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=50, choices=EventType.choices)
    old_status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, null=True, blank=True)
    new_status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, null=True, blank=True)
    old_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='old_plan_history')
    new_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='new_plan_history')
    old_feature = models.ForeignKey(Feature, on_delete=models.SET_NULL, null=True, blank=True, related_name='old_feature_history')
    new_feature = models.ForeignKey(Feature, on_delete=models.SET_NULL, null=True, blank=True, related_name='new_feature_history')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['subscription'], name='idx_history_subscription_id'),
            models.Index(fields=['created_at'], name='idx_history_created_at'),
        ]

    def __str__(self):
        return f"{self.event_type} for {self.subscription}"

class Coupon(models.Model):
    coupon_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    max_uses = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True)
    max_uses_per_customer = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    applies_to_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True)
    applies_to_feature = models.ForeignKey(Feature, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['code'], name='idx_coupon_code'),
            models.Index(fields=['is_active'], name='idx_coupon_is_active'),
        ]

    def __str__(self):
        return self.code

class CouponRedemption(models.Model):
    redemption_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coupon = models.ForeignKey(Coupon, on_delete=models.RESTRICT)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    redeemed_at = models.DateTimeField(default=timezone.now)
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        indexes = [
            models.Index(fields=['subscription'], name='idx_coupon_subscription_id'),
            models.Index(fields=['coupon'], name='idx_coupon_coupon_id'),
        ]

    def __str__(self):
        return f"{self.coupon.code} for {self.subscription}"