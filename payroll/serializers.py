from rest_framework import serializers
from django.utils.translation import gettext as _
from .models import Rule, RuleTarget, Period, Override, Record, Component
from CRE.models import Company, Restaurant, Branch, CustomUser

class RuleTargetSerializer(serializers.ModelSerializer):
    """
    Serializes RuleTarget model for mapping rules to roles or users.
    Validates target_type and target_value for role/user assignments.
    """
    class Meta:
        model = RuleTarget
        fields = ['target_type', 'target_value', 'branch']
        extra_kwargs = {
            'branch': {'required': False}
        }

    def validate(self, data):
        """
        Validates that target_value is valid for the given target_type.
        """
        target_type = data.get('target_type')
        target_value = data.get('target_value')

        if target_type == 'role' and target_value not in dict(CustomUser.ROLE_CHOICES).keys():
            raise serializers.ValidationError(
                _("Invalid role: {target_value}").format(target_value=target_value)
            )
        if target_type == 'user' and not CustomUser.objects.filter(id=target_value).exists():
            raise serializers.ValidationError(
                _("User with ID {target_value} does not exist").format(target_value=target_value)
            )
        return data

class RuleSerializer(serializers.ModelSerializer):
    """
    Serializes Rule model for creating and updating payroll rules.
    Supports scoping to company, restaurant, branch, or user.
    """
    targets = RuleTargetSerializer(many=True, required=False)

    class Meta:
        model = Rule
        fields = [
            'id', 'name', 'rule_type', 'amount', 'percentage', 'scope',
            'company', 'restaurant', 'branch', 'priority', 'effective_from',
            'is_active', 'created_by', 'targets'
        ]
        extra_kwargs = {
            'company': {'required': False},
            'restaurant': {'required': False},
            'branch': {'required': False},
            'created_by': {'read_only': True}
        }

    def validate(self, data):
        """
        Validates scope and related fields (company, restaurant, branch).
        Ensures amount or percentage is provided based on rule_type.
        """
        scope = data.get('scope')
        company = data.get('companies')[0]
        restaurant = data.get('restaurants')[0]
        branch = data.get('branches')[0]

        if scope == 'company' and not company:
            raise serializers.ValidationError(_("Company is required for company-scoped rules"))
        if scope == 'restaurant' and not restaurant:
            raise serializers.ValidationError(_("Restaurant is required for restaurant-scoped rules"))
        if scope == 'branch' and not branch:
            raise serializers.ValidationError(_("Branch is required for branch-scoped rules"))
        if scope == 'user' and not data.get('targets'):
            raise serializers.ValidationError(_("Targets are required for user-scoped rules"))

        if data.get('amount') is None and data.get('percentage') is None:
            raise serializers.ValidationError(_("Either amount or percentage must be provided"))

        return data

    async def acreate(self, validated_data):
        """
        Creates a rule with associated targets in a transaction.
        Sets created_by to the requesting user.
        """
        targets_data = validated_data.pop('targets', [])
        rule = await Rule.objects.acreate(created_by=self.context['request'].user, **validated_data)
        for target_data in targets_data:
            await RuleTarget.objects.acreate(rule=rule, **target_data)
        return rule

    async def aupdate(self, validated_data):
        """
        Updates a rule and its targets, preserving existing fields if not provided.
        """
        targets_data = validated_data.pop('targets', None)
        rule = super().aupdate(validated_data)
        if targets_data is not None:
            await rule.targets.all().adelete()
            for target_data in targets_data:
                await RuleTarget.objects.acreate(rule=rule, **target_data)
        return rule

class PeriodSerializer(serializers.ModelSerializer):
    """
    Serializes Period model for payroll period grouping.
    """
    class Meta:
        model = Period
        fields = ['id', 'month', 'year']

    def validate(self, data):
        """
        Validates that month is 1-12 and year is reasonable.
        """
        if not (1 <= data['month'] <= 12):
            raise serializers.ValidationError(_("Month must be between 1 and 12"))
        if data['year'] < 2000:
            raise serializers.ValidationError(_("Year must be 2000 or later"))
        return data

class OverrideSerializer(serializers.ModelSerializer):
    """
    Serializes Override model for special-case adjustments.
    Supports add, override, and remove actions with audit notes.
    """
    class Meta:
        model = Override
        fields = [
            'id', 'rule', 'period', 'user', 'override_type', 'amount',
            'percentage', 'notes', 'effective_from', 'expires_at', 'branch',
            'created_by'
        ]
        extra_kwargs = {
            'amount': {'required': False},
            'percentage': {'required': False},
            'branch': {'required': False},
            'created_by': {'read_only': True}
        }

    def validate(self, data):
        """
        Validates override_type and ensures amount/percentage are provided for add/replace.
        """
        override_type = data.get('override_type')
        amount = data.get('amount')
        percentage = data.get('percentage')

        if override_type in ['add', 'replace'] and amount is None and percentage is None:
            raise serializers.ValidationError(
                _("Amount or percentage is required for add/replace overrides")
            )
        if override_type == 'remove' and (amount is not None or percentage is not None):
            raise serializers.ValidationError(
                _("Amount and percentage must be null for remove overrides")
            )
        return data

    async def acreate(self, validated_data):
        """
        Creates an override with the requesting user as created_by.
        """
        override = await Override.objects.acreate(created_by=self.context['request'].user, **validated_data)
        return override

class ComponentSerializer(serializers.ModelSerializer):
    """
    Serializes Component model for individual rule contributions in a payslip.
    """
    rule_name = serializers.CharField(source='rule.name', read_only=True)

    class Meta:
        model = Component
        fields = ['rule_name', 'amount']

class RecordSerializer(serializers.ModelSerializer):
    """
    Serializes Record model for payslip data.
    Includes components for detailed breakdown.
    """
    components = ComponentSerializer(many=True, read_only=True)
    user = serializers.CharField(source='user.username', read_only=True)
    period = serializers.CharField(source='period.__str__', read_only=True)

    class Meta:
        model = Record
        fields = [
            'user', 'period', 'base_salary', 'total_bonus',
            'total_deduction', 'net_pay', 'components'
        ]