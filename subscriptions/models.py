from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import uuid

class BillingType(models.TextChoices):
    MONTHLY_FIXED = 'monthly_fixed', _('Monthly Fixed')
    PAY_PER_ORDER = 'pay_per_order', _('Pay Per Order')
    YEARLY_FIXED = 'yearly_fixed', _('Yearly Fixed')

class SubscriptionStatus(models.TextChoices):
    TRIAL = 'trial', _('Trial')
    ACTIVE = 'active', _('Active')
    GRACE_PERIOD = 'grace_period', _('Grace Period')
    SUSPENDED = 'suspended', _('Suspended')
    CANCELED = 'canceled', _('Canceled')

class EventType(models.TextChoices):
    CREATED = 'created', _('Created')
    RENEWED = 'renewed', _('Renewed')
    CANCELED = 'canceled', _('Canceled')
    PLAN_CHANGED = 'plan_changed', _('Plan Changed')
    STATUS_CHANGED = 'status_changed', _('Status Changed')
    FEATURE_ADDED = 'feature_added', _('Feature Added')
    FEATURE_REMOVED = 'feature_removed', _('Feature Removed')
    PLAN_CREATED = 'plan_created', _('Plan Created')
    PLAN_UPDATED = 'plan_updated', _('Plan Updated')
    PLAN_DELETED = 'plan_deleted', _('Plan Deleted')
    FEATURE_CREATED = 'feature_created', _('Feature Created')
    FEATURE_UPDATED = 'feature_updated', _('Feature Updated')
    FEATURE_DELETED = 'feature_deleted', _('Feature Deleted')
    COUPON_CREATED = 'coupon_created', _('Coupon Created')
    COUPON_UPDATED = 'coupon_updated', _('Coupon Updated')
    COUPON_DELETED = 'coupon_deleted', _('Coupon Deleted')
    COUPON_REDEEMED = 'coupon_redeemed', _('Coupon Redeemed')

class DiscountType(models.TextChoices):
    PERCENTAGE = 'percentage', _('Percentage')
    FIXED_AMOUNT = 'fixed_amount', _('Fixed Amount')

class EntityType(models.TextChoices):
    COMPANY = 'company', _('Company')
    RESTAURANT = 'restaurant', _('Restaurant')
    BRANCH = 'branch', _('Branch')
    ANY = 'any', _('Any')

class CouponEffectType(models.TextChoices):
    DISCOUNT_PERCENTAGE = 'discount_percentage', _('Discount Percentage')
    DISCOUNT_FIXED = 'discount_fixed', _('Fixed Amount Discount')
    CREDIT_ADDITION = 'credit_addition', _('Credit Addition')
    TRIAL_EXTENSION = 'trial_extension', _('Trial Extension')
    FREE_FEATURE = 'free_feature', _('Free Feature')
    BUNDLE_DISCOUNT = 'bundle_discount', _('Bundle Discount')

class Plan(models.Model):
    plan_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('Plan ID'))
    name = models.CharField(max_length=100, verbose_name=_('Name'))
    billing_type = models.CharField(max_length=20, choices=BillingType.choices, verbose_name=_('Billing Type'))
    monthly_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(0)], verbose_name=_('Monthly Price')
    )
    included_credits = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=_('Included Credits'))
    grace_credits = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=_('Grace Credits'))
    grace_days = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=_('Grace Days'))
    trial_days = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=_('Trial Days'))
    is_active = models.BooleanField(default=True, verbose_name=_('Is Active'))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_('Created At'))

    class Meta:
        indexes = [
            models.Index(fields=['is_active'], name='idx_plan_is_active'),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(billing_type='monthly_fixed', monthly_price__gt=0, included_credits__gte=0) |
                    models.Q(billing_type='pay_per_order', monthly_price__gte=0, included_credits__gt=0) |
                    models.Q(billing_type='yearly_fixed', monthly_price__gt=0, included_credits__gte=0)
                ),
                name='valid_pricing'
            ),
        ]
        verbose_name = _('Plan')
        verbose_name_plural = _('Plans')

    def __str__(self):
        return self.name

class Feature(models.Model):
    feature_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('Feature ID'))
    name = models.CharField(max_length=100, verbose_name=_('Name'))
    description = models.TextField(blank=True, verbose_name=_('Description'))
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], verbose_name=_('Price'))
    is_active = models.BooleanField(default=True, verbose_name=_('Is Active'))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_('Created At'))

    class Meta:
        indexes = [
            models.Index(fields=['is_active'], name='idx_feature_is_active'),
        ]
        verbose_name = _('Feature')
        verbose_name_plural = _('Features')

    def __str__(self):
        return self.name

class Subscription(models.Model):
    subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('Subscription ID'))
    entity_type = models.CharField(max_length=20, choices=EntityType.choices, verbose_name=_('Entity Type'))
    entity_id = models.IntegerField(verbose_name=_('Entity ID'))
    plan = models.ForeignKey(Plan, on_delete=models.RESTRICT, verbose_name=_('Plan'))
    features = models.ManyToManyField(Feature, related_name='subscriptions', verbose_name=_('Features'))
    status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, verbose_name=_('Status'))
    start_date = models.DateTimeField(default=timezone.now, verbose_name=_('Start Date'))
    trial_end_date = models.DateTimeField(null=True, blank=True, verbose_name=_('Trial End Date'))
    current_period_start = models.DateTimeField(default=timezone.now, verbose_name=_('Current Period Start'))
    current_period_end = models.DateTimeField(null=True, blank=True, verbose_name=_('Current Period End'))
    auto_renew = models.BooleanField(default=True, verbose_name=_('Auto Renew'))
    cancel_at_period_end = models.BooleanField(default=False, verbose_name=_('Cancel at Period End'))
    balance = models.IntegerField(default=0, verbose_name=_('Balance'))  # Can go negative up to grace_credits
    plan_name = models.CharField(max_length=100, verbose_name=_('Plan Name'))  # Denormalized for read efficiency
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        indexes = [
            models.Index(fields=['entity_type', 'entity_id'], name='idx_subscription_entity'),
            models.Index(fields=['status'], name='idx_subscription_status'),
            models.Index(fields=['plan'], name='idx_subscription_plan_id'),
            models.Index(fields=['current_period_start', 'current_period_end'], name='idx_subscription_period'),
        ]
        verbose_name = _('Subscription')
        verbose_name_plural = _('Subscriptions')

    def __str__(self):
        return f"{self.plan_name} ({self.entity_type}:{self.entity_id})"

class History(models.Model):
    history_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('History ID'))
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True, blank=True, verbose_name=_('Subscription'))
    event_type = models.CharField(max_length=50, choices=EventType.choices, verbose_name=_('Event Type'))
    old_status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, null=True, blank=True, verbose_name=_('Old Status'))
    new_status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, null=True, blank=True, verbose_name=_('New Status'))
    old_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='old_plan_history', verbose_name=_('Old Plan'))
    new_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='new_plan_history', verbose_name=_('New Plan'))
    old_feature = models.ForeignKey(Feature, on_delete=models.SET_NULL, null=True, blank=True, related_name='old_feature_history', verbose_name=_('Old Feature'))
    new_feature = models.ForeignKey(Feature, on_delete=models.SET_NULL, null=True, blank=True, related_name='new_feature_history', verbose_name=_('New Feature'))
    notes = models.TextField(null=True, blank=True, verbose_name=_('Notes'))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_('Created At'))

    class Meta:
        indexes = [
            models.Index(fields=['subscription'], name='idx_history_subscription_id'),
            models.Index(fields=['created_at'], name='idx_history_created_at'),
        ]
        verbose_name = _('Subscription History')
        verbose_name_plural = _('Subscription Histories')

    def __str__(self):
        return f"{self.event_type} for {self.subscription or 'system'}"

class Coupon(models.Model):
    coupon_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('Coupon ID'))
    code = models.CharField(max_length=50, unique=True, verbose_name=_('Code'))
    effect_type = models.CharField(max_length=20, choices=CouponEffectType.choices, verbose_name=_('Effect Type'))
    coupon_metadata = models.JSONField(default=dict, blank=True, verbose_name=_('Coupon Metadata'))
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, null=True, blank=True, verbose_name=_('Discount Type'))
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True, verbose_name=_('Discount Value'))
    max_uses = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True, verbose_name=_('Max Uses'))
    max_uses_per_entity = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True, verbose_name=_('Max Uses Per Entity'))
    start_date = models.DateTimeField(default=timezone.now, verbose_name=_('Start Date'))
    end_date = models.DateTimeField(null=True, blank=True, verbose_name=_('End Date'))
    applies_to_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Applies to Plan'))
    applies_to_feature = models.ForeignKey(Feature, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Applies to Feature'))
    applies_to_entity_type = models.CharField(max_length=20, choices=EntityType.choices, default='any', verbose_name=_('Applies to Entity Type'))
    is_active = models.BooleanField(default=True, verbose_name=_('Is Active'))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_('Created At'))

    class Meta:
        indexes = [
            models.Index(fields=['code'], name='idx_coupon_code'),
            models.Index(fields=['is_active'], name='idx_coupon_is_active'),
        ]
        verbose_name = _('Coupon')
        verbose_name_plural = _('Coupons')

    def __str__(self):
        return self.code

class CouponRedemption(models.Model):
    redemption_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('Redemption ID'))
    coupon = models.ForeignKey(Coupon, on_delete=models.RESTRICT, verbose_name=_('Coupon'))
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True, blank=True, verbose_name=_('Subscription'))
    entity_type = models.CharField(max_length=20, choices=EntityType.choices, verbose_name=_('Entity Type'))
    entity_id = models.IntegerField(verbose_name=_('Entity ID'))
    effect_applied = models.JSONField(default=dict, blank=True, verbose_name=_('Effect Applied'))
    redeemed_at = models.DateTimeField(default=timezone.now, verbose_name=_('Redeemed At'))
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], verbose_name=_('Discount Applied'))

    class Meta:
        indexes = [
            models.Index(fields=['subscription'], name='idx_coupon_subscription_id'),
            models.Index(fields=['coupon'], name='idx_coupon_coupon_id'),
            models.Index(fields=['entity_type', 'entity_id'], name='idx_redemption_entity'),
        ]
        verbose_name = _('Coupon Redemption')
        verbose_name_plural = _('Coupon Redemptions')

    def __str__(self):
        return f"{self.coupon.code} for {self.subscription or f'{self.entity_type}:{self.entity_id}'}"