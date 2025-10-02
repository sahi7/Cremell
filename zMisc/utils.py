import pytz
import json
import logging
from typing import Any
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from redis.asyncio import Redis
from asgiref.sync import async_to_sync
from typing import List, Dict, Optional
from django.utils.translation import gettext as _
from django.db.models import Q
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone, translation
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from notifications.models import BranchActivity, RestaurantActivity
from cre.models import Branch, Restaurant, Company, Country, ShiftSwapRequest
from payroll.models import *
from printing.models import Device, Printer

logger = logging.getLogger('web')
CustomUser = get_user_model()

def validate_scope(user, data, allowed_scopes):
    """
    Validates if the request data falls within the allowed scope of the user.
    
    Args:
        user (CustomUser): The request user.
        data (dict): The data being validated (e.g., request.data).
        allowed_scopes (dict): A dictionary defining allowed fields and their values.
        
    Returns:
        None: If validation passes.
        
    Raises:
        serializers.ValidationError: If validation fails.
    """
    for field, scope in allowed_scopes.items():
        if not field in data:
            raise PermissionDenied(_("{} required.").format(field))
        if data[field] not in scope:
            raise ValidationError({
                field: _("You cannot create objects outside your assigned {}.").format(field)
            })


# async def determine_activity_model(user):
def determine_activity_model(user, obj_type):
    """
    Determines the appropriate activity model based on user's role value.
    Returns a tuple: (Model, scope_field).
    Raises PermissionDenied if no valid scope is found.
    """
    # Check user role and get numeric value
    role_value = async_to_sync(user.get_role_value)()
    target_mapping = {
        'branch': (BranchActivity, 'branch'),
        'restaurant': (RestaurantActivity, 'restaurant'),
    }

    # Determine model and scope based on role value threshold
    if obj_type is None:
        if role_value >= 5:  # Branch-level roles
            return target_mapping['branch']
        elif 1 <= role_value <= 4:  # Company/Restaurant-level roles
            return target_mapping['restaurant']
    if obj_type in target_mapping:
        if role_value >= 1:  # All roles can act on branch/restaurant if authorized elsewhere
            return target_mapping[obj_type]
        raise PermissionDenied(_("Insufficient role for this action"))
    
    raise PermissionDenied(_("User has no valid scope for activity logging"))


def validate_role(role_to_create):
    """
    Validate if the role to create is in the available roles.

    Args:
        role_to_create: The role to create.
        available_roles: A set of available roles.

    Returns:
        bool: True if the role is valid, False otherwise.
    """
    available_roles = {role for role, _ in CustomUser.ROLE_CHOICES}
    return role_to_create in available_roles

def  send_del_notification(
        model_name: str,
        obj: Any,
        message: str,
        subject: str,
        extra_context: Dict[str, Any],
        template_name: str = "emails/object_deleted.html",
        max_role_value: int = 5,
        include_lower_roles: bool = False
    ) -> None:
    """
    Send deletion notifications to stakeholders for a given object.
    
    Args:
        model_name: The name of the model (e.g., 'Branch', 'Restaurant', 'Company', 'Country').
        obj: The model instance being deleted.
        message: The notification message.
        subject: The email subject.
        extra_context: Additional context for the email template.
        template_name: The template to use for rendering the email.
        max_role_value: Maximum role value for stakeholder filtering.
        include_lower_roles: Whether to include roles with higher numeric values.
    """
    from notifications.tasks import send_batch_notifications
    # Determine scope for stakeholders
    company_id = None
    restaurant_id = None
    branch_id = None
    country_id = None
    object_id = obj.id

    if model_name == 'Branch':
        branch_id = object_id
        restaurant_id = getattr(obj, 'restaurant_id', None)
        country_id = getattr(obj, 'country_id', None)
        # Handle both company-owned and standalone restaurants
        company_id = getattr(obj.restaurant, 'company_id', None) if hasattr(obj, 'restaurant') and obj.restaurant else None
    elif model_name == 'Restaurant':
        restaurant_id = object_id
        country_id = getattr(obj, 'country_id', None)
        company_id = getattr(obj, 'company_id', None)
    elif model_name == 'Company':
        company_id = object_id
    elif model_name == 'Country':
        country_id = object_id
    else:
        raise ValueError(f"Unsupported model_name: {model_name}")

    # Trigger notification task
    send_batch_notifications.delay(
        company_id=company_id,
        restaurant_id=restaurant_id,
        branch_id=branch_id,
        country_id=country_id,
        message=message,
        subject=subject,
        extra_context=extra_context,
        template_name=template_name,
        # max_role_value=max_role_value,
        # include_lower_roles=include_lower_roles
    )

def clean_request_data(request_data, fields_to_remove=None):
    """
    Removes specified fields and their _id variants from request data.
    Default removes branch, restaurant, company and their _id variants.
    """
    default_fields = {'branch', 'restaurant', 'company', 'countries', 'branches','restaurants', 'companies'}
    fields = fields_to_remove or default_fields
    
    # Generate all field variants to remove
    variants = set(fields)
    for field in fields:
        variants.update({f"{field}_id", f"{field}Id"})
    
    # Return cleaned dict (preserves original request.data)
    return {
        k: v for k, v in request_data.items()
        if k not in variants
    }

async def compare_role_values(user, role_to_create):
    """
    Compare the role value of the user with the role value to create.

    Args:
        user: The user object.
        role_to_create: The role to create.

    Returns:
        bool: True if the role to create has a lower or equal value than the user's role, False otherwise.
    """
    user_role_value = await user.get_role_value()
    # print("user_role_value: ", user_role_value)
    role_to_create_value = await user.get_role_value(role_to_create)
    # print("calc: ", role_to_create_value, " <= ", user_role_value)
    result = role_to_create_value <= user_role_value
    # print("result: ", result)
    return result

import time
import asyncio
from django.db.models import Prefetch
async def get_scopes_and_groups(user, requires=None, get_instance=False):
    # # Prefetch companies, countries, and groups in one query
    # if isinstance(user, (int, str)):
    #     user_id = user
    # else:
    #     user_id = user.id
    # Determine user_id
    start = time.perf_counter()
    user_id = user.id if not isinstance(user, (int, str)) else user
    try:
        # Construct cache keys
        role_cache_key = f"scopes:{user.role}:{user_id}"
    except AttributeError:
        logger.info(f"AttributeError on user {user_id}: Fetching user instance")
        user = await CustomUser.objects.prefetch_related(
                'companies', 'countries', 'branches', 'restaurants'
            ).aget(id=user)
        if get_instance:
            return user
        role_cache_key = f"scopes:{user.role}:{user_id}"
    
    user_cache_key = f"user_scopes:{user_id}" 
    cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    if not get_instance:
        cached_data = await cache.mget(role_cache_key, user_cache_key)
        print(f"scopes cache section took {(time.perf_counter() - start) * 1000:.3f} ms")
        if cached_data[0]:  # Role cache hit
            # return {k: set(v) for k,v in json.loads(cached).items()}
            return json.loads(cached_data[0])
        if cached_data[1]:  # User cache hit
            return json.loads(cached_data[1])
    # user = await CustomUser.objects.prefetch_related('companies', 'countries', 'branches', 'restaurants', 'groups').aget(id=user_id)
    # if get_instance:
    #     return user
    
    # result = {
    #     'company': [c.id async for c in user.companies.all()], 
    #     'country': [c.id async for c in user.countries.all()],
    #     'restaurant': [c.id async for c in user.restaurants.all()],
    #     'branch': [c.id async for c in user.branches.all()],
    #     'groups': [g.name async for g in user.groups.all()],
    #     'role': user.role
    # }
    #########################

    # Define all possible Prefetch objects
    prefetch_configs = {
        'companies': Prefetch('companies', queryset=Company.objects.filter(status='active').only('id')),
        'countries': Prefetch('countries', queryset=Country.objects.only('id')),
        'restaurants': Prefetch('restaurants', queryset=Restaurant.objects.filter(status='active').only('id')),
        'branches': Prefetch('branches', queryset=Branch.objects.filter(status='active').only('id'))  
    }

    # Determine which relations to fetch
    relations_to_fetch = requires if requires is not None else prefetch_configs.keys()
    prefetch_objects = [prefetch_configs[field] for field in relations_to_fetch if field in prefetch_configs]

    # Fetch user with only required relations
    user = await CustomUser.objects.only('role', 'r_val').prefetch_related(*prefetch_objects).aget(id=user_id)

    # Async function to extract field or instance
    async def extract_field(manager, field='id'):
        if get_instance:
            return [obj async for obj in manager.all()]
        return [getattr(obj, field) async for obj in manager.all()]

    # Prepare extraction tasks based on requires
    tasks = []
    field_mappings = {
        'companies': ('companies', 'id'),
        'countries': ('countries', 'id'),
        'restaurants': ('restaurants', 'id'),
        'branches': ('branches', 'id'),
        'groups': ('groups', 'name'),
        'role': user.role
    }

    for field in relations_to_fetch:
        if field in field_mappings:
            manager_name, extract_field_name = field_mappings[field]
            tasks.append(extract_field(getattr(user, manager_name), extract_field_name))

    # Run extractions concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Build result dictionary
    result = {field: results[i] for i, field in enumerate(relations_to_fetch) if field in field_mappings}
    # r_val = await user.get_role_value()
    if not user.r_val > 5:
        result['groups'] = [g.name async for g in user.groups.all()]
    print(f"scopes section took {(time.perf_counter() - start) * 1000:.3f} ms")
    # await cache.set(cache_key, json.dumps({k: list(v) for k,v in result.items()}), ex=3600)
    # filtered_result = {
    #     k: v for k, v in result.items() 
    #     if v or isinstance(v, (int, float, bool))  # Keep non-empty or non-sequential types
    # }

    # Cache filtered data
    print("filtered_result: ", result)
    await cache.set(user_cache_key, json.dumps(result), ex=3600)
    return result

# async def get_scopes_and_groups(user_id, get_instance=False, prefetch: list[str] | str = 'all'):
#     """
#     Efficiently fetch user scopes (IDs) with minimal DB queries.
#     Args:
#         user_id: ID of the CustomUser.
#         get_instance: If True, return user instance instead of scopes.
#         prefetch: Either:
#             - 'all' (prefetch all relations)
#             - List of specific relations to prefetch
#     Returns:
#         Dict of sets with IDs for requested relations, or user instance if get_instance=True.
#     """
#     all_fields = ['companies', 'countries', 'branches', 'restaurants', 'groups']
#     to_prefetch = all_fields if prefetch == 'all' else [field for field in (prefetch if isinstance(prefetch, list) else [prefetch]) if field in all_fields]
#     cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
#     cache_key = f"user_scopes:{user_id}:{'-'.join(sorted(to_prefetch))}"
    
#     # Try cache if not getting full instance
#     if not get_instance:
#         cached = await cache.get(cache_key)
#         if cached:
#             return {k: set(v) for k,v in json.loads(cached).items()}

#     user = await CustomUser.objects.prefetch_related(*to_prefetch).aget(id=user_id)
#     if get_instance:
#         return user

#     result = {}
#     if 'companies' in to_prefetch:
#         result['company'] = [c.id async for c in user.companies.all()]
#     if 'countries' in to_prefetch:
#         result['country'] = [c.id async for c in user.countries.all()]
#     if 'restaurants' in to_prefetch:
#         result['restaurant'] = [c.id async for c in user.restaurants.all()]
#     if 'branches' in to_prefetch:
#         result['branch'] = [c.id async for c in user.branches.all()]
#     if 'groups' in to_prefetch:
#         result['groups'] = {g.name async for g in user.groups.all()}

#     await cache.set(cache_key, json.dumps({k: list(v) for k,v in result.items()}), ex=600)

#     return result

async def get_user_data(
    user_id: int,
    branch_id: int = None,
    company_id: int = None,
    restaurant_id: int = None,
    country_id: int = None,
    include_related_data: bool = False,
) -> dict:
    """Fetch comprehensive user data with prefetching and caching, supporting all roles."""
    redis_client = Redis.from_url('redis://localhost:6379')
    cache_key = f'user_data_{user_id}_{branch_id or "none"}_{company_id or "none"}_{restaurant_id or "none"}'
    data = await redis_client.get(cache_key)
    if data:
        return json.loads(data.decode() if isinstance(data, bytes) else data)

    # Prefetch all related objects to minimize database hits
    user = await get_scopes_and_groups(user_id, get_instance=True)
    specific_branch = None
    specific_company = None
    specific_restaurant = None

    # Specific branch, company, or restaurant for context (optional)
    if branch_id:
        specific_branch = await Branch.objects.aget(id=branch_id) if branch_id else await user.get_associated_branch()
    if company_id:
        specific_company = await user.companies.aget(id=company_id) if company_id else []
    if restaurant_id:
        specific_restaurant = await user.restaurants.aget(id=restaurant_id) if restaurant_id else []
    timezones = await CustomUser.get_timezone_language(user_id) 

    # Collect IDs and names for all associated objects
    data = {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role,
        'role_value': await user.get_role_value(),
        'timezone': timezones[user.id]['timezone'],
        'language': timezones[user.id]['language'],
        # Contextual data for specific scope
        'organization_name': (
            specific_company.name if specific_company else 
            specific_restaurant.name if specific_restaurant else
            specific_branch.name if specific_branch else 'Unknown'
        ),
        'branch_name': specific_branch.name if specific_branch else None,
        'restaurant_name': specific_restaurant.name if specific_restaurant else None,
    }

    # Include related data only if requested
    if include_related_data:
        _scopes = await get_scopes_and_groups(user)
        data.update(**_scopes)
        # data.update({
        #     'companies': [{'id': c.id, 'name': c.name} async for c in user.companies.all()] if company_id else [],
        #     'countries': [{'id': c.id, 'name': c.name, 'code': c.code} async for c in user.countries.all()] if country_id else [],
        #     'restaurants': [{'id': r.id, 'name': r.name} async for r in user.restaurants.all()] if restaurant_id else [],
        #     'branches': [{'id': b.id, 'name': b.name} async for b in user.branches.all()] if branch_id else [],
        #     'groups': {g.name async for g in user.groups.all()},
        # })

    await redis_client.set(cache_key, json.dumps(data), ex=3600)  # Cache for 1 hour
    return data

async def render_notification_template(user_data: dict, message: str, template_name: str, extra_context: dict = None) -> str:
    """Render notification template with user’s language and timezone."""
    user_tz = pytz.timezone(user_data['timezone'])
    localized_timestamp = timezone.localtime(timezone.now(), user_tz)
    # localized_timestamp = timezone.localtime(timezone.now(), user_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
    # print("user_tz: ", user_tz)
    # print("localized_timestamp: ", localized_timestamp)
    
    # Base required context
    context = {
        'username': user_data.get('username', ''),
        'role': user_data.get('role', ''),
        'message': message,
        'localized_timestamp': localized_timestamp,
    }
    
    # Dynamic organization context (only include available fields)
    org_fields = {
        'organization_name': user_data.get('organization_name'),
        'branch_name': user_data.get('branch_name'),
        'restaurant_name': user_data.get('restaurant_name'),
        'company_name': user_data.get('company_name'),
        'country_name': user_data.get('country_name')
    }
    context.update({k: v for k, v in org_fields.items() if v is not None})
    
    # Optional extra context
    if extra_context:
        context.update(extra_context)

    with translation.override(user_data['language']):
        return render_to_string(template_name, context)
    
async def get_stakeholders(
        company_id: Optional[int] = None,
        restaurant_id: Optional[int] = None,
        branch_id: Optional[int] = None,
        country_id: Optional[int] = None,
        max_role_value: int = 5,  # Default: managers/admins (company_admin to branch_manager)
        include_lower_roles: bool = False,
        limit: int = 1000,
        offset: int = 0,
        include_related_data: bool = False
) -> List[Dict]:
    print(f'stakeholders_{company_id or "none"}_{restaurant_id or "none"}_{branch_id or "none"}_{country_id or "none"}_{max_role_value}_{include_lower_roles}')
    """
    Fetch stakeholders for specified objects with role filtering, minimizing database hits.
    
    Args:
        company_id: Filter users associated with this company.
        restaurant_id: Filter users associated with this restaurant.
        branch_id: Filter users associated with this branch.
        country_id: Filter users associated with this country.
        max_role_value: Maximum role value (e.g., 5 for branch_manager).
        include_lower_roles: If True, include roles with higher numeric values (lower hierarchy).
    
    Returns:
        List of dictionaries with user data (id, username, email, role, etc.).

    Note:
        Multiple scope filters (e.g., branch_id and restaurant_id) use AND logic:
        - Users must be associated with *both* the specified branch AND restaurant.
        - To get all users for a restaurant (including all its branches), specify only restaurant_id.

    Usage Examples:
    # Get all managers for a company
    managers = await get_stakeholders(company_id=1, max_role_value=5)

    # Get all users (including lower roles) for a branch
    all_users = await get_stakeholders(branch_id=10, max_role_value=12, include_lower_roles=True)

    # Get country managers for a country
    country_managers = await get_stakeholders(country_id=5, max_role_value=3)
    """
    # redis_client = Redis.from_url('redis://localhost:6379')
    # cache_key = f'stakeholders_{company_id or "none"}_{restaurant_id or "none"}_{branch_id or "none"}_{country_id or "none"}_{max_role_value}_{include_lower_roles}_{limit}_{offset}'
    # pipeline = redis_client.pipeline()
    # pipeline.get(cache_key)
    # cached_data = await pipeline.execute()
    # if cached_data[0]:
    #     return json.loads(cached_data[0].decode() if isinstance(cached_data[0], bytes) else cached_data[0])

    # Precompute valid roles
    # Build querysets for each scope
    querysets = []
    # Fetch scope objects for organization_name
    branch = await Branch.objects.aget(id=branch_id) if branch_id else None
    restaurant = await Restaurant.objects.aget(id=restaurant_id) if restaurant_id else None
    company = await Company.objects.aget(id=company_id) if company_id else None
    country = await Country.objects.aget(id=country_id) if country_id else None
    chunk_size: int = 1000
    if branch_id:
        querysets.append(Branch.objects.filter(id=branch_id).values('employees__id', 'employees__username', 'employees__email', 'employees__role').exclude(employees__id__isnull=True))
    if restaurant_id:
        querysets.append(Restaurant.objects.filter(id=restaurant_id).values('employees__id', 'employees__username', 'employees__email', 'employees__role').exclude(employees__id__isnull=True))
    if company_id:
        querysets.append(Company.objects.filter(id=company_id).values('users__id', 'users__username', 'users__email', 'users__role').exclude(users__id__isnull=True))
    if country_id:
        querysets.append(Country.objects.filter(id=country_id).values('users__id', 'users__username', 'users__email', 'users__role').exclude(users__id__isnull=True))

    # Combine querysets with union
    queryset = querysets[0]
    for qs in querysets[1:]:
        queryset = queryset.union(qs, all=False)

    # Paginate
    queryset = queryset[offset:offset + limit]

    # Fetch users in batches
    stakeholders = []
    count = await queryset.acount()

    # Pre-fetch related objects in bulk
    user_ids = []
    async for user_data in queryset:
        user_id = user_data['employees__id'] if branch_id or restaurant_id else user_data['users__id']
        if user_id:  # Skip None values
            user_ids.append(user_id)

    # Bulk fetch users for further processing
    users = {u.id: u async for u in CustomUser.objects.filter(id__in=user_ids).only('id', 'username', 'email', 'role', 'r_val')}
    timezones = await CustomUser.get_timezone_language(user_ids) 
    # print(f"In get stake - users: {users}")
    # Process users in chunks
    for start in range(0, count, chunk_size):
        async for user_data in queryset.aiterator(chunk_size=chunk_size):
            user_id = user_data['employees__id'] if branch_id or restaurant_id else user_data['users__id']
            if not user_id:
                continue
            user = users.get(user_id)
            if not user:
                continue
            # role_value = await user.get_role_value()
            role_value = user.r_val
            if role_value > max_role_value and not include_lower_roles:
                continue

            # Validate role-specific assignments
            valid_user = False
            if user.role == 'branch_manager' and branch_id and branch:
                # Check if user is the branch manager
                valid_user = branch.manager_id == user.id
            elif user.role == 'restaurant_manager' and restaurant_id and restaurant:
                # Check if user is the restaurant manager
                valid_user = restaurant.manager_id == user.id
            elif user.role == 'country_manager' and country_id and company_id:
                # Check if user is associated with both country and company
                valid_user = await user.countries.filter(id=country_id).aexists() and await user.companies.filter(id=company_id).aexists()
            elif user.role == 'restaurant_owner' and restaurant_id and restaurant:
                valid_user = restaurant.created_by_id == user.id or await user.restaurants.filter(id=restaurant_id).aexists()
            elif user.role == 'company_admin' and company_id:
                # Check if user is associated with the company
                valid_user = await user.companies.filter(id=company_id).aexists()
            elif role_value > 5 and branch_id:
                valid_user = await user.branches.filter(id=branch_id).aexists()
            else:
                # Fallback: allow user if no specific validation applies
                valid_user = True

            if not valid_user:
                continue

            # Determine organization_name using provided scope objects
            organization_name = 'Unknown'
            if company_id and company:
                organization_name = company.name
            elif restaurant_id and restaurant:
                organization_name = restaurant.name
            elif branch_id and branch:
                organization_name = branch.name
            else:
                # Fallback for users with other associations
                branch = await user.get_associated_branch()
                if branch:
                    organization_name = branch.name
                    branch_name = branch.name
                else:
                    restaurant_fallback = await user.restaurants.afirst()
                    if restaurant_fallback:
                        organization_name = restaurant_fallback.name
                    else:
                        company_fallback = await user.companies.afirst()
                        if company_fallback:
                            organization_name = company_fallback.name

            stakeholder = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'role': user.role,
                'role_value': role_value,
                'timezone': timezones[user.id]['timezone'],
                'language': timezones[user.id]['language'],
                'organization_name': organization_name,
                'restaurant_name': restaurant.name if restaurant else None,
                'country_name': country.name if country else None,
                'branch_name': branch.name if branch else None
            }

            # Include related data only if requested
            if include_related_data:
                _scopes = await get_scopes_and_groups(user)
                stakeholder.update(**_scopes)
                # stakeholder.update({
                #     'companies': [{'id': c.id, 'name': c.name} async for c in user.companies.all()] if company_id else [],
                #     'countries': [{'id': c.id, 'name': c.name, 'code': c.code} async for c in user.countries.all()] if country_id else [],
                #     'restaurants': [{'id': r.id, 'name': r.name} async for r in user.restaurants.all()] if restaurant_id else [],
                #     'branches': [{'id': b.id, 'name': b.name} async for b in user.branches.all()] if branch_id else [],
                # })

            stakeholders.append(stakeholder)   

    # Cache result
    # await redis_client.set(cache_key, json.dumps(stakeholders), ex=3600)
    return stakeholders


async def validate_order_role(user, task_type):
    role_mapping = {
        'prepare': ['cook', 'head_cook', 'sous_chef'],
        'serve': ['food_runner', 'server', 'waiter'],
        'payment': ['cashier', 'branch_manager']
    }
    valid_roles = role_mapping.get(task_type, [])
    return user.role in valid_roles

async def get_branch_device(branch_id, user_id=None):
    """
    Get device for order with priority:
    1. First find device assigned to the current user
    2. Then find default device
    3. Finally take any available device
    """
    def get_deviceID(devices):
        default_device = None
        first_device = devices[0]
        for device in devices:
            if device.get('user_id') == user_id:
                return device['device_id']  # Return immediately if user device found
            
            if device.get('is_default') and not default_device:
                default_device = device
            
            # Keep track of first device as fallback
        
        if default_device:
            return default_device['device_id']
        return first_device['device_id']
    
    # Get all devices for the branch with only needed fields
    cache_key = f"devices:{branch_id}"
    cached_devices = await redis_client.get(cache_key)
    if cached_devices:
        return get_deviceID(json.loads(cached_devices))
    devices = await sync_to_async(list)(
        Device.objects.only("device_id", "is_default", "user_id")
        .filter(branch_id=branch_id)
        .order_by('id')
    )
    print("full devices: ", devices)
    
    if devices:
        devices_data = [{
            'device_id': device.device_id,
            'is_default': device.is_default,
            'user_id': device.user_id,
        } for device in devices]
    
        await redis_client.set(cache_key, json.dumps(devices_data), ex=3600)  # Cache for 1 hour
        return get_deviceID(devices_data)


class AttributeChecker:
    async def check_manager(self, manager_id, company_id=None, manager_type='restaurant'):
        """
        Validates that a user has the specified manager role and belongs to the correct company.
        Args:
            manager_id: ID of the user to check.
            company_id: Optional company ID to validate against
        """
        try:
            manager = await get_scopes_and_groups(manager_id)
            manager_group = manager['groups']
        except CustomUser.DoesNotExist:
            raise PermissionDenied(_("The specified manager does not exist."))

        # Check if the manager belongs to the appropriate group
        expected_group = "RestaurantManager" if manager_type == 'restaurant' else "BranchManager"
        if expected_group not in manager_group:
            raise PermissionDenied(_(f"The manager must be a {manager_type} manager."))

        # If company_id is provided, validate the manager belongs to the company
        if company_id:
            if company_id not in manager['company']:
                raise PermissionDenied(_("The manager must belong to your company."))

        # For standalone restaurants
        else:
            if await manager.companies.aexists():
                raise PermissionDenied(_("The manager cannot belong to any company."))

from cre.models import Order, OvertimeRequest, ShiftPattern, StaffShift, Shift
from notifications.models import Task, EmployeeTransfer          
class LowRoleQsFilter:
    @staticmethod
    async def cook_order_filter(user, branch_ids):
        """Filter orders for cooks based on claimed prepare tasks."""
        task_order_ids = [
            order_id async for order_id in 
            Task.objects.filter(
                status__in=['completed', 'claimed'],
                claimed_by=user,
            ).values_list('order_id', flat=True)
        ]
        return (Q(id__in=task_order_ids) | Q(created_by_id=user.id)) & Q(branch_id__in=branch_ids)

    @staticmethod
    async def shift_leader_order_filter(user, branch_ids):
        """Filter orders for shift leaders in their branches."""
        return Q(branch_id__in=branch_ids)

    @staticmethod
    async def food_runner_order_filter(user, branch_ids):
        """Filter orders for food runners based on orders they created."""
        return Q(created_by=user, branch_id__in=branch_ids)

    @staticmethod
    async def overtime_request_filter(user, branch_ids):
        """Filter overtime requests for user's shifts in branches."""
        return Q(staff_shift__user=user, staff_shift__branch_id__in=branch_ids)

    @staticmethod
    async def staff_shift_filter(user, branch_ids):
        """Filter staff shifts for user in branches."""
        return Q(user=user, branch_id__in=branch_ids)

    @staticmethod
    async def shift_pattern_filter(user, branch_ids):
        """Filter shift patterns in branches."""
        return Q(branch_id__in=branch_ids)

    @staticmethod
    async def shift_filter(user, branch_ids):
        """Filter shifts in branches."""
        return Q(branch_id__in=branch_ids)

    @staticmethod
    async def branch_filter(user, branch_ids):
        """Filter branches by IDs."""
        return Q(id__in=branch_ids)

    @staticmethod
    async def restaurant_filter(user, branch_ids):
        """Filter restaurants by branch IDs."""
        return Q(branches__id__in=branch_ids)

    @staticmethod
    async def employee_transfer_filter(user, branch_ids):
        """Filter employee transfers by branch or manager."""
        return Q(from_branch_id__in=branch_ids) | Q(manager=user)

    @staticmethod
    async def custom_user_filter(user, branch_ids):
        """Filter users by branch membership."""
        return Q(branches__id__in=branch_ids)
    
    @staticmethod
    async def shift_swap_filter(user, branch_ids):
        """Filter users by branch membership and role."""
        # return Q(initiator=user, branch_id__in=branch_ids) | Q(counterparty=user, branch_id__in=branch_ids)
        return Q(branch_id__in=branch_ids, initiator__role=user.role) & (Q(initiator=user) | Q(counterparty=user) | Q(status='pending'))

    @staticmethod
    async def default_device_filter(user, branch_ids):
        return Q(user=user, branch_id__in=branch_ids)
    
    @staticmethod
    async def default_record_filter(user, branch_ids):
        return Q(user_id=user.id, branch_id__in=branch_ids)

    @staticmethod
    async def default_empty_filter(user, branch_ids):
        """Default empty filter for invalid models or roles."""
        return Q(pk__in=[])

    FILTER_TEMPLATES = {
        Record: {
            'default': default_record_filter
        },
        Device: {
            'default': default_device_filter
        },
        ShiftSwapRequest: {
            'default': shift_swap_filter
        },
        Order: {
            'cook': cook_order_filter,
            'shift_leader': shift_leader_order_filter,
            'food_runner': food_runner_order_filter,
            'default': default_empty_filter  # Food runners use same filter as default
        },
        OvertimeRequest: {
            'default': overtime_request_filter
        },
        StaffShift: {
            'default': staff_shift_filter
        },
        ShiftPattern: {
            'default': shift_pattern_filter
        },
        Shift: {
            'default': shift_filter
        },
        Branch: {
            'default': branch_filter
        },
        Restaurant: {
            'default': restaurant_filter
        },
        EmployeeTransfer: {
            'default': employee_transfer_filter
        },
        CustomUser: {
            'default': custom_user_filter
        }
    }


from cre.models import OrderItem, MenuItem, MenuCategory, Menu
class HighRoleQsFilter:

    @staticmethod
    async def ca_scopes(user):
        """Compute scopes for CompanyAdmin."""
        # Check Redis cache for scopes
        cache_key = f"scopes:{user.role}:{user.id}"
        client = Redis.from_url(settings.REDIS_URL)
        cached_scopes = await client.get(cache_key)
        if cached_scopes:
            await client.close()
            return json.loads(cached_scopes)
        
        # Get company IDs (async list comprehension)
        company_ids = [
            id async for id in 
            user.companies.filter(is_active=True).values_list('id', flat=True)
        ]
        companies_set = set(company_ids)
        
        # Get country IDs (async list comprehension)
        country_ids = [
            id async for id in
            user.countries.values_list('id', flat=True)
        ]
        
        # Get restaurant IDs (async list comprehension)
        restaurant_ids = [
            id async for id in
            Restaurant.objects.filter(
                company_id__in=companies_set,
                status='active'
            ).values_list('id', flat=True)
        ]
        
        # Get branch IDs (async list comprehension)
        branch_ids = [
            id async for id in
            Branch.objects.filter(
                company_id__in=companies_set,
                is_active=True
            ).values_list('id', flat=True)
        ]

        groups = [g.name async for g in user.groups.all()]
        
        scopes = {
            'companies': companies_set,
            'countries': set(country_ids),
            'restaurants': set(restaurant_ids),
            'branches': set(branch_ids),
            'groups': set(groups)
        }
        await client.set(cache_key, json.dumps(scopes, default=list), ex=3600)  # Cache for 1 hour
        await client.close()
        return scopes

    @staticmethod
    async def ca_queryset_filter(user, model, scopes):
        """Filter querysets for CompanyAdmin."""
        companies = scopes['companies']
        return {
            Override: Q(rule__company__in=companies),
            Device: Q(branch__company__in=companies),
            ShiftSwapRequest: Q(branch__company__in=companies),
            Rule: Q(company_id__in=companies, is_active=True),
            OrderItem: Q(order__branch__company__in=companies),
            Order: Q(branch__company__in=companies),
            MenuItem: Q(category__menu__branch__company__in=companies),
            MenuCategory: Q(menu__branch__company__in=companies),
            Menu: Q(branch__company__in=companies),
            OvertimeRequest: Q(staff_shift__branch__company__in=companies),
            StaffShift: Q(branch__company__in=companies),
            ShiftPattern: Q(branch__company__in=companies),
            Shift: Q(branch__restaurant__company__in=companies),
            Branch: Q(company__in=companies, is_active=True),
            Restaurant: Q(company__in=companies, status='active'),
            EmployeeTransfer: Q(from_branch__restaurant__company__in=companies) | Q(from_restaurant__company__in=companies),
            CustomUser: Q(companies__in=companies)
        }.get(model, Q(pk__in=[]))

    @staticmethod
    async def cm_scopes(user):
        """Compute scopes for CountryManager."""
        # Check Redis cache for scopes
        cache_key = f"scopes:{user.role}:{user.id}"
        client = Redis.from_url(settings.REDIS_URL)
        cached_scopes = await client.get(cache_key)
        if cached_scopes:
            await client.close()
            return json.loads(cached_scopes)
        
        # Get company IDs
        company_ids = [
            id async for id in 
            user.companies.filter(is_active=True).values_list('id', flat=True)
        ]
        companies_set = set(company_ids)
        
        # Get country IDs
        country_ids = [
            id async for id in
            user.countries.values_list('id', flat=True)
        ]
        countries_set = set(country_ids)
        
        # Get restaurant IDs
        restaurant_ids = [
            id async for id in
            Restaurant.objects.filter(
                company_id__in=companies_set,
                country_id__in=countries_set,
                status='active'
            ).values_list('id', flat=True)
        ]
        
        # Get branch IDs
        branch_ids = [
            id async for id in
            Branch.objects.filter(
                company_id__in=companies_set,
                country_id__in=countries_set,
                is_active=True
            ).values_list('id', flat=True)
        ]

        groups = [g.name async for g in user.groups.all()]
        
        scopes = {
            'companies': companies_set,
            'countries': countries_set,
            'restaurants': set(restaurant_ids),
            'branches': set(branch_ids),
            'groups': set(groups)
        }
        await client.set(cache_key, json.dumps(scopes, default=list), ex=3600)  # Cache for 1 hour
        await client.close()
        return scopes

    @staticmethod
    async def cm_queryset_filter(user, model, scopes):
        """Filter querysets for CountryManager."""
        companies = scopes['companies']
        countries = scopes['countries']
        return {
            Override: Q(rule__company__in=companies),
            Device: Q(branch__company__in=companies) & Q(branch__country__in=countries),
            ShiftSwapRequest: Q(branch__company__in=companies) & Q(branch__country__in=countries),
            Rule: Q(company_id__in=companies, is_active=True) & (Q(branch__country__in=countries, is_active=True) & Q(restaurant__country__in=countries, is_active=True)),
            OrderItem: Q(order__branch__company__in=companies) & Q(order__branch__country__in=countries),
            Order: Q(branch__company__in=companies) & Q(branch__country__in=countries),
            MenuItem: Q(category__menu__branch__company__in=companies) & Q(category__menu__branch__country__in=countries),
            MenuCategory: Q(menu__branch__company__in=companies) & Q(menu__branch__country__in=countries),
            Menu: Q(branch__company__in=companies) & Q(branch__country__in=countries),
            OvertimeRequest: Q(staff_shift__branch__company__in=companies) & Q(staff_shift__branch__country__in=countries),
            StaffShift: Q(branch__company__in=companies) & Q(branch__country__in=countries),
            ShiftPattern: Q(branch__company__in=companies) & Q(branch__country__in=countries),
            Shift: Q(branch__restaurant__company__in=companies) & Q(branch__restaurant__country__in=countries),
            Branch: Q(company_id__in=companies, country_id__in=countries, is_active=True),
            Restaurant: Q(company_id__in=companies, country_id__in=countries, status='active'),
            EmployeeTransfer: Q(from_branch__restaurant__country__in=countries) | Q(from_restaurant__country__in=countries) | Q(to_branch__restaurant__country__in=countries) | Q(to_restaurant__country__in=countries),
            CustomUser: Q(countries__in=countries)
        }.get(model, Q(pk__in=[]))

    @staticmethod
    async def ro_scopes(user):
        """Compute scopes for RestaurantOwner."""
        # Check Redis cache for scopes
        cache_key = f"scopes:{user.role}:{user.id}"
        client = Redis.from_url(settings.REDIS_URL)
        cached_scopes = await client.get(cache_key)
        if cached_scopes:
            await client.close()
            return json.loads(cached_scopes)
        
        restaurant_ids = [
            id async for id in 
            user.restaurants.filter(status='active').values_list('id', flat=True)
        ]

        # Combine restaurant IDs
        restaurants_set = set(restaurant_ids) 
        branch_ids = [
            id async for id in
            Branch.objects.filter(restaurant_id__in=restaurants_set, is_active=True)
            .values_list('id', flat=True)
        ]

        groups = [g.name async for g in user.groups.all()]
        
        scopes = {
            'restaurants': restaurants_set,
            'branches': set(branch_ids),
            'groups': set(groups)
        }
        print("rm scopes: ", scopes)
        await client.set(cache_key, json.dumps(scopes, default=list), ex=3600)  # Cache for 1 hour
        await client.close()
        return scopes

    @staticmethod
    async def ro_queryset_filter(user, model, scopes):
        """Filter querysets for RestaurantOwner."""
        restaurants = scopes['restaurants']
        return {
            Override: Q(rule__restaurant__in=restaurants),
            Device: Q(branch__restaurant__in=restaurants),
            ShiftSwapRequest: Q(branch__restaurant__in=restaurants),
            Rule: Q(restaurant__in=restaurants, is_active=True),
            OrderItem: Q(order__branch__restaurant__in=restaurants),
            Order: Q(branch__restaurant__in=restaurants),
            MenuItem: Q(category__menu__branch__restaurant__in=restaurants),
            MenuCategory: Q(menu__branch__restaurant__in=restaurants),
            Menu: Q(branch__restaurant__in=restaurants),
            OvertimeRequest: Q(staff_shift__branch__restaurant__in=restaurants),
            StaffShift: Q(branch__restaurant__in=restaurants),
            ShiftPattern: Q(branch__restaurant__in=restaurants),
            Shift: Q(branch__restaurant__in=restaurants),
            Branch: Q(restaurant__in=restaurants, is_active=True),
            Restaurant: Q(id__in=restaurants, status='active'),
            EmployeeTransfer: Q(from_branch__restaurant__in=restaurants) | Q(initiated_by=user),
            CustomUser: Q(restaurants__in=restaurants)
        }.get(model, Q(pk__in=[]))

    @staticmethod
    async def rm_scopes(user):
        """Compute scopes for RestaurantManager."""
        # Check Redis cache for scopes
        cache_key = f"scopes:{user.role}:{user.id}"
        client = Redis.from_url(settings.REDIS_URL)
        cached_scopes = await client.get(cache_key)
        if cached_scopes:
            await client.close()
            return json.loads(cached_scopes)
        
        # Get base restaurant IDs (user.restaurants.all())
        user_restaurant_ids = [
            id async for id in 
            user.restaurants.filter(status='active').values_list('id', flat=True)
        ]
        
        # Get managed restaurant IDs
        managed_restaurant_ids = [
            id async for id in
            Restaurant.objects.filter(manager=user, status='active')
            .values_list('id', flat=True)
        ]
        
        # Combine restaurant IDs
        restaurants_set = set(user_restaurant_ids) | set(managed_restaurant_ids)
        
        # Get branch IDs
        branch_ids = [
            id async for id in
            Branch.objects.filter(
                restaurant_id__in=restaurants_set,
                is_active=True
            ).values_list('id', flat=True)
        ]

        groups = [g.name async for g in user.groups.all()]
        
        scopes = {
            'restaurants': restaurants_set,
            'branches': set(branch_ids),
            'groups': set(groups)
        }
        await client.set(cache_key, json.dumps(scopes, default=list), ex=3600)  # Cache for 1 hour
        await client.close()
        return scopes

    @staticmethod
    async def rm_queryset_filter(user, model, scopes):
        """Filter querysets for RestaurantManager."""
        restaurants = scopes['restaurants']
        return {
            Override: Q(rule__restaurant__in=restaurants),
            Device: Q(branch__restaurant__in=restaurants),
            ShiftSwapRequest: Q(branch__restaurant__in=restaurants),
            Rule: Q(restaurant__in=restaurants, is_active=True),
            OrderItem: Q(order__branch__restaurant__in=restaurants),
            Order: Q(branch__restaurant__in=restaurants),
            MenuItem: Q(category__menu__branch__restaurant__in=restaurants),
            MenuCategory: Q(menu__branch__restaurant_id__in=restaurants),
            Menu: Q(branch__restaurant__in=restaurants),
            OvertimeRequest: Q(staff_shift__branch__restaurant__in=restaurants),
            StaffShift: Q(branch__restaurant__in=restaurants),
            ShiftPattern: Q(branch__restaurant__in=restaurants),
            Shift: Q(branch__restaurant__in=restaurants),
            Branch: Q(restaurant__in=restaurants, is_active=True),
            Restaurant: Q(id__in=restaurants, status='active'),
            EmployeeTransfer: Q(from_branch__restaurant__in=restaurants) | Q(from_restaurant__in=restaurants)
        }.get(model, Q(pk__in=[]))

    @staticmethod
    async def bm_scopes(user):
        """Compute scopes for BranchManager."""
        # Check Redis cache for scopes
        cache_key = f"scopes:{user.role}:{user.id}"
        client = Redis.from_url(settings.REDIS_URL)
        cached_scopes = await client.get(cache_key)
        if cached_scopes:
            await client.close()
            return json.loads(cached_scopes)
        
        branches = [
            id async for id in 
            user.branches.filter(is_active=True).values_list('id', flat=True)
        ]
        scopes = {'branches': set(branches)}
        await client.set(cache_key, json.dumps(scopes, default=list), ex=3600)  # Cache for 1 hour
        await client.close()
        return scopes

    @staticmethod
    async def bm_queryset_filter(user, model, scopes):
        """Filter querysets for BranchManager."""
        branches = scopes['branches']
        return {
            Override: Q(rule__branch_id__in=branches),
            Device: Q(branch_id__in=branches),
            ShiftSwapRequest: Q(branch_id__in=branches),
            Rule: Q(branch_id__in=branches, is_active=True),
            OrderItem: Q(order__branch_id__in=branches),
            Order: Q(branch_id__in=branches),
            MenuItem: Q(category__menu__branch__in=branches),
            MenuCategory: Q(menu__branch_id__in=branches),
            Menu: Q(branch_id__in=branches),
            OvertimeRequest: Q(staff_shift__branch_id__in=branches),
            StaffShift: Q(branch_id__in=branches),
            ShiftPattern: Q(branch_id__in=branches),
            Shift: Q(branch_id__in=branches),
            Branch: Q(id__in=branches, is_active=True),
            Restaurant: Q(branches__in=branches, status='active'),
            EmployeeTransfer: Q(from_branch__in=branches) | Q(manager=user)
        }.get(model, Q(pk__in=[]))

    @staticmethod
    async def default_scopes(user):
        """Default empty scopes."""
        return {'companies': set(), 'countries': set(), 'restaurants': set(), 'branches': set()}

    @staticmethod
    async def default_queryset_filter(user, model, scopes):
        """Default empty queryset filter."""
        return Q(pk__in=[])
    
from permissions.models import BranchPermissionAssignment
from asgiref.sync import sync_to_async
redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
async def get_user_permissions(user) -> list:
    cache_key = f"user_permissions:{user.id}"
    cached_perms = await redis_client.get(cache_key)
    
    if cached_perms:
        return json.loads(cached_perms)
    
    try:
        # Fetch active permissions for the user (direct or role-based) in a single query
        assignments = await sync_to_async(list)(
            BranchPermissionAssignment.objects.filter(
                Q(user=user) | Q(role=user.role),
                Q(end_time__gte=timezone.now()) | Q(end_time__isnull=True),
                status='active',
            ).select_related('permission').values(
                'permission_id',
                'permission__codename',
                'branch_id'
            ).iterator()
        )
        # Format permissions for caching and validation
        permissions = [
            {
                'permission_id': assignment['permission_id'],
                'codename': assignment['permission__codename'],
                'branch_id': assignment['branch_id']
            }
            for assignment in assignments
        ]
        
        # Cache permissions with 1-hour TTL
        await redis_client.set(cache_key, json.dumps(permissions), ex=3600)
        
        return permissions
    
    except Exception as e:
        logger.info(f"Error fetching permissions for user {user.id}")
        return []