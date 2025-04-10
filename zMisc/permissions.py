from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.db.models import Q
from asgiref.sync import sync_to_async
from CRE.models import Branch, Restaurant, Country, Company
from zMisc.policies import ScopeAccessPolicy

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
                'companies': lambda user, ids: Company.objects.filter(Q(id__in=ids) & Q(company_id__in=user.companies.all()) & Q(status='active')).count(),
                'countries': lambda user, ids: Country.objects.filter(Q(id__in=ids)).count(),
                'restaurants': lambda user, ids: set(Restaurant.objects.filter( Q(id__in=ids) & Q(company_id__in=user.companies.all()) & Q(status='active')).values_list('id', flat=True)),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(company_id__in=user.companies.all()) & Q(status='active')).count(),
            },
            'queryset_filters': lambda user, company_ids: CustomUser.objects.filter(
                companies__id__in=company_ids
            )
        },
        'country_manager': {
            'requires': ['companies', 'countries'],
            'scopes': {
                'countries': lambda user, ids: Country.objects.filter(Q(id__in=set(ids) & set(user.countries.values_list('id', flat=True)))).count(),
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=ids) & Q(country_id__in=user.countries.all()) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(country_id__in=user.countries.all()) & Q(status='active')).count(),
            },
            'queryset_filters': lambda user, company_ids, country_ids: CustomUser.objects.filter(
                Q(companies__id__in=company_ids) & 
                Q(countries__id__in=country_ids)
            )
        },
        'restaurant_owner': {
            'requires': ['restaurants'],
            'scopes': {
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=set(ids) & set(user.restaurants.values_list('id', flat=True))) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(restaurant_id__in=user.restaurants.all()) & Q(status='active')).count(),
            },
            'queryset_filters': lambda user, restaurant_ids: CustomUser.objects.filter(
                restaurants__id__in=restaurant_ids
            )
        },
        'restaurant_manager': {
            'requires': ['restaurants'],
            'scopes': {
                'restaurants': lambda user, ids: Restaurant.objects.filter(Q(id__in=set(ids) & set(user.restaurants.values_list('id', flat=True))) & Q(status='active')).count(),
                'branches': lambda user, ids: Branch.objects.filter(Q(id__in=ids) & Q(restaurant_id__in=user.restaurants.all()) & Q(status='active')).count(),
            },
            'queryset_filters': lambda user, restaurant_ids: CustomUser.objects.filter(
                Q(restaurants__id__in=restaurant_ids) |
                (Q(branches__restaurant_id__in=restaurant_ids) & Q(companies__in=user.companies.all()))
            )
        },
        'branch_manager': {
            'requires': ['branches'],
            'scopes': {
                'branches': lambda user, ids: user.branches.filter(id__in=ids, status='active').count()
            },
            'queryset_filters': lambda user, branch_ids: CustomUser.objects.filter(
                branches__id__in=branch_ids
            )
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
    
    async def _get_ids(self, relation):
        """Helper to fetch IDs asynchronously from a user relation."""
        return [item.id async for item in relation.all()]

    async def get_queryset(self, request):
        """
        Async method to return a queryset of CustomUser objects within the requester’s scope.
        """
        user = request.user
        role = user.role  # Assumes CustomUser has a 'role' field
        if role not in self.SCOPE_RULES:
            return await sync_to_async(CustomUser.objects.none)()

        # Get the queryset filter for the user’s role

        queryset_filter = self.SCOPE_RULES[role]['queryset_filters']
        required_relations = self.SCOPE_RULES[role]['requires']

        # Dynamically fetch required IDs
        id_args = [await self._get_ids(getattr(user, rel)) for rel in required_relations]
        print(id_args)

        # Apply the filter with user and fetched IDs
        return await sync_to_async(queryset_filter)(user, *id_args)

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
    If the requesting user has companies, a company field is required in the request,
    and it must be within the user's companies. Optimized to reduce queries.
    Uses .aget() (async get) and async iteration (async for).
    Leverage Django’s async ORM methods (e.g., .aget(), .afilter(), .avalues_list()
    """
    async def has_permission(self, request, view):
        if view.action in ['create', 'update', 'partial_update']:
            await self._check_manager_for_branch(request)
        return True

    async def _check_manager_for_branch(self, request):
        manager_id = request.data.get('manager')
        company_id = request.data.get('company')
        user = request.user

        # Fetch user's company IDs once and reuse
        user_company_ids = set([company.id async for company in user.companies.all()]) # Query 1

        # Check company requirement and validity
        if user_company_ids:  # Non-empty set means user has companies
            if not company_id:
                raise PermissionDenied(_("A company is required."))
            if company_id not in user_company_ids:
                raise PermissionDenied(_("company must be one of your affiliated companies."))

        # Validate manager if provided
        if manager_id:
            # Fetch manager with groups and companies in one query
            try:
                manager = await CustomUser.objects.prefetch_related('groups', 'companies').aget(id=manager_id) # Query 2
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("manager does not exist."))

            # Check group membership in Python (groups already prefetched)
            group_names = [group.name async for group in manager.groups.all()]
            if "BranchManager" not in group_names:
                raise PermissionDenied(_("The manager must be a BranchManager."))

            # Check manager's company if company_id is provided (companies already prefetched)
            if company_id:
                manager_company_ids = {company.id async for company in manager.companies.all()}
                if company_id not in manager_company_ids:
                    raise PermissionDenied(_("The manager must belong to company."))

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


class EntityUpdatePermission(BasePermission):
    """
    Custom permission to validate user assignment and object scope for updates.
    Complements ScopeAccessPolicy with specific checks.
    """
    MODEL_MAP = {
        'user': CustomUser,
        'branch': Branch,
        'restaurant': Restaurant,
    }

    ROLE_FIELD_MAP = {
        'restaurant': {'manager': 'RestaurantManager'},
        'branch': {'manager': 'BranchManager'},
        # Add future mappings: 'driver': 'Driver', 'delivery_man': 'DeliveryMan'
    }

    async def has_permission(self, request, view):

        data = request.data
        object_type = data.get('object_type')
        object_id = data.get('object_id')
        field_name = data.get('field_name')
        user_id = data.get('user_id')

        model = self.MODEL_MAP.get(object_type)
        if not model or not object_id:
            return False

        # Fetch object and validate existence
        try:
            obj = await sync_to_async(model.objects.get)(id=object_id)
        except model.DoesNotExist:
            raise PermissionDenied(_("{object_type} ID {object_id} does not exist").format(object_type=object_type, object_id=object_id))

        # Validate object scope
        if not await self._is_object_in_scope(request, obj, model):
            raise PermissionDenied(_("Object not in your scope"))

        # Handle user assignment
        if user_id:
            try:
                user = await sync_to_async(CustomUser.objects.get)(id=user_id)
                if user == request.user:
                    return False
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("User ID {user_id} does not exist").format(user_id=user_id))

            # Check user role for specific fields
            expected_role = self.ROLE_FIELD_MAP.get(object_type, {}).get(field_name)
            if expected_role and not await sync_to_async(user.groups.filter(name=expected_role).exists)():
                raise PermissionDenied(_(f"User must be in {expected_role} group for {field_name} assignment"))

            # Validate user scope
            if not await self._is_object_in_scope(request, user, CustomUser):
                raise PermissionDenied(_("Assigned user not in your scope"))

        return True

    async def _is_object_in_scope(self, request, obj, model):
        """Check if object aligns with requester's scope."""
        requester = request.user
        config = await sync_to_async(ScopeAccessPolicy().get_role_config)(requester)
        if not config:
            return False

        allowed_scopes = await sync_to_async(config["scopes"])(requester)
        obj_scope_ids = await self._get_object_scope_ids(obj, model)

        return obj_scope_ids and any(obj_scope_ids.issubset(allowed_scopes.get(scope, set())) 
                                    for scope in ['companies', 'restaurants', 'branches'])

    async def _get_object_scope_ids(self, obj, model):
        """Extract scope-relevant IDs from the object (reused from EntityUpdateViewSet)."""
        if model == CustomUser:
            if await sync_to_async(obj.companies.exists)():
                return await sync_to_async(lambda: set(obj.companies.values_list('id', flat=True)))()
            elif await sync_to_async(obj.restaurants.exists)():
                return await sync_to_async(lambda: set(obj.restaurants.values_list('id', flat=True)))()
            elif await sync_to_async(obj.branches.exists)():
                return await sync_to_async(lambda: set(obj.branches.values_list('id', flat=True)))()
        elif model == Branch:
            if obj.company_id:
                return {obj.company_id}
            elif obj.restaurant_id:
                return {obj.restaurant_id}
            return {obj.id}
        elif model == Restaurant:
            if obj.company_id:
                return {obj.company_id}
            return {obj.id}
        return set()
