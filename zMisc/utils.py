from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError

from asgiref.sync import sync_to_async
from django.utils.translation import gettext as _
from django.db.models import Q
from notifications.models import BranchActivity, RestaurantActivity
from CRE.models import Branch, Restaurant

def check_user_role(user, max_role_value=4):
    """
    Check if a user's role value is within the allowed range.
    Raises a PermissionDenied exception if not.

    Args:
        user (CustomUser): The user object to check.
        max_role_value (int): The maximum allowed role value.

    Returns:
        None: If the user is authorized.
    """
    role_value = user.get_role_value()
    if role_value is None or role_value > max_role_value:
        raise PermissionDenied(_("You are not authorized to perform this action."))
    return role_value

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

def filter_queryset_by_scopes(queryset, user, allowed_scopes):
    """
    Filters the queryset based on user roles and allowed scopes with complex logic.
    
    :param queryset: The original queryset to filter.
    :param user: The current user performing the query.
    :param allowed_scopes: Dictionary of fields with the corresponding scope to filter.
    :return: Filtered queryset based on the allowed scopes.
    """
    if not allowed_scopes:
        return queryset.none()
    for field, scope in allowed_scopes.items():
        # Apply complex filtering for specific fields based on the type of scope
        if isinstance(scope, Q):  # If the scope is a complex Q object
            queryset = queryset.filter(scope)
        elif isinstance(scope, tuple):  # Check if scope is a tuple (field, value)
            queryset = queryset.filter(**{field: scope[0]})
        elif isinstance(scope, dict):  # Handling more complex nested filters
            for key, value in scope.items():
                queryset = queryset.filter(**{key: value})
        elif callable(scope):  # If scope is a callable function (custom logic)
            queryset = scope(queryset, user)

        if not queryset.exists():
            raise PermissionDenied(_("You do not have permission to access this data."))

    return queryset


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


async def log_activity(user, activity_type, details=None, obj=None, obj_type=None):
    """
    Logs an activity for a target user, choosing the model based on their role scope.
    
    Args:
        activity_type: Matches ACTIVITY_CHOICES (e.g., 'manager_assign', 'status_update').
        user: The CustomUser performing the action (e.g., request.user).
        details: JSON-compatible dict with additional info (optional).
        obj: Object (e.g., Restaurant instance) to set as scope_field value.
    """
    # Determine model and scope field
    model, scope_field = await sync_to_async(determine_activity_model)(user, obj_type)

    # Validate and set scope_value from obj
    if obj:
        expected_obj = Restaurant if scope_field == 'restaurant' else Branch
        if not isinstance(obj, expected_obj):
            raise ValueError(_("{scope_field} object must be a {expected_obj}").format(scope_field=scope_field, expected_obj=expected_obj.__name__))
        scope_value = obj
    else:
        scope_value = None

    # Prepare activity data
    activity_data = {
        scope_field: scope_value,
        'activity_type': activity_type,
        'user': user,
        'details': details or {},
    }

    # Create activity
    await sync_to_async(model.objects.create)(**activity_data)
