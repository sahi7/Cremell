from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError

from asgiref.sync import sync_to_async
from django.utils.translation import gettext as _
from django.db.models import Q
from django.contrib.auth import get_user_model
from notifications.models import BranchActivity, RestaurantActivity
from django.db.models import Prefetch
from django.contrib.auth.models import Group
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
    role_value = user.get_role_value()
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


async def get_scopes_and_groups(user):
    # Prefetch companies, countries, and groups in one query
    user = await CustomUser.objects.prefetch_related('companies', 'countries', 'groups').aget(id=user.id)
    
    result = {
        'company': [c.id async for c in user.companies.all()], 
        'country': [c.id async for c in user.countries.all()],
        'groups': {g.name async for g in user.groups.all()}
    }
    return result

