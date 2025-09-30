from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import Plan, Feature, Subscription, History, Coupon, CouponRedemption

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'billing_type', 'monthly_price', 'included_credits', 'is_active', 'created_at')
    list_filter = ('billing_type', 'is_active')
    search_fields = ('name',)
    readonly_fields = ('plan_id', 'created_at')
    list_select_related = True
    ordering = ('-created_at',)
    fieldsets = (
        (None, {
            'fields': ('plan_id', 'name', 'billing_type', 'monthly_price', 'included_credits', 'grace_credits', 'grace_days', 'trial_days', 'is_active')
        }),
        (_('Metadata'), {
            'fields': ('created_at',),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    readonly_fields = ('feature_id', 'created_at')
    list_select_related = True
    ordering = ('-created_at',)
    fieldsets = (
        (None, {
            'fields': ('feature_id', 'name', 'description', 'price', 'is_active')
        }),
        (_('Metadata'), {
            'fields': ('created_at',),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('plan_name', 'entity_type', 'entity_id', 'status', 'start_date', 'trial_end_date', 'auto_renew')
    list_filter = ('status', 'entity_type', 'auto_renew')
    search_fields = ('plan_name', 'entity_id')
    readonly_fields = ('subscription_id', 'created_at', 'updated_at')
    list_select_related = ('plan',)
    prefetch_related_objects = ('features',)
    ordering = ('-created_at',)
    fieldsets = (
        (None, {
            'fields': ('subscription_id', 'entity_type', 'entity_id', 'plan', 'features', 'status', 'plan_name')
        }),
        (_('Billing'), {
            'fields': ('start_date', 'trial_end_date', 'current_period_start', 'current_period_end', 'auto_renew', 'cancel_at_period_end', 'balance')
        }),
        (_('Metadata'), {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(History)
class HistoryAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'subscription', 'old_status', 'new_status', 'created_at')
    list_filter = ('event_type', 'old_status', 'new_status')
    search_fields = ('subscription__plan_name', 'notes')
    readonly_fields = ('history_id', 'created_at')
    list_select_related = ('subscription', 'old_plan', 'new_plan', 'old_feature', 'new_feature')
    ordering = ('-created_at',)
    fieldsets = (
        (None, {
            'fields': ('history_id', 'subscription', 'event_type', 'old_status', 'new_status', 'old_plan', 'new_plan', 'old_feature', 'new_feature', 'notes')
        }),
        (_('Metadata'), {
            'fields': ('created_at',),
        }),
    )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False  # History records should not be deleted

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'effect_type', 'discount_type', 'discount_value', 'is_active', 'start_date', 'end_date')
    list_filter = ('effect_type', 'discount_type', 'is_active', 'applies_to_entity_type')
    search_fields = ('code',)
    readonly_fields = ('coupon_id', 'created_at')
    list_select_related = ('applies_to_plan', 'applies_to_feature')
    ordering = ('-created_at',)
    fieldsets = (
        (None, {
            'fields': ('coupon_id', 'code', 'effect_type', 'coupon_metadata', 'discount_type', 'discount_value', 'max_uses', 'max_uses_per_entity')
        }),
        (_('Applicability'), {
            'fields': ('applies_to_plan', 'applies_to_feature', 'applies_to_entity_type')
        }),
        (_('Validity'), {
            'fields': ('start_date', 'end_date', 'is_active')
        }),
        (_('Metadata'), {
            'fields': ('created_at',),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ('coupon', 'entity_type', 'entity_id', 'redeemed_at', 'discount_applied')
    list_filter = ('entity_type',)
    search_fields = ('coupon__code', 'entity_id', 'subscription__plan_name')
    readonly_fields = ('redemption_id', 'redeemed_at')
    list_select_related = ('coupon', 'subscription')
    ordering = ('-redeemed_at',)
    fieldsets = (
        (None, {
            'fields': ('redemption_id', 'coupon', 'subscription', 'entity_type', 'entity_id', 'effect_applied', 'discount_applied')
        }),
        (_('Metadata'), {
            'fields': ('redeemed_at',),
        }),
    )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False  # Redemptions should not be deleted