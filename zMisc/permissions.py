import redis.asyncio as redis
from django.conf import settings
from channels.layers import get_channel_layer
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
        user_role_value = await sync_to_async(user.get_role_value)()
        role_value_to_create = await sync_to_async(user.get_role_value)(role_to_create)
        if not user_role or user_role not in self.SCOPE_RULES or user_role_value > 5 or user_role_value > role_value_to_create:
            raise PermissionDenied(_("You do not have permission to create users."))

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
    # Redis client for caching
    redis_client = redis.from_url(settings.REDIS_URL)

    async def _get_cached_scope(self, user_id, model_name):
        """Fetch or cache user's scope for branches/restaurants."""
        cache_key = f"scope:{user_id}:{model_name}"
        cached = await self.redis_client.get(cache_key)
        if cached:
            return set(cached.decode().split(","))
        # Placeholder: Fetch scope async (assumes _is_object_in_scope is efficient)
        scope_ids = []  # Implement based on _is_object_in_scope
        await self.redis_client.setex(cache_key, 300, ",".join(map(str, scope_ids)))
        return set(scope_ids)

    async def has_permission(self, request, view):
        if view.action != "create":
            return True
        
        user = request.user
        employee_id = request.data.get('user')
        from_branch = request.data.get('from_branch')
        from_restaurant = request.data.get('from_restaurant')
        to_branch = request.data.get('to_branch')
        to_restaurant = request.data.get('to_restaurant')

        if not from_branch and not from_restaurant:
            raise PermissionDenied(_("At least one of from_branch or from_restaurant is required."))
        if not employee_id:
            raise PermissionDenied(_("Employee ID is required."))

        user_role = user.role
        if not user_role or user_role not in UserCreationPermission.SCOPE_RULES:
            raise PermissionDenied(_("You do not have permission to transfer users."))
        
        entity_permission = EntityUpdatePermission()
        same_restaurant = False

        # Validate source (from_branch/from_restaurant)
        if employee_id:
            try:
                employee = await CustomUser.objects.aget(pk=employee_id)
                if not await entity_permission._is_object_in_scope(request, employee, CustomUser):
                    raise PermissionDenied(_("Employee not in scope."))
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("Invalid Employee ID."))
        if from_branch:
            try:
                branch = await Branch.objects.select_related('restaurant').aget(pk=from_branch)
                in_scope = await entity_permission._is_object_in_scope(request, branch, Branch)
                if not in_scope:
                    user_restaurants = await self._get_cached_scope(user.id, 'restaurant')
                    if str(branch.restaurant.pk) in user_restaurants:
                        same_restaurant = True
                    else:
                        raise PermissionDenied(_("You are not authorized to transfer from this branch."))
            except Branch.DoesNotExist:
                raise PermissionDenied(_("Invalid from_branch ID."))

        if from_restaurant:
            try:
                restaurant = await Restaurant.objects.aget(pk=from_restaurant)
                if not await entity_permission._is_object_in_scope(request, restaurant, Restaurant):
                    raise PermissionDenied(_("You are not authorized to transfer from this restaurant."))
            except Restaurant.DoesNotExist:
                raise PermissionDenied(_("Invalid from_restaurant ID."))

        # Validate destination (to_branch/to_restaurant)
        if to_branch:
            try:
                branch = await Branch.objects.select_related('restaurant').aget(pk=to_branch)
                if not await entity_permission._is_object_in_scope(request, branch, Branch):
                    raise PermissionDenied(_("You can only transfer to active branches within your scope."))
            except Branch.DoesNotExist:
                raise PermissionDenied(_("Invalid to_branch ID."))

        if to_restaurant:
            try:
                restaurant = await Restaurant.objects.aget(pk=to_restaurant)
                if not await entity_permission._is_object_in_scope(request, restaurant, Restaurant):
                    raise PermissionDenied(_("You can only transfer to active restaurants within your scope."))
            except Restaurant.DoesNotExist:
                raise PermissionDenied(_("Invalid to_restaurant ID."))

        # Branch manager: Allow omitting to_branch/to_restaurant
        if user_role == 'branch_manager' and not to_branch and not to_restaurant:
            if not from_branch:
                raise PermissionDenied(_("Branch managers must specify a source branch."))
            # Flag for view to handle as awaiting_destination
            request.same_restaurant_transfer = False
            request.awaiting_destination = True
            return True

        # Flag same-restaurant transfer for view
        request.same_restaurant_transfer = same_restaurant
        request.awaiting_destination = False
        return True
    
    async def has_object_permission(self, request, view, obj):
        print(f"Checking review permission for TransferRequest {obj.id}, user {request.user.id}")
        user = request.user
        user_role = user.role
        if not user_role or user_role not in UserCreationPermission.SCOPE_RULES:
            raise PermissionDenied(_("You do not have permission to review transfers."))
        # Check if transfer is in scope
        entity_permission = EntityUpdatePermission()

        # Check from_branch or to_branch
        for branch in [obj.from_branch, obj.to_branch]:
            if branch and not await entity_permission._is_object_in_scope(request, branch, Branch):
                print(f"Branch {branch.pk} not in scope for user {user.id}")
                raise PermissionDenied(_("All branches must be within your scope."))
        
        # Check from_restaurant or to_restaurant
        for restaurant in [obj.from_restaurant, obj.to_restaurant]:
            if restaurant and not await entity_permission._is_object_in_scope(request, restaurant, Restaurant):
                print(f"Restaurant {restaurant.pk} not in scope for user {user.id}")
                raise PermissionDenied(_("All restaurants must be within your scope."))

        # If not in scope, check same-restaurant for from_branch
        if obj.from_branch:
            user_restaurants = await self._get_cached_scope(user.id, 'restaurant')
            if str(obj.from_branch.restaurant.pk) in user_restaurants:
                print(f"Same restaurant {obj.from_branch.restaurant.pk} for user {user.id}")
                return True

        print(f"TransferRequest {obj.id} not in scope for user {user.id}")
        raise PermissionDenied(_("You can only review transfers within your scope."))

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
            if await obj.branches.aexists():
                return {branch.id async for branch in obj.branches.all()}
            elif await obj.restaurants.aexists():
                return {restaurant.id async for restaurant in obj.restaurants.all()}
            elif await obj.companies.aexists():
                return {company.id async for company in obj.companies.all()}
        elif model == Branch:
            if obj.id:  # Always true if object exists, but explicit for clarity
                return {obj.id}
            elif obj.restaurant_id:
                return {obj.restaurant_id}
            elif obj.company_id:
                return {obj.company_id}
        elif model == Restaurant:
            if obj.id:
                return {obj.id}
            elif obj.company_id:
                return {obj.company_id}
        return set()
