import pytz
import json
import logging
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from redis.asyncio import Redis
from asgiref.sync import async_to_sync
from typing import List, Dict, Optional
from django.utils.translation import gettext as _
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils import timezone, translation
from django.template.loader import render_to_string
from notifications.models import BranchActivity, RestaurantActivity
from CRE.models import Branch, Restaurant, Company, Country

logger = logging.getLogger(__name__)
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
    role_to_create_value = await user.get_role_value(role_to_create)
    return role_to_create_value <= user_role_value


async def get_scopes_and_groups(user_id):
    # Prefetch companies, countries, and groups in one query
    user = await CustomUser.objects.prefetch_related('companies', 'countries', 'restaurants', 'groups').aget(id=user_id)
    
    result = {
        'company': [c.id async for c in user.companies.all()], 
        'country': [c.id async for c in user.countries.all()],
        'restaurant': [c.id async for c in user.restaurants.all()],
        'groups': {g.name async for g in user.groups.all()}
    }
    return result

async def get_user_data(user_id: int, branch_id: int = None, company_id: int = None, restaurant_id: int = None) -> dict:
    """Fetch comprehensive user data with prefetching and caching, supporting all roles."""
    redis_client = Redis.from_url('redis://localhost:6379')
    cache_key = f'user_data_{user_id}_{branch_id or "none"}_{company_id or "none"}_{restaurant_id or "none"}'
    data = await redis_client.get(cache_key)
    if data:
        return data.decode() if isinstance(data, bytes) else data

    # Prefetch all related objects to minimize database hits
    user = await CustomUser.objects.prefetch_related(
        'companies', 'countries', 'restaurants', 'branches', 'groups'
    ).aget(id=user_id)

    # Specific branch, company, or restaurant for context (optional)
    specific_branch = await Branch.objects.aget(id=branch_id) if branch_id else await user.get_associated_branch()
    specific_company = await user.companies.aget(id=company_id) if company_id else await user.companies.afirst()
    specific_restaurant = await user.restaurants.aget(id=restaurant_id) if restaurant_id else await user.restaurants.afirst()

    # Collect IDs and names for all associated objects
    data = {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role,
        'role_value': await user.get_role_value(),
        'timezone': await user.get_effective_timezone(),
        'language': await user.get_effective_language(),
        'companies': [{'id': c.id, 'name': c.name} async for c in user.companies.all()],
        'countries': [{'id': c.id, 'name': c.name, 'code': c.code} async for c in user.countries.all()],
        'restaurants': [{'id': r.id, 'name': r.name} async for r in user.restaurants.all()],
        'branches': [{'id': b.id, 'name': b.name} async for b in user.branches.all()],
        'groups': {g.name async for g in user.groups.all()},
        # Contextual data for specific scope
        'organization_name': (
            specific_branch.restaurant.company.name if specific_branch else
            specific_restaurant.company.name if specific_restaurant else
            specific_company.name if specific_company else 'Unknown'
        ),
        'branch_name': specific_branch.name if specific_branch else None,
        'restaurant_name': specific_restaurant.name if specific_restaurant else None,
    }

    await redis_client.set(cache_key, data, ex=3600)  # Cache for 1 hour
    return data

async def render_notification_template(user_data: dict, message: str, template_name: str, extra_context: dict = None) -> str:
    """Render notification template with user’s language and timezone."""
    user_tz = pytz.timezone(user_data['timezone'])
    localized_timestamp = timezone.localtime(timezone.now(), user_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
    
    # Base required context
    context = {
        'user_name': user_data['username'],
        'role': user_data['role'],
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
    users = {u.id: u async for u in CustomUser.objects.filter(id__in=user_ids).only('id', 'username', 'email', 'role')}
    timezones = await CustomUser.get_timezone_language(user_ids) 
    # Process users in chunks
    for start in range(0, count, chunk_size):
        async for user_data in queryset.aiterator(chunk_size=chunk_size):
            user_id = user_data['employees__id'] if branch_id or restaurant_id else user_data['users__id']
            if not user_id:
                continue
            user = users.get(user_id)
            if not user:
                continue
            role_value = await user.get_role_value()
            if role_value > max_role_value and not include_lower_roles:
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
                stakeholder.update({
                    'companies': [{'id': c.id, 'name': c.name} async for c in user.companies.all()] if company_id else [],
                    'countries': [{'id': c.id, 'name': c.name, 'code': c.code} async for c in user.countries.all()] if country_id else [],
                    'restaurants': [{'id': r.id, 'name': r.name} async for r in user.restaurants.all()] if restaurant_id else [],
                    'branches': [{'id': b.id, 'name': b.name} async for b in user.branches.all()] if branch_id else [],
                })

            stakeholders.append(stakeholder)   

    # Cache result
    # await redis_client.set(cache_key, json.dumps(stakeholders), ex=3600)
    return stakeholders


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

