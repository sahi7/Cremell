from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from CRE.models import Company, Restaurant, Branch


CustomUser = get_user_model()

# Choices for rule types and scopes
RULE_TYPES = (
    ('bonus', _('Bonus')),        # Positive adjustment to payroll (e.g., transport bonus)
    ('deduction', _('Deduction')), # Negative adjustment to payroll (e.g., tax deduction)
)

SCOPE_LEVELS = (
    ('company', _('Company')),     # Applies to all users in the company
    ('restaurant', _('Restaurant')), # Applies to a specific restaurant
    ('branch', _('Branch')),       # Applies to a specific branch
    ('user', _('User')),           # Applies to specific users
)

class Rule(models.Model):
    """
    Defines a fixed or percentage-based earning or deduction rule.
    Rules can be scoped to company, restaurant, branch, or specific users/roles.
    Used as the base for payroll calculations, with overrides applied for special cases.
    """
    name = models.CharField(max_length=150, help_text=_("Descriptive name of the rule (e.g., 'Transport Bonus')"))
    rule_type = models.CharField(max_length=20, choices=RULE_TYPES, help_text=_("Type of rule: bonus or deduction"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text=_("Fixed amount for the rule"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text=_("Percentage-based amount, if applicable (e.g., 5.00 for 5%)"))
    scope = models.CharField(max_length=20, choices=SCOPE_LEVELS, default='restaurant',
        help_text=_("Scope of rule application (company, restaurant, branch, user)"))
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True,
        help_text=_("Company this rule applies to, if scope=company"))
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True,
        help_text=_("Restaurant this rule applies to, if scope=restaurant"))
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True,
        help_text=_("Branch this rule applies to, if scope=branch"))
    priority = models.IntegerField(default=0, help_text=_("Higher priority rules override lower ones within the same scope"))
    effective_from = models.DateField(default=timezone.now, help_text=_("Date from which the rule is effective"))
    is_active = models.BooleanField(default=True, help_text=_("Whether the rule is currently active"))
    created_at = models.DateTimeField(auto_now_add=True, help_text=_("Timestamp when the rule was created"))
    updated_at = models.DateTimeField(auto_now=True, help_text=_("Timestamp when the rule was last updated"))
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='rules_created',
        help_text=_("Manager who created the rule, for audit purposes")
    )

    class Meta:
        indexes = [
            models.Index(fields=['scope', 'company', 'restaurant', 'branch', 'is_active', 'effective_from']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0), name='non_negative_amount'
            ),
            models.CheckConstraint(
                check=models.Q(percentage__gte=0) | models.Q(percentage__isnull=True),
                name='non_negative_percentage'
            ),
        ]

    def __str__(self):
        return _("{name} [{scope}]").format(name=self.name, scope=self.scope)
    

class RuleTarget(models.Model):
    """
    Maps a rule to specific roles or users for targeted application.
    Replaces ArrayField for better query performance and scalability.
    """
    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name='targets',
        help_text=_("The rule this target applies to"))
    target_type = models.CharField(max_length=20, choices=[('role', _('Role')), ('user', _('User'))],
        help_text=_("Type of target: role (e.g., 'server') or user (user ID)"))
    target_value = models.CharField(
        max_length=30, help_text=_("Value of the target (role name or user ID)"))
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True,
        help_text=_("Branch this target applies to, if scoped"))

    class Meta:
        indexes = [
            models.Index(fields=['rule', 'target_type', 'target_value']),
            models.Index(fields=['branch', 'target_type', 'target_value']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['rule', 'target_type', 'target_value', 'branch'],
                name='unique_rule_target'
            ),
        ]

    def __str__(self):
        return _("{rule_name} -> {target_type}:{target_value}").format(
            rule_name=self.rule.name,
            target_type=self.target_type,
            target_value=self.target_value
        )

class Period(models.Model):
    """
    Represents a month/year grouping for payroll batches.
    Used to organize payroll records and overrides.
    """
    month = models.IntegerField(help_text=_("Month of the payroll period (1-12)"))
    year = models.IntegerField(help_text=_("Year of the payroll period"))

    class Meta:
        unique_together = ('month', 'year')
        indexes = [
            models.Index(fields=['month', 'year']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(month__gte=1, month__lte=12),
                name='valid_month'
            ),
            models.CheckConstraint(
                check=models.Q(year__gte=2000), name='valid_year'
            ),
        ]

    def __str__(self):
        return _("{month}/{year}").format(month=self.month, year=self.year)
    

class Override(models.Model):
    """
    Defines an override for a specific rule, user, and period.
    Supports add, override, and remove actions for special cases (e.g., custom bonuses).
    Includes audit notes and accountability tracking.
    """
    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name='overrides',
        help_text=_("The rule being overridden"))
    period = models.ForeignKey(Period, on_delete=models.CASCADE, related_name='overrides',
        help_text=_("Payroll period this override applies to"))
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='overrides',
        help_text=_("User this override applies to"))
    override_type = models.CharField(max_length=20, choices=[('replace', _('Replace')), ('add', _('Add')), ('remove', _('Remove'))],
        help_text=_("Action: replace rule value, add to it, or remove the rule"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=_("Override amount, if applicable"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text=_("Override percentage, if applicable"))
    notes = models.TextField(blank=True, help_text=_("Audit notes explaining the override (e.g., 'One-time bonus')"))
    effective_from = models.DateField(default=timezone.now, help_text=_("Date from which the override is effective"))
    expires_at = models.DateField(null=True, blank=True, help_text=_("Date the override expires, if temporary"))
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True,
        help_text=_("Branch this override applies to, if scoped"))
    created_at = models.DateTimeField(auto_now_add=True, help_text=_("Timestamp when the override was created"))
    updated_at = models.DateTimeField(auto_now=True, help_text=_("Timestamp when the override was last updated"))
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='overrides_created',
        help_text=_("Manager who created the override, for audit purposes"))

    class Meta:
        indexes = [
            models.Index(fields=['user', 'period', 'rule']),
            models.Index(fields=['branch', 'period']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0) | models.Q(amount__isnull=True),
                name='non_negative_override_amount'
            ),
            models.CheckConstraint(
                check=models.Q(percentage__gte=0) | models.Q(percentage__isnull=True),
                name='non_negative_override_percentage'
            ),
        ]

    def __str__(self):
        return _("{override_type} {rule_name} for {user} ({period})").format(
            override_type=self.override_type,
            rule_name=self.rule.name,
            user=self.user.username,
            period=self.period
        )
    
class EffectiveRule(models.Model):
    """
    Stores precomputed rules for a user, period, and branch after applying overrides.
    Optimizes payroll generation by avoiding dynamic rule merging.
    """
    period = models.ForeignKey(Period, on_delete=models.CASCADE, related_name='effective_rules',
        help_text=_("Payroll period for this effective rule"))
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='effective_rules',
        help_text=_("User this rule applies to"))
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='effective_rules',
        help_text=_("Branch this rule applies to"))
    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name='effective_rules',
        help_text=_("The rule being applied"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text=_("Final computed amount after overrides"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text=_("Final computed percentage after overrides, if applicable"))
    source = models.CharField(max_length=20, choices=SCOPE_LEVELS,
        help_text=_("Scope from which the rule originates (company, restaurant, branch, user)"))
    created_at = models.DateTimeField(auto_now_add=True, help_text=_("Timestamp when the effective rule was created"))

    class Meta:
        indexes = [
            models.Index(fields=['period', 'user', 'branch']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0), name='non_negative_effective_amount'
            ),
            models.CheckConstraint(
                check=models.Q(percentage__gte=0) | models.Q(percentage__isnull=True),
                name='non_negative_effective_percentage'
            ),
        ]

    def __str__(self):
        return _("{rule_name} for {user} ({period})").format(
            rule_name=self.rule.name,
            user=self.user.username,
            period=self.period
        )


class Record(models.Model):
    """
    Represents the final computed payroll record for a user in a specific period and branch.
    Stores the base salary, total bonuses/deductions, and net pay.
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='payroll_records',
        help_text=_("User this payroll record applies to"))
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='payroll_records',
        help_text=_("Branch this payroll record applies to"))
    period = models.ForeignKey(Period, on_delete=models.CASCADE, related_name='payroll_records',
        help_text=_("Payroll period for this record"))
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, help_text=_("Base salary for the user"))
    total_bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0,
        help_text=_("Total bonuses applied (sum of bonus components)"))
    total_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0,
        help_text=_("Total deductions applied (sum of deduction components)"))
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, help_text=_("Final net pay (base + bonuses - deductions)"))
    status = models.CharField(max_length=20, choices=[('pending', _('Pending')), ('generated', _('Generated')), ('failed', _('Failed'))],
        default='pending', help_text=_("Processing status of the payroll record"))
    generated_at = models.DateTimeField(auto_now_add=True, help_text=_("Timestamp when the payroll record was generated"))

    class Meta:
        indexes = [
            models.Index(fields=['period', 'branch', 'user']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(base_salary__gte=0), name='non_negative_base_salary'
            ),
            models.CheckConstraint(
                check=models.Q(total_bonus__gte=0), name='non_negative_total_bonus'
            ),
            models.CheckConstraint(
                check=models.Q(total_deduction__gte=0), name='non_negative_total_deduction'
            ),
            models.CheckConstraint(
                check=models.Q(net_pay__gte=0), name='non_negative_net_pay'
            ),
        ]

    def __str__(self):
        return _("{user} - {period}").format(user=self.user.username, period=self.period)


class Component(models.Model):
    """
    Represents a single rule’s contribution to a payroll record.
    Links a payroll record to a rule with the applied amount.
    """
    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='components',
        help_text=_("Payroll record this component belongs to"))
    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name='components',
        help_text=_("Rule contributing to the payroll record"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text=_("Amount applied for this rule"))

    class Meta:
        indexes = [
            models.Index(fields=['record', 'rule']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0), name='non_negative_component_amount'
            ),
        ]

    def __str__(self):
        return _("{rule_name}: {amount}").format(rule_name=self.rule.name, amount=self.amount)