from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError

from asgiref.sync import async_to_sync
from django.utils.translation import gettext as _
from django.db.models import Q
from django.contrib.auth import get_user_model
from notifications.models import BranchActivity, RestaurantActivity
from CRE.models import Branch, Restaurant, Company, Country

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

