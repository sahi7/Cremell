import json
import asyncio
import redis.asyncio as redis

from typing import List, Dict
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.apps import apps
from django.db.models import ForeignKey
from CRE.tasks import log_activity
# from CRE.models import Branch, Restaurant, Country, Company, Shift, StaffShift, ShiftPattern
from CRE.models import *
from payroll.models import Rule
from notifications.models import RoleAssignment, EmployeeTransfer
from zMisc.policies import ScopeAccessPolicy
from zMisc.utils import AttributeChecker, LowRoleQsFilter, compare_role_values, validate_role, get_scopes_and_groups

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

    async def check_companies(user, ids, scopes, user_role):
        query = Q(id__in=ids)
        if user_role == 'company_admin':
            query &= Q(id__in=scopes['company']) & Q(status='active')
        else:
            return 0
        return await Company.objects.filter(query).acount()

    async def check_countries(user, ids, scopes, user_role):
        query = Q(id__in=ids)
        if user_role == 'country_manager':
            query &= Q(id__in=scopes['country'])
        else:
            return 0
        return await Country.objects.filter(query).acount()

    async def check_restaurants(user, ids, scopes, user_role):
        query = Q(id__in=ids)
        if user_role == 'company_admin':
            query &= Q(company_id__in=scopes['company']) & Q(status='active')
            return set(await Restaurant.objects.filter(query).avalues_list('id', flat=True))
        elif user_role == 'country_manager':
            query &= Q(country_id__in=scopes['country']) & Q(status='active')
        elif user_role in ('restaurant_owner', 'restaurant_manager'):
            query &= Q(id__in=scopes['restaurant']) & Q(status='active')
        else:
            return 0
        return await Restaurant.objects.filter(query).acount()

    async def check_branches(user, ids, scopes, user_role):
        query = Q(id__in=ids)
        if user_role == 'company_admin':
            query &= Q(company_id__in=scopes['company']) & Q(status='active')
        elif user_role == 'country_manager':
            query &= Q(country_id__in=scopes['country']) & Q(status='active')
        elif user_role in ('restaurant_owner', 'restaurant_manager'):
            query &= Q(restaurant_id__in=scopes['restaurant']) & Q(status='active')
        elif user_role == 'branch_manager':
            query &= Q(id__in=scopes['branch']) & Q(status='active')
        else:
            return 0
        return await Branch.objects.filter(query).acount()

    # Async queryset_filters (for potential future use)
    async def filter_company_users(self, user, max_r_val, company_ids):
        return CustomUser.objects.filter(
            companies__id__in=company_ids,
            r_val__gte=max_r_val
        )

    async def filter_country_users(self, user, max_r_val, company_ids, country_ids):
        return CustomUser.objects.filter(
            Q(companies__id__in=company_ids) & Q(countries__id__in=country_ids),
            r_val__gte=max_r_val
        )

    async def filter_restaurant_owner_users(self, user, max_r_val, restaurant_ids):
        return CustomUser.objects.filter(
            Q(restaurants__id__in=restaurant_ids) | 
            Q(branches__restaurant__id__in=restaurant_ids),
            r_val__gte=max_r_val
        )

    async def filter_restaurant_manager_users(self, user, max_r_val, restaurant_ids):
        return CustomUser.objects.filter(
            Q(restaurants__id__in=restaurant_ids) |
            (Q(branches__restaurant_id__in=restaurant_ids) & Q(companies__in=user.companies.all())),
            r_val__gte=max_r_val
        )

    async def filter_branch_users(self, user, max_r_val, branch_ids):
        return CustomUser.objects.filter(
            branches__id__in=branch_ids,
            r_val__gte=max_r_val
        )
    
    # Async scope rules configuration
    SCOPE_RULES = {
        'company_admin': {
            'requires': ['companies'],
            'scopes': {
                'companies': check_companies,
                'countries': check_countries,
                'restaurants': check_restaurants,
                'branches': check_branches,
            },
            'queryset_filters': 'filter_company_users'
        },
        'country_manager': {
            'requires': ['companies', 'countries'],
            'scopes': {
                'countries': check_countries,
                'restaurants': check_restaurants,
                'branches': check_branches,
            },
            'queryset_filters': 'filter_country_users'
        },
        'restaurant_owner': {
            'requires': ['restaurants'],
            'scopes': {
                'restaurants': check_restaurants,
                'branches': check_branches,
            },
            'queryset_filters': 'filter_restaurant_owner_users'
        },
        'restaurant_manager': {
            'requires': ['restaurants'],
            'scopes': {
                'restaurants': check_restaurants,
                'branches': check_branches,
            },
            'queryset_filters': 'filter_restaurant_manager_users'
        },
        'branch_manager': {
            'requires': ['branches'],
            'scopes': {
                'branches': check_branches
            },
            'queryset_filters': 'filter_branch_users'
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
        user_role_value = await user.get_role_value()
        role_value_to_create = await user.get_role_value(role_to_create)
        scopes = await get_scopes_and_groups(user)

        # Basic role validation
        if not user_role or user_role not in self.SCOPE_RULES or user_role_value > 5 or user_role_value >= role_value_to_create:
            raise PermissionDenied(_("You do not have permission to create users."))

        rules = self.SCOPE_RULES[user_role]
        scope_checks = rules.get('scopes', {})

        # Enforce required fields
        required = rules.get('requires', [])
        for field in required:
            if not requested[field]:
                singular_field = self.FIELD_SINGULAR.get(field, field)
                raise PermissionDenied(_(f"New users must be associated with at least one {singular_field}"))

        # Validate scopes using async checks
        for field, check_func in scope_checks.items():
            requested_ids = requested.get(field, [])
            if requested_ids:
                # Call the appropriate async check function
                valid_count = await check_func(user, requested_ids, scopes, user_role)
                
                # Special handling for restaurants which returns a set
                if field == 'restaurants' and isinstance(valid_count, set):
                    missing_ids = set(requested_ids) - valid_count
                    print("missing8: ",missing_ids, set(requested_ids) , valid_count)
                    if missing_ids:
                        singular_field = self.FIELD_SINGULAR.get(field, field)
                        raise PermissionDenied(_(f"You can only assign active {singular_field} within your scope. Invalid IDs: {missing_ids}"))
                elif valid_count != len(requested_ids):
                    print("ccoun: ", valid_count, requested_ids, len(requested_ids))
                    singular_field = self.FIELD_SINGULAR.get(field, field)
                    message = _(f"You can only assign {singular_field} within your scope.") if field == 'countries' else _(f"You can only assign active {singular_field} within your scope.")
                    raise PermissionDenied(message)

        # Set status based on role and branch assignments
        request.data['status'] = 'active' if role_to_create in self.SCOPE_RULES else ('active' if requested['branches'] else 'pending')

        return True
    
    async def _get_ids(self, relation):
        """Helper to fetch IDs asynchronously from a user relation."""
        return [item.id async for item in relation.all()]

    async def get_queryset(self):
        """
        Async method to return a queryset of CustomUser objects within the requester’s scope,
        excluding users with role_value <= requester's role_value.
        """
        user = self.request.user
        role = user.role

        if role not in self.SCOPE_RULES:
            return CustomUser.objects.none()

        # Get the requester's role_value
        user_role_value = await user.get_role_value()

        # Get the queryset filter function and required relations
        filter_func_name = self.SCOPE_RULES[role]['queryset_filters']
        required_relations = self.SCOPE_RULES[role]['requires']

        # Map filter function names to actual functions
        filter_functions = {
            'filter_company_users': self.filter_company_users,
            'filter_country_users': self.filter_country_users,
            'filter_restaurant_owner_users': self.filter_restaurant_owner_users,
            'filter_restaurant_manager_users': self.filter_restaurant_manager_users,
            'filter_branch_users': self.filter_branch_users,
        }

        queryset_filter = filter_functions.get(filter_func_name)
        if not queryset_filter:
            return CustomUser.objects.none()

        FIELD_MAP = {
            'companies': 'company',
            'countries': 'country',
            'restaurants': 'restaurant',
            'branches': 'branch'
        }
        def normalize_scope_field(field):
            """Convert any field name to its plural form using FIELD_MAP"""
            return FIELD_MAP.get(field, field)
        # Fetch required IDs concurrently
        _scopes = await get_scopes_and_groups(user)
        reqius = [_scopes.get(normalize_scope_field(rel), _scopes.get(rel)) for rel in required_relations]
        # id_args = await asyncio.gather(*[self._get_ids(getattr(user, rel)) for rel in required_relations]) 

        # Apply the async filter function with role_value filtering
        # return await queryset_filter(user, user_role_value, *id_args)
        return await queryset_filter(user, user_role_value, *reqius)
    
class StaffAccessPolicy(BasePermission):
    """
    Async permission class for users with roles in ROLE_CHOICES but not in SCOPE_CONFIG groups.
    Optimized for low latency with Redis-cached branch IDs and direct object checks.
    """
    # Map models to object permission checks
    OBJECT_CHECKS = {
        Order: lambda obj, user, branch_ids: obj.created_by_id == user.id and obj.branch_id in branch_ids,
        OvertimeRequest: lambda obj, user, branch_ids: StaffShift.objects.select_related('branch').filter(
            id=obj.staff_shift_id, user=user, branch_id__in=branch_ids
        ).aexists(),
        StaffShift: lambda obj, user, branch_ids: obj.user_id == user.id and obj.branch_id in branch_ids,
        ShiftPattern: lambda obj, user, branch_ids: obj.branch_id in branch_ids,
        Shift: lambda obj, user, branch_ids: obj.branch_id in branch_ids,
        Branch: lambda obj, user, branch_ids: obj.id in branch_ids,
        Restaurant: lambda obj, user, branch_ids: Branch.objects.filter(restaurant_id=obj.id, id__in=branch_ids).aexists(),
        EmployeeTransfer: lambda obj, user, branch_ids: (
            Branch.objects.filter(id=obj.from_branch_id, id__in=branch_ids).aexists() or
            obj.manager_id == user.id
        ),
        CustomUser: lambda obj, user, branch_ids: obj.branches.filter(id__in=branch_ids).aexists(),
    }

    @staticmethod
    async def cook_order_filter(user, branch_ids):
        """Filter orders for cooks based on claimed prepare tasks."""
        return Q(task__task_type='prepare', task__claimed_by=user, task__status__in=['claimed', 'completed'])

    @staticmethod
    async def shift_leader_order_filter(user, branch_ids):
        """Filter orders for shift leaders in their branches."""
        return Q(branch_id__in=branch_ids)
    
    # # Map models to queryset filters
    # QUERYSET_FILTERS = {
    #     Order: lambda user, branch_ids: Q(created_by=user, branch_id__in=branch_ids),
    #     OvertimeRequest: lambda user, branch_ids: Q(staff_shift__user=user, staff_shift__branch_id__in=branch_ids),
    #     StaffShift: lambda user, branch_ids: Q(user=user, branch_id__in=branch_ids),
    #     ShiftPattern: lambda user, branch_ids: Q(branch_id__in=branch_ids),
    #     Shift: lambda user, branch_ids: Q(branch_id__in=branch_ids),
    #     Branch: lambda user, branch_ids: Q(id__in=branch_ids),
    #     Restaurant: lambda user, branch_ids: Q(branches__id__in=branch_ids),
    #     EmployeeTransfer: lambda user, branch_ids: Q(from_branch_id__in=branch_ids) | Q(manager=user),
    #     CustomUser: lambda user, branch_ids: Q(branches__id__in=branch_ids),
    # }
    
    async def has_permission(self, request, view):
        user = request.user
        if not validate_role(user.role):
            return False

        # Check if user has a role but no SCOPE_CONFIG groups
        scopes = await get_scopes_and_groups(user)

        # Check required branches in request body
        requested_branches = set(request.data.get("branches", []))
        if not requested_branches:
            return False  # Requires branches
        if not scopes.get('branch'):
            return False
        return requested_branches.issubset(scopes['branch'])

    async def has_object_permission(self, request, view, obj):
        user = request.user
        scopes = await get_scopes_and_groups(user)
        branch_ids = scopes['branch']
        
        # Use dictionary dispatch to avoid if/elif
        check = self.OBJECT_CHECKS.get(obj.__class__)
        if not check:
            return False
        return await check(obj, user, branch_ids)

    async def get_queryset_scope(self, user, view=None):
        model = view.queryset.model
        scopes = await get_scopes_and_groups(user)
        branch_ids = scopes['branch']
        role = scopes['role']
        filters = LowRoleQsFilter.FILTER_TEMPLATES.get(model, {})
        filter_func = filters.get(role, filters.get('default', LowRoleQsFilter.default_empty_filter))
        return await filter_func(user, branch_ids)
        
        # filter_func = self.QUERYSET_FILTERS.get(model, lambda u, b: Q(pk__in=[]))
        # return filter_func(user, branch_ids)
    

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
                if employee == user:
                    raise PermissionDenied(_("Must be assigned."))
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
        print(_(f"Checking review permission for TransferRequest {obj.id}, user {request.user.id}"))
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

        return True

class RestaurantPermission(BasePermission):
    """
    Ensures that the specified manager for a restaurant belongs to the correct scope.
    """
    async def has_permission(self, request, view):
        if view.action not in ['create', 'update', 'partial_update']:
            return True  # Only apply to these actions
        user = request.user
        manager_id = request.data.get('manager')
        company_id = request.data.get('company')
        country_id = request.data.get('country')
        scopes_and_groups = await get_scopes_and_groups(user)
        
        # Attach to request for reuse in view
        request.user_scope = scopes_and_groups
        if manager_id:
            await AttributeChecker().check_manager(manager_id, company_id)
        if country_id and "CountryManager" in scopes_and_groups['groups']:
            if country_id not in scopes_and_groups['country']:
                raise PermissionDenied(_(f'You do not have permission to {view.action} branches in this country.'))
        if company_id:
            if company_id not in scopes_and_groups['company']:
                raise PermissionDenied(_(f'You do not have permission to {view.action} restaurants in this company.'))
            if view.action == 'create':
                restaurant = await Restaurant.objects.filter(company_id=company_id).afirst()
                if not restaurant:
                    return False
                has_branch = await restaurant.branches.aexists()
                return has_branch
            return True   
        
        return True


class BranchPermission(BasePermission):
    """
    Ensures that the specified manager for a branch belongs to the correct scope.
    If the requesting user has companies, a company field is required in the request,
    and it must be within the user's companies. 
    """
    async def has_permission(self, request, view):
        if view.action not in ['create', 'update', 'partial_update']:
            return True  # Only apply to these actions
        
        manager_id = request.data.get('manager')
        company_id = request.data.get('company')
        country_id = request.data.get('country')
        scopes_and_groups = await get_scopes_and_groups(request.user)

        # Attach to request for reuse in view
        request.user_scope = scopes_and_groups
        if manager_id:
            await AttributeChecker().check_manager(manager_id, company_id, 'branch')
        if company_id:
            if company_id not in scopes_and_groups['company']:
                raise PermissionDenied(_("You do not have permission to create branches in this company."))
        if country_id and "CountryManager" in scopes_and_groups['groups']:
            if country_id not in scopes_and_groups['country']:
                raise PermissionDenied(_("You do not have permission to create branches in this country."))
        return True


class ObjectStatusPermission(BasePermission):
    """
    Ensures that actions are allowed only on objects with status 'active'.
    Prevents setting objects to 'active' if their direct parent is inactive.
    """
    """
    Ensures that actions are allowed only on objects with status 'active'.
    Prevents setting objects to 'active' if their direct parent is inactive.
    """
    async def check_parent_status(self, obj, field_name, field_value, request=None):
        """
        Check if setting field_name to field_value is allowed based on parent status.
        Blocks setting status='active' if parent is inactive or is_active=False.
        """
        from archive.tasks import revert_deletion_task
        print(f"Checking parent status for {obj.__class__.__name__} {obj.id}, field {field_name}={field_value}")

        # Check if action is trying to set status='active'
        if field_name != 'status' or field_value != 'active':
            return  # Not an activation attempt, no parent check needed

        # Define parent relationships
        parent_field_map = {
            Branch: ('restaurant', Restaurant),
            Restaurant: ('company', Company),
            Company: (None, None)  # No parent
        }
        parent_field, parent_model = parent_field_map.get(obj.__class__, (None, None))

        # Check parent status if applicable
        if parent_field:
            try:
                parent_obj = await sync_to_async(getattr)(obj, parent_field)
                if parent_obj is None:
                    # print(f"Parent {parent_model.__name__} not found for {obj.__class__.__name__} {obj.id}")
                    raise PermissionDenied(_(f"Parent {parent_model.__name__.lower()} does not exist."))
                parent_inactive = (hasattr(parent_obj, 'status') and parent_obj.status == 'inactive') or \
                                 (hasattr(parent_obj, 'is_active') and not parent_obj.is_active)
                if parent_inactive:
                    # print(f"Parent {parent_model.__name__} is inactive for {obj.__class__.__name__} {obj.id}")
                    raise PermissionDenied(_(f"Cannot set {obj.__class__.__name__.lower()} to active: parent {parent_model.__name__.lower()} is inactive."))
            except parent_model.DoesNotExist:
                # print(f"Parent {parent_model.__name__} not found for {obj.__class__.__name__} {obj.id}")
                raise PermissionDenied(_(f"Parent {parent_model.__name__.lower()} does not exist."))
        
        app_label = obj._meta.app_label
        model_name = obj.__class__.__name__
        revert_deletion_task.delay(f'{app_label}.{model_name}', obj.id, request.user.id)

    async def has_object_permission(self, request, view, obj):
        # Check object status
        status = getattr(obj, 'status', None)
        is_active = getattr(obj, 'is_active', None)
        if view.action == 'partial_update':
            if request.data.get('status') == status:
                raise PermissionDenied(_("Already set"))
        if status != 'active':
            if is_active == False:
                raise PermissionDenied(_("Object does not exist"))
            allowed_fields = {'status', 'manager'}
            modified_fields = set(request.data.keys())
            user_groups = await get_scopes_and_groups(request.user)

            if not modified_fields.issubset(allowed_fields):
                raise PermissionDenied(_("Modification status unknown: status | manager"))
            
            # Only CompanyAdmin or RestaurantOwner can modify inactive objects
            if not any(group in ["CompanyAdmin", "RestaurantOwner"] for group in user_groups['groups']):
                # print(f"Non-privileged user {request.user.id} cannot modify inactive {obj.__class__.__name__} {obj.id}")
                raise PermissionDenied(_("This object is inactive and cannot be modified."))
            
            field_map = {'manager': 'manager_id'}  # Field name mappings
            obj_attrs = {
                field_map.get(field, field): getattr(obj, field_map.get(field, field))
                for field in modified_fields
                if field in request.data
            }
            if all(obj_attrs[field] == request.data[field] for field in obj_attrs):
                raise PermissionDenied(_("No changes to modified fields"))
                        
            if 'status' in request.data and request.data['status'] == 'active':
                await self.check_parent_status(obj, 'status', 'active', request)
            
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
        'rule': Rule,
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
        user_ids = data.get('user_ids')
        action = data.get('action')

        model = self.MODEL_MAP.get(object_type)
        if not model or not object_id:
            return False

        # Fetch object and validate existence
        try:
            obj = await model.objects.aget(id=object_id)
        except model.DoesNotExist:
            raise PermissionDenied(_("{object_type} ID {object_id} does not exist").format(object_type=object_type, object_id=object_id))

        # Validate object scope
        if not await self._is_object_in_scope(request, obj, model):
            raise PermissionDenied(_("Object not in your scope"))

        # Handle user assignment
        if user_id:
            try:
                user = await CustomUser.objects.aget(id=user_id)
                if user == request.user:
                    return False
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("User ID {user_id} does not exist").format(user_id=user_id))

            # Check user role for specific fields
            expected_role = self.ROLE_FIELD_MAP.get(object_type, {}).get(field_name)
            if expected_role and not await user.groups.filter(name=expected_role).aexists():
                raise PermissionDenied(_(f"User must be in {expected_role} group for {field_name} assignment"))

            # Validate user scope
            if not await self._is_object_in_scope(request, user, CustomUser):
                raise PermissionDenied(_("Assigned user not in your scope"))

        # Handle bulk user assignment
        if action == "assign_users" and user_ids:
            # Fetch users in bulk and validate existence
            # users = [user.id async for user in CustomUser.objects.filter(id__in=user_ids).values_list('id', flat=True)]
            # users = [user_id async for user_id in CustomUser.objects.filter(id__in=user_ids).values_list('id', flat=True)]
            users = await sync_to_async(list)(CustomUser.objects.filter(id__in=user_ids).values_list('id', flat=True))
            user_ids_found = set(users)
            if len(user_ids_found) != len(user_ids):
                missing_ids = set(user_ids) - user_ids_found
                raise PermissionDenied(_("Users with IDs {missing_ids} do not exist").format(missing_ids=missing_ids))


            # Validate all users are in scope using UserCreationPermission.get_queryset
            permission = UserCreationPermission()
            permission.request = request
            scope_queryset = await permission.get_queryset()
            scoped_user_ids = {u.id async for u in scope_queryset.filter(id__in=user_ids)}
            if not all(uid in scoped_user_ids for uid in user_ids):
                raise PermissionDenied(_("Some users are not in your scope"))

            request.bulk_users = users
           
        return True

    async def _is_object_in_scope(self, request, obj, model):
        """Check if object aligns with requester's scope."""
        requester = request.user
        config = await ScopeAccessPolicy().get_role_config(requester)
        if not config:
            return False

        allowed_scopes = await config["scopes"](requester)
        obj_scope_ids = await self._get_object_scope_ids(obj, model)
        print("allowed_scopes: ", allowed_scopes)
        print("obj_scope_ids: ", obj_scope_ids)

        return obj_scope_ids and any(obj_scope_ids.issubset(allowed_scopes.get(scope, set())) 
                                    for scope in ['companies', 'countries', 'restaurants', 'branches'])

    async def _get_object_scope_ids(self, obj, model):
        """Extract scope-relevant IDs from the object (reused from EntityUpdateViewSet)."""
        if model == CustomUser:
            if await obj.companies.aexists():
                return {company.id async for company in obj.companies.all()}
            elif await obj.restaurants.aexists():
                return {restaurant.id async for restaurant in obj.restaurants.all()}
            elif await obj.branches.aexists():
                return {branch.id async for branch in obj.branches.all()}
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
        elif model == Country:
            if obj.id:
                return {obj.id}
        elif model == Company:
            if obj.id:
                return {obj.id}
        elif model == Menu:
            if obj.branch.restaurant_id:
                return {obj.branch.restaurant_id}
            elif obj.branch.company_id:
                return {obj.branch.company_id}
        elif model == MenuCategory:
            if obj.menu.branch.restaurant_id:
                return {obj.menu.branch.restaurant_id}
            elif obj.menu.branch.company_id:
                return {obj.menu.branch.company_id}
        elif model == Rule:
            if obj.branch_id:
                return {obj.id}
            elif obj.restaurant_id:
                return {obj.restaurant_id}
            elif obj.company_id:
                return {obj.company_id}
        return set()

class     RoleAssignmentPermission(BasePermission):
    async def has_permission(self, request, view):
        data = request.data
        target_user_id = data.get('target_user')
        assignment_type = data.get('type')
        role = data.get('role')

        entity_permission = EntityUpdatePermission()
            
        if view.action == 'send_assignment':
            if not assignment_type or not target_user_id or not role:
                raise PermissionDenied(_("Assignment type/target_user/role required."))
            
            if assignment_type == 'transfer' and not data.get('restaurants'):
                raise PermissionDenied("Restaurant is required for ownership transfer.")
            
            if not validate_role(role):
                return False
            
            if await compare_role_values(request.user, role):
                return False
            
            if target_user_id:
                try:
                    target_user = await CustomUser.objects.aget(id=target_user_id)
                    if not await entity_permission._is_object_in_scope(request, target_user, CustomUser):
                        return False
                except CustomUser.DoesNotExist:
                    return False
            return True

        elif view.action == 'handle_assignment':
            try:
                assignment = await RoleAssignment.objects.select_related('target_user').aget(
                        token=view.kwargs['token'],
                        status='pending'
                    )

                # Check if the requesting user is the target user or matches the target email
                if assignment.target_user:
                    if request.user.id != assignment.target_user.id:
                        return False
                elif assignment.target_email:
                    if request.user.email != assignment.target_email:
                        raise PermissionDenied(_("Sign up with the invited email."))
                else:
                    return False
            except RoleAssignment.DoesNotExist:
                return False
        return True
    
class DeletionPermission(BasePermission):

    async def _set_inactive(self, obj):
        """Set object to inactive based on status or is_active field."""
        # model = obj.__class__
        if hasattr(obj, 'status'):
            obj.status = 'inactive'
        elif hasattr(obj, 'is_active'):
            obj.is_active = False
        await obj.asave()
        # print(f"Set {model.__name__} {obj.id} to inactive")

    async def has_object_permission(self, request, view, obj):
        # print(f"Checking deletion permission for {obj.__class__.__name__} {obj.id}, user {request.user.id}")
        user = request.user
        user_role = getattr(user, 'role', None)
        if not user_role:
            raise False

        # Check if object is in scope
        entity_permission = EntityUpdatePermission()
        model = obj.__class__
        if not await entity_permission._is_object_in_scope(request, obj, model):
            # print(f"{model.__name__} {obj.id} not in scope for user {user.id}")
            raise PermissionDenied(_(f"You can only delete {model.__name__.lower()}s within your scope."))
        
        if view.action == 'destroy':
            # Get all models with ForeignKey to the target model
            dependent_models = []
            for app_model in apps.get_models():
                for field in app_model._meta.get_fields():
                    if isinstance(field, ForeignKey) and field.related_model == model:
                        dependent_models.append((app_model, field.name))
                        # print(f"Found dependent model: {app_model.__name__} via field {field.name}")

            # Check if force=True for privileged 
            PRIVILEGED_MODELS = {
                'company_admin': {'Restaurant', 'Company'},
                'restau_owner': {'Restaurant', 'Branch'}
            }
            force_delete = request.data.get('force', False)
            is_privileged = model.__name__ in PRIVILEGED_MODELS.get(user_role, set())
            
            if is_privileged and force_delete:
                print(f"Privileged user {user.id} with force=true, auto-setting inactive")
                try:
                    for dep_model, fk_field in dependent_models:
                        # Try status field
                        if hasattr(dep_model, 'status'):
                            count = await dep_model.objects.filter(**{fk_field: obj, 'status': 'active'}).aupdate(status='inactive')
                            print(f"Set {count} {dep_model.__name__} objects to inactive")
                        # Try is_active field
                        elif hasattr(dep_model, 'is_active'):
                            count = await dep_model.objects.filter(**{fk_field: obj, 'is_active': True}).aupdate(is_active=False)
                            print(f"Set {count} {dep_model.__name__} objects to is_active=False")
                        # Handle SET_NULL or CASCADE (no status/is_active)
                        # else:
                        #     print(f"No status/is_active for {dep_model.__name__}, relying on on_delete")
                    # Set target object to inactive
                    await self._set_inactive(obj)
                except Exception as e:
                    # print(f"Failed to auto-set inactive: {str(e)}")
                    raise PermissionDenied(_(f"Failed to set dependent objects to inactive: {str(e)}"))
                return True

            # Default: Check if dependent objects are inactive
            try:
                for dep_model, fk_field in dependent_models:
                    if hasattr(dep_model, 'status'):
                        if await dep_model.objects.filter(**{fk_field: obj, 'status': 'active'}).acount() > 0:
                            print(f"Active {dep_model.__name__} objects found")
                            raise PermissionDenied(_(f"All {dep_model.__name__.lower()} objects must be inactive before deletion."))
                    elif hasattr(dep_model, 'is_active'):
                        if await dep_model.objects.filter(**{fk_field: obj, 'is_active': True}).acount() > 0:
                            print(f"Active {dep_model.__name__} objects found")
                            raise PermissionDenied(_(f"All {dep_model.__name__.lower()} objects must be inactive before deletion."))
                    # else:
                    #     print(f"No status/is_active for {dep_model.__name__}, skipping active check")
                await self._set_inactive(obj)
            except Exception as e:
                # print(f"Error checking dependent objects: {str(e)}")
                raise PermissionDenied(_(f"Error checking dependent objects: {str(e)}"))
            
            # Set target object to inactive (common for both cases)

            # Log deletion activity
            model_name = model.__name__.lower()
            is_special_model = model_name in {'branch', 'restaurant'}

            # Build the base arguments that are always needed
            log_args = [
                user.id,
                f'{model_name}_delete',
                f'{{{model.__name__}: {obj.id}, message: {model.__name__} marked inactive}}' 
            ]
            # Conditionally extend with additional arguments
            if is_special_model:
                log_args.extend([obj.id, model_name])
            log_activity.delay(*log_args)

            print(f"Deletion permission granted for {model.__name__} {obj.id}")    
            return True
        return True
    

class ShiftPermission(BasePermission):
    """Permission class for Shift model, checking branch scope."""
    entity_permission = EntityUpdatePermission()

    ROLE_ACTIONS: Dict[str, List[str]] = {
        'RestaurantOwner': ['list', 'retrieve', 'create', 'update', 'partial_update', 'destroy'],
        'RestaurantManager': ['list', 'retrieve', 'create', 'update', 'partial_update'],
        'BranchManager': ['list', 'retrieve', 'update', 'partial_update'],
        'CountryManager': ['list', 'retrieve', 'create', 'update'],
        'CompanyAdmin': ['list', 'retrieve', 'create', 'update', 'partial_update', 'destroy'],
    }

    async def has_permission(self, request, view) -> bool:
        if request.method in ['GET',]:
            return True
        user = request.user
        branch = request.data.get('branches')
        
        end_time = request.data.get('end_time')
        start_time = request.data.get('start_time')
        scopes_and_groups = await get_scopes_and_groups(user)
        user_groups = scopes_and_groups['groups']
        action = view.action
        has_permission = False

        if not branch or not isinstance(branch, list) or len(branch) != 1:
            raise PermissionDenied({
                'branches': _('Branch[] must be specified.')
            })     
           
        branch_id = int(branch[0])
        overlapping_shifts = await Shift.objects.filter(
            branch_id=branch_id,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).aexists()

        if overlapping_shifts:
            raise PermissionDenied({
                'error': _('Shift times overlap with an existing shift.')
            })

        # Check each role the user has against the current action
        for role, allowed_actions in self.ROLE_ACTIONS.items():
            if role in user_groups and action in allowed_actions:
                has_permission = True
                break


        return has_permission

    async def has_object_permission(self, request, view, obj):
        """Check if user can perform retrieve, update, or delete on a shift."""
        return await self.entity_permission._is_object_in_scope(request, obj, Shift)
    
class ShiftPatternPermission(BasePermission):
    async def has_permission(self, request, view):
        if view.action == 'regenerate':
            return await self._regenerate_checks(request, view)
        else:
            return await self._standard_checks(request, view)
    
    async def _standard_checks(self, request, view):
        if request.method == 'GET':
            return True
        data = request.data
        roles = data.get('roles', [])
        branch = data.get('branch')
        user_ids = data.get('users', [])

        out_of_scope = {'invalid_roles': [], 'out_of_scope_users': []}
        entity_permission = EntityUpdatePermission()

        # Validate branch
        try: 
            branch = await Branch.objects.aget(id=branch)
        except Branch.DoesNotExist:
            raise PermissionDenied(_("Branch not found"))
        if not await entity_permission._is_object_in_scope(request, branch, Branch):
            raise PermissionDenied(_("You can only set shift patterns within your branch."))
        
        # Validate roles
        valid_roles = {choice[0] for choice in CustomUser.ROLE_CHOICES}  # e.g., {'cashier', 'restaurant_manager'}
        for role in roles:
            if not validate_role(role) or await compare_role_values(request.user, role):
                out_of_scope['invalid_roles'].append(role)

        if out_of_scope['invalid_roles']:
            raise PermissionDenied(
                _("Invalid roles: %(roles)s") % {'roles': out_of_scope['invalid_roles']}
            )
        
        # Validate users (if provided)
        if user_ids:
            try:
                # Batch query for users
                queryset = CustomUser.objects.filter(id__in=user_ids).prefetch_related('companies', 'countries', 'branches', 'restaurants')
                users = queryset.aiterator() # evaluates the queryset
                # found_user_ids = set()
                
                # # Check for non-existent users
                # missing_users = set(user_ids) - set(found_user_ids)
                # if missing_users:
                #     out_of_scope['out_of_scope_users'].extend(list(missing_users))
                
                # Check scope for existing users
                async for user in users:
                    # print("user in users: ", user, user.branches.all())
                    # found_user_ids.add(user.id)
                    if not await user.branches.filter(id=branch.id).aexists():
                        out_of_scope['out_of_scope_users'].append(user.id)
                    elif not await entity_permission._is_object_in_scope(request, user, CustomUser):
                        out_of_scope['out_of_scope_users'].append(user.id)
            
            except Exception as e:
                raise PermissionDenied(_("Error validating users: %s") % str(e))

        if out_of_scope['out_of_scope_users']:
            raise PermissionDenied(
                _("Out-of-scope users: %(users)s") % {'users': out_of_scope['out_of_scope_users']}
            )
        
        return True
    
    async def _regenerate_checks(self, request, view):
        """Regenerate-specific checks"""
        pk = view.kwargs.get('pk')
        # obj = await ShiftPattern.objects.aget(pk=pk)
        request.pattern_id = view.kwargs.get('pk')

        user = request.user
        user_group = await user.get_group(user.role)
        policy = ScopeAccessPolicy()
        config = policy.SCOPE_CONFIG[user_group]

        model_class = view.queryset.model
        queryset_filter = config['queryset_filter'](user, model_class)
        
        allowed = await model_class.objects.filter(Q(pk=pk) & queryset_filter).aexists()
        # print("model class: ", model_class.__name__)

        if not allowed:
            raise PermissionDenied("You are not authorized to access this shift pattern")
        
        return True
        
    
    async def has_object_permission(self, request, view, obj):
        """Check if user can perform retrieve, update, or delete on a shift."""
        if obj.active_until:
            if obj.active_until <= timezone.now().date():
                raise PermissionDenied(_("Shift pattern is no longer active."))
            
        return True
        
        # return await self.entity_permission._is_object_in_scope(request, obj, Shift)

class StaffShiftPermission(BasePermission):
    async def has_permission(self, request, view):
        if request.method == 'GET':
            return True
        
        from datetime import datetime
        staff_shift_id = view.kwargs.get('pk')
        request.staff_shift_id = staff_shift_id
        entity_permission = EntityUpdatePermission()
        
        data = request.data

        if 'shift_id' not in data or 'date' not in data or 'user_id' not in data:
            # return False
            raise PermissionDenied(_("A required field is missing"))
        
        shift_id = data["shift_id"]
        date = data["date"]
        user_id = data.get("user_id")

        # Fetch existing StaffShift
        try:
            staff_shift = await StaffShift.objects.select_related('user').aget(id=staff_shift_id)
            request.staff_shift = staff_shift
        except StaffShift.DoesNotExist:
            raise PermissionDenied(_("StaffShift not found"))

        # Check if shift_id belongs to the same branch as staff_shift
        cache_key = f"shift_ids:branch_{staff_shift.branch_id}"
        cache = redis.from_url(settings.REDIS_URL)
        valid_shift_ids = await cache.get(cache_key)
        if valid_shift_ids is None:
            queryset = Shift.objects.filter(branch_id=staff_shift.branch_id).values_list('id', flat=True)
            valid_shift_ids = set()
            async for sid in queryset.aiterator():
                valid_shift_ids.add(sid)
            await cache.set(cache_key, json.dumps(list(valid_shift_ids)), ex=3600)
        else:
            # Parse the JSON string back into a Python object
            valid_shift_ids = set(json.loads(valid_shift_ids))
        
        # print("valid_shift_ids: ", valid_shift_ids)
        if shift_id not in valid_shift_ids:
            raise PermissionDenied(_("Shift ID does not belong to the same branch as StaffShift"))
        try:
            date = datetime.strptime(data["date"], "%Y-%m-%d").date()
            # print("UI Date: ",date, date.strftime("%a"))
        except ValueError:
            raise PermissionDenied(_("Invalid date format, expected YYYY-MM-DD"))
        if date <= timezone.now().date():
            raise PermissionDenied(_("Shifts can only be set on a future date"))
        
        request.shift_id = shift_id
        request.date = date
        
        if user_id:
            try:
                target_user = await CustomUser.objects.aget(id=user_id)
                if not await entity_permission._is_object_in_scope(request, target_user, CustomUser):
                    return False
            except CustomUser.DoesNotExist:
                return False
            
            request.user_id = user_id

        return True
    
class OvertimeRequestPermission(BasePermission):
    async def has_permission(self, request, view):
        rv = await request.user.get_role_value()
        print("view.action: ", view.action)
        if rv > 5:
            if view.action not in ['list', 'retrieve','create', 'update', 'partial_update']:
                return False
    
        return True
    
    async def has_object_permission(self, request, view, obj):
        # print("in has_object_permission")
        user = request.user
        policy = StaffAccessPolicy()

        scopes = await get_scopes_and_groups(user)
        branch_ids = scopes.get('branch', set())
        check = policy.OBJECT_CHECKS.get(obj.__class__)
        print("check: ", check)
        if not check:
            return False
        return await check(obj, user, branch_ids)

class MenuPermission(BasePermission):
    async def has_permission(self, request, view):
        if request.method == 'GET':
            return True
        user = request.user
        r_val = await user.get_role_value()
        if r_val > 5:
            return False
        
        return True
        
class MenuCategoryPermission(BasePermission):
    async def has_permission(self, request, view):
        if request.method == 'GET':
            return True
        user = request.user
        data = request.data
        r_val = await user.get_role_value()
        entity_permission = EntityUpdatePermission()
        if r_val > 5:
            return False
        try:
            menu = await Menu.objects.prefetch_related('branch').aget(id=data['menu'])
        except Menu.DoesNotExist:
            raise PermissionDenied(_("Menu not found"))
        except KeyError:
            raise PermissionDenied(_("All required fields not provided"))
        
        if not await entity_permission._is_object_in_scope(request, menu, Menu):
            raise PermissionDenied(_("Menu not within your branch."))
        
        return True
    
class MenuItemPermission(BasePermission):
    async def has_permission(self, request, view):
        if request.method == 'GET':
            return True
        
        user = request.user
        data = request.data
        r_val = await user.get_role_value()
        entity_permission = EntityUpdatePermission()

        if r_val > 5:
            return False
        try:
            menu_cat = await MenuCategory.objects.prefetch_related('menu__branch').aget(id=data['category'])
        except MenuCategory.DoesNotExist:
            raise PermissionDenied(_("Menu category not found"))
        except KeyError:
            raise PermissionDenied(_("All required fields not provided"))
        
        if not await entity_permission._is_object_in_scope(request, menu_cat, MenuCategory):
            raise PermissionDenied(_("Menu category not within your branch."))
        
        return True
    
class OrderPermission(BasePermission):
    async def has_permission(self, request, view):
        if request.method == 'GET':
            return True
        
        user = request.user
        r_val = await user.get_role_value()

        if r_val < 1 or (r_val > 5 and r_val not in [6, 9]):
            return False
        
        
        return True
    
    async def has_object_permission(self, request, view, obj):
        # print("in has_object_permission")
        user = request.user
        policy = StaffAccessPolicy()

        scopes = await get_scopes_and_groups(user)
        branch_ids = scopes.get('branch', set())
        check = policy.OBJECT_CHECKS.get(obj.__class__)
        print("check: ", check)
        if not check:
            return False
        return await check(obj, user, branch_ids)
    
class ShiftSwapPermission(BasePermission):
    async def has_permission(self, request, view):
        if request.method == 'GET':
            return True
        user = request.data
        pk = view.kwargs.get('pk')
        data = request.data
        desired_date = data.get('desired_date')
        initiator_shift = data.get("initiator_shift")
        _scopes = await get_scopes_and_groups(user)
        if view.action in ['create', 'update']:
            try:
                initiator = await StaffShift.objects.aget(
                    id=initiator_shift,
                    user_id=user.id,  # Critical ownership check
                    # is_swappable=True
                )
            except StaffShift.DoesNotExist:
                raise ValidationError("Invalid or non-swappable shift selected")
        
        if view.action == 'accept':
            try:
                shift_swap = await ShiftSwapRequest.objects.select_related('initiator').aget(id=pk, status='pending')
                desired_date = shift_swap.desired_date
                if shift_swap.initiator.role != user.role:
                    raise ValidationError(_("Invalid department"))
                counterparty = await StaffShift.objects.aget(
                    date=desired_date,
                    user_id=user.id,
                    # is_swappable=True,
                    branch_id__in=_scopes['branch'] # Same branch
                )
                
                request.shift_swap = shift_swap
                request.counterparty_shift = counterparty.id
            except StaffShift.DoesNotExist:
                raise ValidationError(_("Invalid counterparty shift"))
        