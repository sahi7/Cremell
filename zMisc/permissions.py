from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.db.models import Q
from asgiref.sync import sync_to_async
from CRE.models import Branch, Restaurant, Country, Company

CustomUser = get_user_model()

class UserCreationPermission(BasePermission):
    """
    Permission for user creation with scope and status validation using Q objects.
    - Uses user.role from CustomUser.
    - Supports future role extensions.
    - Uses count-based checks for all entity types, optimized for 10M branches.
    How It Works:
        allowed_scopes['branches'] is computed as Branch.objects.filter(company_id__in=user.companies.all()).values_list('id', flat=True), which returns all branch IDs under the CompanyAdmin’s companies.
        requested['branches'] (from request.data) is checked against allowed_scopes['branches'] using issubset.
        If any requested branch ID isn’t in the allowed set, PermissionDenied is raised.
    """
    
    # Role-specific scope definitions
    SCOPE_RULES = {
        'company_admin': {
            'requires': ['companies'],
            'scopes': {
                'companies': lambda user, ids: Company.objects.filter(Q(id__in=ids) & Q(status='active')).count(),
                'countries': lambda user, ids: Country.objects.filter(Q(id__in=ids)).count(),
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=ids) & Q(company_id__in=user.companies.all()) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(company_id__in=user.companies.all()) & Q(status='active')).count(),
            }
        },
        'country_manager': {
            'requires': ['countries'],
            'scopes': {
                'countries': lambda user, ids: Country.objects.filter(Q(id__in=set(ids) & set(user.countries.all().values_list('id', flat=True)))).count(),
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=ids) & Q(country_id__in=user.countries.all()) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(country_id__in=user.countries.all()) & Q(status='active')).count(),
            }
        },
        'restaurant_owner': {
            'requires': ['restaurants'],
            'scopes': {
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=set(ids) & set(user.restaurants.all().values_list('id', flat=True))) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(restaurant_id__in=user.restaurants.all()) & Q(status='active')).count(),
            }
        },
        'restaurant_manager': {
            'scopes': {
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=set(ids) & set(user.restaurants.all().values_list('id', flat=True))) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(restaurant_id__in=user.restaurants.all()) & Q(status='active')).count(),
            }
        },
        'branch_manager': {
            'scopes': {
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=set(ids) & set(user.branches.all().values_list('id', flat=True))) & Q(status='active')).count(),
            }
        },
    }

    # Singular field names for error messages
    FIELD_SINGULAR = {
        'companies': 'company',
        'countries': 'country',
        'restaurants': 'restaurant',
        'branches': 'branch',
    }

    async def has_permission(self, request, view):
        if view.action != "create":
            return True
        
        user, role_to_create = request.user, request.data.get('role')
        requested = {field: request.data.get(field, []) for field in ['companies', 'countries', 'restaurants', 'branches']}

        user_role = user.role
        # if not user_role or user_role not in self.SCOPE_RULES:
        #     raise PermissionDenied(_("You do not have permission to create users."))

        rules = self.SCOPE_RULES[user_role]
        scope_checks = rules.get('scopes', {})

        # Enforce required fields
        required = rules.get('requires', [])
        for field in required:
            if not requested[field]:
                singular_field = self.FIELD_SINGULAR.get(field, field)
                raise PermissionDenied(_(f"New users must be associated with at least one {singular_field}"))

        # Validate scopes
        for field in scope_checks.keys():  # Only check defined scopes
            requested_ids = requested.get(field, [])
            if requested_ids:
                check_func = scope_checks[field]
                valid_count = await sync_to_async(check_func)(user, requested_ids)
                if valid_count != len(requested_ids):
                    singular_field = self.FIELD_SINGULAR.get(field, field)
                    message = _(f"You can only assign {singular_field} within your scope.") if field == 'countries' else _(f"You can only assign active {singular_field} within your scope.")
                    raise PermissionDenied(message)

        # Set pending status for non-SCOPE_RULES roles without branches
        request.data['status'] = 'active' if role_to_create in self.SCOPE_RULES else ('active' if requested['branches'] else 'pending')

        return True

class TransferPermission(BasePermission):
    async def has_permission(self, request, view):
        if view.action != "create":
            return True
        
        user = request.user
        to_branch = request.data.get('to_branch')
        to_restaurant = request.data.get('to_restaurant')

        user_role = user.role
        if not user_role or user_role not in UserCreationPermission.SCOPE_RULES:
            raise PermissionDenied(_("You do not have permission to transfer users."))

        rules = UserCreationPermission.SCOPE_RULES[user_role]
        scope_checks = rules.get('scopes', {})

        if to_branch:
            check_func = scope_checks.get('branches')
            if user_role == 'branch_manager':
                # BranchManager can only initiate out of their branches, not specify to_branch
                if to_branch and (not check_func or await sync_to_async(check_func)(user, [to_branch]) != 1):
                    raise PermissionDenied(_("You can only transfer users out of your branches. Destination must be set by a higher role."))
            if not check_func or await sync_to_async(check_func)(user, [to_branch]) != 1:
                raise PermissionDenied(_("You can only transfer to active branches within your scope."))

        if to_restaurant:
            check_func = scope_checks.get('restaurants')
            if not check_func or await sync_to_async(check_func)(user, [to_restaurant]) != 1:
                raise PermissionDenied(_("You can only transfer to active restaurants within your scope."))

        return True

class RManagerScopePermission(BasePermission):
    """
    Ensures that the specified manager for a restaurant belongs to the correct scope.
    """
    def has_permission(self, request, view):
        if view.action in ['create', 'update', 'partial_update']:
            self._check_manager_for_restaurant(request)
        return True

    def _check_manager_for_restaurant(self, request):
        manager_id = request.data.get('manager')
        company_id = request.data.get('company')

        if manager_id:
            try:
                manager = CustomUser.objects.get(id=manager_id)
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("The specified manager does not exist."))

            # Check if the manager belongs to the RestaurantManager group
            if not manager.groups.filter(name="RestaurantManager").exists():
                raise PermissionDenied(_("The manager must be a restaurant manager."))

            # If company_id is provided, validate the manager belongs to the company
            if company_id:
                if not manager.companies.filter(id=company_id).exists():
                    raise PermissionDenied(_("The manager must belong to the specified company."))

            # For standalone restaurants
            else:
                if manager.created_by != request.user:
                    raise PermissionDenied(
                        _("For standalone restaurants, the manager must be created by the owner.")
                    )
                if manager.companies.exists():
                    raise PermissionDenied(
                        _("The manager cannot belong to any company.")
                    )

class BManagerScopePermission(BasePermission):
    """
    Ensures that the specified manager for a branch belongs to the correct scope.
    """
    def has_permission(self, request, view):
        if view.action in ['create', 'update', 'partial_update']:
            self._check_manager_for_branch(request)
        return True

    def _check_manager_for_branch(self, request):
        manager_id = request.data.get('manager')

        if manager_id:
            try:
                manager = CustomUser.objects.get(id=manager_id)
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("The specified manager does not exist."))

            # Check if the manager belongs to the BranchManager group
            if not manager.groups.filter(name="BranchManager").exists():
                raise PermissionDenied(_("The manager must belong to the BranchManager group."))


class ObjectStatusPermission(BasePermission):
    """
    Ensures that actions are allowed only on objects with status "active".
    """
    def has_object_permission(self, request, view, obj):
        """
        Check the status of the restaurant or branch.
        Deny any actions on inactive objects.
        """
        if obj.status != 'active':
            # Only (CompanyAdmin, CountryManager) can modify the status
            if not request.user.groups.filter(name__in=["CompanyAdmin", "CountryManager", "RestaurantOwner"]).exists():
                raise PermissionDenied(_("This object is inactive and cannot be modified."))
        return True
